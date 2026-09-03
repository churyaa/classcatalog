from __future__ import annotations

import argparse
import base64
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence

from classcatalog.models import CourseSection, ProfessorMetrics

RMP_GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
RMP_SCHOOL_LEGACY_ID = 877
RMP_SCHOOL_RELAY_ID = "U2Nob29sLTg3Nw=="  # base64("School-877")
RMP_PROFILE_BASE_URL = "https://www.ratemyprofessors.com/professor/"
RMP_AUTHORIZATION = "Basic dGVzdDp0ZXN0"
RMP_PROVIDER = "RateMyProfessors"

# SDSU roster names do not always match the preferred/display name used by RMP.
# Keys are normalized SDSU names; values are additional RMP search/name variants.
RMP_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "mark dunster": ("Timothy (Mark) Dunster",),
    "satchithanandam venkataraman": ("Satchi Venkataraman",),
}

# Stable RMP legacy profile IDs for SDSU roster names whose RMP display name
# differs enough that the public name-search endpoint can omit the profile.
# These are used as deterministic fallbacks; the live profile metrics are still
# fetched from RateMyProfessors during sync rather than hard-coded.
RMP_PROFILE_ID_ALIASES: dict[str, int] = {
    "mark dunster": 1681,
    "satchithanandam venkataraman": 444442,
}

SEARCH_QUERY = """
query NewSearchTeachersQuery($query: TeacherSearchQuery!) {
  newSearch {
    teachers(query: $query) {
      resultCount
      edges {
        node {
          id
          legacyId
          firstName
          lastName
          department
          avgRating
          avgDifficulty
          numRatings
          wouldTakeAgainPercent
        }
      }
    }
  }
}
""".strip()

DETAIL_QUERY = """
query GetTeacher($id: ID!) {
  node(id: $id) {
    __typename
    ... on Teacher {
      id
      legacyId
      firstName
      lastName
      department
      avgRating
      avgDifficulty
      numRatings
      wouldTakeAgainPercent
      courseCodes { courseName courseCount }
    }
  }
}
""".strip()


class RatingsSource(Protocol):
    async def find_professor(
        self,
        *,
        name: str,
        department: str | None,
    ) -> ProfessorMetrics | None: ...


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def canonical_instructor_name(name: str) -> str:
    """Normalize SDSU/RMP instructor names without making fuzzy identity guesses."""

    value = " ".join((name or "").strip().split())
    if not value:
        return ""
    if value.count(",") == 1:
        last, first = (part.strip() for part in value.split(",", maxsplit=1))
        if first and last:
            value = f"{first} {last}"
    return " ".join(value.split())


