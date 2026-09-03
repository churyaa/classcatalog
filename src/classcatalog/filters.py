from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import time

from classcatalog.models import (
    CourseSection,
    GradingType,
    InstructionMode,
    ProgramClassification,
    SeatStatus,
    SortBy,
    Weekday,
)


@dataclass(frozen=True, slots=True)
class SearchFilters:
    terms: tuple[str, ...] = ()
    campuses: tuple[str, ...] = ()
    query: str | None = None
    requirements: tuple[str, ...] = ()
    units_min: float | None = None
    units_max: float | None = None
    gradings: tuple[GradingType, ...] = ()
    program: str | None = None
    catalog_year: str | None = None
    classifications: tuple[ProgramClassification, ...] = ()
    major_only: bool = True
    completed_courses: tuple[str, ...] = ()
    days: tuple[Weekday, ...] = ()
    time_from: time | None = None
    time_to: time | None = None
    instruction_modes: tuple[InstructionMode, ...] = ()
    seat_statuses: tuple[SeatStatus, ...] = ()
    rating_min: float | None = None
    difficulty_max: float | None = None
    would_take_again_min: float | None = None
    reviews_min: int | None = None
    attendance_required: bool | None = None
    textbook_required: bool | None = None
    sort_by: SortBy = SortBy.COURSE_A_Z
    page: int = 1
    page_size: int = 50


def normalize_course_code(value: str) -> str:
    return " ".join(value.strip().upper().split())


_COURSE_CODE_PREFIX_QUERY_RE = re.compile(r"^[A-Z][A-Z ]*\s+\d+[A-Z]*$")


def _course_code_prefix_query(value: str) -> str | None:
    """Return a normalized course-code prefix for course-like queries."""

    normalized = normalize_course_code(value)
    if _COURSE_CODE_PREFIX_QUERY_RE.fullmatch(normalized):
        return normalized
    return None


def _matches_program_classification(
    section: CourseSection,
    filters: SearchFilters,
) -> bool:
    if not filters.major_only and not filters.classifications:
        return True
    if (
        filters.program is None
        and filters.catalog_year is None
        and not filters.classifications
    ):
        return True

    for tag in section.program_tags:
        if filters.program is not None and tag.program != filters.program:
            continue
        if filters.catalog_year is not None and tag.catalog_year != filters.catalog_year:
            continue
        if filters.classifications and tag.classification not in filters.classifications:
            continue
        return True
    return False


def _matches_time_window(section: CourseSection, filters: SearchFilters) -> bool:
    if filters.time_from is None and filters.time_to is None:
        return True

    timed_meetings = [
        meeting
        for meeting in section.meetings
        if meeting.start_time is not None and meeting.end_time is not None
    ]
    if not timed_meetings:
        return False

    for meeting in timed_meetings:
        assert meeting.start_time is not None
        assert meeting.end_time is not None
        if filters.time_from is not None and meeting.start_time < filters.time_from:
            return False
        if filters.time_to is not None and meeting.end_time > filters.time_to:
            return False
    return True



def normalize_campus(value: str) -> str:
    folded = " ".join(value.strip().casefold().split())
    if "imperial" in folded:
        return "Imperial Valley Campus"
    if "san diego" in folded:
        return "San Diego Campus"
    return " ".join(value.strip().split())


def matches(section: CourseSection, filters: SearchFilters) -> bool:
    if filters.terms and section.term not in filters.terms:
        return False

    if filters.campuses:
        selected_campuses = {normalize_campus(value) for value in filters.campuses}
        if normalize_campus(section.campus) not in selected_campuses:
            return False

    if filters.completed_courses:
        completed = {normalize_course_code(code) for code in filters.completed_courses}
        if normalize_course_code(section.course_code) in completed:
            return False

    if filters.query:
        course_code_prefix = _course_code_prefix_query(filters.query)
        if course_code_prefix is not None:
            if not normalize_course_code(section.course_code).startswith(course_code_prefix):
                return False
        else:
            haystack = " ".join(
                value
                for value in (
                    section.course_code,
                    section.title,
                    section.description or "",
                    section.instructor or "",
                )
                if value
            ).casefold()
            if filters.query.casefold().strip() not in haystack:
                return False

    if filters.requirements and not set(filters.requirements).issubset(
        set(section.requirement_tags)
    ):
        return False

    section_units_min = section.units_min if section.units_min is not None else section.units
    section_units_max = section.units_max if section.units_max is not None else section.units
    if filters.units_min is not None and section_units_max < filters.units_min:
        return False
    if filters.units_max is not None and section_units_min > filters.units_max:
        return False

    if filters.gradings and section.grading not in filters.gradings:
        return False

    if not _matches_program_classification(section, filters):
        return False

    if filters.days:
        selected_days = set(filters.days)
        if not any(selected_days.intersection(meeting.days) for meeting in section.meetings):
            return False

    if not _matches_time_window(section, filters):
        return False

    if filters.instruction_modes and section.instruction_mode not in filters.instruction_modes:
        return False

    if filters.seat_statuses and section.seat_status not in filters.seat_statuses:
        return False

    professor = section.professor
    if filters.rating_min is not None:
        if professor is None or professor.rating is None or professor.rating < filters.rating_min:
            return False
    if filters.difficulty_max is not None:
        if (
            professor is None
            or professor.difficulty is None
            or professor.difficulty > filters.difficulty_max
        ):
            return False
    if filters.would_take_again_min is not None:
        if (
            professor is None
            or professor.would_take_again_percent is None
            or professor.would_take_again_percent < filters.would_take_again_min
        ):
            return False
    if filters.reviews_min is not None:
        if professor is None or professor.num_reviews < filters.reviews_min:
            return False
    if filters.attendance_required is not None:
        if professor is None or professor.attendance_required is not filters.attendance_required:
            return False
    if filters.textbook_required is not None:
        if professor is None or professor.textbook_required is not filters.textbook_required:
            return False

    return True


