from __future__ import annotations
from classcatalog.instruction_modes import classify_sdsu_instruction_mode

import re
from dataclasses import dataclass
from datetime import datetime, time
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from classcatalog.models import GradingType, InstructionMode, SeatStatus, Weekday
from classcatalog.scraping.facet_parser import normalize_space
from classcatalog.scraping.models import (
    ClassInformationRecord,
    ClassMeetingRecord,
    CourseClassOption,
    CourseInfoRecord,
    SdsuCourseSectionRecord,
)
from classcatalog.scraping.units import parse_units_text


class ClassDetailParseError(ValueError):
    """Raised when a PeopleSoft class-information modal cannot be parsed safely."""


class ClassDetailTabNotFound(ClassDetailParseError):
    """Raised when a requested class-information tab cannot be found."""


@dataclass(frozen=True, slots=True)
class ClassDetailTab:
    label: str
    value: str
    action_id: str
    field_name: str
    selected: bool


@dataclass(frozen=True, slots=True)
class PeopleSoftTabPost:
    action_url: str
    fields: tuple[tuple[str, str], ...]


_DAY_NAMES: tuple[tuple[str, Weekday], ...] = (
    ("Monday", Weekday.MON),
    ("Tuesday", Weekday.TUE),
    ("Wednesday", Weekday.WED),
    ("Thursday", Weekday.THU),
    ("Friday", Weekday.FRI),
    ("Saturday", Weekday.SAT),
    ("Sunday", Weekday.SUN),
)
_TIME_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*(?:to|[-\u2013\u2014])\s*"
    r"(?P<end>\d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)


def _form_with_state(soup: BeautifulSoup) -> Tag:
    for form in soup.find_all("form"):
        if not isinstance(form, Tag):
            continue
        if form.find("input", attrs={"name": "ICStateNum"}) is not None:
            return form
    raise ClassDetailParseError(
        "No stateful PeopleSoft form containing ICStateNum was found on the class page."
    )


def _selected_option_value(select: Tag) -> str:
    selected = select.find("option", selected=True)
    if selected is None:
        selected = select.find("option")
    if not isinstance(selected, Tag):
        return ""
    value = selected.get("value")
    if isinstance(value, str):
        return value
    return normalize_space(selected.get_text(" ", strip=True))


def _successful_controls(form: Tag) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = []
    for control in form.find_all(["input", "select", "textarea"]):
        if not isinstance(control, Tag) or control.has_attr("disabled"):
            continue
        name = control.get("name")
        if not isinstance(name, str) or not name:
            continue

        tag_name = control.name.casefold()
        if tag_name == "select":
            fields.append((name, _selected_option_value(control)))
            continue
        if tag_name == "textarea":
            fields.append((name, control.get_text()))
            continue

        input_type = str(control.get("type", "text")).casefold()
        if input_type in {"submit", "button", "image", "file", "reset"}:
            continue
        if input_type in {"checkbox", "radio"} and not control.has_attr("checked"):
            continue
        value = control.get("value")
        fields.append((name, value if isinstance(value, str) else ""))
    return tuple(fields)


def _replace_field(fields: list[tuple[str, str]], name: str, value: str) -> None:
    replaced = False
    output: list[tuple[str, str]] = []
    for field_name, field_value in fields:
        if field_name == name:
            if not replaced:
                output.append((name, value))
                replaced = True
            continue
        output.append((field_name, field_value))
    if not replaced:
        output.append((name, value))
    fields[:] = output


def _tag_text_by_id(soup: BeautifulSoup | Tag, element_id: str) -> str | None:
    tag = soup.find(id=element_id)
    if not isinstance(tag, Tag):
        return None
    value = normalize_space(tag.get_text(" ", strip=True))
    return value or None


