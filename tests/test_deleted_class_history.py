"""Persistent manual deletion and future replacement scanning (offline tests)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from classcatalog.deleted_history import DeletedClassHistory
from classcatalog.models import CourseSection, Meeting, Weekday
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.modification_review import ModificationReviewService

OFFICIAL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_CLSRCH_FL.SSR_CRSE_INFO_FL.GBL?STRM=2273'
)


def _setup(tmp_path: Path):
    template = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    old = template.model_copy(update={
        'id': 'sp27-latam-797-8182', 'term': 'Spring 2027', 'term_code': '2273',
        'subject': 'LATAM', 'course_code': 'LATAM 797', 'catalog_number': '797',
        'crse_id': '042739', 'acad_career': 'GRAD', 'schedule_number': '8182',
        'section_number': '01', 'source_url': OFFICIAL,
        'option_group_ids': (), 'option_primary_group_ids': (),
        'seat_updated_at': (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(),
        'meetings': (Meeting(days=(Weekday.MON,), start_time=time(10), end_time=time(11),
                             location='Old room', instructor='Old professor'),),
        'instructor': 'Old professor', 'location': 'Old room',
    })
    summer = old.model_copy(update={
        'id': 'su27-other-class', 'term': 'Summer 2027', 'term_code': '2275',
        'schedule_number': '9901', 'course_code': 'LATAM 798', 'catalog_number': '798',
    })
    replacement = old.model_copy(update={
        'id': 'sp27-latam-797-5244', 'schedule_number': '5244', 'section_number': '02',
        'instructor': 'New professor', 'location': 'New room', 'professor': None,
        'meetings': (Meeting(days=(Weekday.TUE,), start_time=time(14), end_time=time(15),
                             location='New room', instructor='New professor'),),
    })
    data = tmp_path / 'sections.json'
    data.write_text(json.dumps([s.model_dump(mode='json') for s in (old, summer)]), encoding='utf-8')
    return old, summer, replacement, data, CourseRepository((old, summer))


def _delete_first(old: CourseSection, data: Path, repo: CourseRepository) -> None:
    review = RemovalReviewService(repo, data, probe=lambda _section: True)
    review._verify(_key(old), old)
    result = review.delete_confirmed(_key(old))
    assert result['deleted'] is True


def test_manual_deletion_keeps_durable_identity_and_preserves_other_term(tmp_path: Path) -> None:
    old, summer, _new, data, repo = _setup(tmp_path)
    _delete_first(old, data, repo)
    history = DeletedClassHistory(data)
    archived = history.sources('Spring 2027', 'LATAM 797')
    assert len(archived) == 1
    assert archived[0].model_dump(mode='json') == old.model_dump(mode='json')
    assert history.contains(old)
    assert not history.contains(old.model_copy(update={'crse_id': 'wrong'}))
    assert len(json.loads(data.read_text(encoding='utf-8'))) == 1
    assert repo.sections[0].id == summer.id
    # The record survives a fresh service instance / simulated backend restart.
    assert DeletedClassHistory(data).sources('Spring 2027')[0].schedule_number == '8182'
    payload = json.loads(history.path.read_text(encoding='utf-8'))
    assert payload['deletions'][0]['deleted_at']
    assert Path(payload['deletions'][0]['backup_path']).is_file()


def test_admin_scan_can_add_replacement_after_old_class_deleted(tmp_path: Path, monkeypatch) -> None:
    old, summer, new, data, repo = _setup(tmp_path)
    _delete_first(old, data, repo)
    scan = ModificationReviewService(repo, data,
                                     fetch_grid=lambda _section: (_ for _ in ()).throw(
                                         AssertionError('Obsolete course page should not be fetched')))
    # Same two independently verified live snapshots as the pre-deletion path,
    # mocked to avoid network; the integration chooses the archive source.
    calls = []
    replacement = scan.replacements
    monkeypatch.setattr(replacement, '_fresh_course_snapshot',
                        lambda source: (calls.append(('search', source.schedule_number)) or ({'5244'}, {})))
    replacement.verify_replacement = lambda source, number, expected: (
        calls.append(('verify', number, tuple(sorted(expected)))) or new)
    assert replacement.historical_sources('Spring 2027', 'LATAM 797') == (old,)
    assert 'Spring 2027' in scan.overview()['available_terms']
    scan._scan_worker((old,))
    assert [section.schedule_number for section in repo.sections] == ['9901', '5244']
    assert json.loads(data.read_text(encoding='utf-8'))[-1]['schedule_number'] == '5244'
    assert replacement.history()[0]['status'] == 'added'
    assert 'deleted-class history' in replacement.history()[0]['detail']
    assert calls == [('search', '8182'), ('verify', '5244', ('5244',))]
    assert len(list((data.parent / 'replacement-backups').glob('*.json'))) == 1
    # Rescan must not duplicate the replacement or resurrect 8182.
    scan._scan_worker((old,))
    assert len(repo.sections) == 2
    assert old.schedule_number not in {section.schedule_number for section in repo.sections}
    assert DeletedClassHistory(data).contains(old)
    assert summer.id in {section.id for section in repo.sections}


def test_historical_scan_can_be_queued_without_any_active_spring_class(tmp_path: Path, monkeypatch) -> None:
    old, _summer, _new, data, repo = _setup(tmp_path)
    _delete_first(old, data, repo)
    scan = ModificationReviewService(repo, data, fetch_grid=lambda _: None)
    # Avoid spawning a real thread; request_scan should select archived sources.
    import classcatalog.modification_review as module
    started = []
    class FakeThread:
        def __init__(self, *, target, args, **_kwargs):
            started.append((target, args))
        def start(self):
            return None
    monkeypatch.setattr(module, 'Thread', FakeThread)
    response = scan.request_scan('Spring 2027', 'LATAM 797')
    assert response == {'accepted': True, 'course_pages': 1}
    assert started[0][1] == ((old,),)


def test_missing_or_invalid_history_never_authorizes_arbitrary_replacement(tmp_path: Path, monkeypatch) -> None:
    old, _summer, new, data, repo = _setup(tmp_path)
    _delete_first(old, data, repo)
    history = DeletedClassHistory(data)
    history.path.write_text('{invalid', encoding='utf-8')
    with pytest.raises(ValueError, match='history'):
        history.sources()
    scan = ModificationReviewService(repo, data, fetch_grid=lambda _: None)
    with pytest.raises(ValueError, match='history'):
        scan.request_scan('Spring 2027', 'LATAM 797')


def test_history_write_failure_prevents_removal(tmp_path: Path, monkeypatch) -> None:
    old, _summer, _new, data, repo = _setup(tmp_path)
    original = data.read_bytes()
    review = RemovalReviewService(repo, data, probe=lambda _: True)
    review._verify(_key(old), old)
    monkeypatch.setattr(review.deleted_history, '_write',
                        lambda _: (_ for _ in ()).throw(OSError('read-only filesystem')))
    with pytest.raises(OSError, match='read-only'):
        review.delete_confirmed(_key(old))
    assert data.read_bytes() == original
    assert any(s.id == old.id for s in repo.sections)


def test_failed_dataset_replace_cancels_uncommitted_history(tmp_path: Path, monkeypatch) -> None:
    old, _summer, _new, data, repo = _setup(tmp_path)
    review = RemovalReviewService(repo, data, probe=lambda _: True)
    review._verify(_key(old), old)
    original = data.read_bytes()
    original_write = Path.write_text

    def reject_dataset_temp(path, text, *args, **kwargs):
        if path.parent == data.parent and path.name.startswith('sections.json.') and path.name.endswith('.tmp'):
            raise OSError('simulated disk failure while writing dataset')
        return original_write(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', reject_dataset_temp)
    with pytest.raises(OSError, match='simulated disk failure'):
        review.delete_confirmed(_key(old))
    assert data.read_bytes() == original
    assert any(section.id == old.id for section in repo.sections)
    assert DeletedClassHistory(data).sources() == ()
