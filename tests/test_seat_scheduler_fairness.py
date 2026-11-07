"""Offline regression tests for fair semester seat refresh scheduling.

No SDSU network calls and no production data access.
"""
from __future__ import annotations

from pathlib import Path

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService


BASE_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def _service(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    specs = (
        ("Spring 2027", "2273", 3),
        ("Fall 2026", "2267", 9),
        ("Winter 2027", "2271", 2),
        ("Summer 2027", "2275", 2),
    )
    sections = []
    by_term = {}
    for name, code, count in specs:
        by_term[name] = []
        for index in range(count):
            url = f"{BASE_URL}&test={code}-{index}"
            by_term[name].append(url)
            sections.append(sample.model_copy(update={
                "id": f"fair-{code}-{index}",
                "term": name,
                "term_code": code,
                "schedule_number": str(int(code) * 100 + index),
                "source_url": url,
                "course_code": "MATH 151",
                "subject": "MATH",
                "catalog_number": "151",
            }))
    repo = CourseRepository(tuple(sections))
    service = SeatRefreshService(
        repo,
        cache_path=tmp_path / "seat-cache.json",
        instructor_cache_path=tmp_path / "instructor-cache.json",
        preferred_term="Spring 2027",
        request_delay_seconds=0,
    )
    return service, by_term


def test_nonpreferred_turns_rotate_fall_summer_winter_despite_unequal_page_counts(tmp_path):
    service, by_term = _service(tmp_path)
    other_turns = [
        service._next_source(iteration)
        for iteration in range(45)
    ]
    names = {
        url: name for name, urls in by_term.items() for url in urls
    }
    nonpreferred = [names[url] for url in other_turns if names[url] != "Spring 2027"]
    assert nonpreferred == ["Fall 2026", "Summer 2027", "Winter 2027"] * 3
    assert sum(names[url] == "Spring 2027" for url in other_turns) == 36


def test_least_recently_refreshed_sources_come_before_recent_sources(tmp_path):
    service, by_term = _service(tmp_path)
    fall = by_term["Fall 2026"]
    with service._lock:
        for url in fall:
            service._source_last_success[url] = 100.0
        service._source_last_success[fall[2]] = 10.0
        service._source_last_success.pop(fall[5])
        assert service._next_ready_routine(tuple(fall), "_other_cursor", 200.0) == fall[5]
        service._source_last_success[fall[5]] = 200.0
        assert service._next_ready_routine(tuple(fall), "_other_cursor", 200.0) == fall[2]


def test_cooldown_skips_only_affected_term_without_starving_others(tmp_path):
    service, by_term = _service(tmp_path)
    with service._lock:
        for url in by_term["Summer 2027"]:
            service._source_retry_after[url] = float("inf")
    names = {
        url: name for name, urls in by_term.items() for url in urls
    }
    chosen = [service._next_source(i) for i in range(30)]
    other_terms = [names[url] for url in chosen if names[url] != "Spring 2027"]
    assert "Summer 2027" not in other_terms
    assert set(other_terms) == {"Fall 2026", "Winter 2027"}
    with service._lock:
        service._source_retry_after.clear()
    later = [service._next_source(i + 30) for i in range(20)]
    assert "Summer 2027" in [names[url] for url in later]


def test_manual_and_currently_viewed_classes_remain_higher_priority(tmp_path):
    service, by_term = _service(tmp_path)
    winter_url = by_term["Winter 2027"][0]
    winter_number = service._sources[winter_url].schedule_to_term
    number = next(iter(winter_number))
    service.register_interest((number,))
    assert service._next_source(0) == winter_url
    assert service.request_course_refresh("MATH 151") == 16
    # The manually queued page wins over routine work, even on cooldown.
    with service._lock:
        service._source_retry_after[winter_url] = float("inf")
        service._manual_queue.clear()
        service._manual_queue.append(winter_url)
    assert service._next_source(1) == winter_url
