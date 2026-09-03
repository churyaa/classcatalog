from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_DEGREE_REPLACEMENTS = {
    "b.a.": " ba ",
    "b.s.": " bs ",
    "b.f.a.": " bfa ",
    "b.m.": " bm ",
    "b.arch.": " barch ",
    "b.mus.": " bmus ",
}
_DEGREE_TOKENS = {"ba", "bs", "bfa", "bm", "barch", "bmus"}
_STRUCTURE_TOKENS = {
    "emphasis",
    "specialization",
    "concentration",
    "track",
    "degree",
    "major",
}
_FILLER_TOKENS = {"in", "the", "of", "for", "with", "and"}


@dataclass(frozen=True, slots=True)
class AcademicPlan:
    plan_code: str
    name: str
    catalog_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageMatch:
    plan_code: str
    plan_name: str
    catalog_name: str
    method: str


@dataclass(frozen=True, slots=True)
class CoverageReport:
    expected_count: int
    scraped_count: int
    unique_scraped_count: int
    matched_count: int
    matched_catalog_count: int
    matches: tuple[CoverageMatch, ...]
    missing: tuple[AcademicPlan, ...]
    unexpected: tuple[str, ...]
    ambiguous: tuple[tuple[AcademicPlan, tuple[str, ...]], ...]

    @property
    def coverage_percent(self) -> float:
        if not self.expected_count:
            return 100.0
        return self.matched_count / self.expected_count * 100.0


def _ascii(value: str) -> str:
    value = html.unescape(value)
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def normalize_program_name(value: str) -> str:
    value = _ascii(value).casefold().replace("&", " and ")
    for old, new in _DEGREE_REPLACEMENTS.items():
        value = value.replace(old, new)
    value = re.sub(r"\bsped\b", " special education ", value)
    value = re.sub(r"\bmgt\b", " management ", value)
    value = re.sub(r"\bops\b", " operations ", value)
    value = re.sub(r"\btech\b", " technology ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(normalize_program_name(value).split())


def _degree_tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in _tokens(value) if token in _DEGREE_TOKENS)


def program_signature(value: str, *, include_degree: bool) -> tuple[str, ...]:
    tokens: list[str] = []
    for token in _tokens(value):
        if token in _FILLER_TOKENS or token in _STRUCTURE_TOKENS:
            continue
        if not include_degree and token in _DEGREE_TOKENS:
            continue
        tokens.append(token)
    return tuple(tokens)


def load_expected_programs(path: Path) -> tuple[AcademicPlan, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("plans", payload) if isinstance(payload, dict) else payload
    plans: list[AcademicPlan] = []
    for row in rows:
        plans.append(
            AcademicPlan(
                plan_code=str(row["plan_code"]),
                name=str(row["name"]),
                catalog_names=tuple(str(item) for item in row.get("catalog_names", ())),
            )
        )
    return tuple(plans)


def validate_program_coverage(
    expected: Iterable[AcademicPlan],
    scraped_names: Iterable[str],
) -> CoverageReport:
    expected = tuple(expected)
    raw_scraped_names = tuple(scraped_names)
    scraped_names = tuple(dict.fromkeys(raw_scraped_names))

    exact_lookup: dict[str, list[str]] = {}
    degree_lookup: dict[tuple[str, ...], list[str]] = {}
    no_degree_lookup: dict[tuple[str, ...], list[str]] = {}
    for name in scraped_names:
        exact_lookup.setdefault(normalize_program_name(name), []).append(name)
        degree_lookup.setdefault(program_signature(name, include_degree=True), []).append(name)
        no_degree_lookup.setdefault(program_signature(name, include_degree=False), []).append(name)

    matches: list[CoverageMatch] = []
    missing: list[AcademicPlan] = []
    ambiguous: list[tuple[AcademicPlan, tuple[str, ...]]] = []
    matched_catalog_names: set[str] = set()

    for plan in expected:
        candidate_names = (plan.name, *plan.catalog_names)
        selected: str | None = None
        method = ""

        # Explicit aliases and exact normalized labels are the strongest match.
        for candidate in candidate_names:
            hits = exact_lookup.get(normalize_program_name(candidate), ())
            if len(hits) == 1:
                selected = hits[0]
                method = "exact"
                break

        # Next, compare semantic signatures while preserving an explicitly supplied degree.
        if selected is None:
            for candidate in candidate_names:
                signature = program_signature(candidate, include_degree=True)
                hits = degree_lookup.get(signature, ())
                if len(hits) == 1:
                    selected = hits[0]
                    method = "signature"
                    break

        # SDSU's plan dropdown often omits BA/BS when only one catalog program has that
        # semantic name.  Permit a degree-less match only when it is unique.
        if selected is None:
            candidate_hits: set[str] = set()
            for candidate in candidate_names:
                if _degree_tokens(candidate):
                    continue
                signature = program_signature(candidate, include_degree=False)
                candidate_hits.update(no_degree_lookup.get(signature, ()))
            if len(candidate_hits) == 1:
                selected = next(iter(candidate_hits))
                method = "unique_without_degree"
            elif len(candidate_hits) > 1:
                ambiguous.append((plan, tuple(sorted(candidate_hits))))
                continue

        if selected is None:
            missing.append(plan)
            continue

        matches.append(
            CoverageMatch(
                plan_code=plan.plan_code,
                plan_name=plan.name,
                catalog_name=selected,
                method=method,
            )
        )
        matched_catalog_names.add(selected)

    unexpected = tuple(name for name in scraped_names if name not in matched_catalog_names)
    return CoverageReport(
        expected_count=len(expected),
        scraped_count=len(raw_scraped_names),
        unique_scraped_count=len(scraped_names),
        matched_count=len(matches),
        matched_catalog_count=len(matched_catalog_names),
        matches=tuple(matches),
        missing=tuple(missing),
        unexpected=unexpected,
        ambiguous=tuple(ambiguous),
    )
