"""Offline regression tests: verified session reuse never trusts wrong subject data."""
from __future__ import annotations

from types import SimpleNamespace
import time

from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService
from classcatalog.scraping.session import SdsuPeopleSoftSession, SdsuHttpConfig

BASE = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def fixture(tmp_path, monkeypatch, *, enabled=True):
    monkeypatch.setenv("CLASSCATALOG_SEAT_REUSE_SUBJECT_SESSION", "1" if enabled else "0")
    model = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    specs = [
        ("HIST 110", "HIST", "Spring 2027", "2273", "11310", "037075"),
        ("HIST 111", "HIST", "Spring 2027", "2273", "11311", "037076"),
        ("FIN 331", "FIN", "Fall 2026", "2267", "1267", "035728"),
        ("BIOL 350", "BIOL", "Spring 2027", "2273", "1871", "034111"),
    ]
    sections = []
    urls = []
    for i, (code, subject, term, strm, number, crse) in enumerate(specs):
        url = f"{BASE}&STRM={strm}&SYNTH={i}"
        urls.append(url)
        sections.append(model.model_copy(update={
            "id": f"reuse-fixture-{i}", "source_url": url, "term": term,
            "term_code": strm, "subject": subject, "course_code": code,
            "catalog_number": code.split()[-1], "schedule_number": number,
            "section_number": "01", "crse_id": crse, "crse_offer_nbr": "1",
            "acad_career": "UGRD",
        }))
    service = SeatRefreshService(
        CourseRepository(tuple(sections)),
        cache_path=tmp_path / "seats.json",
        instructor_cache_path=tmp_path / "instructors.json",
        preferred_term="Spring 2027", request_delay_seconds=0,
    )
    return service, urls


class FakeClient:
    def __init__(self):
        self.public_session_generation = 0
        self.calls = []
        self.active_context = None
        self.fail_reuse = False
        self.wrong_identity = False
        self.internal_reset = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bootstrap(self):
        pass

    def rehydrate_subject_context(self, *, term, term_code, subject, reset_session=True):
        self.calls.append((subject, reset_session))
        if not reset_session and self.fail_reuse:
            raise ValueError("stale Fluid subject state")
        if reset_session or (self.internal_reset and not reset_session):
            self.public_session_generation += 1
        self.active_context = (term, term_code, subject)
        return SimpleNamespace(term=term, term_code=term_code, subject=subject, verified_absent=False)

    def parse_subject_pages(self, pages):
        crse = {
            "HIST": ("HIST 110", "037075"),
            "FIN": ("FIN 331", "035728"),
            "BIOL": ("BIOL 350", "034111"),
        }[pages.subject]
        return SimpleNamespace(
            term=pages.term, term_code=pages.term_code, subject=pages.subject,
            complete=True,
            courses=(SimpleNamespace(
                term=pages.term, term_code=pages.term_code, subject=pages.subject,
                course_code=crse[0],
                crse_id="WRONG" if self.wrong_identity else crse[1],
                crse_offer_nbr="1",
            ),),
        )


def run_worker(service, urls, fake, monkeypatch, *, failures=()):
    queue = list(urls)
    def next_source(_iteration):
        if queue:
            return queue.pop(0)
        service._stop.set()
        return None
    def refresh(_client, source_url):
        if source_url in failures:
            service._failed_sources += 1
            service._worker_context_affinity = None
        else:
            service._successful_sources += 1
            service._source_last_success[source_url] = time.time()
    monkeypatch.setattr(service, "_next_source", next_source)
    monkeypatch.setattr(service, "_refresh_source", refresh)
    service._session_factory = lambda: fake
    service._run()


def metrics(service):
    return service.public_status()["seat_refresh_throughput"]["subject_session_reuse"]


def test_reuse_is_off_by_default_and_reports_flag(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch, enabled=False)
    fake = FakeClient()
    run_worker(service, [urls[0], urls[2]], fake, monkeypatch)
    assert fake.calls == [("HIST", True), ("FIN", True)]
    assert not metrics(service)["enabled"]
    assert metrics(service)["attempts_last_300s"] == 0


def test_verified_previous_page_allows_exact_subject_reuse(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch)
    fake = FakeClient()
    run_worker(service, [urls[0], urls[1], urls[2]], fake, monkeypatch)
    assert fake.calls == [("HIST", True), ("FIN", False)]
    assert metrics(service)["verified_last_300s"] == 1
    assert metrics(service)["fallbacks_last_300s"] == 0


def test_wrong_course_identity_falls_back_to_fresh_subject(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch)
    fake = FakeClient()
    fake.wrong_identity = True
    run_worker(service, [urls[0], urls[2]], fake, monkeypatch)
    assert fake.calls == [("HIST", True), ("FIN", False), ("FIN", True)]
    assert metrics(service)["attempts_last_300s"] == 1
    assert metrics(service)["fallbacks_last_300s"] == 1
    assert metrics(service)["verified_last_300s"] == 0


def test_failed_noreset_search_falls_back_to_fresh_subject(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch)
    fake = FakeClient()
    fake.fail_reuse = True
    run_worker(service, [urls[0], urls[2]], fake, monkeypatch)
    assert fake.calls == [("HIST", True), ("FIN", False), ("FIN", True)]
    assert metrics(service)["fallbacks_last_300s"] == 1


def test_internal_session_reset_not_counted_as_reuse(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch)
    fake = FakeClient()
    fake.internal_reset = True
    run_worker(service, [urls[0], urls[2]], fake, monkeypatch)
    assert fake.calls == [("HIST", True), ("FIN", False)]
    assert metrics(service)["internal_resets_last_300s"] == 1
    assert metrics(service)["verified_last_300s"] == 0


def test_unverified_previous_page_cannot_enable_reuse(tmp_path, monkeypatch):
    service, urls = fixture(tmp_path, monkeypatch)
    fake = FakeClient()
    run_worker(service, [urls[0], urls[2], urls[3]], fake, monkeypatch, failures={urls[0]})
    assert fake.calls[0:2] == [("HIST", True), ("FIN", True)]
    assert metrics(service)["attempts_last_300s"] == 1  # FIN -> BIOL only


def test_public_session_generation_counts_resets():
    class Cookies:
        def clear(self):
            pass
    class Http:
        def __init__(self):
            self.headers = {}
            self.cookies = Cookies()
        def mount(self, *_):
            pass
    http = Http()
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0), http_session=http,
    )
    assert client.public_session_generation == 0
    client.reset_public_session()
    client.reset_public_session()
    assert client.public_session_generation == 2
