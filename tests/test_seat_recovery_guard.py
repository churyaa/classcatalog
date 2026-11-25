"""Offline regression: bound *additional* recovery work, not verified seat data.

These tests never access SDSU, production caches, or a live service.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.session import PeopleSoftSessionError
from classcatalog.seats import (
    SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS,
    SEAT_OPTIONAL_RECOVERY_COOLDOWN_SECONDS,
    SeatRefreshRecoveryDeferredError,
    SeatRefreshService,
)
from classcatalog import seats

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM=2273&TEST=1"
)


def make_service(tmp_path: Path):
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    section = base.model_copy(update={
        "id": "recovery-guard-test",
        "source_url": URL,
        "term": "Spring 2027",
        "term_code": "2273",
        "subject": "CHEM",
        "course_code": "CHEM 201",
        "catalog_number": "201",
        "schedule_number": "11234",
        "section_number": "01",
        "crse_id": "012345",
        "crse_offer_nbr": "1",
        "acad_career": "UGRD",
    })
    return SeatRefreshService(
        CourseRepository((section,)),
        cache_path=tmp_path / "seats.json",
        instructor_cache_path=tmp_path / "instructors.json",
        request_delay_seconds=0,
        preferred_term="Spring 2027",
    )


def test_budget_does_not_reject_work_before_threshold(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    monkeypatch.setattr(seats.time, "monotonic", lambda: 100.0)
    svc._check_optional_seat_recovery_budget(
        100.0 - SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS + 0.5,
        "retry",
    )
    with pytest.raises(SeatRefreshRecoveryDeferredError) as failure:
        svc._check_optional_seat_recovery_budget(
            100.0 - SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS,
            "retry",
        )
    assert failure.value.stage == "retry"
    assert svc.public_status()["seat_refresh_throughput"]["recovery_deferred_last_300s"] == 1


def test_slow_failed_initial_fetch_does_not_start_cascading_retries(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(seats.time, "monotonic", lambda: clock[0])
    resets = []

    class SlowClient:
        def fetch_course_info_page(self, url):
            assert url == URL
            clock[0] += SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS + 1
            raise PeopleSoftSessionError("simulated slow response")

        def reset_public_session(self):
            resets.append(True)

    before = svc.repository.sections[0].seat_updated_at
    svc._refresh_source(SlowClient(), URL)
    assert resets == []  # No second session or PeopleSoft request was started.
    assert svc.repository.sections[0].seat_updated_at == before
    assert svc._failure_history[URL]["error_type"] == "SeatRefreshRecoveryDeferredError"
    assert SEAT_OPTIONAL_RECOVERY_COOLDOWN_SECONDS - 2 <= (
        svc._source_retry_after[URL] - time.time()
    ) <= SEAT_OPTIONAL_RECOVERY_COOLDOWN_SECONDS + 2
    metrics = svc.public_status()["seat_refresh_throughput"]
    assert metrics["seat_refresh_phases_last_300s"]["direct"]["attempts"] == 1
    assert metrics["seat_refresh_phases_last_300s"]["direct"]["seconds"] > 50
    assert metrics["seat_refresh_phases_last_300s"]["retry"]["attempts"] == 0
    assert metrics["recovery_deferred_last_300s"] == 1


def test_successful_session_retry_invalidates_worker_context(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    calls = []
    svc._worker_context_affinity = ("Spring 2027", "2273", "CHEM")

    class Client:
        def fetch_course_info_page(self, url):
            calls.append("fetch")
            if calls.count("fetch") == 1:
                raise PeopleSoftSessionError("stale session")
            return SimpleNamespace(html="<html></html>", canonical_url=url, expansion_count=0)

        def reset_public_session(self):
            calls.append("reset")

        def bootstrap(self):
            calls.append("bootstrap")

    monkeypatch.setattr(svc, "_parse_expected_source_page", lambda *_: SimpleNamespace(
        options=(), options_complete=False, term_code="2273", course_code="CHEM 201",
    ))
    svc._refresh_source(Client(), URL)
    assert calls == ["fetch", "reset", "bootstrap", "fetch"]
    assert svc._worker_context_affinity is None
    metrics = svc.public_status()["seat_refresh_throughput"]["seat_refresh_phases_last_300s"]
    assert metrics["retry"]["attempts"] == 1
    assert metrics["session_reset"]["attempts"] == 1
    assert svc._failed_sources == 0


def test_linked_modals_stop_at_deadline_without_erasing_existing_seats(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    calls = []
    monkeypatch.setattr(seats.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(seats, "find_class_number_actions", lambda _: (
        SimpleNamespace(class_number="11234"),
    ))

    class Client:
        def fetch_class_detail_tab_page(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("A new optional modal must not be started after deadline")

    rows, updates = {}, {}
    svc._refresh_linked_component_seats(
        Client(), SimpleNamespace(html="<html></html>", canonical_url=URL),
        svc._sources[URL], parsed_term_code="2273",
        cache_rows=rows, updates=updates, refreshed_at="2026-11-25T02:00:00+00:00",
        deadline_monotonic=100.0,
    )
    assert not calls
    assert rows == {} and updates == {}
    assert svc.public_status()["seat_refresh_throughput"]["linked_modal_deferred_last_300s"] == 1


def test_inventory_mismatch_still_gets_long_protection(tmp_path):
    from classcatalog.seats import SeatInventoryMismatchError

    svc = make_service(tmp_path)
    source = svc._sources[URL]
    error = SeatInventoryMismatchError(
        course_code="CHEM 201", term="Spring 2027",
        expected_class_numbers={"11234"}, observed_class_numbers={"99888"},
    )
    svc._record_source_failure(URL, source, error, duration_seconds=0.1)
    assert svc._source_retry_after[URL] - time.time() >= 1798
    assert svc._missing_numbers_by_source[URL] == {"11234"}
    assert svc._unlisted_numbers_by_source[URL] == {"99888"}


def test_slow_but_verified_direct_result_is_still_accepted(tmp_path, monkeypatch):
    from classcatalog.models import SeatStatus

    svc = make_service(tmp_path)
    clock = [100.0]
    monkeypatch.setattr(seats.time, "monotonic", lambda: clock[0])

    class Client:
        def fetch_course_info_page(self, url):
            clock[0] += SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS + 5
            return SimpleNamespace(html="<html></html>", canonical_url=url, expansion_count=0)

    option = SimpleNamespace(
        class_number="11234", instructor="TBA", status=SeatStatus.OPEN,
        open_seats=5, seat_capacity=30, seats_enrolled=25,
    )
    monkeypatch.setattr(svc, "_parse_expected_source_page", lambda *_: SimpleNamespace(
        options=(option,), options_complete=True, term_code="2273",
        course_code="CHEM 201",
    ))
    svc._refresh_source(Client(), URL)
    assert svc._failed_sources == 0
    assert svc._source_last_success.get(URL) is not None
    assert svc.repository.sections[0].seats_available == 5
    assert svc.public_status()["seat_refresh_throughput"]["recovery_deferred_last_300s"] == 0
