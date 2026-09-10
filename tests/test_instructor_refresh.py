from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.models import CourseClassOption, CourseInfoRecord
from classcatalog.seats import SeatRefreshService

COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def _repository(*, instructor: str | None = "To Be Announced") -> CourseRepository:
    base = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    section = base.model_copy(
        update={
            "source_url": COURSE_INFO_URL,
            "term": "Fall 2026",
            "term_code": "2267",
            "course_code": "MATH 150",
            "subject": "MATH",
            "catalog_number": "150",
            "title": "Calculus I",
            "schedule_number": "6528",
            "section_number": "01",
            "crse_id": "038518",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
            "instructor": instructor,
            "professor": None,
            "seat_status": SeatStatus.OPEN,
            "seats_available": 12,
            "seat_capacity": 30,
            "seats_enrolled": 18,
        }
    )
    duplicate = section.model_copy(update={"id": f"{section.id}-duplicate"})
    return CourseRepository((section, duplicate))


def _parsed(instructor: str | None = "Ada Lovelace", *, include_seats: bool = True) -> CourseInfoRecord:
    return CourseInfoRecord(
        term="Fall 2026",
        term_code="2267",
        subject="MATH",
        catalog_number="150",
        course_code="MATH 150",
        title="Calculus I",
        units=4,
        units_min=4,
        units_max=4,
        units_text="4 units",
        grading="letter",
        source_url=COURSE_INFO_URL,
        options=(
            CourseClassOption(
                option_number=1,
                option_group_index=1,
                status=SeatStatus.UNKNOWN if not include_seats else SeatStatus.OPEN,
                class_number="6528",
                instructor=instructor,
                open_seats=None if not include_seats else 10,
                seat_capacity=None if not include_seats else 30,
                seats_enrolled=None if not include_seats else 20,
                source_row_index=1,
            ),
        ),
    )


class FakeClient:
    def reset_public_session(self) -> None:
        pass

    def bootstrap(self) -> str:
        return "ok"

    def fetch_course_info_page(self, url: str):
        assert url == COURSE_INFO_URL
        return SimpleNamespace(html="<html></html>", canonical_url=url, expansion_count=0)


def test_tba_refresh_queues_only_refreshable_tba_pages(tmp_path: Path) -> None:
    repository = _repository()
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat.json",
        instructor_cache_path=tmp_path / "instructors.json",
        request_delay_seconds=0,
    )

    pages, physical = service.request_tba_instructor_refresh()

    assert pages == 1
    assert physical == 1
    status = service.admin_status()
    assert status["tba_instructor_physical_sections"] == 1
    assert status["tba_instructor_course_pages"] == 1
    assert status["manual_refresh_pending"] == 1


def test_seat_fetch_opportunistically_upgrades_tba_and_persists_cache(monkeypatch, tmp_path: Path) -> None:
    repository = _repository()
    instructor_cache = tmp_path / "instructors.json"
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat.json",
        instructor_cache_path=instructor_cache,
        request_delay_seconds=0,
    )
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed())

    service._refresh_source(FakeClient(), COURSE_INFO_URL)

    assert all(section.instructor == "Ada Lovelace" for section in repository.sections)
    assert instructor_cache.is_file()
    payload = json.loads(instructor_cache.read_text(encoding="utf-8"))
    record = payload["sections"]["2267::6528"]
    assert record["instructor"] == "Ada Lovelace"
    assert service.admin_status()["last_instructor_updates"] == 1
    assert service.admin_status()["tba_instructor_physical_sections"] == 0
    assert service.seat_records(("6528",))["Fall 2026::6528"]["instructor"] == "Ada Lovelace"


def test_instructor_upgrade_does_not_require_seat_values(monkeypatch, tmp_path: Path) -> None:
    repository = _repository()
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat.json",
        instructor_cache_path=tmp_path / "instructors.json",
        request_delay_seconds=0,
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page",
        lambda *_args, **_kwargs: _parsed(include_seats=False),
    )

    service._refresh_source(FakeClient(), COURSE_INFO_URL)

    assert repository.sections[0].instructor == "Ada Lovelace"
    assert repository.sections[0].seats_available == 12


def test_cached_instructor_is_applied_when_service_restarts(tmp_path: Path) -> None:
    cache = tmp_path / "instructors.json"
    cache.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sections": {
                    "2267::6528": {
                        "term": "2267",
                        "schedule_number": "6528",
                        "instructor": "Ada Lovelace",
                        "updated_at": "2026-09-09T23:00:00+00:00",
                        "source_url": COURSE_INFO_URL,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    repository = _repository()

    SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat.json",
        instructor_cache_path=cache,
        request_delay_seconds=0,
    )

    assert all(section.instructor == "Ada Lovelace" for section in repository.sections)


def test_real_instructor_is_never_replaced_by_targeted_refresh(monkeypatch, tmp_path: Path) -> None:
    repository = _repository(instructor="Grace Hopper")
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat.json",
        instructor_cache_path=tmp_path / "instructors.json",
        request_delay_seconds=0,
    )
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed("Ada Lovelace"))

    pages, physical = service.request_tba_instructor_refresh()
    service._refresh_source(FakeClient(), COURSE_INFO_URL)

    assert (pages, physical) == (0, 0)
    assert all(section.instructor == "Grace Hopper" for section in repository.sections)
