"""Offline regressions for Seat Refresh Patch 3: fairness, telemetry, cooldowns.

No SDSU network calls, browser sessions or production datasets are needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService


BASE_URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL'
)


def _service(tmp_path: Path) -> tuple[SeatRefreshService, dict[str, str]]:
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    urls = {
        'Spring 2027': BASE_URL + '&sample=spring1',
        'Fall 2026': BASE_URL + '&sample=fall1',
        'Winter 2027': BASE_URL + '&sample=winter1',
    }
    specifications = (
        ('Spring 2027', '2273', '41001', 'MATH 151'),
        ('Fall 2026', '2267', '42001', 'MATH 152'),
        ('Winter 2027', '2271', '43001', 'MATH 153'),
    )
    sections = []
    for name, term_code, schedule, course_code in specifications:
        sections.append(sample.model_copy(update={
            'id': f'patch3-{term_code}-{schedule}',
            'term': name,
            'term_code': term_code,
            'schedule_number': schedule,
            'course_code': course_code,
            'source_url': urls[name],
            'seat_status': SeatStatus.OPEN,
        }))
    # Cross-listed duplicate: one physical seat, not two in freshness reports.
    sections.append(sections[0].model_copy(update={'id': 'patch3-spring-duplicate'}))
    repo = CourseRepository(tuple(sections))
    service = SeatRefreshService(
        repo,
        cache_path=tmp_path / 'seats.json',
        instructor_cache_path=tmp_path / 'instructors.json',
        preferred_term='Spring 2027',
        request_delay_seconds=0,
    )
    return service, urls


def test_semester_freshness_unique_physical_keys_and_unknown_cache(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    now = datetime.now(timezone.utc)
    with service._lock:
        service._records[('2273', '41001')] = {
            'updated_at': (now - timedelta(minutes=1)).isoformat(),
        }
        service._records[('2267', '42001')] = {
            'updated_at': (now - timedelta(minutes=45)).isoformat(),
        }
        service._records[('2271', '43001')] = {'updated_at': None}
        # An obsolete term is excluded from current-inventory coverage.
        service._records[('2000', '99999')] = {'updated_at': now.isoformat()}
    rows = {row['term']: row for row in service.admin_status()['semester_freshness']}
    assert rows['Spring 2027']['refreshable_sections'] == 1
    assert rows['Spring 2027']['fresh_cached_sections'] == 1
    assert rows['Spring 2027']['fresh_percent'] == 100.0
    assert rows['Fall 2026']['stale_cached_sections'] == 1
    assert rows['Winter 2027']['pending_cached_sections'] == 1
    assert sum(row['refreshable_sections'] for row in rows.values()) == 3


def test_cooldown_skips_automatic_work_but_manual_refresh_bypasses(tmp_path: Path) -> None:
    service, urls = _service(tmp_path)
    spring_url = urls['Spring 2027']
    spring = service._sources[spring_url]
    service._record_source_failure(spring_url, spring, RuntimeError('retry test'), duration_seconds=1.25)
    now = service._source_retry_after[spring_url] - 60.0
    assert 55.0 <= service._source_retry_after[spring_url] - now <= 65.0
    # Every Spring routine turn falls back to the other semester pool.
    results = [service._next_source(i) for i in range(8)]
    assert all(url != spring_url for url in results)
    assert all(url is not None for url in results)
    assert service.request_course_refresh('MATH 151') == 1
    assert service._next_source(9) == spring_url
    # Repeated failures double the cooldown, capped at thirty minutes.
    service._record_source_failure(spring_url, spring, RuntimeError('retry test 2'), duration_seconds=2.0)
    assert service._source_failure_streak[spring_url] == 2
    assert 110 <= service._source_retry_after[spring_url] - __import__('time').time() <= 121
    for _ in range(10):
        service._record_source_failure(spring_url, spring, RuntimeError('retry test'), duration_seconds=0.1)
    assert service._source_retry_after[spring_url] - __import__('time').time() <= 1801
    assert service._source_failure_streak[spring_url] == 12


def test_success_clears_failure_cooldown_and_reports_outcomes(tmp_path: Path, monkeypatch) -> None:
    service, urls = _service(tmp_path)
    url = urls['Spring 2027']
    source = service._sources[url]
    service._record_source_failure(url, source, RuntimeError('temporary'), duration_seconds=1.0)
    monkeypatch.setattr(service, '_parse_expected_source_page', lambda *_args: SimpleNamespace(options=()))
    client = SimpleNamespace(fetch_course_info_page=lambda _url: SimpleNamespace(
        html='<html></html>', canonical_url=url, expansion_count=0,
    ))
    service._refresh_source(client, url)
    assert url not in service._source_retry_after
    assert url not in service._source_failure_streak
    status = service.admin_status()
    spring = next(row for row in status['semester_freshness'] if row['term'] == 'Spring 2027')
    assert spring['attempts_since_start'] == 1
    assert spring['successes_since_start'] == 1
    assert spring['failures_since_start'] == 1
    assert spring['source_pages_successful_since_start'] == 1
    assert status['cooldown_sources'] == 0
    assert status['recent_duration_samples'] >= 2


def test_duration_ring_buffer_and_public_status_stay_lightweight(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with service._lock:
        for i in range(150):
            service._record_duration_locked(float(i))
    status = service.admin_status()
    assert status['recent_duration_samples'] == 100
    assert status['recent_duration_avg_seconds'] == 99.5
    assert status['recent_duration_p95_seconds'] == 144.0
    assert status['slow_refresh_attempts'] == 130
    public = service.public_status()
    assert public['preferred_term'] == 'Spring 2027'
    assert 'semester_freshness' not in public
    assert 'recent_duration_samples' not in public


def test_spring_four_to_one_routine_and_interest_priority_remain(tmp_path: Path) -> None:
    service, urls = _service(tmp_path)
    selected = [service._next_source(i) for i in range(10)]
    assert selected.count(urls['Spring 2027']) == 8
    assert sum(url != urls['Spring 2027'] for url in selected) == 2
    service.register_interest(('43001',))
    assert service._next_source(10) == urls['Winter 2027']


def test_admin_frontend_renders_freshness_without_exposing_it_publicly() -> None:
    root = Path(__file__).resolve().parents[1]
    js = (root / 'src/classcatalog/static/app.js').read_text(encoding='utf-8')
    html = (root / 'src/classcatalog/static/index.html').read_text(encoding='utf-8')
    assert 'seatTermBody' in js
    assert 'seats.semester_freshness' in js
    assert 'id="admin-seat-term-body"' in html
    assert 'app.js?v=76' in html
