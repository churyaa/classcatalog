from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Final

from classcatalog.models import (
    CourseSection,
    GradingType,
    InstructionMode,
    Meeting,
    SeatStatus,
    Weekday,
)

DEFAULT_OUTPUT: Final[Path] = Path("src/classcatalog/data/sections.json")
DEFAULT_SOURCE_URL: Final[str] = "https://www.sdsu.edu/schedule"

_HEADER_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "class_number": ("class nbr", "class number", "class no", "class #"),
    "subject": ("subject", "subject code"),
    "catalog_number": ("catalog nbr", "catalog number", "course number"),
    "title": ("title", "course title", "class title"),
    "description": ("description", "course description", "catalog description"),
    "section_number": ("class section", "section", "section number"),
    "units": ("component units", "units", "minimum units", "unit"),
    "facility": ("facility id", "facility", "room", "location"),
    "meeting_start": ("meeting start", "start time", "meeting start time"),
    "meeting_end": ("meeting end", "end time", "meeting end time"),
    "meeting_pattern": (
        "standard meeting pattern",
        "meeting pattern",
        "days",
        "meeting days",
    ),
    "last_name": ("last name", "instructor last name"),
    "initials": ("initials", "instructor initials"),
    "instructor": ("instructor", "instructor name", "faculty"),
    "class_status": ("class status", "status", "seat status"),
    "instruction_mode": ("instruction mode", "mode", "instruction format"),
    "campus": ("campus", "campus description", "location campus"),
    "grading": ("grading", "grading basis", "grading scheme"),
    "seat_capacity": (
        "enrollment capacity",
        "seat capacity",
        "class capacity",
        "capacity",
    ),
    "seats_enrolled": (
        "enrollment total",
        "total enrollment",
        "seats enrolled",
        "enrolled seats",
        "current enrollment",
    ),
    "seats_available": ("seats available", "available seats", "open seats"),
    "waitlist_available": (
        "waitlist available",
        "available waitlist seats",
        "wait list available",
    ),
}

_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "class_number",
    "subject",
    "catalog_number",
    "title",
    "section_number",
    "units",
)

_DAY_CODES: Final[dict[str, Weekday]] = {
    "M": Weekday.MON,
    "T": Weekday.TUE,
    "W": Weekday.WED,
    "R": Weekday.THU,
    "F": Weekday.FRI,
    "S": Weekday.SAT,
    "U": Weekday.SUN,
}

_DAY_WORDS: Final[tuple[tuple[re.Pattern[str], Weekday], ...]] = (
    (re.compile(r"\bMON(?:DAY)?\b", re.I), Weekday.MON),
    (re.compile(r"\bTUE(?:SDAY)?\b", re.I), Weekday.TUE),
    (re.compile(r"\bWED(?:NESDAY)?\b", re.I), Weekday.WED),
    (re.compile(r"\bTHU(?:RSDAY)?\b", re.I), Weekday.THU),
    (re.compile(r"\bFRI(?:DAY)?\b", re.I), Weekday.FRI),
    (re.compile(r"\bSAT(?:URDAY)?\b", re.I), Weekday.SAT),
    (re.compile(r"\bSUN(?:DAY)?\b", re.I), Weekday.SUN),
)


