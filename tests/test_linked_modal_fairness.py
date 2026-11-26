"""The viewed physical class must not starve behind unrelated course options."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.models import CourseClassOption, CourseInfoRecord
from classcatalog.seats import SeatRefreshService

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "STRM=2273&CLASS_NBR=11659"
)


def service(tmp_path: Path) -> SeatRefreshService:
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    sections = tuple(
        base.model_copy(update={
            "id": f"chem201-{number}", "term": "Spring 2027",
            "term_code": "2273", "subject": "CHEM", "course_code": "CHEM 201",
            "catalog_number": "201", "schedule_number": number,
            "source_url": URL, "seat_status": SeatStatus.WAITLIST,
            "seats_available": 0, "seat_capacity": 32, "seats_enrolled": 32,
            "waitlist_available": 998, "waitlist_capacity": 999,
            "seat_updated_at": None, "linked_components": (),
        }) for number in ("3976", "3983", "3991", "4010", "4020")
    )
    return SeatRefreshService(
        CourseRepository(sections), cache_path=tmp_path / "seat_cache.json",
        request_delay_seconds=0,
    )


def test_viewed_hidden_discussion_gets_verified_modal(monkeypatch, tmp_path):
    svc = service(tmp_path)
    svc.register_interest(("3991",))
    parsed = CourseInfoRecord(
        term="Spring 2027", term_code="2273", subject="CHEM",
        catalog_number="201", course_code="CHEM 201", title="General Chemistry",
        units=5, units_min=5, units_max=5, units_text="5 units", grading="letter",
        options=(CourseClassOption(
            option_number=1, option_group_index=1, status=SeatStatus.WAITLIST,
            class_number="3976", open_seats=0, seat_capacity=32,
            seats_enrolled=32, source_row_index=0,
        ),),
    )
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_a, **_k: parsed)
    monkeypatch.setattr("classcatalog.seats.parse_class_information_page", lambda html: SimpleNamespace(
        class_number=html, selected_tab_value="CA", status=SeatStatus.WAITLIST,
        seats_available=0, seat_capacity=32, seats_enrolled=32,
        waitlist_available=970 if html == "3991" else 990,
        waitlist_capacity=999, waitlist_total=29 if html == "3991" else 9,
    ))

    class Client:
        modal_numbers = []
        def fetch_course_info_page(self, _url):
            return SimpleNamespace(html=(
                '<a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">'
                'Lrg Lect - 3976</a>'
            ), canonical_url=URL, expansion_count=0)
        def fetch_class_detail_tab_page(self, _url, number, tab):
            assert tab == "CA"
            self.modal_numbers.append(number)
            # The real SDSU session can search expanded option pages for number.
            return SimpleNamespace(html=number)

    client = Client()
    svc._refresh_source(client, URL)
    assert client.modal_numbers[0] == "3991"
    assert len(client.modal_numbers) <= 4
    row = next(s for s in svc.repository.sections if s.schedule_number == "3991")
    assert row.waitlist_available == 970
    assert row.seat_updated_at is not None


def test_nonviewed_hidden_classes_do_not_add_modal_requests(monkeypatch, tmp_path):
    svc = service(tmp_path)
    source = svc._sources[URL]
    class Client:
        def fetch_class_detail_tab_page(self, *_a, **_kw):
            raise AssertionError("Routine sweep must not probe hidden physical classes")
    # None is visible or parsed, so the routine case must be a no-op.
    svc._refresh_linked_component_seats(
        Client(), SimpleNamespace(html="<html></html>", canonical_url=URL),
        source, parsed_term_code="2273", cache_rows={}, updates={},
        refreshed_at="2026-11-26T08:00:00+00:00",
    )


def test_unverified_grid_waitlist_row_cannot_hide_viewed_component(monkeypatch, tmp_path):
    """A status-only grid row must not suppress the viewed class's CA lookup."""
    svc = service(tmp_path)
    svc.register_interest(("3991",))
    parsed = CourseInfoRecord(
        term="Spring 2027", term_code="2273", subject="CHEM",
        catalog_number="201", course_code="CHEM 201", title="General Chemistry",
        units=5, units_min=5, units_max=5, units_text="5 units", grading="letter",
        options=(
            CourseClassOption(
                option_number=1, option_group_index=1, status=SeatStatus.WAITLIST,
                class_number="3976", open_seats=0, seat_capacity=32,
                seats_enrolled=32, source_row_index=0,
            ),
            CourseClassOption(
                option_number=2, option_group_index=2, status=SeatStatus.WAITLIST,
                class_number="3991", open_seats=0, seat_capacity=32,
                seats_enrolled=32, source_row_index=1,
            ),
        ),
    )
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_a, **_kw: parsed)
    monkeypatch.setattr("classcatalog.seats.parse_class_information_page", lambda number: SimpleNamespace(
        class_number=number, selected_tab_value="CA", status=SeatStatus.WAITLIST,
        seats_available=0, seat_capacity=32, seats_enrolled=32,
        waitlist_available=989 if number == "3991" else 990,
        waitlist_capacity=999, waitlist_total=10 if number == "3991" else 9,
    ))

    class Client:
        def __init__(self):
            self.requested = []

        def fetch_course_info_page(self, _url):
            # Lecture is clickable; viewed discussion is present in parsed
            # options but not among this page's clickable class actions.
            html = ('<a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">'
                    'Lrg Lect - 3976</a>')
            return SimpleNamespace(html=html, canonical_url=URL, expansion_count=0)

        def fetch_class_detail_tab_page(self, _url, number, tab):
            assert tab == "CA"
            self.requested.append(number)
            return SimpleNamespace(html=number)

    client = Client()
    svc._refresh_source(client, URL)
    assert client.requested and client.requested[0] == "3991"
    assert len(client.requested) <= 4
    record = svc.seat_records(("3991",))["Spring 2027::3991"]
    assert record["waitlist_available"] == 989
    assert record["waitlist_capacity"] == 999
    assert record["updated_at"] is not None