def _tag_texts_by_id_prefix(soup: BeautifulSoup | Tag, prefix: str) -> tuple[str, ...]:
    values: list[str] = []
    for tag in soup.find_all(id=re.compile(rf"^{re.escape(prefix)}(?:\$\d+)?$", re.I)):
        if not isinstance(tag, Tag):
            continue
        value = normalize_space(tag.get_text(" ", strip=True))
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _parse_status(text: str | None) -> SeatStatus:
    normalized = normalize_space(text or "").casefold()
    normalized = re.sub(r"^status\s*:\s*", "", normalized)
    if normalized.startswith("open"):
        return SeatStatus.OPEN
    if "wait" in normalized:
        return SeatStatus.WAITLIST
    if normalized.startswith("closed"):
        return SeatStatus.CLOSED
    return SeatStatus.UNKNOWN


def _parse_class_line(text: str | None) -> tuple[str | None, str | None]:
    if text is None:
        return None, None
    match = re.search(r"^(?P<component>.*?)\s*[-\u2013\u2014]\s*(?P<class_number>\d+)\s*$", text)
    if match is None:
        number = re.search(r"\b(\d{3,})\b", text)
        return None, number.group(1) if number else None
    component = normalize_space(match.group("component")) or None
    return component, match.group("class_number")


def _parse_time_value(value: str) -> time | None:
    normalized = normalize_space(value).upper()
    for fmt in ("%I:%M%p", "%I:%M %p"):
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    return None


def _parse_days(value: str | None) -> tuple[Weekday, ...]:
    if value is None:
        return ()
    return tuple(
        weekday
        for name, weekday in _DAY_NAMES
        if name.casefold() in value.casefold()
    )


def _parse_time_range(value: str | None) -> tuple[time | None, time | None]:
    if value is None:
        return None, None
    match = _TIME_RANGE_RE.search(value)
    if match is None:
        return None, None
    return _parse_time_value(match.group("start")), _parse_time_value(match.group("end"))


def _parse_grading(value: str | None) -> GradingType | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    has_letter = "letter" in normalized
    has_crnc = "cr nc" in normalized or "credit no credit" in normalized
    if has_letter and has_crnc:
        return GradingType.LETTER_OR_CREDIT_NO_CREDIT
    if has_letter:
        return GradingType.LETTER
    if has_crnc:
        return GradingType.CREDIT_NO_CREDIT
    return GradingType.OTHER


def _parse_instruction_mode(value: str | None) -> InstructionMode | None:
    return classify_sdsu_instruction_mode(value)


def _grid_tables(soup: BeautifulSoup) -> tuple[tuple[tuple[str, ...], Tag], ...]:
    tables: list[tuple[tuple[str, ...], Tag]] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag) or table.find("tbody") is None:
            continue
        header_row = table.find("thead")
        if not isinstance(header_row, Tag):
            continue
        headers = tuple(
            normalize_space(th.get_text(" ", strip=True))
            for th in header_row.find_all("th", recursive=True)
            if isinstance(th, Tag)
        )
        if headers:
            tables.append((headers, table))
    return tuple(tables)


def _grid_rows_with_headers(
    soup: BeautifulSoup,
    required_headers: set[str],
) -> tuple[dict[str, Tag], ...]:
    normalized_required = {header.casefold() for header in required_headers}
    for headers, table in _grid_tables(soup):
        normalized_headers = {header.casefold() for header in headers}
        if not normalized_required.issubset(normalized_headers):
            continue
        rows: list[dict[str, Tag]] = []
        tbody = table.find("tbody")
        if not isinstance(tbody, Tag):
            continue
        for row in tbody.find_all("tr", recursive=False):
            if not isinstance(row, Tag):
                continue
            cells = [cell for cell in row.find_all("td", recursive=False) if isinstance(cell, Tag)]
            row_map = {
                headers[index]: cell
                for index, cell in enumerate(cells)
                if index < len(headers)
            }
            if row_map:
                rows.append(row_map)
        return tuple(rows)
    return ()


def _cell_text(row: dict[str, Tag], header: str) -> str | None:
    cell = row.get(header)
    if cell is None:
        # Tolerate harmless whitespace/case changes in PeopleSoft labels.
        for key, candidate in row.items():
            if key.casefold() == header.casefold():
                cell = candidate
                break
    if cell is None:
        return None
    text = normalize_space(cell.get_text(" ", strip=True))
    return text or None