class ScheduleCsvError(ValueError):
    """Raised when an SDSU schedule export cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class ImportReport:
    rows_read: int
    sections_created: int
    meetings_created: int
    output_path: Path | None = None


@dataclass(slots=True)
class _SectionAccumulator:
    term: str
    class_number: str
    subject: str
    catalog_number: str
    section_number: str
    title: str
    description: str | None
    units: float
    campus: str
    grading: GradingType
    seat_status: SeatStatus
    seat_capacity: int | None
    seats_enrolled: int | None
    seats_available: int | None
    waitlist_available: int | None
    source_updated_at: str
    source_url: str
    instruction_mode_values: set[str] = field(default_factory=set)
    meetings: list[Meeting] = field(default_factory=list)
    instructors: list[str] = field(default_factory=list)

    def add_row(self, row: Mapping[str, str], columns: Mapping[str, str]) -> None:
        instruction_mode = _value(row, columns, "instruction_mode")
        if instruction_mode:
            self.instruction_mode_values.add(instruction_mode)

        instructor = _parse_instructor(row, columns)
        if instructor and instructor not in self.instructors:
            self.instructors.append(instructor)

        meeting = _parse_meeting(row, columns)
        if meeting is not None and meeting not in self.meetings:
            self.meetings.append(meeting)

        campus = _value(row, columns, "campus")
        if campus and self.campus == "SDSU":
            self.campus = campus

        description = _value(row, columns, "description")
        if description and self.description is None:
            self.description = description

        if self.seat_capacity is None:
            self.seat_capacity = _parse_nonnegative_int(
                _value(row, columns, "seat_capacity")
            )
        if self.seats_enrolled is None:
            self.seats_enrolled = _parse_nonnegative_int(
                _value(row, columns, "seats_enrolled")
            )
        if self.seats_available is None:
            self.seats_available = _parse_nonnegative_int(
                _value(row, columns, "seats_available")
            )
        if self.waitlist_available is None:
            self.waitlist_available = _parse_nonnegative_int(
                _value(row, columns, "waitlist_available")
            )

    def build(self) -> CourseSection:
        instruction_mode = _parse_instruction_mode(
            self.instruction_mode_values,
            has_timed_meeting=any(
                meeting.start_time is not None and meeting.end_time is not None
                for meeting in self.meetings
            ),
        )
        instructor = "; ".join(self.instructors) if self.instructors else None
        term_slug = re.sub(r"[^a-z0-9]+", "-", self.term.casefold()).strip("-")
        course_code = f"{self.subject} {self.catalog_number}".strip()
        seats_enrolled = self.seats_enrolled
        seats_available = self.seats_available
        if (
            seats_enrolled is None
            and self.seat_capacity is not None
            and seats_available is not None
        ):
            seats_enrolled = max(self.seat_capacity - seats_available, 0)
        if (
            seats_available is None
            and self.seat_capacity is not None
            and seats_enrolled is not None
        ):
            seats_available = max(self.seat_capacity - seats_enrolled, 0)
        seat_status = _derive_seat_status(
            self.seat_status,
            seat_capacity=self.seat_capacity,
            seats_enrolled=seats_enrolled,
            seats_available=seats_available,
            waitlist_available=self.waitlist_available,
        )
        return CourseSection(
            id=f"{term_slug}-{self.class_number}",
            term=self.term,
            course_code=course_code,
            subject=self.subject,
            catalog_number=self.catalog_number,
            section_number=self.section_number,
            schedule_number=self.class_number,
            title=self.title,
            description=self.description,
            units=self.units,
            grading=self.grading,
            instruction_mode=instruction_mode,
            seat_status=seat_status,
            seat_capacity=self.seat_capacity,
            seats_enrolled=seats_enrolled,
            seats_available=seats_available,
            waitlist_available=self.waitlist_available,
            campus=self.campus,
            instructor=instructor,
            meetings=tuple(self.meetings),
            source_url=self.source_url,
            source_updated_at=self.source_updated_at,
        )


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold().lstrip("\ufeff"))


def _resolve_columns(fieldnames: Sequence[str] | None) -> dict[str, str]:
    if not fieldnames:
        raise ScheduleCsvError("The CSV file has no header row.")

    normalized_to_original = {_normalize_header(name): name for name in fieldnames if name}
    resolved: dict[str, str] = {}
    for canonical_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            original = normalized_to_original.get(_normalize_header(alias))
            if original is not None:
                resolved[canonical_name] = original
                break

    missing = [name for name in _REQUIRED_COLUMNS if name not in resolved]
    if missing:
        readable = ", ".join(missing)
        found = ", ".join(fieldnames)
        raise ScheduleCsvError(
            f"Missing required schedule columns: {readable}. Headers found: {found}"
        )
    return resolved


def _clean_row(row: Mapping[str, str | None]) -> dict[str, str]:
    return {key: (value or "").strip() for key, value in row.items() if key is not None}


def _value(row: Mapping[str, str], columns: Mapping[str, str], name: str) -> str:
    source_name = columns.get(name)
    if source_name is None:
        return ""
    return row.get(source_name, "").strip()


def _parse_units(value: str) -> float:
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        raise ScheduleCsvError("A schedule row is missing its units value.")
    try:
        units = float(cleaned)
    except ValueError as exc:
        raise ScheduleCsvError(f"Invalid units value: {value!r}") from exc
    if units < 0:
        raise ScheduleCsvError(f"Units cannot be negative: {value!r}")
    return units


def _parse_nonnegative_int(value: str) -> int | None:
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        parsed = int(float(cleaned))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_time(value: str) -> time | None:
    cleaned = value.strip()
    if not cleaned:
        return None

    time_match = re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M\b", cleaned, re.I)
    candidates = [time_match.group(0)] if time_match else [cleaned]
    formats = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate.strip().upper())
        for format_string in formats:
            try:
                return datetime.strptime(normalized, format_string).time()
            except ValueError:
                continue
    return None


def _parse_days(value: str) -> tuple[Weekday, ...]:
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"arr", "arranged", "tba", "online"}:
        return ()

    word_matches: list[Weekday] = []
    for pattern, weekday in _DAY_WORDS:
        if pattern.search(cleaned) and weekday not in word_matches:
            word_matches.append(weekday)
    if word_matches:
        return tuple(word_matches)

    compact = re.sub(r"[^A-Z]", "", cleaned.upper())
    parsed: list[Weekday] = []
    for character in compact:
        weekday = _DAY_CODES.get(character)
        if weekday is not None and weekday not in parsed:
            parsed.append(weekday)
    return tuple(parsed)


def _parse_meeting(row: Mapping[str, str], columns: Mapping[str, str]) -> Meeting | None:
    location = _value(row, columns, "facility") or None
    days = _parse_days(_value(row, columns, "meeting_pattern"))
    start_time = _parse_time(_value(row, columns, "meeting_start"))
    end_time = _parse_time(_value(row, columns, "meeting_end"))

    if not days and start_time is None and end_time is None:
        if location is None or location.casefold() in {"online", "tba", "arr"}:
            return None
    return Meeting(days=days, start_time=start_time, end_time=end_time, location=location)


def _parse_instructor(row: Mapping[str, str], columns: Mapping[str, str]) -> str | None:
    full_name = _value(row, columns, "instructor")
    if full_name:
        return full_name

    last_name = _value(row, columns, "last_name")
    initials = _value(row, columns, "initials")
    if last_name and initials:
        return f"{last_name}, {initials}"
    return last_name or None


def _parse_grading(value: str) -> GradingType:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    if not normalized:
        return GradingType.OTHER
    if "credit" in normalized and "letter" in normalized:
        return GradingType.LETTER_OR_CREDIT_NO_CREDIT
    if normalized in {"cr nc", "credit no credit", "credit noncredit"}:
        return GradingType.CREDIT_NO_CREDIT
    if "letter" in normalized or normalized in {"graded", "grade"}:
        return GradingType.LETTER
    return GradingType.OTHER


def _parse_seat_status(value: str) -> SeatStatus:
    normalized = re.sub(r"[^a-z]+", " ", value.casefold()).strip()
    if normalized in {"open", "available", "o"}:
        return SeatStatus.OPEN
    if normalized in {"waitlist", "wait list", "waiting list", "w"}:
        return SeatStatus.WAITLIST
    if normalized in {"closed", "full", "c"}:
        return SeatStatus.CLOSED
    return SeatStatus.UNKNOWN


def _derive_seat_status(
    explicit_status: SeatStatus,
    *,
    seat_capacity: int | None,
    seats_enrolled: int | None,
    seats_available: int | None,
    waitlist_available: int | None,
) -> SeatStatus:
    if explicit_status is not SeatStatus.UNKNOWN:
        return explicit_status
    if seats_available is not None and seats_available > 0:
        return SeatStatus.OPEN
    if (
        seat_capacity is not None
        and seats_enrolled is not None
        and seats_enrolled < seat_capacity
    ):
        return SeatStatus.OPEN
    if waitlist_available is not None and waitlist_available > 0:
        return SeatStatus.WAITLIST
    if seats_available == 0 or (
        seat_capacity is not None
        and seats_enrolled is not None
        and seats_enrolled >= seat_capacity
    ):
        return SeatStatus.CLOSED
    return SeatStatus.UNKNOWN


def _parse_instruction_mode(
    values: Iterable[str],
    *,
    has_timed_meeting: bool,
) -> InstructionMode:
    normalized = {
        re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip() for value in values if value
    }
    compact = {value.replace(" ", "") for value in normalized}

    has_hybrid = bool(
        normalized.intersection({"hybrid", "hy flex", "hyflex"})
        or compact.intersection({"hy", "hybrid", "hyflex"})
    )
    has_in_person = bool(
        normalized.intersection({"in person", "face to face", "on campus"})
        or compact.intersection({"p", "ip", "inperson", "facetoface"})
    )
    has_online = bool(
        normalized.intersection({"online", "fully online", "web"})
        or compact.intersection({"on", "online", "fullyonline", "web"})
    )

    if has_hybrid or (has_in_person and has_online):
        return InstructionMode.HYBRID
    if has_online:
        return (
            InstructionMode.ONLINE_SYNCHRONOUS
            if has_timed_meeting
            else InstructionMode.ONLINE_ASYNCHRONOUS
        )
    if has_in_person:
        return InstructionMode.IN_PERSON
    return InstructionMode.OTHER


def _natural_catalog_key(value: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.findall(r"\d+|\D+", value)
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts)


def import_schedule_csv(
    path: Path,
    *,
    term: str,
    default_campus: str = "SDSU",
    source_url: str = DEFAULT_SOURCE_URL,
) -> tuple[tuple[CourseSection, ...], ImportReport]:
    if not path.is_file():
        raise ScheduleCsvError(f"Schedule CSV not found: {path}")
    if not term.strip():
        raise ScheduleCsvError("Term cannot be blank.")

    source_updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    accumulators: OrderedDict[str, _SectionAccumulator] = OrderedDict()
    rows_read = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _resolve_columns(reader.fieldnames)
        for raw_row in reader:
            row = _clean_row(raw_row)
            if not any(row.values()):
                continue
            rows_read += 1

            class_number = _value(row, columns, "class_number")
            subject = _value(row, columns, "subject")
            catalog_number = _value(row, columns, "catalog_number")
            title = _value(row, columns, "title")
            section_number = _value(row, columns, "section_number")
            if not all((class_number, subject, catalog_number, title, section_number)):
                raise ScheduleCsvError(
                    f"Row {rows_read + 1} is missing a required value: {row!r}"
                )

            key = class_number
            accumulator = accumulators.get(key)
            if accumulator is None:
                campus = _value(row, columns, "campus") or default_campus
                accumulator = _SectionAccumulator(
                    term=term.strip(),
                    class_number=class_number,
                    subject=subject,
                    catalog_number=catalog_number,
                    section_number=section_number,
                    title=title,
                    description=_value(row, columns, "description") or None,
                    units=_parse_units(_value(row, columns, "units")),
                    campus=campus,
                    grading=_parse_grading(_value(row, columns, "grading")),
                    seat_status=_parse_seat_status(
                        _value(row, columns, "class_status")
                    ),
                    seat_capacity=_parse_nonnegative_int(
                        _value(row, columns, "seat_capacity")
                    ),
                    seats_enrolled=_parse_nonnegative_int(
                        _value(row, columns, "seats_enrolled")
                    ),
                    seats_available=_parse_nonnegative_int(
                        _value(row, columns, "seats_available")
                    ),
                    waitlist_available=_parse_nonnegative_int(
                        _value(row, columns, "waitlist_available")
                    ),
                    source_updated_at=source_updated_at,
                    source_url=source_url,
                )
                accumulators[key] = accumulator
            else:
                expected = (accumulator.subject, accumulator.catalog_number)
                actual = (subject, catalog_number)
                if expected != actual:
                    raise ScheduleCsvError(
                        f"Class number {class_number!r} refers to multiple courses: "
                        f"{expected!r} and {actual!r}."
                    )
            accumulator.add_row(row, columns)

    sections = tuple(
        sorted(
            (accumulator.build() for accumulator in accumulators.values()),
            key=lambda section: (
                section.subject.casefold(),
                _natural_catalog_key(section.catalog_number),
                _natural_catalog_key(section.section_number),
                section.schedule_number,
            ),
        )
    )
    report = ImportReport(
        rows_read=rows_read,
        sections_created=len(sections),
        meetings_created=sum(len(section.meetings) for section in sections),
    )
    return sections, report


def write_sections_json(
    sections: Sequence[CourseSection],
    *,
    output: Path = DEFAULT_OUTPUT,
    append: bool = False,
) -> ImportReport:
    combined: dict[str, CourseSection] = {}
    if append and output.is_file():
        existing_raw = json.loads(output.read_text(encoding="utf-8"))
        for item in existing_raw:
            existing = CourseSection.model_validate(item)
            combined[existing.id] = existing

    for section in sections:
        combined[section.id] = section

    ordered = sorted(
        combined.values(),
        key=lambda section: (
            section.term,
            section.subject.casefold(),
            _natural_catalog_key(section.catalog_number),
            _natural_catalog_key(section.section_number),
            section.schedule_number,
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = [section.model_dump(mode="json") for section in ordered]
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return ImportReport(
        rows_read=0,
        sections_created=len(ordered),
        meetings_created=sum(len(section.meetings) for section in ordered),
        output_path=output,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert an SDSU Schedule of Classes CSV export into ClassCatalog sections.json"
        )
    )
    parser.add_argument("input", type=Path, help="Path to the SDSU schedule CSV")
    parser.add_argument("--term", required=True, help='Display term, for example "Fall 2026"')
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge this term into an existing output file instead of replacing it",
    )
    parser.add_argument(
        "--default-campus",
        default="SDSU",
        help="Campus label used when the export has no Campus column",
    )
    args = parser.parse_args()

    sections, import_report = import_schedule_csv(
        args.input,
        term=args.term,
        default_campus=args.default_campus,
    )
    write_report = write_sections_json(sections, output=args.output, append=args.append)
    print(
        f"read {import_report.rows_read} CSV rows; "
        f"created {len(sections)} sections for {args.term}; "
        f"wrote {write_report.sections_created} total sections to {args.output}"
    )


if __name__ == "__main__":
    main()
