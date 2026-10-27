"""Offline regression checks for the conservative backend performance patch.

No SDSU HTTP requests, production JSON writes, or Oracle resources are used.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import CourseSection, GradingType, InstructionMode, SeatStatus
from classcatalog.repository import CourseRepository
from classcatalog.removal_review import RemovalReviewService
from classcatalog.seats import SeatRefreshService


def _section(number: str = "21001", **overrides) -> CourseSection:
    values = dict(
        id="test-" + number,
        term="Fall 2026",
        term_code="2267",
        course_code="CS 210",
        subject="CS",
        catalog_number="210",
        section_number="01",
        schedule_number=number,
        component="Lecture",
        title="Data Structures",
        units=3.0,
        grading=GradingType.LETTER,
        instruction_mode=InstructionMode.IN_PERSON,
        seat_status=SeatStatus.OPEN,
        seats_available=10,
        seat_capacity=30,
        seats_enrolled=20,
        instructor="Example Professor",
        campus="San Diego Campus",
    )
    values.update(overrides)
    return CourseSection(**values)


def test_seat_count_change_does_not_rebuild_options(monkeypatch) -> None:
    original = _section()
    repo = CourseRepository((original,))
    initial_options = repo.options()

    def unexpected_rebuild():
        raise AssertionError("Seat counts must not rebuild full search options")

    monkeypatch.setattr(repo, "_build_options", unexpected_rebuild)
    changed = repo.apply_seat_updates({("2267", "21001"): {"seats_available": 7, "seats_enrolled": 23}})
    assert changed == 1
    assert repo.options() is initial_options
    assert repo.sections[0].seats_available == 7
    assert repo.sections[0].seats_enrolled == 23


def test_status_transition_updates_only_seat_filter() -> None:
    repo = CourseRepository((_section(), _section("21002")))
    initial = repo.options()
    changed = repo.apply_seat_updates({("2267", "21001"): {"seat_status": SeatStatus.WAITLIST, "seats_available": 0}})
    assert changed == 1
    assert repo.options().seat_statuses == (SeatStatus.OPEN, SeatStatus.WAITLIST)
    assert repo.options().courses == initial.courses
    assert repo.options().instructors == initial.instructors
    assert repo.options().programs == initial.programs
    assert repo.sections[0].seat_status is SeatStatus.WAITLIST
    assert repo.sections[1].seat_status is SeatStatus.OPEN

    repo.apply_seat_updates({("2267", "21002"): {"seat_status": SeatStatus.WAITLIST}})
    assert repo.options().seat_statuses == (SeatStatus.WAITLIST,)


def test_no_op_seat_update_preserves_section_tuple_and_options() -> None:
    repo = CourseRepository((_section(),))
    previous_sections, previous_options = repo.sections, repo.options()
    changed = repo.apply_seat_updates({("2267", "21001"): {"seats_available": 10, "seat_status": SeatStatus.OPEN}})
    assert changed == 0
    assert repo.sections is previous_sections
    assert repo.options() is previous_options


def test_only_missing_number_failures_are_reported(tmp_path: Path) -> None:
    repo = CourseRepository((_section(),))
    service = SeatRefreshService(
        repo, cache_path=tmp_path / "seats.json", instructor_cache_path=tmp_path / "instructors.json",
    )
    service._failure_history["missing"] = {
        "detail": "The course-information page contains none of the expected class numbers.",
        "last_failed_timestamp": 100.0, "resolved": False,
    }
    service._failure_history["ordinary"] = {"detail": "Network timeout", "resolved": False}
    assert service.missing_number_source_urls() == {"missing"}
    service._source_last_success["missing"] = 101.0
    assert service.missing_number_source_urls() == set()


def test_removal_candidate_fast_path_avoids_full_seat_admin_status(tmp_path: Path) -> None:
    section = _section()
    repo = CourseRepository((section,))
    dataset = tmp_path / "sections.json"
    dataset.write_text(json.dumps([section.model_dump(mode="json")]), encoding="utf-8")

    class SeatStub:
        def missing_number_source_urls(self):
            return set()

        def admin_status(self):
            raise AssertionError("Full admin_status must not run for candidates")

    review = RemovalReviewService(repo, dataset, seat_service=SeatStub())
    assert review.candidates()["total_flagged"] == 0


def test_fastapi_routes_and_html_still_work(tmp_path: Path) -> None:
    section = _section()
    repo = CourseRepository((section,))
    dataset = tmp_path / "sections.json"
    dataset.write_text(json.dumps([section.model_dump(mode="json")]), encoding="utf-8")
    app = create_app(repo, data_path=dataset, seat_refresh_enabled=False, admin_password="test-only-password")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        options = client.get("/api/options")
        assert options.status_code == 200
        assert "CS 210" in options.text
        classes = client.get("/api/classes", params={"page_size": 1})
        assert classes.status_code == 200
        assert classes.json()["items"]
        assert client.get("/api/seats/status").status_code == 200
        assert client.get("/api/hidden-classes").json() == {"hidden": []}
        assert client.get("/subjects").status_code == 200
        assert client.get("/courses/cs-210").status_code == 200
        login = client.post("/api/admin/login", json={"password": "test-only-password"})
        assert login.status_code == 200
        assert client.get("/api/admin/removals").status_code == 200