def _common_modal_fields(
    soup: BeautifulSoup,
    html: str,
) -> tuple[
    tuple[ClassDetailTab, ...],
    ClassDetailTab | None,
    str | None,
    str | None,
    str | None,
    str | None,
    SeatStatus,
]:
    title = _tag_text_by_id(soup, "PT_TITLE")
    if title != "Class Information":
        raise ClassDetailParseError("The response is not a Class Information modal.")

    tabs = find_class_detail_tabs(html)
    selected = next((tab for tab in tabs if tab.selected), None)
    course_label = _tag_text_by_id(soup, "DERIVED_SSR_FL_SSR_SBJ_CAT_NBR")
    raw_status = _tag_text_by_id(soup, "DERIVED_SSR_FL_SSR_CLASS_SPECS")
    class_line = _tag_text_by_id(soup, "DERIVED_SSR_FL_SSR_SESSION_TRAN")
    component, class_number = _parse_class_line(class_line)
    if class_number is None:
        raise ClassDetailParseError("Class Information modal is missing its class number.")
    return (
        tabs,
        selected,
        course_label,
        raw_status,
        component,
        class_number,
        _parse_status(raw_status),
    )


def find_class_detail_tabs(html: str) -> tuple[ClassDetailTab, ...]:
    """Discover class-information tabs by their live radio controls and labels."""

    soup = BeautifulSoup(html, "html.parser")
    tabs: list[ClassDetailTab] = []
    for radio in soup.find_all("input", attrs={"type": "radio", "role": "tab"}):
        if not isinstance(radio, Tag):
            continue
        action_id = radio.get("id")
        field_name = radio.get("name")
        value = radio.get("value")
        if not all(isinstance(item, str) and item for item in (action_id, field_name, value)):
            continue
        label_tag = soup.find("label", attrs={"for": action_id})
        label = (
            normalize_space(label_tag.get_text(" ", strip=True))
            if isinstance(label_tag, Tag)
            else value
        )
        tabs.append(
            ClassDetailTab(
                label=label,
                value=value,
                action_id=action_id,
                field_name=field_name,
                selected=radio.has_attr("checked"),
            )
        )
    if not tabs:
        raise ClassDetailParseError("No class-information tab controls were found.")
    return tuple(tabs)


def find_class_detail_tab(html: str, selector: str) -> ClassDetailTab:
    normalized = normalize_space(selector).casefold()
    for tab in find_class_detail_tabs(html):
        if tab.value.casefold() == normalized or tab.label.casefold() == normalized:
            return tab
    available = ", ".join(f"{tab.label} ({tab.value})" for tab in find_class_detail_tabs(html))
    raise ClassDetailTabNotFound(
        f"No class-information tab matched {selector!r}. Available tabs: {available}."
    )


def build_class_detail_tab_post(
    html: str,
    *,
    current_url: str,
    tab: ClassDetailTab,
) -> PeopleSoftTabPost:
    """Rebuild live PeopleSoft modal state for one class-information tab click."""

    soup = BeautifulSoup(html, "html.parser")
    form = _form_with_state(soup)
    fields = list(_successful_controls(form))
    _replace_field(fields, tab.field_name, tab.value)
    _replace_field(fields, "ICAction", tab.action_id)

    form_action = form.get("action")
    action_url = (
        urljoin(current_url, form_action)
        if isinstance(form_action, str) and form_action
        else current_url
    )
    return PeopleSoftTabPost(action_url=action_url, fields=tuple(fields))


