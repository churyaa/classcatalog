from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from classcatalog.filters import normalize_course_code
from classcatalog.models import CourseSection, InstructionMode, Meeting, SeatStatus, Weekday
from classcatalog.repository import CourseRepository
from classcatalog.subjects import subject_display_name, subject_slug

LOGGER = logging.getLogger(__name__)
SITE_URL = "https://classcatalog.cc"
TEMPLATE_DIR = Path(__file__).parent / "templates" / "seo"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class SeoCourse:
    course_code: str
    slug: str
    subject_code: str
    subject_name: str
    subject_slug: str
    title: str
    description: str | None
    units_text: str
    prerequisite_text: str | None
    enrollment_requirements: tuple[str, ...]
    option_count: int
    campuses: tuple[str, ...]
    instruction_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeoSubject:
    code: str
    name: str
    slug: str
    course_count: int
    option_count: int
    courses: tuple[SeoCourse, ...]


@dataclass(frozen=True, slots=True)
class SeoCatalog:
    primary_term: str | None
    terms: tuple[str, ...]
    subjects: tuple[SeoSubject, ...]
    courses: tuple[SeoCourse, ...]
    _subjects_by_slug: dict[str, SeoSubject]
    _courses_by_slug: dict[str, SeoCourse]
    _courses_by_code: dict[str, SeoCourse]

    @classmethod
    def from_repository(cls, repository: CourseRepository) -> SeoCatalog:
        terms = repository.options().terms
        primary_term = terms[-1] if terms else None
        if not terms:
            return cls(None, (), (), (), {}, {}, {})

        displayed = repository.displayed_options()
        by_course: dict[str, list[CourseSection]] = defaultdict(list)
        for option in displayed:
            code = normalize_course_code(option.course_code)
            if code:
                by_course[code].append(option)

        courses: list[SeoCourse] = []
        course_slugs: dict[str, str] = {}
        for code, options in sorted(by_course.items()):
            course = _build_course(code, options)
            previous_code = course_slugs.setdefault(course.slug, code)
            if previous_code != code:
                raise ValueError(
                    f"SEO course slug collision: {previous_code!r} and {code!r} -> {course.slug!r}"
                )
            courses.append(course)

        courses_by_subject: dict[str, list[SeoCourse]] = defaultdict(list)
        for course in courses:
            courses_by_subject[course.subject_code].append(course)

        subjects: list[SeoSubject] = []
        subject_slugs: dict[str, str] = {}
        for code, subject_courses in sorted(courses_by_subject.items()):
            name = subject_display_name(code)
            slug = subject_slug(code)
            previous_code = subject_slugs.setdefault(slug, code)
            if previous_code != code:
                raise ValueError(
                    f"SEO subject slug collision: {previous_code!r} and {code!r} -> {slug!r}"
                )
            ordered_courses = tuple(sorted(subject_courses, key=_course_sort_key))
            subjects.append(
                SeoSubject(
                    code=code,
                    name=name,
                    slug=slug,
                    course_count=len(ordered_courses),
                    option_count=sum(course.option_count for course in ordered_courses),
                    courses=ordered_courses,
                )
            )

        ordered_subjects = tuple(
            sorted(subjects, key=lambda item: (item.name.casefold(), item.code))
        )
        ordered_courses = tuple(sorted(courses, key=_course_sort_key))
        return cls(
            primary_term=primary_term,
            terms=terms,
            subjects=ordered_subjects,
            courses=ordered_courses,
            _subjects_by_slug={item.slug: item for item in ordered_subjects},
            _courses_by_slug={item.slug: item for item in ordered_courses},
            _courses_by_code={item.course_code: item for item in ordered_courses},
        )

    def subject_by_slug(self, slug: str) -> SeoSubject | None:
        return self._subjects_by_slug.get(slug.casefold())

    def course_by_slug(self, slug: str) -> SeoCourse | None:
        return self._courses_by_slug.get(slug.casefold())

    def course_by_code(self, course_code: str) -> SeoCourse | None:
        return self._courses_by_code.get(normalize_course_code(course_code))


