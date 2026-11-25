"""Offline coverage for separate saved/viewed priority and public term freshness.

No SDSU network, production dataset, account data or browser processes used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SEAT_MAX_VIEWED_BURST, SeatRefreshService

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def make_service(tmp_path: Path):
    example = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    fixtures = (
        ("HIST 110", "HIST", "Spring 2027", "2273", "11001"),
        ("HIST 111", "HIST", "Spring 2027", "2273", "11002"),
        ("HIST 112", "HIST", "Spring 2027", "2273", "11003"),
        ("CHEM 100", "CHEM", "Spring 2027", "2273", "11004"),
        ("MATH 150", "MATH", "Fall 2026", "2267", "12001"),
    )
    sections = []
    urls = {}
    for index, (course, subject, term, code, number) in enumerate(fixtures):
        url = f"{BASE}&STRM={code}&TEST={index}"
        urls[course] = url
        sections.append(example.model_copy(update={
            "id": f"fresh-arch-{index}",
            "source_url": url,
            "course_code": course,
            "subject": subject,
            "catalog_number": course.split()[-1],
            "term": term,
            "term_code": code,
            "schedule_number": number,
            "section_number": "01",
            "crse_id": f"{index + 100:06d}",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
            "seat_updated_at": None,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(sections)),
        cache_path=tmp_path / "seats.json",
        instructor_cache_path=tmp_path / "instructors.json",
        request_delay_seconds=0,
        preferred_term="Spring 2027",
    )
    return service, urls


def test_saved_sources_are_deduplicated_and_not_viewed(tmp_path):
    service, urls = make_service(tmp_path)
    assert service.register_saved_interest(("11002", "11002", "not-a-class")) == 1
    assert not service._interests
    assert len(service._saved_interests) == 1
    assert service._next_source(0) == urls["HIST 111"]
    metrics = service.public_status()["seat_refresh_throughput"]
    assert metrics["saved_priority_selections_last_60s"] == 1
    assert metrics["saved_priority_selections_last_300s"] == 1
    assert metrics["priority_preemptions_last_300s"] == 0


def test_term_qualified_saved_interest_does_not_prioritize_same_number_in_other_term(tmp_path):
    service, urls = make_service(tmp_path)
    # Model a recycled schedule number in the nonmatching term.
    service._source_by_schedule.setdefault("11001", set()).add(urls["MATH 150"])
    service.register_saved_interest((), section_keys=("Spring 2027::11001",))
    assert list(service._saved_interests) == [urls["HIST 110"]]
    assert service._next_source(0) == urls["HIST 110"]


def test_viewed_and_manual_preempt_saved(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_saved_interest(("11002",))
    service.register_interest(("11004",))
    assert service._next_source(0) == urls["CHEM 100"]
    service._manual_queue.append(urls["MATH 150"])
    assert service._next_source(1) == urls["MATH 150"]
    # Existing viewed class cooldown is independent of saved demand.
    service._source_last_attempt[urls["CHEM 100"]] = time.time()
    assert service._next_source(2) == urls["HIST 111"]


def test_four_saved_priority_selections_are_followed_by_routine(tmp_path):
    service, urls = make_service(tmp_path)
    assert SEAT_MAX_VIEWED_BURST == 4
    service.register_saved_interest(("11001", "11002", "11003", "11004", "12001"))
    priorities = []
    for i in range(SEAT_MAX_VIEWED_BURST):
        selected = service._next_source(i)
        assert selected in urls.values()
        assert service._seat_throughput_events[-1][1] == "saved"
        priorities.append(selected)
        # The real worker stores its attempt before taking the next source.
        service._source_last_attempt[selected] = time.time()
    assert len(set(priorities)) == SEAT_MAX_VIEWED_BURST
    assert service._next_source(4) is not None
    assert service._seat_throughput_events[-1][1] == "routine"
    assert service._consecutive_viewed_selections == 0


def test_recently_verified_saved_source_is_not_refetched(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_saved_interest(("11002",))
    service._source_last_success[urls["HIST 111"]] = time.time() - 30
    selected = service._next_source(0)
    assert selected != urls["HIST 111"]
    assert service._seat_throughput_events[-1][1] == "routine"


def test_cooling_or_expired_saved_source_is_not_preemptive(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_saved_interest(("11002",))
    service._source_retry_after[urls["HIST 111"]] = time.time() + 3600
    service._next_source(0)
    assert service._seat_throughput_events[-1][1] == "routine"
    service._saved_interests[urls["HIST 111"]] = time.time() - 1
    service._next_source(1)
    assert urls["HIST 111"] not in service._saved_interests


def test_saved_context_affinity_is_bounded_by_attempt_age(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_saved_interest(("11001", "11002", "11004"))
    now = time.time()
    service._source_last_attempt[urls["CHEM 100"]] = now - 800
    service._source_last_attempt[urls["HIST 111"]] = now - 790
    service._source_last_attempt[urls["HIST 110"]] = now - 780
    service._worker_context_affinity = ("Spring 2027", "2273", "HIST")
    assert service._next_source(0) == urls["HIST 111"]
    assert service.public_status()["seat_refresh_throughput"]["affinity_reorders_last_60s"] == 1

    other, urls2 = make_service(tmp_path / "different")
    other.register_saved_interest(("11001", "11002", "11004"))
    other._worker_context_affinity = ("Spring 2027", "2273", "HIST")
    other._source_last_attempt[urls2["CHEM 100"]] = now - 2400
    other._source_last_attempt[urls2["HIST 111"]] = now - 800
    other._source_last_attempt[urls2["HIST 110"]] = now - 790
    assert other._next_source(0) == urls2["CHEM 100"]


def test_term_freshness_is_separate_from_fast_status_and_does_not_expose_classes(tmp_path):
    service, _ = make_service(tmp_path)
    now = datetime.now(timezone.utc)
    service._records[("2273", "11001")] = {"updated_at": now.isoformat()}
    service._records[("2273", "11002")] = {
        "updated_at": (now - timedelta(hours=2)).isoformat()
    }
    terms = service.public_term_freshness()
    assert terms["preferred_term"] == "Spring 2027"
    spring = next(row for row in terms["terms"] if row["term"] == "Spring 2027")
    fall = next(row for row in terms["terms"] if row["term"] == "Fall 2026")
    assert spring["refreshable_sections"] == 4
    assert spring["fresh_cached_sections"] == 1
    assert spring["stale_cached_sections"] == 1
    assert spring["uncached_sections"] == 2
    assert spring["fresh_percent"] == 25.0
    assert fall["uncached_sections"] == 1
    assert "source_url" not in str(terms)
    assert "semester_freshness" not in service.public_status()


def test_frontend_saved_interest_and_honest_term_banner_are_wired():
    root = Path(__file__).parents[1]
    js = (root / "src/classcatalog/static/app.js").read_text(encoding="utf-8")
    main = (root / "src/classcatalog/main.py").read_text(encoding="utf-8")
    assert 'function savedSeatSectionKeys()' in js
    assert 'kind: "saved", schedule_numbers: [], section_keys: sectionKeys' in js
    assert 'queueSavedSeatInterest();' in js
    assert 'function loadSeatTermFreshness()' in js
    assert '"/api/seats/freshness"' in js
    assert 'verified in ${threshold} min' in js
    assert 'Seats updated just now' not in js
    assert '@app.get("/api/seats/freshness")' in main
    privacy = (root / "src/classcatalog/static/privacy.html").read_text(encoding="utf-8")
    assert "While the ClassCatalog tab is open and active" in privacy
