from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from classcatalog.dataset.install import (
    _active_terms,
    _incoming_term,
    _load_sections,
    _matches_term,
    _section_sort_key,
    _validate_unique_sections,
    _write_sections,
)
from classcatalog.models import CourseSection
from classcatalog.ratings import is_placeholder_instructor
from classcatalog.repository import CourseRepository
from classcatalog.seo import SeoCatalog, build_sitemap_xml


@dataclass(frozen=True, slots=True)
class CachePruneSummary:
    path: str | None
    records_before: int = 0
    records_after: int = 0
    sources_before: int = 0
    sources_after: int = 0
    skipped: bool = False
    error: str | None = None

    @property
    def removed_records(self) -> int:
        return max(0, self.records_before - self.records_after)

    @property
    def removed_sources(self) -> int:
        return max(0, self.sources_before - self.sources_after)


@dataclass(frozen=True, slots=True)
class InventoryRotationPlan:
    incoming_term: str
    incoming_term_code: str | None
    retire_term: str
    active_terms_before: tuple[str, ...]
    candidate_terms: tuple[str, ...]
    active_sections_before: int
    incoming_sections: int
    replaced_incoming_term_sections: int
    retired_sections: int
    preserved_sections: int
    candidate_sections: int
    candidate_physical_sections: int
    candidate_displayed_options: int
    candidate_courses: int
    candidate_subjects: int
    subjects_added: tuple[str, ...]
    subjects_removed: tuple[str, ...]
    courses_added: tuple[str, ...]
    courses_removed: tuple[str, ...]
    new_instructors: tuple[str, ...]
    coverage_status: str
    api_validation_status: str
    sitemap_urls: int


@dataclass(frozen=True, slots=True)
class InventoryRotationApplySummary:
    plan: InventoryRotationPlan
    backup_path: str
    backup_sha256: str
    seat_cache: CachePruneSummary
    instructor_cache: CachePruneSummary


