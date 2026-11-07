"""Regression tests: multiple physical classes in one SDSU option row."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.models import CourseClassOption, CourseInfoRecord
from classcatalog.seats import SeatRefreshService


COURSE_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "STRM=2273&CLASS_NBR=1002"
)


def _repository() -> CourseRepository:
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    def section(number: str, available: int):
        return base.model_copy(update={
            "id": f"ae303-spring-{number}",
            "term": "Spring 2027",
            "term_code": "2273",
            "course_code": "A E 303",
            "subject": "A E",
            "catalog_number": "303",
            "title": "Experimental Aerodynamics",
            "component": "Discussion" if number == "1002" else "Laboratory",
            "schedule_number": number,
            "section_number": "01" if number == "1002" else "02",
            "source_url": COURSE_URL,
            "seat_status": SeatStatus.OPEN,
            "seats_available": available,
            "seat_capacity": 70,
            "seats_enrolled": 70 - available,
            "seat_updated_at": None,
            "linked_components": (),
        })
    return CourseRepository((section("1002", 64), section("1003", 70)))


def _parsed_primary_only() -> CourseInfoRecord:
    return CourseInfoRecord(
        term="Spring 2027", term_code="2273", subject="A E",
        catalog_number="303", course_code="A E 303",
        title="Experimental Aerodynamics", units=2, units_min=2,
        units_max=2, units_text="2.00", grading="letter",
        options=(CourseClassOption(
            option_number=1, option_group_index=1,
            status=SeatStatus.OPEN, component="Discussion",
            class_number="1002", open_seats=64, seat_capacity=70,
            seats_enrolled=6, source_row_index=0,
        ),),
    )


def _grid_html(*, include_lab: bool) -> str:
    secondary = (
        '<a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$295$$0">'
        'Laboratory - 1003</a>'
    ) if include_lab else ""
    return (
        '<html><body><table><tr id="SSR_CLS_DTLS_VW$0_row_0"><td>'
        '<a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">'
        'Discussion - 1002</a>' + secondary +
        '</td></tr></table></body></html>'
    )


class Client:
    def __init__(self, *, include_lab: bool = True):
        self.include_lab = include_lab
        self.modal_numbers: list[str] = []

    def fetch_course_info_page(self, url: str):
        assert url == COURSE_URL
        return SimpleNamespace(
            html=_grid_html(include_lab=self.include_lab),
            canonical_url=COURSE_URL, expansion_count=0,
        )

    def fetch_class_detail_tab_page(self, url: str, number: str, tab: str):
        assert url == COURSE_URL
        assert tab == "CA"
        self.modal_numbers.append(number)
        return SimpleNamespace(html=f"class-modal-{number}")


def test_secondary_lab_uses_its_own_class_availability_not_discussion_counts(
    monkeypatch, tmp_path: Path,
) -> None:
    repository = _repository()
    service = SeatRefreshService(
        repository, cache_path=tmp_path / "seat_cache.json",
        request_delay_seconds=0,
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page",
        lambda *_args, **_kwargs: _parsed_primary_only(),
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_class_information_page",
        lambda html: SimpleNamespace(
            class_number=html.rsplit("-", 1)[-1],
            selected_tab_value="CA", seat_status=SeatStatus.OPEN,
            status=SeatStatus.OPEN, seats_available=70, seat_capacity=70,
            seats_enrolled=0,
        ),
    )
    client = Client()
    service._refresh_source(client, COURSE_URL)
    service._flush_cache(force=True)

    assert client.modal_numbers == ["1003"]
    by_schedule = {row.schedule_number: row for row in repository.sections}
    assert by_schedule["1002"].seats_available == 64
    assert by_schedule["1002"].seat_updated_at is not None
    assert by_schedule["1003"].seats_available == 70
    assert by_schedule["1003"].seat_capacity == 70
    assert by_schedule["1003"].seat_updated_at is not None
    assert service.seat_records(("1002", "1003"))["Spring 2027::1003"]["updated_at"]
    assert service.admin_status()["cached_sections"] == 2
    assert service.admin_status()["failed_course_refreshes"] == 0
    assert (tmp_path / "seat_cache.json").is_file()
    assert service.missing_section_keys() == set()


def test_unlisted_secondary_is_not_assumed_fresh(monkeypatch, tmp_path: Path) -> None:
    repository = _repository()
    service = SeatRefreshService(
        repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0,
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page",
        lambda *_args, **_kwargs: _parsed_primary_only(),
    )
    client = Client(include_lab=False)
    service._refresh_source(client, COURSE_URL)

    assert client.modal_numbers == []
    section = next(row for row in repository.sections if row.schedule_number == "1003")
    assert section.seat_updated_at is None
    assert "1003" in service.missing_section_keys() or service.admin_status()["cached_sections"] == 1


def test_incorrect_modal_class_cannot_stamp_lab_fresh(monkeypatch, tmp_path: Path) -> None:
    repository = _repository()
    service = SeatRefreshService(
        repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0,
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page",
        lambda *_args, **_kwargs: _parsed_primary_only(),
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_class_information_page",
        lambda _html: SimpleNamespace(
            class_number="9999", selected_tab_value="CA", status=SeatStatus.OPEN,
            seats_available=70, seat_capacity=70, seats_enrolled=0,
        ),
    )
    client = Client()
    service._refresh_source(client, COURSE_URL)
    section = next(row for row in repository.sections if row.schedule_number == "1003")
    assert client.modal_numbers == ["1003"]
    assert section.seat_updated_at is None
    assert service.admin_status()["cached_sections"] == 1
