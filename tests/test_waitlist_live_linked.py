"""Regression coverage for Waitlist Available Places and independent linked seats."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.models import CourseClassOption, CourseInfoRecord
from classcatalog.scraping.parser import _parse_waitlist_available_places
from classcatalog.seats import SeatRefreshService

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "STRM=2273&CLASS_NBR=3976"
)
CLASSES = {"3976": ("Lrg Lect", 180, 970), "3983": ("Laboratory", 24, 996), "3991": ("Discussion", 32, 989)}


def repository():
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    rows = []
    for number, (component, size, _) in CLASSES.items():
        rows.append(base.model_copy(update={
            "id": f"chem201-spring-{number}",
            "term": "Spring 2027", "term_code": "2273",
            "subject": "CHEM", "course_code": "CHEM 201", "catalog_number": "201",
            "title": "General Chemistry", "schedule_number": number,
            "component": component, "source_url": URL,
            "seat_status": SeatStatus.WAITLIST, "seats_available": 0,
            "seat_capacity": size, "seats_enrolled": size,
            "waitlist_available": 999, "waitlist_capacity": 999,
            "waitlist_total": 0, "seat_updated_at": None, "linked_components": (),
        }))
    return CourseRepository(tuple(rows))


def primary_grid():
    return CourseInfoRecord(
        term="Spring 2027", term_code="2273", subject="CHEM",
        course_code="CHEM 201", catalog_number="201", title="General Chemistry",
        units=5, units_min=5, units_max=5, units_text="5 units",
        grading="letter", options=(CourseClassOption(
            option_number=1, option_group_index=1, status=SeatStatus.WAITLIST,
            class_number="3976", open_seats=0, seat_capacity=180,
            seats_enrolled=180, source_row_index=0,
        ),),
    )


class Client:
    def __init__(self):
        self.requested = []

    def fetch_course_info_page(self, url):
        assert url == URL
        anchors = (
            f'<a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_{i}${293+i}$$0">'
            f'{component} - {number}</a>'
            for i, (number, (component, _, _)) in enumerate(CLASSES.items(), start=1)
        )
        html = '<html><body><table><tr id="SSR_CLS_DTLS_VW$0_row_0">' + ''.join(anchors) + '</tr></table></body></html>'
        return SimpleNamespace(html=html, canonical_url=URL, expansion_count=0)

    def fetch_class_detail_tab_page(self, url, number, tab):
        assert url == URL and tab == "CA"
        self.requested.append(number)
        return SimpleNamespace(html=number)


def test_waitlist_available_places_parser():
    assert _parse_waitlist_available_places("Waitlist Available Places 970 of 999") == (970, 999, 29)
    assert _parse_waitlist_available_places("Wait list Available Places 1,000 of 1,500") == (1000, 1500, 500)
    assert _parse_waitlist_available_places("Open Seats 12 of 42") == (None, None, None)
    assert _parse_waitlist_available_places("Waitlist Available Places 1200 of 999") == (None, None, None)


def test_all_three_chem_components_get_independent_verified_waitlist_counts(monkeypatch, tmp_path: Path):
    repo = repository()
    service = SeatRefreshService(repo, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_a, **_kw: primary_grid())
    monkeypatch.setattr("classcatalog.seats.parse_class_information_page", lambda number: SimpleNamespace(
        class_number=number, selected_tab_value="CA", status=SeatStatus.WAITLIST,
        seats_available=0, seat_capacity=CLASSES[number][1],
        seats_enrolled=CLASSES[number][1],
        waitlist_available=CLASSES[number][2], waitlist_capacity=999,
        waitlist_total=999-CLASSES[number][2],
    ))
    client = Client()
    service._refresh_source(client, URL)
    assert set(client.requested) == set(CLASSES)
    for row in repo.sections:
        assert row.seat_updated_at
        assert row.waitlist_available == CLASSES[row.schedule_number][2]
        assert row.waitlist_capacity == 999
        assert row.waitlist_total == 999 - CLASSES[row.schedule_number][2]
    service._flush_cache(force=True)
    restarted = SeatRefreshService(repository(), cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)
    for row in restarted.repository.sections:
        assert row.waitlist_available == CLASSES[row.schedule_number][2]
    for number, (_, _, avail) in CLASSES.items():
        assert restarted.seat_records((number,))[f"Spring 2027::{number}"]["waitlist_available"] == avail


def test_unverified_modal_does_not_mark_waitlist_count_fresh(monkeypatch, tmp_path: Path):
    repo = repository()
    service = SeatRefreshService(repo, cache_path=tmp_path / "cache.json", request_delay_seconds=0)
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_a, **_kw: primary_grid())
    monkeypatch.setattr("classcatalog.seats.parse_class_information_page", lambda _number: SimpleNamespace(
        class_number="9999", selected_tab_value="CA", status=SeatStatus.WAITLIST,
        seats_available=0, seat_capacity=10, seats_enrolled=10,
        waitlist_available=970, waitlist_capacity=999, waitlist_total=29,
    ))
    service._refresh_source(Client(), URL)
    assert all(row.seat_updated_at is None for row in repo.sections)
    assert all(row.waitlist_available == 999 for row in repo.sections)


def test_waitlist_display_does_not_use_classroom_capacity():
    script = (Path(__file__).resolve().parents[1] / "src/classcatalog/static/app.js").read_text(encoding="utf-8")
    assert 'section.waitlist_available}/${section.waitlist_capacity}' in script
    assert 'const primary = status === "waitlist"' in script
