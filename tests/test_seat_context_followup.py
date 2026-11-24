"""Offline tests for one bounded same-subject follow-up after a viewed success.

No PeopleSoft requests, filesystem production caches, or running server needed.
"""
from __future__ import annotations

import time
from pathlib import Path

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL'
)
HIST = ('Spring 2027', '2273', 'HIST')
BIOL = ('Spring 2027', '2273', 'BIOL')


def make_service(tmp_path: Path):
    base = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = [
        ('HIST 110', 'HIST', 'Spring 2027', '2273', '11310'),
        ('HIST 111', 'HIST', 'Spring 2027', '2273', '11311'),
        ('HIST 112', 'HIST', 'Spring 2027', '2273', '11312'),
        ('BIOL 350', 'BIOL', 'Spring 2027', '2273', '1871'),
        ('MATH 150', 'MATH', 'Fall 2026', '2267', '6528'),
    ]
    rows = []
    urls = {}
    numbers = {}
    for i, (course, subject, term, code, number) in enumerate(specs):
        url = f'{BASE}&STRM={code}&TEST={i}'
        urls[course] = url
        numbers[course] = number
        rows.append(base.model_copy(update={
            'id': f'followup-fixture-{i}',
            'source_url': url,
            'term': term,
            'term_code': code,
            'subject': subject,
            'course_code': course,
            'catalog_number': course.split()[-1],
            'schedule_number': number,
            'section_number': '01',
            'crse_id': f'{i + 100:06d}',
            'crse_offer_nbr': '1',
            'acad_career': 'UGRD',
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(rows)),
        cache_path=tmp_path / 'seats.json',
        instructor_cache_path=tmp_path / 'instructors.json',
        request_delay_seconds=0,
        preferred_term='Spring 2027',
    )
    return service, urls, numbers


def mark_verified_success(service, url, *, context=HIST):
    # Equivalent to the successful completion of a worker's _refresh_source.
    service._source_last_attempt[url] = time.time()
    service._source_last_success[url] = time.time()
    service._worker_context_affinity = context


def test_verified_viewed_page_gives_one_stale_same_subject_followup(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    mark_verified_success(service, urls['HIST 110'])
    assert service._next_source(1) == urls['HIST 111']
    throughput = service.public_status()['seat_refresh_throughput']
    assert throughput['context_followup_selections_last_60s'] == 1
    assert throughput['context_followup_selections_last_300s'] == 1
    assert throughput['routine_selections_last_60s'] == 1
    # The context follow-up cannot itself trigger another follow-up.
    assert service._next_source(2) == urls['BIOL 350']


def test_not_selected_before_viewed_page_finishes_successfully(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    service._worker_context_affinity = HIST
    # An old cached success does not count as success of this attempt.
    service._source_last_success[urls['HIST 110']] = time.time() - 3600
    service._source_last_attempt[urls['HIST 110']] = time.time()
    assert service._next_source(1) == urls['BIOL 350']
    assert service.public_status()['seat_refresh_throughput']['context_followup_selections_last_60s'] == 0


def test_different_context_skips_followup(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    mark_verified_success(service, urls['HIST 110'], context=BIOL)
    assert service._next_source(1) == urls['BIOL 350']


def test_all_stale_siblings_cooling_or_recently_attempted(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    mark_verified_success(service, urls['HIST 110'])
    service._source_retry_after[urls['HIST 111']] = time.time() + 1800
    service._source_last_attempt[urls['HIST 112']] = time.time()
    assert service._next_source(1) == urls['BIOL 350']


def test_fresh_siblings_not_refetched(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    mark_verified_success(service, urls['HIST 110'])
    service._source_last_success[urls['HIST 111']] = time.time() - 120
    service._source_last_success[urls['HIST 112']] = time.time() - 120
    assert service._next_source(1) == urls['BIOL 350']


def test_manual_request_preempts_followup(tmp_path):
    service, urls, nums = make_service(tmp_path)
    service.register_interest((nums['HIST 110'], nums['BIOL 350']))
    assert service._next_source(0) == urls['HIST 110']
    mark_verified_success(service, urls['HIST 110'])
    service._manual_queue.append(urls['MATH 150'])
    assert service._next_source(1) == urls['MATH 150']
    assert service._next_source(2) == urls['BIOL 350']
    assert service.public_status()['seat_refresh_throughput']['context_followup_selections_last_300s'] == 0


def test_routine_without_viewed_interest_unchanged(tmp_path):
    service, urls, _ = make_service(tmp_path)
    assert service._next_source(0) == urls['HIST 110']
    assert service._next_source(1) == urls['HIST 111']
    assert service.public_status()['seat_refresh_throughput']['context_followup_selections_last_300s'] == 0
