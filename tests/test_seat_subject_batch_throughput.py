"""Offline regressions for subject-affinity batching and minute throughput.

No live SDSU calls; no production dataset modifications.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)
HIST = ("Spring 2027", "2273", "HIST")


def _service(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    # Arrange a non-HIST page *between* two HIST pages, so affinity is
    # observable rather than an accident of source insertion order.
    specs = [
        ("Spring 2027", "2273", "HIST", "HIST 110", "11310"),
        ("Spring 2027", "2273", "BIOL", "BIOL 350", "1871"),
        ("Spring 2027", "2273", "HIST", "HIST 111", "11311"),
        ("Spring 2027", "2273", "CHEM", "CHEM 100", "41001"),
        ("Spring 2027", "2273", "HIST", "HIST 112", "11312"),
        ("Fall 2026", "2267", "MATH", "MATH 150", "6528"),
        ("Winter 2027", "2271", "DANCE", "DANCE 101", "1014"),
        ("Summer 2027", "2275", "FIN", "FIN 331", "1267"),
    ]
    sections = []
    urls = {}
    for i, (term, code, subject, course, number) in enumerate(specs):
        url = f"{BASE}&STRM={code}&TEST={i}"
        urls[course] = url
        sections.append(sample.model_copy(update={
            "id": f"batch-test-{i}",
            "term": term,
            "term_code": code,
            "course_code": course,
            "subject": subject,
            "catalog_number": course.split(" ")[-1],
            "schedule_number": number,
            "source_url": url,
            "section_number": "01",
            "crse_id": f"{i + 100:06d}",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
            "seat_status": SeatStatus.OPEN,
            "seat_updated_at": None,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(sections)),
        cache_path=tmp_path / "seat-cache.json",
        instructor_cache_path=tmp_path / "instructor-cache.json",
        preferred_term="Spring 2027",
        request_delay_seconds=0,
    )
    return service, urls


def test_routine_prefers_matching_subject_without_changing_semester_turns(tmp_path):
    service, urls = _service(tmp_path)
    assert service._next_source(0) == urls["HIST 110"]
    service._source_last_attempt[urls["HIST 110"]] = time.time()
    # Represents a subject context that _run already established successfully.
    service._worker_context_affinity = HIST
    assert service._next_source(1) == urls["HIST 111"]
    service._source_last_attempt[urls["HIST 111"]] = time.time()
    assert service._next_source(2) == urls["HIST 112"]
    service._source_last_attempt[urls["HIST 112"]] = time.time()
    # A fourth Spring page is selected before the existing fifth other-term turn.
    assert service._next_source(3) == urls["BIOL 350"]
    assert service._next_source(4) == urls["MATH 150"]


def test_no_affinity_without_valid_worker_context(tmp_path):
    service, urls = _service(tmp_path)
    assert service._next_source(0) == urls["HIST 110"]
    # Without a verified PeopleSoft context, retain oldest/cursor order.
    assert service._next_source(1) == urls["BIOL 350"]
    assert service._next_source(2) == urls["HIST 111"]


def test_subject_affinity_never_displaces_never_refreshed_page(tmp_path):
    service, urls = _service(tmp_path)
    first = service._next_source(0)
    assert first == urls["HIST 110"]
    service._source_last_attempt[first] = time.time()
    service._source_last_success[urls["HIST 111"]] = time.time() - 10
    service._source_last_success[urls["HIST 112"]] = time.time() - 10
    service._worker_context_affinity = HIST
    # Freshly succeeded HIST pages can't jump ahead of untouched BIOL/CHEM.
    assert service._next_source(1) == urls["BIOL 350"]


def test_batch_affinity_is_bounded_and_cooldowns_are_respected(tmp_path):
    service, urls = _service(tmp_path)
    assert service._next_source(0) == urls["HIST 110"]
    service._worker_context_affinity = HIST
    service._routine_batch_count = 8
    assert service._next_source(1) == urls["BIOL 350"]

    another, u = _service(tmp_path / "another")
    assert another._next_source(0) == u["HIST 110"]
    another._worker_context_affinity = HIST
    another._source_retry_after[u["HIST 111"]] = time.time() + 3600
    assert another._next_source(1) == u["HIST 112"]


def test_manual_and_actively_viewed_sources_bypass_affinity(tmp_path):
    service, urls = _service(tmp_path)
    service._worker_context_affinity = HIST
    assert service.request_course_refresh("BIOL 350") == 1
    assert service._next_source(0) == urls["BIOL 350"]
    # Prioritized viewed course is chosen before subject affinity.
    service.register_interest(("1014",))
    assert service._next_source(5) == urls["DANCE 101"]


def test_last_minute_throughput_counts_real_success_and_failures(tmp_path, monkeypatch):
    service, urls = _service(tmp_path)
    course_url = urls["HIST 110"]
    parsed = SimpleNamespace(
        course_code="HIST 110", term="Spring 2027", term_code="2273",
        options_complete=True,
        options=(SimpleNamespace(
            class_number="11310", instructor="Jane Doe", status=SeatStatus.OPEN,
            open_seats=5, seat_capacity=30, seats_enrolled=25,
        ),),
    )
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page", lambda *_a, **_kw: parsed,
    )
    monkeypatch.setattr(
        "classcatalog.seats.find_class_number_actions", lambda _html: (),
    )
    client = SimpleNamespace(fetch_course_info_page=lambda url: SimpleNamespace(
        html="<html></html>", canonical_url=url, expansion_count=0,
    ))
    service._refresh_source(client, course_url)
    assert service.repository.seat_snapshot(("11310",))[("2273", "11310")].seats_available == 5
    other_url = urls["BIOL 350"]
    service._record_source_failure(
        other_url, service._sources[other_url], RuntimeError("offline test"),
        duration_seconds=0.1,
    )
    with service._lock:
        service._seat_throughput_events.append((time.time(), "context", 0))
    status = service.admin_status()
    throughput = status["seat_refresh_throughput"]
    assert throughput["window_seconds"] == 60
    assert throughput["successful_course_pages_last_60s"] == 1
    assert throughput["failed_course_pages_last_60s"] == 1
    assert throughput["section_updates_last_60s"] == 1
    assert throughput["subject_context_rehydrates_last_60s"] == 1
    assert throughput["successful_course_pages_last_300s"] == 1
    assert throughput["failed_course_pages_last_300s"] == 1
    assert "seat_refresh_throughput" not in service.public_status()


def test_throughput_window_excludes_old_events(tmp_path):
    service, _ = _service(tmp_path)
    now = time.time()
    with service._lock:
        service._seat_throughput_events.extend((
            (now - 400, "success", 100),
            (now - 120, "failure", 0),
            (now - 20, "success", 3),
        ))
    throughput = service.admin_status()["seat_refresh_throughput"]
    assert throughput["successful_course_pages_last_60s"] == 1
    assert throughput["section_updates_last_60s"] == 3
    assert throughput["failed_course_pages_last_60s"] == 0
    assert throughput["failed_course_pages_last_300s"] == 1