def parse_class_information_page(html: str) -> ClassInformationRecord:
    """Parse whichever tab is selected in an SDSU Class Information modal."""

    soup = BeautifulSoup(html, "html.parser")
    (
        _tabs,
        selected,
        course_label,
        raw_status,
        component,
        class_number,
        status,
    ) = _common_modal_fields(soup, html)

    requirements: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    notes_label: str | None = None
    units: float | None = None
    units_min: float | None = None
    units_max: float | None = None
    units_text: str | None = None
    grading: GradingType | None = None
    grading_text: str | None = None
    instruction_mode: InstructionMode | None = None
    instruction_mode_text: str | None = None
    location: str | None = None
    campus: str | None = None
    meetings: tuple[ClassMeetingRecord, ...] = ()
    seat_capacity: int | None = None
    seats_enrolled: int | None = None
    seats_available: int | None = None
    waitlist_capacity: int | None = None
    waitlist_total: int | None = None
    waitlist_available: int | None = None
    bookstore_url: str | None = None
    materials_description: str | None = None

    selected_value = selected.value if selected is not None else None
    if selected_value == "EI":
        requirements = _tag_texts_by_id_prefix(
            soup,
            "SSR_CLASSDT2_FL_SSR_CLASSDTL_DATA",
        )
        notes_label = _tag_text_by_id(soup, "DERIVED_SSR_FL_SSR_CLS_NOTE_FL")
        if notes_label and notes_label.casefold() != "no class notes":
            notes = (notes_label,)

    elif selected_value == "CD":
        rows = _grid_rows_with_headers(
            soup,
            {"Units", "Grading", "Instruction Mode", "Location", "Campus"},
        )
        if rows:
            row = rows[0]
            units_text = _cell_text(row, "Units")
            if units_text is not None:
                try:
                    parsed_units = parse_units_text(units_text)
                except ValueError as exc:
                    raise ClassDetailParseError(
                        f"Invalid class units value: {units_text!r}."
                    ) from exc
                units = parsed_units.units
                units_min = parsed_units.units_min
                units_max = parsed_units.units_max
                units_text = parsed_units.units_text
            grading_text = _cell_text(row, "Grading")
            grading = _parse_grading(grading_text)
            instruction_mode_text = _cell_text(row, "Instruction Mode")
            instruction_mode = _parse_instruction_mode(instruction_mode_text)
            location = _cell_text(row, "Location")
            campus = _cell_text(row, "Campus")

    elif selected_value == "MI":
        rows = _grid_rows_with_headers(
            soup,
            {"Meeting Dates", "Days", "Times", "Room", "Instructor"},
        )
        parsed_meetings: list[ClassMeetingRecord] = []
        for row in rows:
            start_time, end_time = _parse_time_range(_cell_text(row, "Times"))
            parsed_meetings.append(
                ClassMeetingRecord(
                    meeting_dates=_cell_text(row, "Meeting Dates"),
                    days=_parse_days(_cell_text(row, "Days")),
                    start_time=start_time,
                    end_time=end_time,
                    room=_cell_text(row, "Room"),
                    instructor=_cell_text(row, "Instructor"),
                )
            )
        meetings = tuple(parsed_meetings)

    elif selected_value == "CA":
        rows = _grid_rows_with_headers(
            soup,
            {
                "Class Capacity",
                "Enrollment Total",
                "Available Seats",
                "Waitlist Capacity",
                "Waitlist Total",
            },
        )
        if rows:
            row = rows[0]

            def parse_count(header: str) -> int | None:
                value = _cell_text(row, header)
                if value is None:
                    return None
                try:
                    return int(value)
                except ValueError as exc:
                    raise ClassDetailParseError(
                        f"Invalid {header} value: {value!r}."
                    ) from exc

            seat_capacity = parse_count("Class Capacity")
            seats_enrolled = parse_count("Enrollment Total")
            seats_available = parse_count("Available Seats")
            waitlist_capacity = parse_count("Waitlist Capacity")
            waitlist_total = parse_count("Waitlist Total")
            if waitlist_capacity is not None and waitlist_total is not None:
                waitlist_available = max(waitlist_capacity - waitlist_total, 0)

    elif selected_value == "TI":
        rows = _grid_rows_with_headers(
            soup,
            {"Course Materials", "Bookstore Link", "Description"},
        )
        if rows:
            row = rows[0]
            materials_description = _cell_text(row, "Description")
            link_cell = next(
                (
                    cell
                    for header, cell in row.items()
                    if header.casefold() == "bookstore link"
                ),
                None,
            )
            if link_cell is not None:
                link = link_cell.find("a", href=True)
                if isinstance(link, Tag):
                    href = link.get("href")
                    if isinstance(href, str) and href:
                        bookstore_url = href

    return ClassInformationRecord(
        class_number=class_number,
        component=component,
        course_label=course_label,
        status=status,
        raw_status=raw_status,
        selected_tab=selected.label if selected is not None else None,
        selected_tab_value=selected_value,
        enrollment_requirements=requirements,
        class_notes=notes,
        raw_class_notes_label=notes_label,
        units=units,
        units_min=units_min,
        units_max=units_max,
        units_text=units_text,
        grading=grading,
        grading_text=grading_text,
        instruction_mode=instruction_mode,
        instruction_mode_text=instruction_mode_text,
        location=location,
        campus=campus,
        meetings=meetings,
        seat_capacity=seat_capacity,
        seats_enrolled=seats_enrolled,
        seats_available=seats_available,
        waitlist_capacity=waitlist_capacity,
        waitlist_total=waitlist_total,
        waitlist_available=waitlist_available,
        bookstore_url=bookstore_url,
        materials_description=materials_description,
        # The tab only links to the bookstore. It does not state whether a textbook is required.
        textbook_required=None,
    )


