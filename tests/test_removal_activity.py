"""Regression checks for live automatic-hiding status and manual hide feedback."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event, Thread

from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def fixture(tmp_path: Path):
    sample = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    stamp = (datetime.now(timezone.utc)-timedelta(hours=76)).isoformat()
    target = sample.model_copy(update={
        'id': 'activity-target', 'term': 'Spring 2027', 'term_code': '2273',
        'course_code': 'LATAM 797', 'subject': 'LATAM', 'catalog_number': '797',
        'schedule_number': '8182', 'seat_updated_at': stamp,
    })
    other = sample.model_copy(update={
        'id': 'activity-other', 'term': 'Summer 2027', 'term_code': '2275',
        'course_code': 'CS 100', 'subject': 'CS', 'catalog_number': '100',
        'schedule_number': '9001', 'seat_updated_at': datetime.now(timezone.utc).isoformat(),
    })
    path=tmp_path/'sections.json'
    path.write_text(json.dumps([s.model_dump(mode='json') for s in (target,other)]),encoding='utf-8')
    return target,path,CourseRepository((target,other))


def test_active_check_reports_class_and_clears_on_completion(tmp_path):
    target,path,repo=fixture(tmp_path)
    before=path.read_bytes()
    working=Event();resume=Event();done=[]
    def probe(section):
        working.set()
        assert resume.wait(6), 'Blocked live probe did not resume'
        return True
    service=RemovalReviewService(repo,path,probe=probe)
    worker=Thread(target=lambda: done.append(service.auto_cycle(max_checks=1)),daemon=True)
    worker.start()
    try:
        assert working.wait(6), 'Automatic check did not start'
        current=service.candidates()['auto_current']
        assert current['term']=='Spring 2027'
        assert current['course_code']=='LATAM 797'
        assert current['class_number']=='8182'
        assert current['phase']=='verifying'
        assert datetime.fromisoformat(current['started_at'])
    finally:
        resume.set();worker.join(timeout=8)
    assert not worker.is_alive()
    assert done==[{'checked':1,'hidden':1}]
    snapshot=service.candidates()
    assert snapshot['auto_current'] is None
    assert snapshot['hidden_items'][0]['class_number']=='8182'
    assert datetime.fromisoformat(snapshot['hidden_items'][0]['hidden_at'])
    assert path.read_bytes()==before


def test_manual_hide_returns_timestamp_and_hidden_record(tmp_path):
    target,path,repo=fixture(tmp_path)
    before=path.read_bytes()
    service=RemovalReviewService(repo,path,probe=lambda _: True)
    service._verify(_key(target),target)
    response=service.hide_confirmed(_key(target))
    assert response['hidden'] is True
    assert response['class_number']=='8182'
    assert datetime.fromisoformat(response['hidden_at'])
    assert response['hidden_at']==service.hidden_items()[0]['hidden_at']
    assert path.read_bytes()==before


def test_dashboard_has_separate_auto_activity_and_persistent_manual_confirmation():
    base=Path(__file__).resolve().parents[1]/'src/classcatalog/static'
    html=(base/'index.html').read_text(encoding='utf-8')
    js=(base/'app.js').read_text(encoding='utf-8')
    assert 'id="admin-removal-activity"' in html
    assert 'id="admin-removal-recent"' in html
    assert 'id="admin-removal-hidden-list"' in html
    assert 'const current = data.auto_current' in js
    assert 'current.phase === "hiding"' in js
    assert 'adminDate(item.hidden_at)' in js
    assert 'successfully HIDDEN' in js
    assert 'successfully UNHIDDEN' in js
    assert 'removalMutationVersion' in js
    assert 'recentLabel.textContent' in js
