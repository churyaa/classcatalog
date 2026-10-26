"""Offline safety tests for automatic SDSU class removal. Never contacts mySDSU."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _fixture(tmp_path: Path, *, age_hours: int = 72):
    source = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    stamp = (datetime.now(timezone.utc)-timedelta(hours=age_hours)).isoformat()
    first = source.model_copy(update={
        'id': 'auto-spring-test', 'term': 'Spring 2027', 'term_code': '2273',
        'course_code': 'CS 574', 'subject': 'CS', 'catalog_number': '574',
        'schedule_number': '57401', 'seat_updated_at': stamp,
    })
    second = source.model_copy(update={
        'id': 'auto-summer-keep', 'term': 'Summer 2027', 'term_code': '2275',
        'course_code': 'CS 570', 'subject': 'CS', 'catalog_number': '570',
        'schedule_number': '57002', 'seat_updated_at': stamp,
    })
    path = tmp_path/'sections.json'
    path.write_text(json.dumps([s.model_dump(mode='json') for s in (first,second)]), encoding='utf-8')
    return first, second, path, CourseRepository((first, second))


def test_48_hour_seat_threshold(tmp_path: Path):
    old, _, path, repo = _fixture(tmp_path, age_hours=40)
    checks=[]
    service=RemovalReviewService(repo,path,probe=lambda s: checks.append(s) or True)
    assert service.candidates()['stale_threshold_hours']==48
    assert service.candidates()['items']==[]
    assert service.auto_cycle()['checked']==0
    assert not checks


def test_auto_double_verifies_and_hides_only_target_without_dataset_edit(tmp_path: Path):
    old, other, path, repo = _fixture(tmp_path)
    calls=[]
    service=RemovalReviewService(repo,path,probe=lambda s: calls.append(s.schedule_number) or True)
    refreshed=[]
    service.on_deleted=lambda:refreshed.append('yes')
    original = path.read_bytes()
    outcome=service.auto_cycle(max_checks=1)
    assert outcome == {'checked':1, 'hidden':1}
    assert calls == ['57401', '57401'] or calls == ['57002', '57002']
    assert path.read_bytes() == original
    assert len(repo.sections)==1
    assert len(service.candidates()['recent_auto_hides'])==1
    latest=service.candidates()['recent_auto_hides'][0]
    assert (tmp_path/'hidden_classes.json').is_file()
    assert len(json.loads(path.read_text())) == 2
    assert refreshed == ['yes']


def test_sdsu_present_never_auto_deletes(tmp_path: Path):
    old, _, path, repo = _fixture(tmp_path)
    before=path.read_bytes()
    calls=[]
    service=RemovalReviewService(repo,path,probe=lambda s: calls.append(s.schedule_number) and False)
    result=service.auto_cycle(max_checks=1)
    assert result == {'checked':1,'hidden':0}
    assert len(calls)==1
    assert path.read_bytes()==before
    assert not (tmp_path/'hidden_classes.json').exists()


def test_inconclusive_probe_never_auto_deletes(tmp_path: Path):
    old, _, path, repo = _fixture(tmp_path)
    before=path.read_bytes()
    def fail(s): raise RuntimeError('SDSU page was truncated')
    service=RemovalReviewService(repo,path,probe=fail)
    assert service.auto_cycle(max_checks=1)=={'checked':1,'hidden':0}
    assert path.read_bytes()==before
    assert not (tmp_path/'hidden_classes.json').exists()
    assert service.candidates()['auto_last_error'] is None  # probe exceptions are captured as inconclusive state


def test_failed_seat_refresh_under_48h_is_not_auto_deleted(tmp_path: Path):
    old, other, path, repo = _fixture(tmp_path, age_hours=1)
    official='https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/SSR_CLSRCH_FL.SSR_CRSE_INFO_FL.GBL?STRM=2273'
    old=old.model_copy(update={'source_url': official})
    repo=CourseRepository((old,other))
    class Failures:
        def admin_status(self):
            return {'seat_failures':[{'source_url':official,'resolved':False,'detail':'The course-information page contains none of the expected class numbers.'}]}
    calls=[]
    service=RemovalReviewService(repo,path,seat_service=Failures(),probe=lambda s:calls.append(s) or True)
    assert service.candidates()['total_flagged']>=1
    assert service.auto_cycle()['checked']==0
    assert calls==[]
    assert path.exists()


def test_dataset_change_during_verification_aborts(tmp_path: Path):
    old, _, path, repo=_fixture(tmp_path)
    def probe(section):
        calls.append(section.schedule_number)
        if len(calls)==2:
            path.write_text(path.read_text()+'\n',encoding='utf-8')
        return True
    calls=[]
    service=RemovalReviewService(repo,path,probe=probe)
    assert service.auto_cycle(max_checks=1)=={'checked':1,'hidden':0}
    assert len(json.loads(path.read_text()))==2
    assert not (tmp_path/'hidden_classes.json').exists()


def test_auto_rechecks_not_before_a_day(tmp_path: Path):
    old, _, path, repo=_fixture(tmp_path)
    calls=[]
    service=RemovalReviewService(repo,path,probe=lambda s:calls.append(s) or False)
    assert service.auto_cycle(max_checks=1)['checked']==1
    assert service.auto_cycle(max_checks=1)['checked']==1  # next distinct class, not same old key
    assert service.auto_cycle(max_checks=1)['checked']==0
    assert len(calls)==2


def test_sdsu_offering_multiplicity_prevents_false_absence(monkeypatch, tmp_path: Path):
    """Same CRSE_ID with two offering numbers: second offering still has old class."""
    from types import SimpleNamespace
    import classcatalog.removal_review as module
    old, _, path, repo = _fixture(tmp_path)
    old = old.model_copy(update={'crse_id': '999999'})
    h1 = SimpleNamespace(course_code='CS 574', crse_id='999999',
                         crse_offer_nbr='1', acad_career='UGRD', detail_url='https://sdsu.edu/first')
    h2 = SimpleNamespace(course_code='CS 574', crse_id='999999',
                         crse_offer_nbr='2', acad_career='UGRD', detail_url='https://sdsu.edu/second')
    result = SimpleNamespace(complete=True, exact_facet_applied=True,
                             courses=(h1, h2), partitioned=False)
    urls = []
    class Client:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def bootstrap(self): pass
        def scrape_subject(self, **_kwargs): return None, result
        def fetch_detail_pages(self, url):
            urls.append(url)
            return SimpleNamespace(course_info_html=url, course_info_url=url)
    def parse(html, **_kwargs):
        number = '57401' if html.endswith('second') else '99999'
        return SimpleNamespace(course_code='CS 574', options_complete=True,
                               options=(SimpleNamespace(class_number=number),))
    monkeypatch.setattr(module, 'SdsuPeopleSoftSession', Client)
    monkeypatch.setattr(module, 'parse_course_info_page', parse)
    assert module.RemovalReviewService._probe_live(old) is False
    assert urls == ['https://sdsu.edu/first', 'https://sdsu.edu/second']
