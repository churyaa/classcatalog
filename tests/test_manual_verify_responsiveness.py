"""Regression checks for manual SDSU verification performance isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from threading import Event, Lock
import time

import classcatalog.removal_review as review_module
from classcatalog.models import CourseSection
from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _fixture(tmp_path: Path):
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    stale = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    section = base.model_copy(update={
        'id': 'manual-verify-safe-test',
        'term': 'Spring 2027', 'term_code': '2273',
        'schedule_number': '87654', 'seat_updated_at': stale,
    })
    data = tmp_path / 'sections.json'
    data.write_text(json.dumps([section.model_dump(mode='json')]), encoding='utf-8')
    return section, data, CourseRepository((section,))


def test_manual_verify_runs_independently_and_never_modifies_dataset(tmp_path: Path) -> None:
    section, path, repo = _fixture(tmp_path)
    original = path.read_bytes()
    completed = Event()
    gate = Lock()
    calls = []

    def probe(value: CourseSection) -> bool:
        with gate:
            calls.append(value.schedule_number)
            if len(calls) == 2:
                completed.set()
        return True

    svc = RemovalReviewService(repo, path, probe=probe)
    key = _key(section)
    svc.request_verification(key)
    assert completed.wait(timeout=5), 'Verification did not execute two separate checks'
    deadline = time.monotonic() + 5
    while svc._states[key]['status'] == 'checking' and time.monotonic() < deadline:
        time.sleep(0.01)
    assert svc._states[key]['status'] == 'confirmed_missing'
    assert calls == ['87654', '87654']
    assert path.read_bytes() == original
    assert not (tmp_path / 'hidden_classes.json').exists()
    assert len(repo.sections) == 1


def test_manual_verify_downgrades_only_verifier_thread_nice_on_linux(
    tmp_path: Path, monkeypatch,
) -> None:
    section, path, repo = _fixture(tmp_path)
    completed = Event()
    calls = []
    if sys.platform.startswith('linux'):
        monkeypatch.setattr(
            review_module.os, 'setpriority',
            lambda category, tid, value: calls.append((category, tid, value)),
        )
    svc = RemovalReviewService(repo, path, probe=lambda _section: completed.set() or False)
    svc.request_verification(_key(section))
    assert completed.wait(timeout=5)
    if sys.platform.startswith('linux'):
        assert len(calls) == 1
        assert calls[0][0] == review_module.os.PRIO_PROCESS
        assert isinstance(calls[0][1], int) and calls[0][1] > 0
        assert calls[0][2] == 10


def test_verify_endpoint_offloads_validation_from_event_loop() -> None:
    main_source = (Path(__file__).resolve().parents[1] / 'src/classcatalog/main.py').read_text(encoding='utf-8-sig')
    assert 'await run_in_threadpool(removal_review.request_verification, payload.key)' in main_source
