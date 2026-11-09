"""Offline seat-worker regression checks for PeopleSoft subject context.

No live SDSU requests; the scheduled worker uses a fake session.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

BASE_URL = (
    'https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/'
    'SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM='
)


def _service(tmp_path: Path) -> tuple[SeatRefreshService, list[str]]:
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    data = [
        ('Spring 2027', '2273', 'HIST', 'HIST 110', '11310', '037075', '1'),
        ('Spring 2027', '2273', 'HIST', 'HIST 111', '11311', '037076', '1'),
        ('Fall 2026', '2267', 'FIN', 'FIN 331', '1267', '035728', '1'),
    ]
    sections = []
    urls = []
    for i, (term, strm, subject, course_code, schedule, crse_id, offer) in enumerate(data):
        url = f'{BASE_URL}{strm}&TEST={i}'
        urls.append(url)
        sections.append(sample.model_copy(update={
            'id': f'context-{i}',
            'course_code': course_code,
            'subject': subject,
            'catalog_number': course_code.split(' ', 1)[-1],
            'term': term,
            'term_code': strm,
            'schedule_number': schedule,
            'source_url': url,
            'section_number': '01',
            'crse_id': crse_id,
            'crse_offer_nbr': offer,
            'acad_career': 'UGRD',
            'seat_status': SeatStatus.OPEN,
            'seat_updated_at': None,
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(sections)),
        cache_path=tmp_path / 'seat-cache.json',
        instructor_cache_path=tmp_path / 'instructor-cache.json',
        preferred_term='Spring 2027',
        request_delay_seconds=0,
    )
    return service, urls


class FakeSession:
    def __init__(self):
        self.context = None
        self.bootstrap_count = 0
        self.rehydrations = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bootstrap(self):
        self.bootstrap_count += 1

    def rehydrate_subject_context(self, *, term, term_code, subject):
        self.context = (term, term_code, subject)
        self.rehydrations.append(self.context)


def _run_urls(service, urls, monkeypatch):
    pending = list(urls)
    def next_source(_iteration):
        if pending:
            return pending.pop(0)
        service._stop.set()
        return None
    monkeypatch.setattr(service, '_next_source', next_source)
    service._run()


def test_worker_primes_and_reuses_context_across_matching_subjects(tmp_path, monkeypatch):
    service, urls = _service(tmp_path)
    fake = FakeSession()
    service._session_factory = lambda: fake
    seen = []
    def observe(client, source_url):
        seen.append((source_url, client.context))
    monkeypatch.setattr(service, '_refresh_source', observe)
    _run_urls(service, [urls[0], urls[1], urls[2], urls[0]], monkeypatch)

    spring = ('Spring 2027', '2273', 'HIST')
    fall = ('Fall 2026', '2267', 'FIN')
    assert fake.bootstrap_count == 1
    assert fake.rehydrations == [spring, fall, spring]
    assert seen == [(urls[0], spring), (urls[1], spring), (urls[2], fall), (urls[0], spring)]


def test_worker_revalidates_context_after_refresh_failure(tmp_path, monkeypatch):
    service, urls = _service(tmp_path)
    fake = FakeSession()
    service._session_factory = lambda: fake
    seen = []
    def fail_first(client, url):
        seen.append(client.context)
        if len(seen) == 1:
            service._failed_sources += 1  # _refresh_source normally updates this.
    monkeypatch.setattr(service, '_refresh_source', fail_first)
    _run_urls(service, [urls[0], urls[1]], monkeypatch)
    assert fake.rehydrations == [('Spring 2027', '2273', 'HIST')] * 2
    assert len(seen) == 2


def test_worker_reports_context_failure_without_fetching_or_marking_seats_fresh(tmp_path, monkeypatch):
    service, urls = _service(tmp_path)
    fake = FakeSession()
    def broken_context(*, term, term_code, subject):
        if subject == 'HIST':
            raise RuntimeError('simulated subject rehydration failure')
        fake.rehydrations.append((term, term_code, subject))
    fake.rehydrate_subject_context = broken_context
    service._session_factory = lambda: fake
    seen = []
    monkeypatch.setattr(service, '_refresh_source', lambda client, url: seen.append(url))
    _run_urls(service, [urls[0], urls[2]], monkeypatch)
    assert seen == [urls[2]]
    assert urls[0] not in service._source_last_success
    assert service.admin_status()['failed_course_refreshes'] == 1
    assert service._source_retry_after[urls[0]] > 0


def test_hist_saved_page_only_parses_after_worker_rehydration(tmp_path, monkeypatch):
    service, urls = _service(tmp_path)
    fake = FakeSession()
    service._session_factory = lambda: fake
    seen_urls = []

    def fetch_after_context(url):
        seen_urls.append(url)
        assert fake.context == ('Spring 2027', '2273', 'HIST')
        return SimpleNamespace(html='COURSE', canonical_url=url, expansion_count=0)
    fake.fetch_course_info_page = fetch_after_context

    def parsed_course(_html, *, source_url):
        return SimpleNamespace(
            course_code='HIST 110', term='Spring 2027', term_code='2273',
            options_complete=True,
            options=(SimpleNamespace(
                class_number='11310', instructor='Jane Doe', status=SeatStatus.OPEN,
                open_seats=5, seat_capacity=30, seats_enrolled=25,
            ),),
        )
    monkeypatch.setattr('classcatalog.seats.parse_course_info_page', parsed_course)
    monkeypatch.setattr('classcatalog.seats.find_class_number_actions', lambda _html: ())
    _run_urls(service, [urls[0]], monkeypatch)
    assert seen_urls == [urls[0]]
    assert service.admin_status()['successful_course_refreshes'] == 1
    snapshot = service.repository.seat_snapshot(('11310',))
    assert snapshot[('2273', '11310')].seats_available == 5
    assert snapshot[('2273', '11310')].seat_updated_at is not None