class SeoRenderer:
    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self._environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._environment.filters["seat_summary"] = seat_summary
        self._environment.filters["seat_status"] = seat_status_label
        self._environment.filters["seat_count"] = seat_count_summary
        self._environment.filters["instruction_mode"] = instruction_mode_label
        self._environment.filters["meeting_summary"] = meeting_summary
        self._environment.filters["meeting_time"] = meeting_time_summary
        self._environment.filters["weekday"] = weekday_label

    def render_subject_index(self, catalog: SeoCatalog) -> str:
        term = _active_terms_label(catalog.terms)
        title = f"SDSU Subjects & Classes – {term} | ClassCatalog"
        description = (
            f"Browse SDSU subjects and classes for {term}. Explore course offerings, units, "
            "campuses, instruction modes, and class details with ClassCatalog."
        )
        return self._environment.get_template("subjects.html").render(
            page_title=title,
            meta_description=description,
            canonical_url=f"{SITE_URL}/subjects",
            schema_json=_breadcrumb_schema((("Home", "/"), ("Subjects", "/subjects"))),
            term=term,
            subjects=catalog.subjects,
        )

    def render_subject(self, catalog: SeoCatalog, subject: SeoSubject) -> str:
        term = _active_terms_label(catalog.terms)
        title = f"SDSU {subject.name} Classes – {term} | ClassCatalog"
        description = (
            f"Browse SDSU {subject.name} classes for {term}, including course titles, units, "
            "and enrollment option counts."
        )
        return self._environment.get_template("subject.html").render(
            page_title=title,
            meta_description=description,
            canonical_url=f"{SITE_URL}/subjects/{subject.slug}",
            schema_json=_breadcrumb_schema(
                (
                    ("Home", "/"),
                    ("Subjects", "/subjects"),
                    (subject.name, f"/subjects/{subject.slug}"),
                )
            ),
            term=term,
            subject=subject,
        )

    def render_course(
        self,
        catalog: SeoCatalog,
        course: SeoCourse,
        options: tuple[CourseSection, ...],
    ) -> str:
        term = _active_terms_label(catalog.terms)
        title_text = (
            f"{course.course_code} – {course.title}" if course.title else course.course_code
        )
        page_title = f"SDSU {title_text} | {term} | ClassCatalog"
        meta_description = _course_meta_description(course, term)
        options_by_term = tuple(
            (term_name, tuple(option for option in options if option.term == term_name))
            for term_name in catalog.terms
            if any(option.term == term_name for option in options)
        )
        return self._environment.get_template("course.html").render(
            page_title=page_title,
            meta_description=meta_description,
            canonical_url=f"{SITE_URL}/courses/{course.slug}",
            schema_json=_breadcrumb_schema(
                (
                    ("Home", "/"),
                    ("Subjects", "/subjects"),
                    (course.subject_name, f"/subjects/{course.subject_slug}"),
                    (course.course_code, f"/courses/{course.slug}"),
                )
            ),
            term=term,
            course=course,
            options=options,
            options_by_term=options_by_term,
        )


def course_slug(course_code: str) -> str:
    normalized = normalize_course_code(course_code).casefold()
    return _SLUG_RE.sub("-", normalized).strip("-")


def build_sitemap_xml(catalog: SeoCatalog) -> str:
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}urlset")
    paths = ["/", "/privacy", "/terms", "/subjects"]
    paths.extend(f"/subjects/{subject.slug}" for subject in catalog.subjects)
    paths.extend(f"/courses/{course.slug}" for course in catalog.courses)
    for path in paths:
        url = ET.SubElement(root, f"{{{namespace}}}url")
        loc = ET.SubElement(url, f"{{{namespace}}}loc")
        loc.text = f"{SITE_URL}{path}"
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def instruction_mode_label(value: InstructionMode | str) -> str:
    raw = value.value if isinstance(value, InstructionMode) else str(value)
    labels = {
        InstructionMode.IN_PERSON.value: "In person",
        InstructionMode.ONLINE_SYNCHRONOUS.value: "Online synchronous",
        InstructionMode.ONLINE_ASYNCHRONOUS.value: "Online asynchronous",
        InstructionMode.ONLINE_WITH_IN_PERSON_EXAMS.value: "Online with in-person exams",
        InstructionMode.HYBRID.value: "Hybrid",
        InstructionMode.OTHER.value: "Other",
    }
    return labels.get(raw, raw.replace("_", " ").strip().title())


