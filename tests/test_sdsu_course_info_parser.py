from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest

from classcatalog.models import GradingType, SeatStatus, Weekday
from classcatalog.scraping.models import CourseInfoRecord
from classcatalog.scraping.parser import ResultParseError, parse_course_info_page

FIXTURE = Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-course-info.html"
SOURCE_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&Action=U&"
    "CRSE_ID=038518&CRSE_OFFER_NBR=1&STRM=2267&INSTITUTION=SDCMP&"
    "ACAD_CAREER=UGRD&CLASS_NBR=3213&SEC=05"
)


def _record() -> CourseInfoRecord:
    return parse_course_info_page(FIXTURE.read_text(encoding="utf-8"), source_url=SOURCE_URL)


def test_parses_course_level_information_from_live_cs_150_fixture() -> None:
    record = _record()
    assert record.term == "Fall 2026"
    assert record.term_code == "2267"
    assert record.course_code == "CS 150"
    assert record.subject == "CS"
    assert record.catalog_number == "150"
    assert record.title == "Intro Computer Programming"
    assert record.description == (
        "Computing methodology, process, and computational problem solving. "
        "Algorithm Design; program design, development, and testing. Formerly numbered "
        "as CS 107. Not open to students with credit in CS 107."
    )
    assert record.units == 3.0
    assert record.units_min == 3.0
    assert record.units_max == 3.0
    assert record.units_text == "3.00"
    assert record.grading == GradingType.LETTER_OR_CREDIT_NO_CREDIT
    assert record.grading_text == "Letter (CR/NC Available)"
    assert record.components == "Large Lecture"
    assert record.course_career == "Undergraduate"


def test_parses_both_cs_150_class_options() -> None:
    record = _record()
    assert len(record.options) == 2
    assert [option.class_number for option in record.options] == ["3212", "3213"]
    assert [option.option_number for option in record.options] == [1, 2]


def test_parses_meeting_and_instructor_data() -> None:
    first = _record().options[0]
    assert first.status == SeatStatus.OPEN
    assert first.session == "Regular Academic Session"
    assert first.component == "Lrg Lect"
    assert first.meeting_dates == "08/24/2026 - 12/11/2026"
    assert first.days == (Weekday.MON, Weekday.WED)
    assert first.start_time == time(14, 0)
    assert first.end_time == time(15, 15)
    assert first.location == "San Diego State University (GMCS 314)"
    assert first.instructor == "Patricia Kraft"


def test_converts_people_soft_open_seats_to_enrollment_ratio() -> None:
    first, second = _record().options
    assert (first.open_seats, first.seat_capacity, first.seats_enrolled) == (1, 60, 59)
    assert (second.open_seats, second.seat_capacity, second.seats_enrolled) == (52, 80, 28)


def test_retains_selected_section_number_only_when_proven_by_url() -> None:
    first, second = _record().options
    assert first.section_number is None
    assert second.section_number == "05"
    assert _record().selected_class_number == "3213"
    assert _record().selected_section_number == "05"


def test_rejects_non_course_information_page() -> None:
    with pytest.raises(ResultParseError, match="subject/catalog"):
        parse_course_info_page("<html><body>not course information</body></html>")


def test_parses_variable_course_units_without_losing_range() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        'id="SSR_CLSRCH_F_WK_UNITS_RANGE">3.00</span>',
        'id="SSR_CLSRCH_F_WK_UNITS_RANGE">1.00 - 3.00</span>',
        1,
    )
    record = parse_course_info_page(html, source_url=SOURCE_URL)

    assert record.units is None
    assert record.units_min == 1.0
    assert record.units_max == 3.0
    assert record.units_text == "1.00 - 3.00"


def test_detects_truncated_people_soft_option_grid() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        ">2 options</span>",
        ">1 - 2 of 3 options</span>",
        1,
    )
    record = parse_course_info_page(html, source_url=SOURCE_URL)

    assert record.options_start == 1
    assert record.options_end == 2
    assert record.options_total == 3
    assert record.options_complete is False


def test_marks_fully_loaded_people_soft_option_grid_complete() -> None:
    record = _record()

    assert record.options_start == 1
    assert record.options_end == 2
    assert record.options_total == 2
    assert record.options_complete is True


def test_repeated_visible_option_numbers_get_distinct_row_groups() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1">2</a>',
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1">1</a>',
        1,
    )
    record = parse_course_info_page(html, source_url=SOURCE_URL)

    assert [option.option_number for option in record.options] == [1, 1]
    assert [option.option_group_index for option in record.options] == [1, 2]


def test_component_rows_inherit_the_last_visible_option_number() -> None:
    html = FIXTURE.read_text(encoding="utf-8").replace(
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1">2</a>',
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1"></a>',
        1,
    )
    record = parse_course_info_page(html, source_url=SOURCE_URL)
    assert [option.option_number for option in record.options] == [1, 1]
    assert [option.option_group_index for option in record.options] == [1, 1]
