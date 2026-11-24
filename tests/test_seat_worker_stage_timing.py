"""Offline seat-worker stage timing telemetry checks; no SDSU requests."""
from __future__ import annotations

import time
from pathlib import Path

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService


def _service(tmp_path: Path) -> SeatRefreshService:
    repository = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    )
    return SeatRefreshService(
        repository,
        cache_path=tmp_path / 'seats.json',
        instructor_cache_path=tmp_path / 'instructors.json',
        request_delay_seconds=0,
    )


def test_stage_timing_empty_is_safe_and_public(tmp_path: Path) -> None:
    service = _service(tmp_path)
    status = service.public_status()['seat_refresh_throughput']
    timing = status['worker_stage_timing_last_300s']
    assert timing == {
        'window_seconds': 300,
        'context_attempts': 0,
        'context_seconds': 0,
        'context_avg_seconds': None,
        'context_max_seconds': None,
        'course_page_attempts': 0,
        'course_page_seconds': 0,
        'course_page_avg_seconds': None,
        'course_page_max_seconds': None,
    }
    assert service.admin_status()['seat_refresh_throughput']['worker_stage_timing_last_300s'] == timing


def test_stage_timing_keeps_only_recent_samples(tmp_path: Path) -> None:
    service = _service(tmp_path)
    now = time.time()
    with service._lock:
        service._seat_stage_durations.extend([
            (now - 400, 'context', 99.0),
            (now - 200, 'context', 4.0),
            (now - 150, 'context', 6.0),
            (now - 100, 'page', 11.0),
            (now - 20, 'page', 3.0),
        ])
    timing = service.public_status()['seat_refresh_throughput']['worker_stage_timing_last_300s']
    assert timing['context_attempts'] == 2
    assert timing['context_seconds'] == 10.0
    assert timing['context_avg_seconds'] == 5.0
    assert timing['context_max_seconds'] == 6.0
    assert timing['course_page_attempts'] == 2
    assert timing['course_page_seconds'] == 14.0
    assert timing['course_page_avg_seconds'] == 7.0
    assert timing['course_page_max_seconds'] == 11.0
    assert len(service._seat_stage_durations) == 4


def test_stage_timing_does_not_change_existing_throughput(tmp_path: Path) -> None:
    service = _service(tmp_path)
    now = time.time()
    with service._lock:
        service._seat_stage_durations.extend([
            (now - 10, 'context', 5.0),
            (now - 9, 'page', 12.0),
        ])
        service._seat_throughput_events.extend([
            (now - 8, 'context', 0),
            (now - 7, 'success', 2),
            (now - 6, 'routine', 0),
        ])
    throughput = service.public_status()['seat_refresh_throughput']
    assert throughput['subject_context_rehydrates_last_60s'] == 1
    assert throughput['successful_course_pages_last_60s'] == 1
    assert throughput['section_updates_last_60s'] == 2
    assert throughput['routine_selections_last_60s'] == 1