def _natural_parts(value: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for part in re.split(r"(\d+)", value.casefold()):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def _alphabetical_key(section: CourseSection) -> tuple[object, ...]:
    return (
        _natural_parts(section.subject),
        _natural_parts(section.catalog_number),
        _natural_parts(section.section_number),
        section.option_number or 0,
        _natural_parts(section.schedule_number),
    )


def _metric_key(
    section: CourseSection,
    value: float | int | None,
    *,
    descending: bool,
) -> tuple[object, ...]:
    numeric = float(value) if value is not None else 0.0
    directed_value = -numeric if descending else numeric
    return (value is None, directed_value, _alphabetical_key(section))


def _sort_by_metric(
    sections: Sequence[CourseSection],
    getter: Callable[[CourseSection], float | int | None],
    *,
    descending: bool,
) -> list[CourseSection]:
    return sorted(
        sections,
        key=lambda section: _metric_key(
            section,
            getter(section),
            descending=descending,
        ),
    )


def _rating(section: CourseSection) -> float | None:
    return section.professor.rating if section.professor else None


def _class_difficulty(section: CourseSection) -> float | None:
    return section.class_difficulty


def _professor_difficulty(section: CourseSection) -> float | None:
    return section.professor.difficulty if section.professor else None


def _reviews(section: CourseSection) -> int | None:
    return section.professor.num_reviews if section.professor else None


def _take_again(section: CourseSection) -> float | None:
    return section.professor.would_take_again_percent if section.professor else None


def sort_sections(
    sections: Sequence[CourseSection],
    sort_by: SortBy,
) -> list[CourseSection]:
    if sort_by is SortBy.COURSE_Z_A:
        return sorted(sections, key=_alphabetical_key, reverse=True)
    if sort_by is SortBy.PROFESSOR_RATING_LOW_TO_HIGH:
        return _sort_by_metric(sections, _rating, descending=False)
    if sort_by is SortBy.PROFESSOR_RATING_HIGH_TO_LOW:
        return _sort_by_metric(sections, _rating, descending=True)
    if sort_by is SortBy.CLASS_DIFFICULTY_LOW_TO_HIGH:
        return _sort_by_metric(sections, _class_difficulty, descending=False)
    if sort_by is SortBy.CLASS_DIFFICULTY_HIGH_TO_LOW:
        return _sort_by_metric(sections, _class_difficulty, descending=True)
    if sort_by is SortBy.PROFESSOR_DIFFICULTY_LOW_TO_HIGH:
        return _sort_by_metric(sections, _professor_difficulty, descending=False)
    if sort_by is SortBy.PROFESSOR_DIFFICULTY_HIGH_TO_LOW:
        return _sort_by_metric(sections, _professor_difficulty, descending=True)
    if sort_by is SortBy.REVIEWS_LOW_TO_HIGH:
        return _sort_by_metric(sections, _reviews, descending=False)
    if sort_by is SortBy.REVIEWS_HIGH_TO_LOW:
        return _sort_by_metric(sections, _reviews, descending=True)
    if sort_by is SortBy.TAKE_AGAIN_LOW_TO_HIGH:
        return _sort_by_metric(sections, _take_again, descending=False)
    if sort_by is SortBy.TAKE_AGAIN_HIGH_TO_LOW:
        return _sort_by_metric(sections, _take_again, descending=True)
    return sorted(sections, key=_alphabetical_key)