def weekday_label(value: Weekday | str) -> str:
    raw = value.value if isinstance(value, Weekday) else str(value)
    return {
        Weekday.MON.value: "Mon",
        Weekday.TUE.value: "Tue",
        Weekday.WED.value: "Wed",
        Weekday.THU.value: "Thu",
        Weekday.FRI.value: "Fri",
        Weekday.SAT.value: "Sat",
        Weekday.SUN.value: "Sun",
    }.get(raw, raw.title())


def meeting_time_summary(meeting: Meeting) -> str:
    parts: list[str] = []
    if meeting.days:
        parts.append("/".join(weekday_label(day) for day in meeting.days))
    if meeting.start_time is not None and meeting.end_time is not None:
        parts.append(f"{_time_label(meeting.start_time)}–{_time_label(meeting.end_time)}")
    elif meeting.start_time is not None:
        parts.append(_time_label(meeting.start_time))
    return " ".join(parts) or "TBA"


def meeting_summary(meeting: Meeting) -> str:
    parts: list[str] = []
    if meeting.days:
        parts.append("/".join(weekday_label(day) for day in meeting.days))
    if meeting.start_time is not None and meeting.end_time is not None:
        parts.append(f"{_time_label(meeting.start_time)}–{_time_label(meeting.end_time)}")
    elif meeting.start_time is not None:
        parts.append(_time_label(meeting.start_time))
    if meeting.location:
        parts.append(meeting.location)
    if meeting.meeting_dates:
        parts.append(meeting.meeting_dates)
    return " · ".join(parts) or "Meeting details not listed"


def seat_status_label(value: SeatStatus | str) -> str:
    raw = value.value if isinstance(value, SeatStatus) else str(value)
    return {
        SeatStatus.OPEN.value: "Open",
        SeatStatus.WAITLIST.value: "Waitlisted",
        SeatStatus.CLOSED.value: "Closed",
        SeatStatus.UNKNOWN.value: "Unknown",
    }.get(raw, raw.replace("_", " ").strip().title())


def seat_count_summary(section: object) -> str:
    enrolled = getattr(section, "seats_enrolled", None)
    available = getattr(section, "seats_available", None)
    capacity = getattr(section, "seat_capacity", None)

    if enrolled is not None and capacity is not None:
        return f"{enrolled} / {capacity}"

    if enrolled is not None:
        return f"{enrolled} enrolled"

    if available is not None and capacity is not None:
        inferred_enrolled = max(capacity - available, 0)
        return f"{inferred_enrolled} / {capacity}"

    if available is not None:
        return f"{available} available"

    if capacity is not None:
        return f"{capacity} capacity"

    return "—"

def seat_summary(section: CourseSection) -> str:
    if section.seat_status is SeatStatus.OPEN:
        if section.seats_available is not None:
            noun = "seat" if section.seats_available == 1 else "seats"
            return f"Open · {section.seats_available} {noun} available"
        return "Open"
    if section.seat_status is SeatStatus.WAITLIST:
        return "Waitlist"
    if section.seat_status is SeatStatus.CLOSED:
        return "Closed"
    return "Seat status unavailable"