def _normalized_term(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _physical_keys(sections: Sequence[CourseSection]) -> set[tuple[str, str]]:
    return {
        ((section.term_code or section.term).strip(), section.schedule_number.strip())
        for section in sections
        if (section.term_code or section.term).strip() and section.schedule_number.strip()
    }


def _source_urls(sections: Sequence[CourseSection]) -> set[str]:
    return {
        str(section.source_url).strip()
        for section in sections
        if section.source_url and str(section.source_url).strip()
    }


def _instructor_names(sections: Sequence[CourseSection]) -> set[str]:
    return {
        " ".join((section.instructor or "").split())
        for section in sections
        if not is_placeholder_instructor(section.instructor)
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _candidate_rotation(
    incoming_path: Path,
    active_path: Path,
    retire_term: str,
) -> tuple[
    tuple[CourseSection, ...],
    tuple[CourseSection, ...],
    tuple[CourseSection, ...],
    str,
    str | None,
    tuple[CourseSection, ...],
]:
    incoming = _load_sections(incoming_path)
    incoming_term, incoming_term_code = _incoming_term(incoming)
    active = _load_sections(active_path)

    retire_normalized = _normalized_term(retire_term)
    if not retire_normalized:
        raise ValueError("Provide a non-empty --retire term.")
    if retire_normalized == _normalized_term(incoming_term):
        raise ValueError(
            "The incoming term and retired term are the same. Use the normal term installer "
            "to replace a rescraped term instead of rotating it."
        )

    retiring = tuple(
        section for section in active if _normalized_term(section.term) == retire_normalized
    )
    if not retiring:
        raise ValueError(f"No active sections were found for term {retire_term!r}.")

    preserved: list[CourseSection] = []
    for section in active:
        if _normalized_term(section.term) == retire_normalized:
            continue
        if _matches_term(section, term=incoming_term, term_code=incoming_term_code):
            continue
        preserved.append(section)

    candidate = tuple(sorted((*preserved, *incoming), key=_section_sort_key))
    if not candidate:
        raise ValueError("Refusing to create an empty active dataset.")
    _validate_unique_sections(candidate, context="inventory rotation candidate")
    return active, incoming, retiring, incoming_term, incoming_term_code, candidate


def _validate_candidate(
    candidate: Sequence[CourseSection],
    *,
    validate_api: bool,
) -> tuple[int, int, int, int, str, str, int]:
    repository = CourseRepository(candidate)
    audit = repository.coverage_audit()
    coverage_status = str(audit.status)
    if coverage_status != "passed":
        raise ValueError(
            "Candidate class coverage audit failed: "
            f"unaccounted_physical_sections={audit.unaccounted_physical_sections} "
            f"unaccounted_course_section_listings={audit.unaccounted_course_section_listings}."
        )

    seo_catalog = SeoCatalog.from_repository(repository)
    sitemap = build_sitemap_xml(seo_catalog)
    if "<urlset" not in sitemap:
        raise ValueError("Candidate SEO sitemap validation failed.")
    sitemap_urls = sitemap.count("<url>")

    api_status = "skipped"
    if validate_api:
        # Import lazily so simple term listing/retirement does not import the full
        # FastAPI validation stack or initialize application-level objects.
        from classcatalog.api_validation.validator import (
            ApiValidationConfig,
            validate_api_sections,
        )

        result = validate_api_sections(
            candidate,
            config=ApiValidationConfig(
                expected_sections=repository.total,
                expected_courses=repository.course_total,
                expected_physical_sections=repository.physical_section_total,
                expected_subjects=repository.subject_total,
                full_pagination=False,
            ),
        )
        api_status = str(result.report.status)
        if result.exit_code != 0:
            raise ValueError(
                "Candidate API validation failed. Run classcatalog-validate-api-data "
                "against the candidate for the detailed report."
            )

    return (
        repository.physical_section_total,
        audit.displayed_options,
        repository.course_total,
        repository.subject_total,
        coverage_status,
        api_status,
        sitemap_urls,
    )


def _build_rotation_plan(
    incoming_path: Path,
    active_path: Path,
    retire_term: str,
    *,
    validate_api: bool,
) -> tuple[InventoryRotationPlan, tuple[CourseSection, ...]]:
    (
        active,
        incoming,
        retiring,
        incoming_term,
        incoming_term_code,
        candidate,
    ) = _candidate_rotation(incoming_path, active_path, retire_term)

    replaced = sum(
        _matches_term(section, term=incoming_term, term_code=incoming_term_code)
        for section in active
        if _normalized_term(section.term) != _normalized_term(retire_term)
    )
    preserved_count = len(candidate) - len(incoming)

    (
        physical_sections,
        displayed_options,
        course_total,
        subject_total,
        coverage_status,
        api_status,
        sitemap_urls,
    ) = _validate_candidate(candidate, validate_api=validate_api)

    retiring_subjects = {section.subject for section in retiring}
    incoming_subjects = {section.subject for section in incoming}
    retiring_courses = {section.course_code for section in retiring}
    incoming_courses = {section.course_code for section in incoming}

    active_without_retiring = tuple(
        section
        for section in active
        if _normalized_term(section.term) != _normalized_term(retire_term)
        and not _matches_term(section, term=incoming_term, term_code=incoming_term_code)
    )
    known_instructors = _instructor_names(active_without_retiring)
    incoming_instructors = _instructor_names(incoming)

    return (
        InventoryRotationPlan(
            incoming_term=incoming_term,
            incoming_term_code=incoming_term_code,
            retire_term=retire_term.strip(),
            active_terms_before=_active_terms(active),
            candidate_terms=_active_terms(candidate),
            active_sections_before=len(active),
            incoming_sections=len(incoming),
            replaced_incoming_term_sections=replaced,
            retired_sections=len(retiring),
            preserved_sections=preserved_count,
            candidate_sections=len(candidate),
            candidate_physical_sections=physical_sections,
            candidate_displayed_options=displayed_options,
            candidate_courses=course_total,
            candidate_subjects=subject_total,
            subjects_added=tuple(sorted(incoming_subjects - retiring_subjects)),
            subjects_removed=tuple(sorted(retiring_subjects - incoming_subjects)),
            courses_added=tuple(sorted(incoming_courses - retiring_courses)),
            courses_removed=tuple(sorted(retiring_courses - incoming_courses)),
            new_instructors=tuple(sorted(incoming_instructors - known_instructors)),
            coverage_status=coverage_status,
            api_validation_status=api_status,
            sitemap_urls=sitemap_urls,
        ),
        candidate,
    )


def plan_inventory_rotation(
    incoming_path: Path,
    active_path: Path,
    retire_term: str,
    *,
    validate_api: bool = True,
) -> InventoryRotationPlan:
    plan, _candidate = _build_rotation_plan(
        incoming_path,
        active_path,
        retire_term,
        validate_api=validate_api,
    )
    return plan


def _default_backup_path(active_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return active_path.parent / "backups" / f"{active_path.stem}-before-rotation-{timestamp}{active_path.suffix}"


def _prune_seat_cache(
    cache_path: Path | None,
    candidate: Sequence[CourseSection],
) -> CachePruneSummary:
    if cache_path is None or not cache_path.is_file():
        return CachePruneSummary(path=str(cache_path) if cache_path is not None else None, skipped=True)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("sections"), dict):
            raise ValueError("seat cache is not a recognized ClassCatalog cache document")
        records = payload["sections"]
        assert isinstance(records, dict)
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            sources = {}
        active_keys = _physical_keys(candidate)
        active_sources = _source_urls(candidate)
        kept_records = {
            key: raw
            for key, raw in records.items()
            if isinstance(raw, dict)
            and (
                str(raw.get("term") or "").strip(),
                str(raw.get("schedule_number") or "").strip(),
            )
            in active_keys
        }
        kept_sources = {
            url: raw for url, raw in sources.items() if str(url).strip() in active_sources
        }
        payload["sections"] = kept_records
        payload["sources"] = kept_sources
        payload["updated_at"] = datetime.now(UTC).isoformat()
        _atomic_write_json(cache_path, payload)
        return CachePruneSummary(
            path=str(cache_path),
            records_before=len(records),
            records_after=len(kept_records),
            sources_before=len(sources),
            sources_after=len(kept_sources),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return CachePruneSummary(path=str(cache_path), skipped=True, error=f"{type(exc).__name__}: {exc}")


def _prune_instructor_cache(
    cache_path: Path | None,
    candidate: Sequence[CourseSection],
) -> CachePruneSummary:
    if cache_path is None or not cache_path.is_file():
        return CachePruneSummary(path=str(cache_path) if cache_path is not None else None, skipped=True)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("sections"), dict):
            raise ValueError("instructor cache is not a recognized ClassCatalog cache document")
        records = payload["sections"]
        assert isinstance(records, dict)
        active_keys = _physical_keys(candidate)
        kept = {
            key: raw
            for key, raw in records.items()
            if isinstance(raw, dict)
            and (
                str(raw.get("term") or "").strip(),
                str(raw.get("schedule_number") or "").strip(),
            )
            in active_keys
        }
        payload["sections"] = kept
        _atomic_write_json(cache_path, payload)
        return CachePruneSummary(
            path=str(cache_path),
            records_before=len(records),
            records_after=len(kept),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return CachePruneSummary(path=str(cache_path), skipped=True, error=f"{type(exc).__name__}: {exc}")


def _preserve_mode(path: Path, original_mode: int | None) -> None:
    if original_mode is None:
        return
    try:
        os.chmod(path, stat.S_IMODE(original_mode))
    except OSError:
        pass


def apply_inventory_rotation(
    incoming_path: Path,
    active_path: Path,
    retire_term: str,
    *,
    backup_path: Path | None = None,
    seat_cache_path: Path | None = None,
    instructor_cache_path: Path | None = None,
    prune_caches: bool = True,
    validate_api: bool = True,
) -> InventoryRotationApplySummary:
    plan, candidate = _build_rotation_plan(
        incoming_path,
        active_path,
        retire_term,
        validate_api=validate_api,
    )

    backup = backup_path or _default_backup_path(active_path)
    if backup.exists():
        raise ValueError(f"Backup path already exists: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)

    original_mode: int | None = None
    try:
        original_mode = active_path.stat().st_mode
    except OSError:
        pass
    shutil.copy2(active_path, backup)
    backup_hash = _sha256(backup)

    try:
        _write_sections(active_path, candidate)
        _preserve_mode(active_path, original_mode)
        installed = _load_sections(active_path)
        if _active_terms(installed) != plan.candidate_terms or len(installed) != plan.candidate_sections:
            raise ValueError("Post-write verification did not match the validated rotation candidate.")
    except Exception:
        shutil.copy2(backup, active_path)
        raise

    if prune_caches:
        seat_summary = _prune_seat_cache(seat_cache_path, candidate)
        instructor_summary = _prune_instructor_cache(instructor_cache_path, candidate)
    else:
        seat_summary = CachePruneSummary(
            path=str(seat_cache_path) if seat_cache_path is not None else None,
            skipped=True,
        )
        instructor_summary = CachePruneSummary(
            path=str(instructor_cache_path) if instructor_cache_path is not None else None,
            skipped=True,
        )

    return InventoryRotationApplySummary(
        plan=plan,
        backup_path=str(backup),
        backup_sha256=backup_hash,
        seat_cache=seat_summary,
        instructor_cache=instructor_summary,
    )


def rotation_report_payload(
    plan: InventoryRotationPlan,
    *,
    apply_summary: InventoryRotationApplySummary | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "plan": asdict(plan),
    }
    if apply_summary is not None:
        payload["apply"] = {
            "backup_path": apply_summary.backup_path,
            "backup_sha256": apply_summary.backup_sha256,
            "seat_cache": asdict(apply_summary.seat_cache),
            "instructor_cache": asdict(apply_summary.instructor_cache),
        }
    return payload


def write_rotation_report(
    path: Path,
    plan: InventoryRotationPlan,
    *,
    apply_summary: InventoryRotationApplySummary | None = None,
) -> None:
    _atomic_write_json(path, rotation_report_payload(plan, apply_summary=apply_summary))
