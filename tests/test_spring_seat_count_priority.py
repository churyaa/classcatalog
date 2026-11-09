"""Regression checks for available/total seat counts and Spring-first fairness."""
from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService
from classcatalog.seo import seat_count_summary

SOURCE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def _service(tmp_path: Path, *, preferred_term: str | None = 'Spring 2027'):
    example = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    definitions = [
        ('spring-a', 'Spring 2027', '2273', '52001'),
        ('spring-b', 'Spring 2027', '2273', '52002'),
        ('fall', 'Fall 2026', '2267', '52003'),
        ('summer', 'Summer 2027', '2275', '52004'),
    ]
    rows = []
    urls = {}
    for identifier, term, code, number in definitions:
        url = f'{SOURCE}&CLASS_NBR={number}'
        urls[identifier] = url
        rows.append(example.model_copy(update={
            'id': f'priority-fixture-{identifier}',
            'term': term,
            'term_code': code,
            'source_url': url,
            'schedule_number': number,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(rows)),
        preferred_term=preferred_term,
        cache_path=tmp_path / 'seat_cache.json',
        instructor_cache_path=tmp_path / 'instructor_cache.json',
        request_delay_seconds=0,
    )
    return service, urls


def test_available_seats_are_numerator_not_enrolled():
    assert seat_count_summary(SimpleNamespace(
        seats_available=60, seats_enrolled=0, seat_capacity=60,
    )) == '60 / 60'
    assert seat_count_summary(SimpleNamespace(
        seats_available=15, seats_enrolled=45, seat_capacity=60,
    )) == '15 / 60'
    assert seat_count_summary(SimpleNamespace(
        seats_available=0, seats_enrolled=60, seat_capacity=60,
    )) == '0 / 60'


def test_count_fallbacks_when_open_seats_missing():
    assert seat_count_summary(SimpleNamespace(
        seats_available=None, seats_enrolled=15, seat_capacity=60,
    )) == '45 / 60'
    assert seat_count_summary(SimpleNamespace(
        seats_available=None, seats_enrolled=None, seat_capacity=60,
    )) == '60 capacity'
    assert seat_count_summary(SimpleNamespace(
        seats_available=12, seats_enrolled=None, seat_capacity=None,
    )) == '12 available'


def test_frontend_cards_render_open_seats_and_not_enrolled():
    source = (Path(__file__).parents[1] / 'src/classcatalog/static/app.js').read_text(encoding='utf-8')
    start = source.index('function seatSummary(section) {')
    end = source.index('function seatFreshnessDetails(', start)
    seat_function = source[start:end]
    assert '`${statusLabel} ${available}/${capacity}`' in seat_function
    assert '`${statusLabel} ${enrolled}/${capacity}`' not in seat_function
    assert 'available = Math.max(capacity - enrolled, 0)' in seat_function


def test_routine_refresh_starts_with_spring_and_favors_it_four_to_one(tmp_path):
    service, urls = _service(tmp_path)
    assert service.preferred_term == 'Spring 2027'
    assert len(service._preferred_sources) == 2
    assert len(service._other_sources) == 2
    choices = [service._next_source(index) for index in range(20)]
    assert all(url in {urls['spring-a'], urls['spring-b']} for url in choices[:4])
    assert choices[4] in {urls['fall'], urls['summer']}
    assert sum(url in service._preferred_sources for url in choices) == 16
    assert sum(url in service._other_sources for url in choices) == 4
    assert set(choices) == set(urls.values())  # No older term is starved.
    status = service.public_status()
    assert status['preferred_term'] == 'Spring 2027'
    assert status['preferred_source_pages'] == 2
    assert status['other_source_pages'] == 2


def test_manual_refresh_still_overrides_spring_first(tmp_path):
    service, urls = _service(tmp_path)
    # Simulate an older class manually queued from Admin. Manual always wins.
    service._manual_queue.append(urls['fall'])
    assert service._next_source(0) == urls['fall']
    assert service._next_source(1) in service._preferred_sources


def test_recently_viewed_other_term_can_still_be_prioritized(tmp_path):
    service, urls = _service(tmp_path)
    assert service.register_interest(('52003',)) == 1
    # Viewed courses now preempt routine batching at the next request boundary.
    assert service._next_source(0) == urls['fall']
    # The real worker records the attempt inside _refresh_source before its
    # next scheduling decision. Simulate that step so the 10-minute cadence
    # prevents immediately selecting the same viewed class again.
    service._source_last_attempt[urls['fall']] = time.time()
    assert service._next_source(1) in service._preferred_sources
    # When the priority cadence expires, viewed work is eligible again.
    service._source_last_attempt[urls['fall']] -= service.priority_interval_seconds + 1
    assert service._next_source(2) == urls['fall']


def test_env_preferred_term_override_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv('CLASSCATALOG_SEAT_PREFERRED_TERM', 'Summer 2027')
    service, urls = _service(tmp_path, preferred_term=None)
    assert service.preferred_term == 'Summer 2027'
    assert service._next_source(1) == urls['summer']
    monkeypatch.delenv('CLASSCATALOG_SEAT_PREFERRED_TERM')
    service_auto, _ = _service(tmp_path, preferred_term=None)
    assert service_auto.preferred_term == 'Spring 2027'
