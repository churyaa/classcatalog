"""Offline scheduling regressions for bounded viewed-class bursts.

No SDSU requests and no production cache or dataset mutation.
"""
from __future__ import annotations

import time
from pathlib import Path

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SEAT_MAX_VIEWED_BURST, SeatRefreshService

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def _service(tmp_path: Path):
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = (
        ("HIST 110", "HIST", "Spring 2027", "2273", "11310"),
        ("BIOL 350", "BIOL", "Spring 2027", "2273", "1871"),
        ("HIST 111", "HIST", "Spring 2027", "2273", "11311"),
        ("CHEM 100", "CHEM", "Spring 2027", "2273", "41001"),
        ("MATH 150", "MATH", "Fall 2026", "2267", "6528"),
    )
    rows = []
    urls = {}
    numbers = {}
    for index, (course, subject, term, code, number) in enumerate(specs):
        url = f"{BASE}&STRM={code}&TEST={index}"
        urls[course] = url
        numbers[course] = number
        rows.append(base.model_copy(update={
            "id": f"priority-v2-{index}",
            "source_url": url,
            "term": term,
            "term_code": code,
            "subject": subject,
            "course_code": course,
            "catalog_number": course.split()[-1],
            "schedule_number": number,
            "section_number": "01",
            "crse_id": f"{index + 100:06d}",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(rows)),
        cache_path=tmp_path / "seats.json",
        instructor_cache_path=tmp_path / "instructors.json",
        preferred_term="Spring 2027",
        request_delay_seconds=0,
    )
    return service, urls, numbers


def test_viewed_burst_still_gives_routine_work_a_slot(tmp_path):
    service, urls, numbers = _service(tmp_path)
    viewed = ("HIST 110", "BIOL 350", "HIST 111", "CHEM 100")
    assert service.register_interest(tuple(numbers[name] for name in viewed)) == 4
    assert SEAT_MAX_VIEWED_BURST == 4
    for iteration in range(SEAT_MAX_VIEWED_BURST):
        selected = service._next_source(iteration)
        assert selected in {urls[name] for name in viewed}
        assert service._seat_throughput_events[-1][1] == "priority"
        # The real worker writes this inside _refresh_source before selecting again.
        service._source_last_attempt[selected] = time.time()
    assert service._consecutive_viewed_selections == 4
    routine = service._next_source(4)
    assert routine is not None
    assert service._seat_throughput_events[-1][1] == "routine"
    assert service._consecutive_viewed_selections == 0
    metrics = service.public_status()["seat_refresh_throughput"]
    assert metrics["priority_preemptions_last_300s"] == 4
    assert metrics["routine_selections_last_300s"] == 1


def test_verified_context_can_batch_close_in_age_viewed_courses(tmp_path):
    service, urls, numbers = _service(tmp_path)
    service.register_interest((numbers["BIOL 350"], numbers["HIST 110"]))
    assert service._seat_source_context(service._sources[urls["HIST 110"]]) == (
        "Spring 2027", "2273", "HIST"
    )
    service._worker_context_affinity = ("Spring 2027", "2273", "HIST")
    now = time.time()
    service._source_last_attempt[urls["BIOL 350"]] = now - 1000
    service._source_last_attempt[urls["HIST 110"]] = now - 990
    assert service._next_source(0) == urls["HIST 110"]


def test_very_old_viewed_source_cannot_be_overtaken_by_affinity(tmp_path):
    service, urls, numbers = _service(tmp_path)
    service.register_interest((numbers["BIOL 350"], numbers["HIST 110"]))
    service._worker_context_affinity = ("Spring 2027", "2273", "HIST")
    now = time.time()
    service._source_last_attempt[urls["BIOL 350"]] = now - 4000
    service._source_last_attempt[urls["HIST 110"]] = now - 900
    assert service._next_source(0) == urls["BIOL 350"]


def test_viewed_source_cooldown_still_applies(tmp_path):
    service, urls, numbers = _service(tmp_path)
    service.register_interest((numbers["BIOL 350"], numbers["HIST 110"]))
    service._worker_context_affinity = ("Spring 2027", "2273", "HIST")
    service._source_retry_after[urls["HIST 110"]] = time.time() + 3600
    assert service._next_source(0) == urls["BIOL 350"]


def test_admin_manual_refresh_still_preempts_quota_and_cooldown(tmp_path):
    service, urls, numbers = _service(tmp_path)
    service.register_interest(tuple(numbers.values()))
    service._consecutive_viewed_selections = SEAT_MAX_VIEWED_BURST
    service._source_retry_after[urls["MATH 150"]] = time.time() + 3600
    assert service.request_course_refresh("MATH 150") == 1
    assert service._next_source(4) == urls["MATH 150"]
    assert service._seat_throughput_events[-1][1] == "priority"


def test_routine_continues_when_viewed_classes_are_on_cooldown(tmp_path):
    service, urls, numbers = _service(tmp_path)
    service.register_interest((numbers["HIST 110"],))
    service._source_retry_after[urls["HIST 110"]] = time.time() + 1000
    assert service._next_source(0) is not None
    assert service._seat_throughput_events[-1][1] == "routine"
