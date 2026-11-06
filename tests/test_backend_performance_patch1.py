"""Regression checks for optimized seat update, status cache and API behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import DEFAULT_STATUS_COUNTS_CACHE_SECONDS, SeatRefreshService


def _sample():
    return CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]


def _indexed_repository() -> CourseRepository:
    base = _sample()
    primary = base.model_copy(update={
        "id": "perf1-first",
        "term": "Spring 2027",
        "term_code": "2273",
        "schedule_number": "44001",
        "seat_status": SeatStatus.OPEN,
        "seats_available": 2,
    })
    crosslist = primary.model_copy(update={"id": "perf1-crosslist"})
    other_term = primary.model_copy(update={
        "id": "perf1-other-term",
        "term": "Fall 2026",
        "term_code": "2267",
        "seat_status": SeatStatus.CLOSED,
    })
    another_course = primary.model_copy(update={
        "id": "perf1-other-course",
        "schedule_number": "44002",
    })
    return CourseRepository((primary, crosslist, other_term, another_course))


def test_indexed_seat_update_preserves_crosslistings_and_other_terms():
    repository = _indexed_repository()
    changed = repository.apply_seat_updates({
        ("2273", "44001"): {
            "seat_status": SeatStatus.WAITLIST,
            "seats_available": 0,
            "seats_enrolled": 40,
        },
    })
    assert changed == 1  # one physical class, two listings
    updated = {row.id: row for row in repository.sections}
    for listing in ("perf1-first", "perf1-crosslist"):
        assert updated[listing].seat_status is SeatStatus.WAITLIST
        assert updated[listing].seats_available == 0
        assert updated[listing].seats_enrolled == 40
    assert updated["perf1-other-term"].seat_status is SeatStatus.CLOSED
    assert updated["perf1-other-course"].seat_status is SeatStatus.OPEN
    assert SeatStatus.WAITLIST in repository.options().seat_statuses
    assert SeatStatus.CLOSED in repository.options().seat_statuses
    assert SeatStatus.OPEN in repository.options().seat_statuses
    assert repository.apply_seat_updates({("2273", "44001"): {
        "seat_status": SeatStatus.WAITLIST,
        "seats_available": 0,
        "seats_enrolled": 40,
    }}) == 0  # no-op is cheap
    stats = repository.seat_update_performance()
    assert stats["calls"] == 2
    assert stats["last_ms"] >= 0
    assert stats["max_ms"] >= stats["last_ms"]


def test_indexed_seat_update_can_reopen_waitlisted_class():
    repository = _indexed_repository()
    repository.apply_seat_updates({("2273", "44001"): {
        "seat_status": SeatStatus.WAITLIST,
        "seats_available": 0,
    }})
    repository.apply_seat_updates({("2273", "44001"): {
        "seat_status": SeatStatus.OPEN,
        "seats_available": 7,
    }})
    for row in repository.sections[:2]:
        assert row.seat_status is SeatStatus.OPEN
        assert row.seats_available == 7
    assert SeatStatus.WAITLIST not in repository.options().seat_statuses


def test_targeted_seat_snapshot_uses_schedule_index_and_retains_term_identity():
    repository = _indexed_repository()
    snapshot = repository.seat_snapshot(("44001", "44001"))
    assert len(snapshot) == 2
    assert set(snapshot) == {("2273", "44001"), ("2267", "44001")}
    assert len(repository.seat_snapshot()) == 3
    assert repository.seat_snapshot(("not-present",)) == {}


def test_inventory_replacement_rebuilds_physical_index():
    repository = _indexed_repository()
    replacement = repository.sections[0].model_copy(update={
        "id": "replacement",
        "schedule_number": "55009",
    })
    repository.replace_inventory((replacement,))
    assert repository.apply_seat_updates({
        ("2273", "44001"): {"seat_status": SeatStatus.CLOSED},
    }) == 0
    assert repository.apply_seat_updates({
        ("2273", "55009"): {"seat_status": SeatStatus.CLOSED},
    }) == 1
    assert repository.sections[0].seat_status is SeatStatus.CLOSED
    assert repository.seat_snapshot(("44001",)) == {}
    assert len(repository.seat_snapshot(("55009",))) == 1


def test_seat_status_reuses_cached_counts_but_keeps_live_age(monkeypatch, tmp_path: Path):
    repository = _indexed_repository()
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        request_delay_seconds=0,
    )
    current = datetime.now(timezone.utc)
    with service._lock:
        service._records = {
            ("2273", "44001"): {"updated_at": current.isoformat()},
            ("2273", "44002"): {"updated_at": (current - timedelta(days=2)).isoformat()},
            ("2267", "44001"): {"updated_at": None},
        }
        service._last_success_at = current.timestamp()

    from classcatalog import seats as seat_module
    real_parse = seat_module._parse_iso_timestamp
    calls = []

    def counted_parse(value):
        calls.append(value)
        return real_parse(value)

    monkeypatch.setattr(seat_module, "_parse_iso_timestamp", counted_parse)
    first = service.public_status()
    second = service.public_status()
    assert first["cached_sections"] == 3
    assert (first["fresh_cached_sections"], first["stale_cached_sections"], first["pending_cached_sections"]) == (1, 1, 1)
    assert second["cached_sections"] == 3
    assert len(calls) == 3  # recount skipped on cache hit

    with service._lock:
        service._freshness_built_at -= DEFAULT_STATUS_COUNTS_CACHE_SECONDS + 1
    service.public_status()
    assert len(calls) == 6

    # Growing the record set must invalidate even within the TTL, keeping sums exact.
    with service._lock:
        service._records[("2273", "44003")] = {"updated_at": current.isoformat()}
    latest = service.public_status()
    assert latest["cached_sections"] == 4
    assert (latest["fresh_cached_sections"] + latest["stale_cached_sections"]
            + latest["pending_cached_sections"]) == 4
    assert len(calls) == 10
    metrics = service.admin_status()
    assert metrics["seat_status_cache_hits"] >= 1
    assert metrics["seat_status_cache_misses"] >= 3
    assert metrics["seat_status_last_recount_ms"] >= 0
    assert metrics["seat_update_performance"]["calls"] >= 0


def test_health_and_seat_status_retains_public_api(tmp_path: Path):
    repository = _indexed_repository()
    service = SeatRefreshService(
        repository,
        cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        request_delay_seconds=0,
    )
    app = create_app(
        repository,
        data_path=SAMPLE_DATA_PATH,
        seat_refresh_service=service,
        seat_refresh_enabled=True,
    )
    # No TestClient context: do not actually start network worker against SDSU.
    client = TestClient(app)
    health = client.get("/api/health")
    status = client.get("/api/seats/status")
    assert health.status_code == 200
    assert status.status_code == 200
    assert health.json()["seat_refresh"]["cached_sections"] == status.json()["cached_sections"]
    assert health.json()["terms"] >= 1
    assert status.headers["cache-control"] == "no-store"
