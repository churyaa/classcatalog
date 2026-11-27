"""Offline regressions: avoid repeat work and reuse a proven subject context.

All SDSU network operations use fake CA modal responses; no production data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import time

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE_URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL'
)
HIST_CONTEXT = ('Spring 2027', '2273', 'HIST')


def make_service(tmp_path: Path):
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = [
        ('HIST 110', 'HIST', 'Spring 2027', '2273', '11310'),
        ('HIST 111', 'HIST', 'Spring 2027', '2273', '11311'),
        ('BIOL 350', 'BIOL', 'Spring 2027', '2273', '1871'),
        ('MATH 150', 'MATH', 'Fall 2026', '2267', '6528'),
    ]
    rows, urls = [], {}
    for index, (course, subject, term, term_code, number) in enumerate(specs):
        url = f'{BASE_URL}&STRM={term_code}&TEST={index}'
        urls[course] = url
        rows.append(base.model_copy(update={
            'id': f'priority-efficiency-{index}',
            'source_url': url, 'term': term, 'term_code': term_code,
            'course_code': course, 'subject': subject,
            'catalog_number': course.split()[-1],
            'schedule_number': number, 'section_number': '01',
            'crse_id': f'{index + 100:06d}', 'crse_offer_nbr': '1',
            'acad_career': 'UGRD', 'seat_updated_at': None,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(rows)),
        cache_path=tmp_path / 'seat-cache.json',
        instructor_cache_path=tmp_path / 'instructor-cache.json',
        request_delay_seconds=0, preferred_term='Spring 2027',
    )
    return service, urls


def timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def test_verified_viewed_class_is_not_reprioritized_while_fresh(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_interest(('11310', '1871'))
    service._records[('2273', '11310')] = {'updated_at': timestamp(time.time())}
    assert not service._has_stale_viewed_class_locked(urls['HIST 110'], time.time())
    assert service._next_source(0) == urls['BIOL 350']
    assert service._seat_throughput_events[-1][1] == 'priority'
    assert urls['HIST 110'] not in service._source_last_viewed_verified


def test_unverified_and_expired_viewed_class_still_get_priority(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_interest(('11310', '1871'))
    service._records[('2273', '11310')] = {
        'updated_at': timestamp(time.time() - service.stale_after_seconds - 10),
    }
    assert service._has_stale_viewed_class_locked(urls['HIST 110'], time.time())
    assert service._next_source(0) == urls['HIST 110']
    assert service._seat_throughput_events[-1][1] == 'priority'


def test_one_fresh_component_cannot_hide_a_stale_linked_component(tmp_path):
    service, urls = make_service(tmp_path)
    url = urls['HIST 110']
    now = time.time()
    service.register_interest(('11310',))
    # Simulates a second linked physical class from the same PeopleSoft source.
    service._sources[url].schedule_to_term['11313'] = '2273'
    service._viewed_class_expirations[(url, '11313')] = now + 900
    service._records[('2273', '11310')] = {'updated_at': timestamp(now)}
    assert service._has_stale_viewed_class_locked(url, now)
    service._records[('2273', '11313')] = {'updated_at': timestamp(now)}
    assert not service._has_stale_viewed_class_locked(url, now)


def test_class_modal_cooldown_prevents_wasteful_priority_sweep(tmp_path):
    service, urls = make_service(tmp_path)
    url = urls['HIST 110']
    service.register_interest(('11310', '1871'))
    service._linked_modal_retry_after[(url, '11310')] = time.time() + 500
    assert not service._has_stale_viewed_class_locked(url, time.time())
    assert service._next_source(0) == urls['BIOL 350']


def fake_verified_modal(monkeypatch, *, wrong_course=False):
    monkeypatch.setattr(
        'classcatalog.seats.parse_class_information_page',
        lambda number: SimpleNamespace(
            class_number=number, selected_tab_value='CA',
            course_label=('MATH 110 Wrong course' if wrong_course else 'HIST 110 History'),
            status=SeatStatus.OPEN,
            seats_available=3, seat_capacity=30, seats_enrolled=27,
            waitlist_available=None, waitlist_capacity=None, waitlist_total=None,
        ),
    )


class FakeClient:
    def __init__(self):
        self.calls = []

    def fetch_class_detail_tab_page(self, url, number, tab):
        assert tab == 'CA'
        self.calls.append((url, number))
        return SimpleNamespace(html=number)


def test_fast_verified_view_unlocks_exactly_one_same_subject_followup(tmp_path, monkeypatch):
    service, urls = make_service(tmp_path)
    service.register_interest(('11310', '1871'))
    assert service._next_source(0) == urls['HIST 110']
    service._worker_context_affinity = HIST_CONTEXT
    fake_verified_modal(monkeypatch)
    client = FakeClient()
    assert service._refresh_viewed_class_fast(client, urls['HIST 110']) == 'verified'
    assert client.calls == [(urls['HIST 110'], '11310')]
    assert urls['HIST 110'] not in service._source_last_success
    assert service._source_last_viewed_verified[urls['HIST 110']] > 0
    assert service._next_source(1) == urls['HIST 111']
    metrics = service.public_status()['seat_refresh_throughput']
    assert metrics['context_followup_selections_last_300s'] == 1
    # The routine sibling is not automatically a second full-course success.
    # Another explicitly viewed subject must not be starved by an endless chain.
    assert service._next_source(2) == urls['BIOL 350']
    assert metrics['context_followup_selections_last_300s'] == 1


def test_wrong_course_modal_cannot_unlock_followup(tmp_path, monkeypatch):
    service, urls = make_service(tmp_path)
    service.register_interest(('11310', '1871'))
    assert service._next_source(0) == urls['HIST 110']
    service._worker_context_affinity = HIST_CONTEXT
    fake_verified_modal(monkeypatch, wrong_course=True)
    assert service._refresh_viewed_class_fast(FakeClient(), urls['HIST 110']) == 'unverified'
    assert urls['HIST 110'] not in service._source_last_success
    assert urls['HIST 110'] not in service._source_last_viewed_verified
    assert service._next_source(1) == urls['BIOL 350']


def test_admin_manual_priority_remains_absolute(tmp_path):
    service, urls = make_service(tmp_path)
    service.register_interest(('11310',))
    service._records[('2273', '11310')] = {'updated_at': timestamp(time.time())}
    assert service.request_course_refresh('HIST 110') == 1
    assert service._next_source(0) == urls['HIST 110']
    assert service._next_selected_was_manual is True
