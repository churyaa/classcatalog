from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests

from classcatalog.models import SeatStatus
from classcatalog.scraping.class_detail import (
    build_class_detail_tab_post,
    find_class_detail_tab,
    find_class_detail_tabs,
    parse_class_information_page,
)
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession

FIXTURE_3212 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-enrollment-3212.html"
FIXTURE_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-enrollment-3213.html"
COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "CRSE_ID=038518&STRM=2267&CLASS_NBR=3213&SEC=05"
)
POST_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL"
)


def _fixture(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _response(url: str, body: str, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")  # noqa: SLF001 - requests test fixture
    response.encoding = "utf-8"
    return response


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.headers: dict[str, str] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self._responses: Iterator[requests.Response] = iter(responses)

    def mount(self, prefix: str, adapter: object) -> None:
        del prefix, adapter

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests.append((method, url, kwargs))
        return next(self._responses)

    def close(self) -> None:
        return None


def test_discovers_all_five_class_information_tabs() -> None:
    tabs = find_class_detail_tabs(_fixture(FIXTURE_3213))
    assert [(tab.label, tab.value) for tab in tabs] == [
        ("Class Details", "CD"),
        ("Meeting Information", "MI"),
        ("Enrollment Information", "EI"),
        ("Class Availability", "CA"),
        ("Textbook/Other Materials", "TI"),
    ]
    assert [tab.value for tab in tabs if tab.selected] == ["EI"]


def test_parses_enrollment_information_for_3212() -> None:
    record = parse_class_information_page(_fixture(FIXTURE_3212))
    assert record.class_number == "3212"
    assert record.component == "Lrg Lect"
    assert record.course_label == "CS 150 Intro Computer Programming"
    assert record.status is SeatStatus.OPEN
    assert record.selected_tab_value == "EI"
    assert record.enrollment_requirements == (
        "Credit or concurrent registration in CS 150L.",
    )
    assert record.class_notes == ()
    assert record.raw_class_notes_label == "No Class Notes"


def test_parses_enrollment_information_for_3213() -> None:
    record = parse_class_information_page(_fixture(FIXTURE_3213))
    assert record.class_number == "3213"
    assert record.status is SeatStatus.OPEN
    assert record.enrollment_requirements == (
        "Credit or concurrent registration in CS 150L.",
    )


def test_builds_meeting_information_tab_post_with_dynamic_action() -> None:
    html = _fixture(FIXTURE_3213)
    tab = find_class_detail_tab(html, "Meeting Information")
    post = build_class_detail_tab_post(html, current_url=POST_URL, tab=tab)
    fields = dict(post.fields)

    assert tab.action_id == "DERIVED_SSR_FL_SSR_CL_DTLS_LFF$68$"
    assert post.action_url == POST_URL
    assert fields["ICStateNum"] == "10"
    assert fields["ICAction"] == tab.action_id
    assert fields["DERIVED_SSR_FL_SSR_CL_DTLS_LFF"] == "MI"


def test_session_reopens_class_modal_then_posts_requested_tab(monkeypatch) -> None:
    enrollment_html = _fixture(FIXTURE_3213)
    meeting_html = enrollment_html.replace(
        "value=\"MI\" onclick=",
        "value=\"MI\" checked='checked' onclick=",
    ).replace("value=\"EI\" checked='checked'", "value=\"EI\"")

    fake = FakeSession([_response(POST_URL, meeting_html)])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    class StubClassPage:
        class_number = "3213"
        html = enrollment_html
        response_url = POST_URL

    monkeypatch.setattr(client, "fetch_class_number_page", lambda *args, **kwargs: StubClassPage())

    page = client.fetch_class_detail_tab_page(
        COURSE_INFO_URL,
        "3213",
        "MI",
    )

    assert len(fake.requests) == 1
    method, url, kwargs = fake.requests[0]
    assert method == "POST"
    assert url == POST_URL
    fields = dict(kwargs["data"])
    assert fields["ICAction"] == "DERIVED_SSR_FL_SSR_CL_DTLS_LFF$68$"
    assert fields["DERIVED_SSR_FL_SSR_CL_DTLS_LFF"] == "MI"
    assert page.class_number == "3213"
    assert page.tab_value == "MI"
    assert page.tab_label == "Meeting Information"


CLASS_DETAILS_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-class-details-3213.html"
MEETING_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-meeting-3213.html"
AVAILABILITY_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-availability-3213.html"
MATERIALS_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "class-detail-materials-3213.html"
COURSE_3213 = Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-course-info.html"
CLASS_POST_PAGE = Path(__file__).parent / "fixtures" / "sdsu" / "class-number-post-page.html"


def test_parses_class_details_tab() -> None:
    from classcatalog.models import GradingType, InstructionMode

    record = parse_class_information_page(_fixture(CLASS_DETAILS_3213))
    assert record.class_number == "3213"
    assert record.selected_tab_value == "CD"
    assert record.units == 3.0
    assert record.units_min == 3.0
    assert record.units_max == 3.0
    assert record.units_text == "3.00"
    assert record.grading is GradingType.LETTER_OR_CREDIT_NO_CREDIT
    assert record.grading_text == "Letter (CR/NC Available)"
    assert record.instruction_mode is InstructionMode.IN_PERSON
    assert record.instruction_mode_text == "In-Person"
    assert record.location == "San Diego State University"
    assert record.campus == "San Diego Campus"


def test_parses_variable_class_units_without_failing_tab_walk() -> None:
    html = _fixture(CLASS_DETAILS_3213).replace(
        "id='DERIVED_SSR_FL_SSR_DTL_FIELD1$0' >3.00</span>",
        "id='DERIVED_SSR_FL_SSR_DTL_FIELD1$0' >1.00 - 3.00</span>",
        1,
    )
    record = parse_class_information_page(html)

    assert record.units is None
    assert record.units_min == 1.0
    assert record.units_max == 3.0
    assert record.units_text == "1.00 - 3.00"


def test_parses_meeting_information_tab() -> None:
    from datetime import time

    from classcatalog.models import Weekday

    record = parse_class_information_page(_fixture(MEETING_3213))
    assert record.selected_tab_value == "MI"
    assert len(record.meetings) == 1
    meeting = record.meetings[0]
    assert meeting.meeting_dates == "08/24/2026 - 12/11/2026"
    assert meeting.days == (Weekday.MON, Weekday.WED)
    assert meeting.start_time == time(16, 0)
    assert meeting.end_time == time(17, 15)
    assert meeting.room == "OP 201"
    assert meeting.instructor == "Patricia Kraft"


def test_parses_class_availability_tab() -> None:
    record = parse_class_information_page(_fixture(AVAILABILITY_3213))
    assert record.selected_tab_value == "CA"
    assert record.seat_capacity == 80
    assert record.seats_enrolled == 28
    assert record.seats_available == 52
    assert record.waitlist_capacity == 999
    assert record.waitlist_total == 0
    assert record.waitlist_available == 999


def test_parses_materials_tab_without_guessing_textbook_required() -> None:
    record = parse_class_information_page(_fixture(MATERIALS_3213))
    assert record.selected_tab_value == "TI"
    assert record.bookstore_url == "https://ezbooks.sdsu.edu/myheoa/SDSU/2267/3213"
    assert record.materials_description is not None
    assert "Click the Bookstore Link" in record.materials_description
    assert record.textbook_required is None


def test_merges_all_tabs_and_assembles_near_final_section() -> None:
    from classcatalog.models import InstructionMode
    from classcatalog.scraping.class_detail import (
        assemble_sdsu_course_section,
        merge_class_information_records,
    )
    from classcatalog.scraping.parser import parse_course_info_page

    enrollment = parse_class_information_page(_fixture(FIXTURE_3213))
    details = parse_class_information_page(_fixture(CLASS_DETAILS_3213))
    meeting = parse_class_information_page(_fixture(MEETING_3213))
    availability = parse_class_information_page(_fixture(AVAILABILITY_3213))
    materials = parse_class_information_page(_fixture(MATERIALS_3213))
    merged = merge_class_information_records(
        enrollment,
        details,
        meeting,
        availability,
        materials,
        section_number="05",
    )
    course = parse_course_info_page(_fixture(COURSE_3213), source_url=COURSE_INFO_URL)
    option = next(option for option in course.options if option.class_number == "3213")
    section = assemble_sdsu_course_section(course, option, merged)

    assert section.term == "Fall 2026"
    assert section.course_code == "CS 150"
    assert section.class_number == "3213"
    assert section.section_number == "05"
    assert section.units == 3.0
    assert section.units_min == 3.0
    assert section.units_max == 3.0
    assert section.units_text == "3.00"
    assert section.instruction_mode is InstructionMode.IN_PERSON
    assert section.prerequisite_text == "Credit or concurrent registration in CS 150L."
    assert section.seat_capacity == 80
    assert section.seats_enrolled == 28
    assert section.seats_available == 52
    assert section.waitlist_capacity == 999
    assert section.waitlist_total == 0
    assert section.waitlist_available == 999
    assert section.instructor == "Patricia Kraft"
    assert section.meetings[0].room == "OP 201"
    assert section.bookstore_url == "https://ezbooks.sdsu.edu/myheoa/SDSU/2267/3213"
    assert section.textbook_required is None
    assert section.course_source_url == COURSE_INFO_URL
    payload = section.model_dump(mode="json")
    assert payload["course_source_url"] == COURSE_INFO_URL
    assert "source_url" not in payload


def test_assembles_variable_units_into_section_record() -> None:
    from classcatalog.scraping.class_detail import (
        assemble_sdsu_course_section,
        merge_class_information_records,
    )
    from classcatalog.scraping.parser import parse_course_info_page

    course_html = _fixture(COURSE_3213).replace(
        'id="SSR_CLSRCH_F_WK_UNITS_RANGE">3.00</span>',
        'id="SSR_CLSRCH_F_WK_UNITS_RANGE">1.00 - 3.00</span>',
        1,
    )
    details_html = _fixture(CLASS_DETAILS_3213).replace(
        "id='DERIVED_SSR_FL_SSR_DTL_FIELD1$0' >3.00</span>",
        "id='DERIVED_SSR_FL_SSR_DTL_FIELD1$0' >1.00 - 3.00</span>",
        1,
    )
    course = parse_course_info_page(course_html, source_url=COURSE_INFO_URL)
    option = next(option for option in course.options if option.class_number == "3213")
    merged = merge_class_information_records(
        parse_class_information_page(_fixture(FIXTURE_3213)),
        parse_class_information_page(details_html),
        section_number="05",
    )

    section = assemble_sdsu_course_section(course, option, merged)

    assert section.units is None
    assert section.units_min == 1.0
    assert section.units_max == 3.0
    assert section.units_text == "1.00 - 3.00"


def test_keeps_every_course_option_when_one_class_detail_merge_is_missing() -> None:
    from classcatalog.scraping.class_detail import (
        assemble_sdsu_course_section,
        ensure_all_course_option_sections,
        merge_class_information_records,
    )
    from classcatalog.scraping.parser import parse_course_info_page

    course = parse_course_info_page(_fixture(COURSE_3213), source_url=COURSE_INFO_URL)
    option_3213 = next(option for option in course.options if option.class_number == "3213")
    merged_3213 = merge_class_information_records(
        parse_class_information_page(_fixture(FIXTURE_3213)),
        parse_class_information_page(_fixture(CLASS_DETAILS_3213)),
        parse_class_information_page(_fixture(MEETING_3213)),
        parse_class_information_page(_fixture(AVAILABILITY_3213)),
        parse_class_information_page(_fixture(MATERIALS_3213)),
        section_number="05",
    )
    section_3213 = assemble_sdsu_course_section(course, option_3213, merged_3213)

    sections, fallback_class_numbers = ensure_all_course_option_sections(
        course,
        [section_3213],
    )

    assert [section.class_number for section in sections] == ["3212", "3213"]
    assert fallback_class_numbers == ("3212",)

    section_3212 = sections[0]
    assert section_3212.section_number is None
    assert section_3212.instructor == "Patricia Kraft"
    assert section_3212.seat_capacity == 60
    assert section_3212.seats_available == 1
    assert section_3212.seats_enrolled == 59
    assert section_3212.meetings[0].room == "San Diego State University (GMCS 314)"
    assert section_3212.course_source_url == COURSE_INFO_URL

    assert sections[1].section_number == "05"
    assert sections[1].course_source_url == COURSE_INFO_URL


def test_session_walks_all_tabs_from_same_class_modal_state() -> None:
    """Do not reopen the 3213-anchored course URL while following class 3212/3213 tabs."""

    from classcatalog.scraping.session import RawClassDetailPage

    enrollment_html = _fixture(FIXTURE_3213)
    fake = FakeSession(
        [
            _response(POST_URL, _fixture(CLASS_DETAILS_3213)),
            _response(POST_URL, _fixture(MEETING_3213)),
            _response(POST_URL, _fixture(AVAILABILITY_3213)),
            _response(POST_URL, _fixture(MATERIALS_3213)),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )
    class_page = RawClassDetailPage(
        class_number="3213",
        action_id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1",
        course_info_url=COURSE_INFO_URL,
        post_url=POST_URL,
        response_url=POST_URL,
        html=enrollment_html,
    )

    pages = client.fetch_class_detail_tab_pages_from_class_page(
        class_page,
        ["CD", "MI", "CA", "TI"],
    )

    assert [method for method, _url, _kwargs in fake.requests] == [
        "POST",
        "POST",
        "POST",
        "POST",
    ]
    assert [page.tab_value for page in pages] == ["CD", "MI", "CA", "TI"]
    assert all(page.class_number == "3213" for page in pages)
    assert [dict(request[2]["data"])["DERIVED_SSR_FL_SSR_CL_DTLS_LFF"] for request in fake.requests] == [
        "CD",
        "MI",
        "CA",
        "TI",
    ]


def test_same_modal_tab_walk_rejects_class_context_switch() -> None:
    from classcatalog.scraping.session import PeopleSoftSessionError, RawClassDetailPage
    import pytest

    class_page = RawClassDetailPage(
        class_number="3212",
        action_id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0",
        course_info_url=COURSE_INFO_URL,
        post_url=POST_URL,
        response_url=POST_URL,
        html=_fixture(FIXTURE_3212),
    )
    # Simulate PeopleSoft unexpectedly returning class 3213 after a tab click.
    fake = FakeSession([_response(POST_URL, _fixture(CLASS_DETAILS_3213))])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(PeopleSoftSessionError, match="context changed"):
        client.fetch_class_detail_tab_pages_from_class_page(class_page, ["CD"])


def test_class_modal_post_failure_cools_down_and_rebuilds_fresh_state(monkeypatch) -> None:
    fake = FakeSession(
        [
            _response(COURSE_INFO_URL, _fixture(CLASS_POST_PAGE)),
            _response(POST_URL, "temporary", status=503),
            _response(COURSE_INFO_URL, _fixture(CLASS_POST_PAGE)),
            _response(POST_URL, _fixture(FIXTURE_3213)),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("classcatalog.scraping.session.time.sleep", sleeps.append)
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            jitter_seconds=0,
            max_retries=0,
            post_recovery_cooldown_seconds=20,
            stateful_post_recovery_attempts=1,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    page = client.fetch_class_number_page(COURSE_INFO_URL, "3213")

    assert page.class_number == "3213"
    assert [method for method, _url, _kwargs in fake.requests] == [
        "GET",
        "POST",
        "GET",
        "POST",
    ]
    assert sleeps == [20]
    first_post = dict(fake.requests[1][2]["data"])
    recovered_post = dict(fake.requests[3][2]["data"])
    assert first_post["ICAction"] == recovered_post["ICAction"]
    assert recovered_post["ICStateNum"] == first_post["ICStateNum"]


def test_tab_post_failure_reopens_modal_and_restarts_complete_tab_walk(monkeypatch) -> None:
    from classcatalog.scraping.session import RawClassDetailPage

    class_page = RawClassDetailPage(
        class_number="3213",
        action_id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1",
        course_info_url=COURSE_INFO_URL,
        post_url=POST_URL,
        response_url=POST_URL,
        html=_fixture(FIXTURE_3213),
    )
    fake = FakeSession(
        [
            # First walk: CD succeeds, MI is throttled.
            _response(POST_URL, _fixture(CLASS_DETAILS_3213)),
            _response(POST_URL, "temporary", status=503),
            # Recovery: fresh course state -> reopen class modal.
            _response(COURSE_INFO_URL, _fixture(CLASS_POST_PAGE)),
            _response(POST_URL, _fixture(FIXTURE_3213)),
            # Restart requested tab sequence from the beginning.
            _response(POST_URL, _fixture(CLASS_DETAILS_3213)),
            _response(POST_URL, _fixture(MEETING_3213)),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("classcatalog.scraping.session.time.sleep", sleeps.append)
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            jitter_seconds=0,
            max_retries=0,
            post_recovery_cooldown_seconds=20,
            stateful_post_recovery_attempts=1,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    pages = client.fetch_class_detail_tab_pages_from_class_page(class_page, ["CD", "MI"])

    assert [page.tab_value for page in pages] == ["CD", "MI"]
    assert [method for method, _url, _kwargs in fake.requests] == [
        "POST",
        "POST",
        "GET",
        "POST",
        "POST",
        "POST",
    ]
    assert sleeps == [20]
    assert dict(fake.requests[4][2]["data"])["DERIVED_SSR_FL_SSR_CL_DTLS_LFF"] == "CD"
    assert dict(fake.requests[5][2]["data"])["DERIVED_SSR_FL_SSR_CL_DTLS_LFF"] == "MI"


def test_section_membership_uses_unique_physical_classes_for_linked_components() -> None:
    from classcatalog.scraping.class_detail import ensure_all_course_option_sections
    from classcatalog.scraping.parser import parse_course_info_page

    course = parse_course_info_page(_fixture(COURSE_3213), source_url=COURSE_INFO_URL)

    sections, fallback_class_numbers = ensure_all_course_option_sections(
        course,
        [],
        expected_classes=(
            ("6403", "Lrg Lect", 0),
            ("7795", "Laboratory", 0),
            ("6403", "Lrg Lect", 1),
            ("12612", "Laboratory", 1),
        ),
    )

    assert [section.class_number for section in sections] == ["6403", "7795", "12612"]
    assert fallback_class_numbers == ("6403", "7795", "12612")
    assert [section.component for section in sections] == [
        "Lrg Lect",
        "Laboratory",
        "Laboratory",
    ]
    assert [section.option_number for section in sections] == [1, 1, 2]