def _build_course(code: str, options: list[CourseSection]) -> SeoCourse:
    subjects = [_normalize_subject(option.subject) for option in options if option.subject.strip()]
    subject_code = _representative(subjects) or _subject_from_course_code(code)
    titles = [option.title.strip() for option in options if option.title.strip()]
    descriptions = [option.description.strip() for option in options if option.description]
    prerequisites = [
        option.prerequisite_text.strip() for option in options if option.prerequisite_text
    ]
    enrollment_requirements = tuple(
        sorted(
            {
                requirement.strip()
                for option in options
                for requirement in option.enrollment_requirements
                if requirement.strip()
            }
        )
    )
    title = _representative(titles) or ""
    description = _representative(descriptions)
    prerequisite_text = _representative(prerequisites)

    _warn_conflict(code, "title", titles)
    _warn_conflict(code, "description", descriptions)
    _warn_conflict(code, "prerequisite", prerequisites)
    _warn_conflict(code, "subject", subjects)

    return SeoCourse(
        course_code=code,
        slug=course_slug(code),
        subject_code=subject_code,
        subject_name=subject_display_name(subject_code),
        subject_slug=subject_slug(subject_code),
        title=title,
        description=description,
        units_text=_units_summary(options),
        prerequisite_text=prerequisite_text,
        enrollment_requirements=enrollment_requirements,
        option_count=len(options),
        campuses=tuple(sorted({option.campus for option in options if option.campus})),
        instruction_modes=tuple(
            sorted({instruction_mode_label(option.instruction_mode) for option in options})
        ),
    )


def _active_terms_label(terms: tuple[str, ...]) -> str:
    if not terms:
        return "Current terms"
    if len(terms) == 1:
        return terms[0]
    if len(terms) == 2:
        return f"{terms[0]} & {terms[1]}"
    return f"{', '.join(terms[:-1])}, & {terms[-1]}"


def _course_meta_description(course: SeoCourse, term: str) -> str:
    label = f"{course.course_code} – {course.title}" if course.title else course.course_code
    return (
        f"View SDSU {label} for {term}, including class numbers, formats, seat availability, "
        "meeting times, locations, professors, and RateMyProfessors data."
    )


def _breadcrumb_schema(items: tuple[tuple[str, str], ...]) -> str:
    payload = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": name,
                "item": f"{SITE_URL}{path}",
            }
            for index, (name, path) in enumerate(items, start=1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _representative(values: list[str]) -> str | None:
    normalized = [value.strip() for value in values if value.strip()]
    if not normalized:
        return None
    counts = Counter(normalized)
    return min(counts, key=lambda value: (-counts[value], value.casefold(), value))


def _warn_conflict(course_code: str, field: str, values: list[str]) -> None:
    unique = sorted({value.strip() for value in values if value.strip()}, key=str.casefold)
    if len(unique) > 1:
        LOGGER.warning(
            "SEO metadata conflict course=%s field=%s values=%s",
            course_code,
            field,
            unique,
        )


def _units_summary(options: list[CourseSection]) -> str:
    labels = [
        option.units_text.strip()
        for option in options
        if option.units_text and option.units_text.strip()
    ]
    if labels and len(set(labels)) == 1:
        return labels[0]

    minimums = [
        option.units_min if option.units_min is not None else option.units for option in options
    ]
    maximums = [
        option.units_max if option.units_max is not None else option.units for option in options
    ]
    if not minimums or not maximums:
        return "Varies"
    minimum = min(minimums)
    maximum = max(maximums)
    if minimum == maximum:
        return _format_units(minimum)
    return f"{_format_units(minimum)}–{_format_units(maximum)}"


def _format_units(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _normalize_subject(value: str) -> str:
    return " ".join(value.strip().upper().split())


def _subject_from_course_code(course_code: str) -> str:
    parts = course_code.rsplit(" ", maxsplit=1)
    return parts[0] if len(parts) == 2 else course_code


def _course_sort_key(course: SeoCourse) -> tuple[tuple[object, ...], str]:
    return (_natural_parts(course.course_code), course.course_code)


def _natural_parts(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
        if part
    )


def _time_label(value: object) -> str:
    hour = int(getattr(value, "hour"))
    minute = int(getattr(value, "minute"))
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"
