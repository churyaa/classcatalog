from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from classcatalog.models import CourseSection


@dataclass(frozen=True, slots=True)
class TermInstallSummary:
    term: str
    term_code: str | None
    incoming_sections: int
    replaced_sections: int
    preserved_sections: int
    active_sections: int
    active_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TermRetirePlan:
    term: str
    removed_sections: int
    remaining_sections: int
    remaining_terms: tuple[str, ...]


def _normalise_course_code(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _term_sort_key(term: str) -> tuple[int, int, str]:
    season_order = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
    parts = term.rsplit(" ", maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        return (9999, 99, term.casefold())
    return (int(parts[1]), season_order.get(parts[0], 99), term.casefold())


def _section_sort_key(section: CourseSection) -> tuple[object, ...]:
    return (
        _term_sort_key(section.term),
        section.subject.casefold(),
        section.catalog_number.casefold(),
        section.section_number.casefold(),
        section.schedule_number,
        section.id,
    )


def _load_sections(path: Path, *, allow_missing: bool = False) -> tuple[CourseSection, ...]:
    if not path.is_file():
        if allow_missing:
            return ()
        raise ValueError(f"Active dataset does not exist: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read section dataset {path}: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(f"Section dataset must be a JSON array: {path}")

    try:
        sections = tuple(CourseSection.model_validate(item) for item in raw)
    except Exception as exc:
        raise ValueError(f"Section dataset failed schema validation: {path}: {exc}") from exc

    _validate_unique_sections(sections, context=str(path))
    return sections


def _validate_unique_sections(
    sections: Sequence[CourseSection],
    *,
    context: str,
) -> None:
    by_id: dict[str, CourseSection] = {}
    by_listing: dict[tuple[str, str, str], CourseSection] = {}

    for section in sections:
        previous_id = by_id.get(section.id)
        if previous_id is not None:
            raise ValueError(
                f"Duplicate section id in {context}: {section.id!r} "
                f"({previous_id.course_code} / {previous_id.schedule_number} and "
                f"{section.course_code} / {section.schedule_number})"
            )
        by_id[section.id] = section

        term_key = section.term.strip()
        listing_key = (
            term_key.casefold(),
            _normalise_course_code(section.course_code),
            section.schedule_number.strip(),
        )
        previous_listing = by_listing.get(listing_key)
        if previous_listing is not None:
            raise ValueError(
                "Duplicate logical class listing in "
                f"{context}: term={term_key!r} course={section.course_code!r} "
                f"schedule_number={section.schedule_number!r}. "
                "A rescrape must replace a term instead of appending another copy."
            )
        by_listing[listing_key] = section


def _write_sections(path: Path, sections: Iterable[CourseSection]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("[\n")
        first = True
        for section in sections:
            if not first:
                handle.write(",\n")
            first = False
            rendered = json.dumps(
                section.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            )
            handle.write("  ")
            handle.write(rendered.replace("\n", "\n  "))
        handle.write("\n]\n")
    temporary.replace(path)


def _incoming_term(sections: Sequence[CourseSection]) -> tuple[str, str | None]:
    if not sections:
        raise ValueError("Refusing to install an empty term dataset.")

    terms = {section.term.strip() for section in sections if section.term.strip()}
    if len(terms) != 1:
        raise ValueError(
            "Incoming sections must contain exactly one term; "
            f"found {sorted(terms)!r}."
        )
    term = next(iter(terms))

    term_codes = {
        section.term_code.strip()
        for section in sections
        if section.term_code and section.term_code.strip()
    }
    if len(term_codes) > 1:
        raise ValueError(
            "Incoming sections must contain one compatible term code; "
            f"found {sorted(term_codes)!r}."
        )
    term_code = next(iter(term_codes)) if term_codes else None
    return term, term_code


def _matches_term(
    section: CourseSection,
    *,
    term: str,
    term_code: str | None,
) -> bool:
    if section.term.strip().casefold() == term.strip().casefold():
        return True
    return bool(
        term_code
        and section.term_code
        and section.term_code.strip() == term_code.strip()
    )


def _active_terms(sections: Sequence[CourseSection]) -> tuple[str, ...]:
    return tuple(sorted({section.term for section in sections}, key=_term_sort_key))


def install_term_sections(
    incoming_path: Path,
    active_path: Path,
) -> TermInstallSummary:
    """Replace exactly one term in active API data while preserving every other term.

    The builder intentionally produces one term at a time. This installer turns that
    single-term output into a safe update of the multi-term active dataset: old records
    for the incoming term are removed, the new records are inserted, and duplicate
    logical listings are rejected before the active file is replaced atomically.
    """

    incoming = _load_sections(incoming_path)
    term, term_code = _incoming_term(incoming)
    active = _load_sections(active_path, allow_missing=True)

    preserved = tuple(
        section
        for section in active
        if not _matches_term(section, term=term, term_code=term_code)
    )
    replaced_count = len(active) - len(preserved)

    merged = tuple(sorted((*preserved, *incoming), key=_section_sort_key))
    _validate_unique_sections(merged, context=f"merged active dataset for {term}")
    _write_sections(active_path, merged)

    return TermInstallSummary(
        term=term,
        term_code=term_code,
        incoming_sections=len(incoming),
        replaced_sections=replaced_count,
        preserved_sections=len(preserved),
        active_sections=len(merged),
        active_terms=_active_terms(merged),
    )


def plan_term_retirement(active_path: Path, term: str) -> TermRetirePlan:
    active = _load_sections(active_path)
    normalized_term = term.strip().casefold()
    if not normalized_term:
        raise ValueError("Provide a non-empty term to retire.")

    remaining = tuple(
        section
        for section in active
        if section.term.strip().casefold() != normalized_term
    )
    removed = len(active) - len(remaining)
    if removed == 0:
        raise ValueError(f"No active sections were found for term {term!r}.")

    return TermRetirePlan(
        term=term.strip(),
        removed_sections=removed,
        remaining_sections=len(remaining),
        remaining_terms=_active_terms(remaining),
    )


def retire_term_sections(
    active_path: Path,
    term: str,
    *,
    allow_empty: bool = False,
) -> TermRetirePlan:
    """Remove one term from active API data without touching browser favorites."""

    active = _load_sections(active_path)
    plan = plan_term_retirement(active_path, term)
    normalized_term = term.strip().casefold()
    remaining = tuple(
        section
        for section in active
        if section.term.strip().casefold() != normalized_term
    )

    if not remaining and not allow_empty:
        raise ValueError(
            "Refusing to retire the final active term. Install a newer term first, "
            "or explicitly allow an empty active dataset."
        )

    _validate_unique_sections(remaining, context=f"active dataset after retiring {term}")
    _write_sections(active_path, sorted(remaining, key=_section_sort_key))
    return plan


def active_term_counts(active_path: Path) -> tuple[tuple[str, int, int], ...]:
    """Return (term, listing count, physical-section count) for the active dataset."""

    sections = _load_sections(active_path)
    rows: list[tuple[str, int, int]] = []
    for term in _active_terms(sections):
        term_sections = tuple(section for section in sections if section.term == term)
        physical = {
            (section.term_code or section.term, section.schedule_number)
            for section in term_sections
        }
        rows.append((term, len(term_sections), len(physical)))
    return tuple(rows)