def normalize_person_name(name: str) -> str:
    value = canonical_instructor_name(name)
    value = _ascii_fold(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _name_signature(name: str) -> tuple[str, str]:
    tokens = normalize_person_name(name).split()
    if not tokens:
        return "", ""
    return tokens[0], tokens[-1]


def is_placeholder_instructor(name: str | None) -> bool:
    normalized = normalize_person_name(name or "")
    return normalized in {
        "",
        "staff",
        "tba",
        "to be announced",
        "instructor tba",
        "instructor",
        "unknown",
    }


def normalize_course_code(course_code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (course_code or "").upper())


class JsonFileRatingsSource:
    """Local/cached professor-rating adapter.

    Supports both the original list fixture format and the RateMyProfessors sync
    document introduced by ClassCatalog v37.
    """

    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = raw.get("records", []) if isinstance(raw, dict) else raw
        self._records: dict[str, ProfessorMetrics] = {}
        for record in records:
            name = record.get("name") or record.get("query_name")
            metrics = record.get("metrics")
            if not name or not metrics:
                continue
            self._records[normalize_person_name(name)] = ProfessorMetrics.model_validate(metrics)

    @property
    def count(self) -> int:
        return len(self._records)

    @staticmethod
    def _key(name: str, department: str | None = None) -> str:
        del department
        return normalize_person_name(name)

    async def find_professor(
        self,
        *,
        name: str,
        department: str | None,
    ) -> ProfessorMetrics | None:
        return self._records.get(self._key(name, department))

    def find_professor_sync(self, name: str) -> ProfessorMetrics | None:
        return self._records.get(self._key(name))


@dataclass(frozen=True)
class InstructorTarget:
    name: str
    normalized_name: str
    course_codes: tuple[str, ...]


@dataclass(frozen=True)
class RmpCandidate:
    node_id: str
    legacy_id: int
    name: str
    department: str | None
    rating: float | None
    difficulty: float | None
    num_reviews: int
    would_take_again_percent: float | None

    @classmethod
    def from_node(cls, node: dict[str, object]) -> "RmpCandidate | None":
        legacy_id = node.get("legacyId")
        node_id = node.get("id")
        if not isinstance(legacy_id, int) or not isinstance(node_id, str):
            return None
        first = str(node.get("firstName") or "").strip()
        last = str(node.get("lastName") or "").strip()
        name = " ".join(part for part in (first, last) if part).strip()
        if not name:
            return None
        num_reviews = int(node.get("numRatings") or 0)

        def metric(value: object, *, allow_negative: bool = False) -> float | None:
            if value is None:
                return None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return None
            if not allow_negative and parsed < 0:
                return None
            return parsed

        rating = metric(node.get("avgRating")) if num_reviews else None
        difficulty = metric(node.get("avgDifficulty")) if num_reviews else None
        take_again = metric(node.get("wouldTakeAgainPercent"))
        if take_again is not None and take_again < 0:
            take_again = None
        return cls(
            node_id=node_id,
            legacy_id=legacy_id,
            name=name,
            department=str(node.get("department") or "").strip() or None,
            rating=rating,
            difficulty=difficulty,
            num_reviews=max(0, num_reviews),
            would_take_again_percent=take_again,
        )

    def metrics(self, confidence: float) -> ProfessorMetrics:
        return ProfessorMetrics(
            provider=RMP_PROVIDER,
            external_id=str(self.legacy_id),
            rating=self.rating,
            difficulty=self.difficulty,
            would_take_again_percent=self.would_take_again_percent,
            num_reviews=self.num_reviews,
            profile_url=f"{RMP_PROFILE_BASE_URL}{self.legacy_id}",
            match_confidence=confidence,
        )


class RmpGraphqlClient:
    def __init__(
        self,
        *,
        timeout: float = 15.0,
        retries: int = 3,
        user_agent: str = "ClassCatalog/0.1 professor-ratings sync",
    ) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.user_agent = user_agent

    def _post(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            RMP_GRAPHQL_URL,
            data=payload,
            headers={
                "Authorization": RMP_AUTHORIZATION,
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                if raw.get("errors"):
                    raise RuntimeError(f"RateMyProfessors GraphQL error: {raw['errors']}")
                data = raw.get("data")
                if not isinstance(data, dict):
                    raise RuntimeError("RateMyProfessors returned no GraphQL data")
                return data
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= self.retries:
                    raise
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= self.retries:
                    raise
            time.sleep(2**attempt)
        raise RuntimeError("RateMyProfessors request failed") from last_error

    def search(self, name: str) -> tuple[RmpCandidate, ...]:
        data = self._post(
            SEARCH_QUERY,
            {"query": {"text": canonical_instructor_name(name), "schoolID": RMP_SCHOOL_RELAY_ID}},
        )
        teachers = data.get("newSearch")
        if not isinstance(teachers, dict):
            return ()
        teachers = teachers.get("teachers")
        if not isinstance(teachers, dict):
            return ()
        edges = teachers.get("edges")
        if not isinstance(edges, list):
            return ()
        candidates: list[RmpCandidate] = []
        for edge in edges:
            if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
                continue
            candidate = RmpCandidate.from_node(edge["node"])
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    @staticmethod
    def relay_teacher_id(legacy_id: int) -> str:
        return base64.b64encode(f"Teacher-{legacy_id}".encode("ascii")).decode("ascii")

    def lookup_legacy_id(self, legacy_id: int) -> RmpCandidate | None:
        data = self._post(DETAIL_QUERY, {"id": self.relay_teacher_id(legacy_id)})
        node = data.get("node")
        if not isinstance(node, dict):
            return None
        return RmpCandidate.from_node(node)

    def course_codes(self, node_id: str) -> frozenset[str]:
        data = self._post(DETAIL_QUERY, {"id": node_id})
        node = data.get("node")
        if not isinstance(node, dict):
            return frozenset()
        rows = node.get("courseCodes")
        if not isinstance(rows, list):
            return frozenset()
        return frozenset(
            normalize_course_code(str(row.get("courseName") or ""))
            for row in rows
            if isinstance(row, dict) and row.get("courseName")
        )


def collect_instructor_targets(sections: Sequence[CourseSection]) -> tuple[InstructorTarget, ...]:
    by_name: dict[str, dict[str, object]] = {}
    for section in sections:
        names: list[str] = []
        if section.instructor:
            names.append(section.instructor)
        names.extend(meeting.instructor for meeting in section.meetings if meeting.instructor)
        for raw_name in names:
            if is_placeholder_instructor(raw_name):
                continue
            display = canonical_instructor_name(raw_name)
            normalized = normalize_person_name(display)
            if not normalized:
                continue
            entry = by_name.setdefault(normalized, {"name": display, "courses": set()})
            courses = entry["courses"]
            assert isinstance(courses, set)
            courses.add(normalize_course_code(section.course_code))
    return tuple(
        InstructorTarget(
            name=str(entry["name"]),
            normalized_name=normalized,
            course_codes=tuple(sorted(str(code) for code in entry["courses"] if code)),
        )
        for normalized, entry in sorted(by_name.items())
    )


def _target_name_variants(target: InstructorTarget) -> tuple[str, ...]:
    variants = [target.name]
    variants.extend(RMP_NAME_ALIASES.get(target.normalized_name, ()))
    # Preserve order while avoiding duplicate normalized variants.
    seen: set[str] = set()
    result: list[str] = []
    for value in variants:
        normalized = normalize_person_name(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return tuple(result)


def _variant_name_score(target_name: str, candidate_name_raw: str) -> float:
    target_name = normalize_person_name(target_name)
    candidate_name = normalize_person_name(candidate_name_raw)
    if not target_name or not candidate_name:
        return 0.0
    if candidate_name == target_name:
        return 1.0

    target_tokens = target_name.split()
    candidate_tokens = candidate_name.split()
    target_first, target_last = target_tokens[0], target_tokens[-1]
    candidate_first, candidate_last = candidate_tokens[0], candidate_tokens[-1]
    if not target_last or target_last != candidate_last:
        return 0.0

    # RMP often stores a legal first name plus a parenthetical/preferred name,
    # e.g. "Timothy (Mark) Dunster" for SDSU's "Mark Dunster".
    candidate_given_names = candidate_tokens[:-1]
    if target_first in candidate_given_names:
        return 0.98

    # Conservative long-name/short-name handling with the same surname. This
    # covers forms such as Satchithanandam -> Satchi without making a broad
    # fuzzy identity guess. Require a meaningful common prefix.
    for candidate_given in candidate_given_names:
        shorter = min(len(target_first), len(candidate_given))
        if shorter >= 4 and (target_first.startswith(candidate_given) or candidate_given.startswith(target_first)):
            return 0.94

    ratio = SequenceMatcher(None, target_name, candidate_name).ratio()
    if target_first == candidate_first and ratio >= 0.86:
        return min(0.96, ratio)
    if target_first and candidate_first and target_first[0] == candidate_first[0] and ratio >= 0.82:
        return min(0.88, ratio)
    return 0.0


def _candidate_name_score(target: InstructorTarget, candidate: RmpCandidate) -> float:
    scores = [_variant_name_score(variant, candidate.name) for variant in _target_name_variants(target)]
    if not scores:
        return 0.0
    best = max(scores)
    # An explicit alias is highly trusted, but preserve 1.0 for a literal SDSU/RMP exact match.
    if normalize_person_name(candidate.name) != target.normalized_name and best == 1.0:
        return 0.99
    return best


def select_candidate(
    target: InstructorTarget,
    candidates: Sequence[RmpCandidate],
    *,
    course_lookup: Callable[[str], Iterable[str]] | None = None,
) -> tuple[RmpCandidate | None, float | None, str]:
    scored = [(candidate, _candidate_name_score(target, candidate)) for candidate in candidates]
    scored = [(candidate, score) for candidate, score in scored if score > 0]
    if not scored:
        return None, None, "no_name_match"
    best_score = max(score for _, score in scored)
    best = [candidate for candidate, score in scored if abs(score - best_score) < 1e-9]
    if len(best) == 1:
        return best[0], best_score, "matched"

    if course_lookup is not None and target.course_codes:
        target_courses = frozenset(target.course_codes)
        course_matches: list[RmpCandidate] = []
        for candidate in best:
            try:
                candidate_courses = frozenset(course_lookup(candidate.node_id))
            except Exception:
                continue
            if target_courses & candidate_courses:
                course_matches.append(candidate)
        if len(course_matches) == 1:
            return course_matches[0], min(1.0, best_score + 0.01), "matched_by_course"
    return None, None, "ambiguous_name"


def load_ratings_metrics(path: Path | None) -> tuple[dict[str, ProfessorMetrics], int]:
    if path is None or not path.is_file():
        return {}, 0
    source = JsonFileRatingsSource(path)
    return dict(source._records), source.count


def apply_cached_ratings(
    sections: Sequence[CourseSection],
    path: Path | None,
) -> tuple[tuple[CourseSection, ...], int]:
    metrics_by_name, record_count = load_ratings_metrics(path)
    if not metrics_by_name:
        return tuple(sections), record_count
    enriched: list[CourseSection] = []
    for section in sections:
        metrics = metrics_by_name.get(normalize_person_name(section.instructor or ""))
        enriched.append(section.model_copy(update={"professor": metrics}) if metrics else section)
    return tuple(enriched), record_count


def _load_sync_records(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    records = raw.get("records", []) if isinstance(raw, dict) else []
    result: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized = str(record.get("normalized_name") or normalize_person_name(str(record.get("name") or "")))
        if normalized and record.get("metrics"):
            result[normalized] = record
    return result


def _write_sync_document(
    path: Path,
    records: Iterable[dict[str, object]],
    *,
    targets: int,
    matched: int,
    unmatched: Sequence[dict[str, object]],
    errors: Sequence[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "provider": RMP_PROVIDER,
        "school": {
            "name": "San Diego State University",
            "legacy_id": RMP_SCHOOL_LEGACY_ID,
            "relay_id": RMP_SCHOOL_RELAY_ID,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "instructors": targets,
            "matched": matched,
            "unmatched": len(unmatched),
            "errors": len(errors),
        },
        "records": sorted(records, key=lambda item: str(item.get("normalized_name") or "")),
        "unmatched": list(unmatched),
        "errors": list(errors),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sync_rate_my_professors(
    *,
    sections_path: Path,
    output_path: Path,
    delay_seconds: float = 0.35,
    timeout: float = 15.0,
    refresh: bool = False,
    limit: int | None = None,
    names: Sequence[str] | None = None,
    client: RmpGraphqlClient | None = None,
) -> int:
    raw_sections = json.loads(sections_path.read_text(encoding="utf-8"))
    sections = tuple(CourseSection.model_validate(item) for item in raw_sections)
    all_targets = collect_instructor_targets(sections)
    all_target_names = {target.normalized_name for target in all_targets}

    selected_names = {normalize_person_name(name) for name in (names or ()) if normalize_person_name(name)}
    targets = tuple(
        target for target in all_targets
        if not selected_names or target.normalized_name in selected_names
    )
    if limit is not None:
        targets = targets[: max(0, limit)]

    existing = _load_sync_records(output_path)
    existing = {key: value for key, value in existing.items() if key in all_target_names}
    if refresh and not selected_names:
        cached: dict[str, dict[str, object]] = {}
    elif refresh:
        cached = {key: value for key, value in existing.items() if key not in selected_names}
    else:
        cached = existing
    records = dict(cached)
    unmatched: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    api = client or RmpGraphqlClient(timeout=timeout)

    print(
        "rmp_sync_started "
        f"school='San Diego State University' instructors={len(targets)} cached={len(cached)} "
        f"output='{output_path}'"
    )
    if selected_names:
        missing_requested = sorted(selected_names - {target.normalized_name for target in targets})
        if missing_requested:
            print(f"rmp_sync_requested_names_not_found names={missing_requested!r}")
    processed_since_save = 0
    for index, target in enumerate(targets, start=1):
        if target.normalized_name in records:
            continue
        try:
            candidates_by_id: dict[str, RmpCandidate] = {}

            # A few SDSU roster names have known RMP profiles that the public
            # text search does not reliably return. Fetch those stable profile
            # IDs directly first, then merge normal search results as usual.
            known_profile_id = RMP_PROFILE_ID_ALIASES.get(target.normalized_name)
            lookup_legacy_id = getattr(api, "lookup_legacy_id", None)
            if known_profile_id is not None and callable(lookup_legacy_id):
                known_candidate = lookup_legacy_id(known_profile_id)
                if known_candidate is not None:
                    candidates_by_id[known_candidate.node_id] = known_candidate

            search_variants = _target_name_variants(target)
            for search_name in search_variants:
                for candidate_item in api.search(search_name):
                    candidates_by_id[candidate_item.node_id] = candidate_item
            candidates = tuple(candidates_by_id.values())
            candidate, confidence, reason = select_candidate(
                target,
                candidates,
                course_lookup=api.course_codes,
            )
            if candidate is None or confidence is None:
                unmatched.append(
                    {
                        "name": target.name,
                        "normalized_name": target.normalized_name,
                        "course_codes": list(target.course_codes),
                        "reason": reason,
                        "candidate_count": len(candidates),
                    }
                )
            else:
                records[target.normalized_name] = {
                    "name": target.name,
                    "normalized_name": target.normalized_name,
                    "course_codes": list(target.course_codes),
                    "rmp_name": candidate.name,
                    "rmp_department": candidate.department,
                    "metrics": candidate.metrics(confidence).model_dump(mode="json"),
                }
        except Exception as exc:  # network/API errors should be resumable
            errors.append(
                {
                    "name": target.name,
                    "normalized_name": target.normalized_name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        processed_since_save += 1
        if processed_since_save >= 10:
            _write_sync_document(
                output_path,
                records.values(),
                targets=len(targets),
                matched=len(records),
                unmatched=unmatched,
                errors=errors,
            )
            processed_since_save = 0
        if index < len(targets) and delay_seconds > 0:
            time.sleep(delay_seconds)

    _write_sync_document(
        output_path,
        records.values(),
        targets=len(targets),
        matched=len(records),
        unmatched=unmatched,
        errors=errors,
    )
    print(
        "rmp_sync_completed "
        f"status={'passed' if not errors else 'partial'} instructors={len(targets)} "
        f"matched={len(records)} unmatched={len(unmatched)} errors={len(errors)} "
        f"output='{output_path}'"
    )
    return 0 if not errors else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cache RateMyProfessors metrics for instructors in the ClassCatalog dataset."
    )
    parser.add_argument(
        "--sections",
        type=Path,
        default=Path(__file__).parent / "data" / "sections.json",
        help="ClassCatalog sections.json input.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "data" / "professor_ratings.json",
        help="Cached professor ratings JSON output.",
    )
    parser.add_argument("--delay", type=float, default=0.35, help="Delay between professor lookups in seconds.")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds.")
    parser.add_argument("--refresh", action="store_true", help="Refresh already matched professors instead of resuming.")
    parser.add_argument(
        "--name",
        action="append",
        default=None,
        help="Only sync this SDSU instructor name. May be supplied more than once.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of instructors for testing.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.sections.is_file():
        raise SystemExit(f"sections data not found: {args.sections}")
    return sync_rate_my_professors(
        sections_path=args.sections,
        output_path=args.output,
        delay_seconds=max(0.0, args.delay),
        timeout=max(1.0, args.timeout),
        refresh=args.refresh,
        limit=args.limit,
        names=args.name,
    )


if __name__ == "__main__":
    raise SystemExit(main())
