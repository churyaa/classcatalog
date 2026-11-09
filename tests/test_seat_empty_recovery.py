"""Offline fresh-detail recovery regressions for stale empty PeopleSoft course pages.

No calls to SDSU and no production dataset writes. A real HIST 110 incident
inspired the fixture: the old course page had no rows, while fresh discovery
showed exactly the seven saved class numbers.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

NUMBERS = ('11310', '11311', '11313', '11890', '11891', '5243', '5244')
URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM=2273'
)
NEW_URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&'
    'STRM=2273&CLASS_NBR=11310'
)
DETAIL_URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?CRSE_ID=037075&CRSE_OFFER_NBR=1'
)
EMPTY_HTML = '<div id="SSR_CLSRCH_F_WK_SSR_MSG_TEXT">No scheduled classes</div>'
RECOVERED_HTML = '<html><body>FRESH_COURSE_INFO</body></html>'


def _service(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    sections = tuple(
        sample.model_copy(update={
            'id': f'hist110-{number}',
            'course_code': 'HIST 110', 'subject': 'HIST', 'catalog_number': '110',
            'term': 'Spring 2027', 'term_code': '2273',
            'schedule_number': number, 'section_number': f'{index:02d}',
            'source_url': URL,
            'crse_id': '037075', 'crse_offer_nbr': '1', 'acad_career': 'UGRD',
            'seat_status': SeatStatus.OPEN, 'seat_updated_at': None,
            'seats_available': 10, 'seat_capacity': 20,
        })
        for index, number in enumerate(NUMBERS, 1)
    )
    service = SeatRefreshService(
        CourseRepository(sections),
        cache_path=tmp_path / 'seats.json',
        instructor_cache_path=tmp_path / 'instructors.json',
        preferred_term='Spring 2027', request_delay_seconds=0,
    )
    return service


def _parsed(*, numbers=(), course='HIST 110', term='Spring 2027',
            term_code='2273', complete=True):
    return SimpleNamespace(
        course_code=course, term=term, term_code=term_code,
        options_complete=complete,
        options=tuple(
            SimpleNamespace(
                class_number=number, instructor='Jane Doe',
                status=SeatStatus.OPEN, open_seats=5,
                seat_capacity=20, seats_enrolled=15,
            ) for number in numbers
        ),
    )


class FakeClient:
    def __init__(self, *, hits=None, search_complete=True):
        if hits is None:
            hits = [self.hit()]
        self.hits = hits
        self.search_complete = search_complete
        self.direct_fetches = 0
        self.searches = 0
        self.details = 0
        self.last_detail_url = None

    @staticmethod
    def hit(**changes):
        attributes = dict(
            course_code='HIST 110', term='Spring 2027', term_code='2273',
            subject='HIST', crse_id='037075', crse_offer_nbr='1',
            acad_career='UGRD', detail_url=DETAIL_URL, section_count=7,
        )
        attributes.update(changes)
        return SimpleNamespace(**attributes)

    def fetch_course_info_page(self, url):
        self.direct_fetches += 1
        return SimpleNamespace(html=EMPTY_HTML, canonical_url=url, expansion_count=0)

    def rehydrate_subject_context(self, *, term, term_code, subject):
        self.searches += 1
        assert (term, term_code, subject) == ('Spring 2027', '2273', 'HIST')
        return object()

    def parse_subject_pages(self, _pages):
        return SimpleNamespace(
            complete=self.search_complete, term='Spring 2027',
            term_code='2273', subject='HIST', courses=self.hits,
        )

    def fetch_detail_pages(self, url):
        self.details += 1
        self.last_detail_url = url
        return SimpleNamespace(
            course_info_html=RECOVERED_HTML,
            course_info_url=NEW_URL,
            course_info_expansion_count=0,
        )


def _mock_parser(monkeypatch, *, recovered=None):
    if recovered is None:
        recovered = _parsed(numbers=NUMBERS)
    monkeypatch.setattr(
        'classcatalog.seats.parse_course_info_page',
        lambda html, **_kw: recovered if html == RECOVERED_HTML else _parsed(),
    )
    monkeypatch.setattr('classcatalog.seats.find_class_number_actions', lambda _html: ())


def test_hist_110_fresh_detail_recovers_all_seven_sections(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_parser(monkeypatch)
    client = FakeClient()
    # Prior version persisted incorrect disappearance evidence; recovery clears it.
    service._missing_numbers_by_source[URL] = set(NUMBERS)

    service._refresh_source(client, URL)

    assert (client.direct_fetches, client.searches, client.details) == (1, 1, 1)
    assert client.last_detail_url == DETAIL_URL
    assert service._resolved_url_by_source[URL] == NEW_URL
    assert URL in service._source_last_success
    assert service.missing_section_keys() == set()
    assert service.unlisted_source_urls() == set()
    assert URL not in service._source_retry_after
    assert service.admin_status()['empty_grid_recovery_successes_since_start'] == 1
    snapshot = service.repository.seat_snapshot(NUMBERS)
    for number in NUMBERS:
        section = snapshot[('2273', number)]
        assert section.seats_available == 5
        assert section.seat_updated_at is not None
    service._flush_cache(force=True)
    cached = json.loads((tmp_path / 'seats.json').read_text(encoding='utf-8'))
    assert cached['sources'][URL]['resolved_url'] == NEW_URL
    assert cached['sources'][URL]['missing_class_numbers'] == []


@pytest.mark.parametrize('hits,complete,expected_status', [
    ([], True, 'course_offering_not_unique_or_missing'),
    ([FakeClient.hit(crse_id='999999')], True, 'course_offering_not_unique_or_missing'),
    ([FakeClient.hit(), FakeClient.hit()], True, 'course_offering_not_unique_or_missing'),
    ([FakeClient.hit()], False, 'incomplete_or_wrong_subject_search'),
    ([FakeClient.hit(detail_url='https://example.org/SSR_CS_WRAP_FL.GBL')],
     True, 'unsafe_fresh_detail_url'),
])
def test_unverified_search_keeps_seats_and_sets_no_missing_flags(
    tmp_path, monkeypatch, hits, complete, expected_status,
):
    service = _service(tmp_path)
    _mock_parser(monkeypatch)
    client = FakeClient(hits=hits, search_complete=complete)
    service._missing_numbers_by_source[URL] = set(NUMBERS)  # Old false evidence.

    service._refresh_source(client, URL)

    assert client.direct_fetches == client.searches == 1
    assert client.details == 0
    assert service.missing_section_keys() == set()
    assert URL not in service._source_last_success
    assert service._source_retry_after[URL] - time.time() > 29 * 60
    failure = service.admin_status()['seat_failures'][0]
    assert failure['error_type'] == 'SeatUnverifiedEmptyGridError'
    assert failure['empty_recovery_status'] == expected_status
    assert service.admin_status()['unverified_empty_course_sources_since_start'] == 1
    for number in NUMBERS:
        section = service.repository.seat_snapshot(NUMBERS)[('2273', number)]
        assert section.seats_available == 10
        assert section.seat_updated_at is None


@pytest.mark.parametrize('recovered,expected_status', [
    (_parsed(numbers=NUMBERS, term='Fall 2026'), 'fresh_detail_not_verified'),
    (_parsed(numbers=NUMBERS, complete=False), 'fresh_detail_not_verified'),
    (_parsed(), 'fresh_recovery_failed'),
])
def test_recovery_rejects_wrong_or_empty_detail(
    tmp_path, monkeypatch, recovered, expected_status,
):
    service = _service(tmp_path)
    _mock_parser(monkeypatch, recovered=recovered)
    client = FakeClient()
    service._refresh_source(client, URL)
    assert client.details == 1
    assert URL not in service._resolved_url_by_source
    assert URL not in service._source_last_success
    assert service.missing_section_keys() == set()
    assert service.admin_status()['seat_failures'][0]['empty_recovery_status'] == expected_status


def test_fresh_detail_explicitly_empty_stays_unverified(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_parser(monkeypatch, recovered=_parsed())
    client = FakeClient()

    def empty_detail(_url):
        client.details += 1
        return SimpleNamespace(
            course_info_html=EMPTY_HTML, course_info_url=NEW_URL,
            course_info_expansion_count=0,
        )

    monkeypatch.setattr(client, 'fetch_detail_pages', empty_detail)
    service._refresh_source(client, URL)
    assert client.details == 1
    assert service.admin_status()['seat_failures'][0]['empty_recovery_status'] == 'fresh_detail_still_empty'
    assert service.missing_section_keys() == set()


def test_verified_different_numbers_after_fresh_detail_remain_review_evidence(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    _mock_parser(monkeypatch, recovered=_parsed(numbers=('55555',)))
    client = FakeClient()
    service._refresh_source(client, URL)
    assert client.details == 1
    assert URL not in service._source_last_success
    failure = service.admin_status()['seat_failures'][0]
    assert failure['error_type'] == 'SeatInventoryMismatchError'
    assert failure['inventory_reason'] == 'different_class_numbers'
    assert service.missing_section_keys() == {f'2273::{number}' for number in NUMBERS}
    assert service.unlisted_source_urls() == {URL}


def test_global_recovery_interval_prevents_repeated_expensive_searches(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    _mock_parser(monkeypatch)
    client = FakeClient()
    service._refresh_source(client, URL)
    assert client.searches == 1
    # Admin manual refreshes bypass source cooldown, but not the global
    # PeopleSoft fresh-search safety limit.
    service._refresh_source(client, URL)
    assert client.searches == 1
    assert client.direct_fetches == 2
    assert service.admin_status()['seat_failures'][0]['empty_recovery_status'] == 'global_rate_limit'
    assert service.missing_section_keys() == set()
