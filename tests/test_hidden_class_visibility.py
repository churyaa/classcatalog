"""Offline tests: verified missing classes are hidden, never deleted from sections.json."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def fixture(tmp_path: Path):
    source = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    old_stamp = (datetime.now(timezone.utc)-timedelta(hours=78)).isoformat()
    first = source.model_copy(update={
        'id':'hide-sp-test','term':'Spring 2027','term_code':'2273',
        'course_code':'LATAM 797','subject':'LATAM','catalog_number':'797',
        'schedule_number':'8182','seat_updated_at':old_stamp,
    })
    second = source.model_copy(update={
        'id':'hide-summer-safe','term':'Summer 2027','term_code':'2275',
        'course_code':'CS 570','subject':'CS','catalog_number':'570',
        'schedule_number':'57001','seat_updated_at':old_stamp,
    })
    data=tmp_path/'sections.json'
    data.write_text(json.dumps([s.model_dump(mode='json') for s in (first,second)]),encoding='utf-8')
    return first,second,data,CourseRepository((first,second))


def test_hide_and_unhide_preserve_original_bytes(tmp_path):
    first,second,path,repo=fixture(tmp_path)
    before=path.read_bytes()
    service=RemovalReviewService(repo,path,probe=lambda _:True)
    service._verify(_key(first),first)
    result=service.hide_confirmed(_key(first))
    assert result['hidden'] is True
    assert path.read_bytes()==before
    assert len(repo.sections)==1 and repo.sections[0].id == second.id
    hidden=service.candidates()['hidden_items']
    assert len(hidden)==1 and hidden[0]['class_number']=='8182'
    assert json.loads((tmp_path/'hidden_classes.json').read_text())['version']==1
    assert service.unhide(_key(first))['unhidden'] is True
    assert len(repo.sections)==2
    assert path.read_bytes()==before
    assert service.candidates()['hidden_items']==[]


def test_startup_reapplies_overlay_without_changing_dataset(tmp_path):
    first,second,path,repo=fixture(tmp_path)
    original=path.read_bytes()
    service=RemovalReviewService(repo,path,probe=lambda _:True)
    service._verify(_key(first),first)
    service.hide_confirmed(_key(first))
    reloaded=CourseRepository.from_json(path,catalog_path=None,ratings_path=None)
    service2=RemovalReviewService(reloaded,path,probe=lambda _:True)
    assert reloaded.physical_section_total == 1
    assert service2.candidates()['total_flagged']==1
    assert len(service2.hidden_items())==1
    assert path.read_bytes()==original
    service2.unhide(_key(first))
    assert reloaded.physical_section_total == 2


def test_replacement_dataset_requires_restart(tmp_path):
    first,second,path,repo=fixture(tmp_path)
    service=RemovalReviewService(repo,path,probe=lambda _:True)
    service._verify(_key(first),first)
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='restart'):
        service.hide_confirmed(_key(first))
    assert len(repo.sections)==2
    assert not (tmp_path/'hidden_classes.json').exists()


def test_non_missing_and_recent_cannot_be_hidden(tmp_path):
    first,second,path,repo=fixture(tmp_path)
    service=RemovalReviewService(repo,path,probe=lambda _:False)
    service._verify(_key(first),first)
    with pytest.raises(ValueError,match='confirmation'):
        service.hide_confirmed(_key(first))
    fresh=first.model_copy(update={'seat_updated_at':datetime.now(timezone.utc).isoformat()})
    live=CourseRepository((fresh,second))
    later=RemovalReviewService(live,path,probe=lambda _:True)
    later._verify(_key(fresh),fresh)
    with pytest.raises(ValueError,match='Recent seat'):
        later.hide_confirmed(_key(fresh))
    assert not (tmp_path/'hidden_classes.json').exists()


def test_admin_can_only_unhide_after_auth(tmp_path):
    first,_,path,repo=fixture(tmp_path)
    service=RemovalReviewService(repo,path,probe=lambda _:True)
    service._verify(_key(first),first)
    service.hide_confirmed(_key(first))
    reloaded = CourseRepository.from_json(path, catalog_path=None, ratings_path=None)
    client=TestClient(create_app(reloaded, data_path=path,admin_password='secret',seat_refresh_enabled=False))
    assert client.post('/api/admin/removals/unhide',json={'key':_key(first)}).status_code==401
    assert client.post('/api/admin/removals/delete',json={'key':_key(first)}).status_code==404
    assert client.post('/api/admin/login',json={'password':'secret'}).status_code==200
    assert client.get('/api/admin/removals').json()['hidden_items'][0]['key']==_key(first)
    assert client.get('/api/hidden-classes').json()['hidden'][0]['key']==_key(first)
    assert client.post('/api/admin/removals/unhide',json={'key':_key(first)}).status_code==200
    assert path.exists()
    assert len(reloaded.sections)==2
    assert client.get('/api/hidden-classes').json()['hidden'] == []


def test_hidden_state_is_scoped_to_class_identity(tmp_path):
    first,second,path,repo=fixture(tmp_path)
    service=RemovalReviewService(repo,path,probe=lambda _:True)
    service._verify(_key(first),first)
    service.hide_confirmed(_key(first))
    new=first.model_copy(update={'id':'reused-number-different-course', 'course_code':'LATAM 798'})
    changed=CourseRepository((new,second))
    restored=RemovalReviewService(changed,path,probe=lambda _:True)
    assert any(s.id==new.id for s in changed.sections)
    assert restored.hidden_items()[0]['applied'] is False


def test_favorites_visibility_overlay_is_wired_into_browser():
    js=(Path(__file__).resolve().parents[1]/'src/classcatalog/static/app.js').read_text(encoding='utf-8')
    assert 'refreshHiddenFavoriteVisibility()' in js
    assert '/api/hidden-classes' in js
    assert 'hiddenFavoriteKeys.has' in js
    assert '!isHiddenFavoriteSection(section)' in js
