"""Offline tests for bounded PeopleSoft subject batching and priority preemption.

All fixtures are synthetic; no SDSU requests or production dataset edits.
"""
from __future__ import annotations

import time
from pathlib import Path

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)
HIST_CONTEXT = ("Spring 2027", "2273", "HIST")


def _service(tmp_path: Path, *, extra_biol: int = 0):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = [
        ("Spring 2027", "2273", "HIST", "HIST 110", "11310"),
        ("Spring 2027", "2273", "BIOL", "BIOL 350", "1871"),
    ]
    for idx in range(extra_biol):
        specs.append(("Spring 2027", "2273", "BIOL", f"BIOL {400+idx}", str(20000+idx)))
    specs.extend([
        ("Spring 2027", "2273", "HIST", "HIST 111", "11311"),
        ("Spring 2027", "2273", "CHEM", "CHEM 100", "41001"),
        ("Spring 2027", "2273", "HIST", "HIST 112", "11312"),
        ("Fall 2026", "2267", "MATH", "MATH 150", "6528"),
        ("Winter 2027", "2271", "DANCE", "DANCE 101", "1014"),
        ("Summer 2027", "2275", "FIN", "FIN 331", "1267"),
    ])
    sections = []
    urls = {}
    for index, (term, code, subject, course, number) in enumerate(specs):
        url = f"{BASE}&STRM={code}&TEST={index}"
        urls[course] = url
        sections.append(sample.model_copy(update={
            "id": f"bounded-test-{index}",
            "term": term,
            "term_code": code,
            "course_code": course,
            "subject": subject,
            "catalog_number": course.split(" ")[-1],
            "schedule_number": number,
            "source_url": url,
            "section_number": "01",
            "crse_id": f"{index + 100:06d}",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
            "seat_status": SeatStatus.OPEN,
            "seat_updated_at": None,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(sections)),
        cache_path=tmp_path / "seats.json",
        instructor_cache_path=tmp_path / "instructors.json",
        preferred_term="Spring 2027",
        request_delay_seconds=0,
    )
    return service, urls


def _simulate_previous_hist_success(service, urls):
    service._source_last_success[urls["HIST 110"]] = time.time() - 100
    service._source_last_attempt[urls["HIST 110"]] = time.time() - 100
    service._worker_context_affinity = HIST_CONTEXT
    service._routine_batch_context = HIST_CONTEXT
    service._routine_batch_count = 1


def test_recently_attempted_same_subject_can_bypass_older_attempt(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    now = time.time()
    service._source_last_attempt[urls["BIOL 350"]] = now - 200
    service._source_last_attempt[urls["HIST 111"]] = now - 190
    service._source_last_attempt[urls["CHEM 100"]] = now - 180
    service._source_last_attempt[urls["HIST 112"]] = now - 170
    assert service._next_source(1) == urls["HIST 111"]
    assert service.admin_status()["seat_refresh_throughput"]["affinity_reorders_last_60s"] == 1


def test_unattempted_page_cannot_be_displaced_by_attempted_affinity(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    service._source_last_attempt[urls["HIST 111"]] = time.time() - 30
    service._source_last_attempt[urls["HIST 112"]] = time.time() - 20
    # BIOL has never been tried; it outranks the already-attempted HIST page.
    assert service._next_source(1) == urls["BIOL 350"]


def test_no_affinity_beyond_age_gap(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    now = time.time()
    service._source_last_attempt[urls["BIOL 350"]] = now - 2000
    service._source_last_attempt[urls["HIST 111"]] = now - 200
    service._source_last_attempt[urls["CHEM 100"]] = now - 50
    service._source_last_attempt[urls["HIST 112"]] = now - 40
    assert service._next_source(1) == urls["BIOL 350"]


def test_never_successful_outranks_recently_successful_affinity(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    service._source_last_success[urls["HIST 111"]] = time.time() - 500
    service._source_last_success[urls["HIST 112"]] = time.time() - 400
    assert service._next_source(1) == urls["BIOL 350"]


def test_no_more_than_twelve_older_pages_are_bypassed(tmp_path):
    service, urls = _service(tmp_path, extra_biol=14)
    _simulate_previous_hist_success(service, urls)
    now = time.time()
    service._source_last_attempt[urls["BIOL 350"]] = now - 200
    for i in range(14):
        service._source_last_attempt[urls[f"BIOL {400+i}"]] = now - 199 + i
    service._source_last_attempt[urls["HIST 111"]] = now - 100
    service._source_last_attempt[urls["CHEM 100"]] = now - 90
    service._source_last_attempt[urls["HIST 112"]] = now - 80
    assert service._next_source(1) == urls["BIOL 350"]


def test_subject_batch_limit_cannot_reorder_ninth_page(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    now = time.time()
    service._routine_batch_count = 8
    service._source_last_attempt[urls["BIOL 350"]] = now - 100
    service._source_last_attempt[urls["HIST 111"]] = now - 90
    # CHEM must also have been attempted; otherwise its untouched page
    # correctly outranks every attempted page regardless of batching.
    service._source_last_attempt[urls["CHEM 100"]] = now - 85
    service._source_last_attempt[urls["HIST 112"]] = now - 80
    assert service._next_source(1) == urls["BIOL 350"]


def test_manual_refresh_preempts_batch_even_while_on_cooldown(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    with service._lock:
        service._source_retry_after[urls["BIOL 350"]] = time.time() + 1800
    assert service.request_course_refresh("BIOL 350") == 1
    assert service._next_source(1) == urls["BIOL 350"]
    assert service._routine_batch_context is None
    assert service._routine_batch_count == 0
    assert service.admin_status()["seat_refresh_throughput"]["priority_preemptions_last_60s"] == 1


def test_viewed_class_preempts_on_non_fifth_turn(tmp_path):
    service, urls = _service(tmp_path)
    _simulate_previous_hist_success(service, urls)
    service.register_interest(("1014",))
    assert service._next_source(1) == urls["DANCE 101"]
    assert service._routine_batch_count == 0
    assert service.admin_status()["seat_refresh_throughput"]["priority_preemptions_last_60s"] == 1


def test_priority_cooldown_still_prevents_extra_requests(tmp_path):
    service, urls = _service(tmp_path)
    service.register_interest(("1014",))
    with service._lock:
        service._source_retry_after[urls["DANCE 101"]] = time.time() + 1800
    assert service._next_source(1) == urls["HIST 110"]
    assert service.admin_status()["seat_refresh_throughput"]["priority_preemptions_last_60s"] == 0


def test_spring_four_to_one_and_other_term_rotation_remain(tmp_path):
    service, urls = _service(tmp_path)
    picks = [service._next_source(i) for i in range(15)]
    terms = [service._source_term_names[u][0] for u in picks]
    assert terms.count("Spring 2027") == 12
    assert [t for t in terms if t != "Spring 2027"] == [
        "Fall 2026", "Summer 2027", "Winter 2027",
    ]


def test_new_metrics_stay_admin_only_and_preserve_existing_counts(tmp_path):
    service, _ = _service(tmp_path)
    now = time.time()
    with service._lock:
        service._seat_throughput_events.extend((
            (now - 400, "batch", 0),
            (now - 100, "batch", 0),
            (now - 10, "batch", 0),
            (now - 9, "priority", 0),
            (now - 8, "success", 3),
            (now - 7, "failure", 0),
            (now - 6, "context", 0),
        ))
    metrics = service.admin_status()["seat_refresh_throughput"]
    assert metrics["affinity_reorders_last_60s"] == 1
    assert metrics["affinity_reorders_last_300s"] == 2
    assert metrics["priority_preemptions_last_60s"] == 1
    assert metrics["priority_preemptions_last_300s"] == 1
    assert metrics["successful_course_pages_last_60s"] == 1
    assert metrics["failed_course_pages_last_60s"] == 1
    assert metrics["subject_context_rehydrates_last_60s"] == 1
    assert "seat_refresh_throughput" not in service.public_status()