def _first_not_none[T](records: tuple[ClassInformationRecord, ...], field: str) -> T | None:
    for record in records:
        value = getattr(record, field)
        if value is not None:
            return value  # type: ignore[no-any-return]
    return None


def _unique_strings(values: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
    output: list[str] = []
    for group in values:
        for value in group:
            if value not in output:
                output.append(value)
    return tuple(output)


def merge_class_information_records(
    *records: ClassInformationRecord,
    section_number: str | None = None,
) -> ClassInformationRecord:
    """Merge the EI/CD/MI/CA/TI partial records for one class number."""

    if not records:
        raise ClassDetailParseError("At least one Class Information record is required.")
    record_tuple = tuple(records)
    class_numbers = {record.class_number for record in record_tuple}
    if len(class_numbers) != 1:
        raise ClassDetailParseError(
            f"Cannot merge different class numbers: {sorted(class_numbers)!r}."
        )

    meetings: list[ClassMeetingRecord] = []
    for record in record_tuple:
        for meeting in record.meetings:
            if meeting not in meetings:
                meetings.append(meeting)

    statuses = [record.status for record in record_tuple if record.status is not SeatStatus.UNKNOWN]
    status = statuses[0] if statuses else SeatStatus.UNKNOWN

    return ClassInformationRecord(
        class_number=record_tuple[0].class_number,
        section_number=section_number or _first_not_none(record_tuple, "section_number"),
        component=_first_not_none(record_tuple, "component"),
        course_label=_first_not_none(record_tuple, "course_label"),
        status=status,
        raw_status=_first_not_none(record_tuple, "raw_status"),
        selected_tab="Combined",
        selected_tab_value=None,
        enrollment_requirements=_unique_strings(
            tuple(record.enrollment_requirements for record in record_tuple)
        ),
        class_notes=_unique_strings(tuple(record.class_notes for record in record_tuple)),
        raw_class_notes_label=_first_not_none(record_tuple, "raw_class_notes_label"),
        units=_first_not_none(record_tuple, "units"),
        units_min=_first_not_none(record_tuple, "units_min"),
        units_max=_first_not_none(record_tuple, "units_max"),
        units_text=_first_not_none(record_tuple, "units_text"),
        grading=_first_not_none(record_tuple, "grading"),
        grading_text=_first_not_none(record_tuple, "grading_text"),
        instruction_mode=_first_not_none(record_tuple, "instruction_mode"),
        instruction_mode_text=_first_not_none(record_tuple, "instruction_mode_text"),
        location=_first_not_none(record_tuple, "location"),
        campus=_first_not_none(record_tuple, "campus"),
        meetings=tuple(meetings),
        seat_capacity=_first_not_none(record_tuple, "seat_capacity"),
        seats_enrolled=_first_not_none(record_tuple, "seats_enrolled"),
        seats_available=_first_not_none(record_tuple, "seats_available"),
        waitlist_capacity=_first_not_none(record_tuple, "waitlist_capacity"),
        waitlist_total=_first_not_none(record_tuple, "waitlist_total"),
        waitlist_available=_first_not_none(record_tuple, "waitlist_available"),
        bookstore_url=_first_not_none(record_tuple, "bookstore_url"),
        materials_description=_first_not_none(record_tuple, "materials_description"),
        textbook_required=_first_not_none(record_tuple, "textbook_required"),
    )


def assemble_sdsu_course_section(
    course: CourseInfoRecord,
    option: CourseClassOption,
    class_info: ClassInformationRecord,
) -> SdsuCourseSectionRecord:
    """Assemble course-level, option-level, and class-tab data into one section record."""

    if option.class_number != class_info.class_number:
        raise ClassDetailParseError(
            "Course option "
            f"{option.class_number} does not match class info {class_info.class_number}."
        )

    meetings = class_info.meetings
    if not meetings and (
            option.meeting_dates
            or option.days
            or option.start_time
            or option.end_time
            or option.location
            or option.instructor
    ):
        meetings = (
            ClassMeetingRecord(
                meeting_dates=option.meeting_dates,
                days=option.days,
                start_time=option.start_time,
                end_time=option.end_time,
                room=option.location,
                instructor=option.instructor,
            ),
        )

    has_timed_meeting = any(
        meeting.start_time is not None or meeting.end_time is not None
        for meeting in meetings
    )

    instruction_mode = classify_sdsu_instruction_mode(
        class_info.instruction_mode_text,
        has_timed_meeting=has_timed_meeting,
        fallback=class_info.instruction_mode or InstructionMode.OTHER,
    ) or InstructionMode.OTHER
    instructor = next(
        (meeting.instructor for meeting in meetings if meeting.instructor),
        option.instructor,
    )
    prerequisite_text = "\n".join(class_info.enrollment_requirements) or None

    return SdsuCourseSectionRecord(
        term=course.term,
        term_code=course.term_code,
        course_code=course.course_code,
        subject=course.subject,
        catalog_number=course.catalog_number,
        title=course.title,
        description=course.description,
        class_number=option.class_number,
        section_number=class_info.section_number or option.section_number,
        option_number=option.option_number,
        option_group_indices=((option.option_group_index,) if option.option_group_index is not None else ()),
        component=class_info.component or option.component,
        units=(
            class_info.units
            if class_info.units_min is not None
            else course.units
        ),
        units_min=(
            class_info.units_min
            if class_info.units_min is not None
            else course.units_min
        ),
        units_max=(
            class_info.units_max
            if class_info.units_max is not None
            else course.units_max
        ),
        units_text=(
            class_info.units_text
            if class_info.units_text is not None
            else course.units_text
        ),
        grading=class_info.grading or course.grading,
        grading_text=class_info.grading_text or course.grading_text,
        prerequisite_text=prerequisite_text,
        enrollment_requirements=class_info.enrollment_requirements,
        class_notes=class_info.class_notes,
        instruction_mode=instruction_mode,
        instruction_mode_text=class_info.instruction_mode_text,
        seat_status=(
            class_info.status
            if class_info.status is not SeatStatus.UNKNOWN
            else option.status
        ),
        seats_available=(
            class_info.seats_available
            if class_info.seats_available is not None
            else option.open_seats
        ),
        seat_capacity=(
            class_info.seat_capacity
            if class_info.seat_capacity is not None
            else option.seat_capacity
        ),
        seats_enrolled=(
            class_info.seats_enrolled
            if class_info.seats_enrolled is not None
            else option.seats_enrolled
        ),
        waitlist_capacity=class_info.waitlist_capacity,
        waitlist_total=class_info.waitlist_total,
        waitlist_available=class_info.waitlist_available,
        campus=class_info.campus,
        location=class_info.location or option.location,
        instructor=instructor,
        meetings=meetings,
        bookstore_url=class_info.bookstore_url,
        materials_description=class_info.materials_description,
        textbook_required=class_info.textbook_required,
        course_source_url=course.source_url,
    )


def ensure_all_course_option_sections(
    course: CourseInfoRecord,
    sections: tuple[SdsuCourseSectionRecord, ...] | list[SdsuCourseSectionRecord],
    *,
    expected_classes: tuple[tuple[str, str | None, int], ...] = (),
) -> tuple[tuple[SdsuCourseSectionRecord, ...], tuple[str, ...]]:
    """Return one section record for every unique physical class discovered.

    Course Information rows represent *enrollment options*, not necessarily physical
    sections.  A lecture+lab course can repeat one lecture class across many option
    rows while pairing it with a different lab each time.  ``expected_classes`` is a
    canonical, already-deduplicated sequence of ``(class_number, component, row)``
    tuples discovered from all clickable component links.  When it is unavailable,
    the primary class numbers from ``course.options`` remain the fallback source.
    """

    by_class = {section.class_number: section for section in sections}
    option_by_class: dict[str, CourseClassOption] = {}
    option_number_by_row: dict[int, int] = {}
    option_group_by_row: dict[int, int] = {}
    current_option_number: int | None = None
    current_option_group_index: int | None = None
    for option in sorted(course.options, key=lambda item: item.source_row_index):
        option_by_class.setdefault(option.class_number, option)
        if option.option_number is not None:
            current_option_number = option.option_number
        if option.option_group_index is not None:
            current_option_group_index = option.option_group_index
        if current_option_number is not None:
            option_number_by_row[option.source_row_index] = current_option_number
        if current_option_group_index is not None:
            option_group_by_row[option.source_row_index] = current_option_group_index

    canonical: list[tuple[str, str | None, int]] = []
    seen_canonical: set[str] = set()
    source = expected_classes or tuple(
        (option.class_number, option.component, option.source_row_index)
        for option in course.options
    )
    group_memberships_by_class: dict[str, set[int]] = {}
    primary_group_memberships_by_class: dict[str, set[int]] = {}
    for option in course.options:
        if option.option_group_index is not None:
            primary_group_memberships_by_class.setdefault(option.class_number, set()).add(
                option.option_group_index
            )
    for class_number, _component, row_index in source:
        group_index = option_group_by_row.get(row_index)
        if group_index is not None:
            group_memberships_by_class.setdefault(class_number, set()).add(group_index)
    for class_number, component, row_index in source:
        if class_number in seen_canonical:
            continue
        seen_canonical.add(class_number)
        canonical.append((class_number, component, row_index))

    fallback_class_numbers: list[str] = []
    for class_number, component, row_index in canonical:
        if class_number in by_class:
            continue
        option = option_by_class.get(class_number)
        if option is None:
            option = CourseClassOption(
                option_number=option_number_by_row.get(row_index),
                option_group_index=option_group_by_row.get(row_index),
                status=SeatStatus.UNKNOWN,
                component=component,
                class_number=class_number,
                source_row_index=row_index,
            )
        fallback_info = ClassInformationRecord(
            class_number=class_number,
            section_number=option.section_number,
            component=component or option.component,
            course_label=f"{course.course_code} {course.title}",
            status=option.status,
            raw_status=option.raw_status,
            selected_tab="Course Information fallback",
        )
        by_class[class_number] = assemble_sdsu_course_section(
            course,
            option,
            fallback_info,
        )
        fallback_class_numbers.append(class_number)

    for class_number, memberships in group_memberships_by_class.items():
        if class_number not in by_class or not memberships:
            continue
        existing = by_class[class_number]
        merged = tuple(sorted(set(existing.option_group_indices) | memberships))
        if merged != existing.option_group_indices:
            by_class[class_number] = existing.model_copy(
                update={"option_group_indices": merged}
            )

    for class_number, memberships in primary_group_memberships_by_class.items():
        if class_number not in by_class or not memberships:
            continue
        existing = by_class[class_number]
        merged = tuple(
            sorted(set(existing.option_primary_group_indices) | memberships)
        )
        if merged != existing.option_primary_group_indices:
            by_class[class_number] = existing.model_copy(
                update={"option_primary_group_indices": merged}
            )

    ordered = [
        by_class[class_number]
        for class_number, _component, _row_index in canonical
        if class_number in by_class
    ]
    seen = {record.class_number for record in ordered}
    ordered.extend(
        record
        for class_number, record in by_class.items()
        if class_number not in seen
    )
    return tuple(ordered), tuple(fallback_class_numbers)
