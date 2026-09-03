from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from classcatalog.scraping.models import CourseSearchHit, SubjectScrapeResult
from classcatalog.scraping.session import (
    PeopleSoftCircuitBreakerOpen,
    PeopleSoftSessionError,
    RawSubjectPages,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sdsu"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _response(url: str, body: str, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")  # noqa: SLF001 - requests test fixture
    response.encoding = "utf-8"
    return response


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.headers: dict[str, str] = {}
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self._responses: Iterator[requests.Response] = iter(responses)

    def mount(self, prefix: str, adapter: object) -> None:
        del prefix, adapter

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.requests.append((method, url, kwargs))
        return next(self._responses)

    def close(self) -> None:
        return None


def test_session_disables_open_only_before_exact_subject_facet() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=CS&ES_STRM=2267"
    post_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, _fixture("initial_cs.html")),
            _response(post_url, _fixture("all_statuses_cs.html")),
            _response(post_url, _fixture("filtered_all_statuses_cs.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
            in_run_state_recovery_attempts=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    observed: list[str] = []
    term_code = client.resolve_term_code("Fall 2026")
    _pages, result = client.scrape_subject(
        term="Fall 2026",
        term_code=term_code,
        subject="CS",
        page_observer=lambda kind, _html: observed.append(kind),
    )

    assert [method for method, _url, _kwargs in fake.requests] == [
        "GET",
        "GET",
        "POST",
        "POST",
    ]
    assert observed == ["initial", "all-statuses", "filtered"]
    assert result.exact_facet_applied
    assert [course.course_code for course in result.courses] == ["CS 150", "CS 155", "CS 160"]

    open_only_data = dict(fake.requests[2][2]["data"])
    assert open_only_data["ICAction"] == "PTS_SELECT$0"
    assert open_only_data["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in open_only_data

    subject_data = dict(fake.requests[3][2]["data"])
    assert subject_data["ICAction"] == "PTS_SELECT$6"
    assert subject_data["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in subject_data
    assert subject_data["PTS_SELECT$chk$6"] == "Y"


def test_session_rejects_login_or_cookie_error_page() -> None:
    fake = FakeSession(
        [
            _response(
                "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/?cmd=login&errorPg=ckreq",
                _fixture("login_error.html"),
            )
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(landing_url="https://example.test/landing", delay_seconds=0),
        http_session=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(PeopleSoftSessionError, match="Do not add credentials"):
        client.bootstrap()


def test_explicit_term_code_does_not_need_landing_request() -> None:
    fake = FakeSession([])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0),
        http_session=fake,  # type: ignore[arg-type]
    )
    assert client.resolve_term_code("Summer 2026", "2261") == "2261"
    assert not fake.requests


def test_invalid_explicit_term_code_is_rejected() -> None:
    fake = FakeSession([])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0),
        http_session=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="four-digit"):
        client.resolve_term_code("Summer 2026", "summer")


def test_get_transient_status_retries_with_configured_backoff(monkeypatch) -> None:
    url = "https://example.test/detail"
    fake = FakeSession(
        [
            _response(url, "temporarily unavailable", status=503),
            _response(url, "<html><body>ok</body></html>"),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("classcatalog.scraping.session.time.sleep", sleeps.append)
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            jitter_seconds=0,
            max_retries=1,
            get_backoff_seconds=(2.0,),
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    html = client.fetch_detail_page(url)

    assert "ok" in html
    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "GET"]
    assert sleeps == [2.0]


def test_get_retry_after_header_extends_backoff(monkeypatch) -> None:
    url = "https://example.test/detail"
    throttled = _response(url, "slow down", status=429)
    throttled.headers["Retry-After"] = "7"
    fake = FakeSession([throttled, _response(url, "<html>ok</html>")])
    sleeps: list[float] = []
    monkeypatch.setattr("classcatalog.scraping.session.time.sleep", sleeps.append)
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            jitter_seconds=0,
            max_retries=1,
            get_backoff_seconds=(2.0,),
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    client.fetch_detail_page(url)

    assert sleeps == [7.0]


def _without_open_status_control(html: str) -> str:
    html = html.replace(
        '<input type="hidden" name="PTS_SELECT$chk$0" value="N" />\n', ""
    )
    html = html.replace(
        '<input type="checkbox" id="PTS_SELECT$0" name="PTS_SELECT$0" value="Y" />\n', ""
    )
    html = html.replace('<label for="PTS_SELECT$0">Open Classes Only</label>\n', "")
    return html


def test_exact_subject_response_may_hide_unselected_open_status_facet() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=CS&ES_STRM=2267"
    post_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )
    filtered = _without_open_status_control(_fixture("filtered_all_statuses_cs.html"))
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, _fixture("initial_cs.html")),
            _response(post_url, _fixture("all_statuses_cs.html")),
            _response(post_url, filtered),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    _pages, result = client.scrape_subject(
        term="Fall 2026", term_code="2267", subject="CS"
    )
    assert result.complete
    assert len(result.courses) == 3


def test_initial_page_can_remove_open_only_from_selected_filters_breadcrumb() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=CS&ES_STRM=2267"
    post_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )
    initial = _fixture("initial_cs.html")
    initial = initial.replace(
        '<input type="hidden" name="PTS_SELECT$chk$0" value="Y" />\n', ""
    )
    initial = initial.replace(
        '<input type="checkbox" id="PTS_SELECT$0" name="PTS_SELECT$0" value="Y" checked />\n',
        "",
    )
    initial = initial.replace('<label for="PTS_SELECT$0">Open Classes Only</label>\n', "")
    initial = initial.replace(
        "<section aria-label=\"Subject\">",
        """
        <div id='win0divPTS_SRCH_PTS_BREADCRUMB_GB'>
          <table title='Selected Filters'><tr><td>
            <a id='PTS_BREADCRUMB_PTS_IMG$0'
               aria-label='Remove Open Classes Only filter'>
              <span class='ps-text'>Open Classes</span>
            </a>
          </td></tr></table>
        </div>
        <section aria-label=\"Subject\">""",
    )
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, initial),
            _response(post_url, _fixture("all_statuses_cs.html")),
            _response(post_url, _fixture("filtered_all_statuses_cs.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    _pages, result = client.scrape_subject(
        term="Fall 2026", term_code="2267", subject="CS"
    )
    assert result.complete
    remove_data = dict(fake.requests[2][2]["data"])
    assert remove_data["ICAction"] == "PTS_BREADCRUMB_PTS_IMG$0"


def test_subject_search_alias_retries_short_prefix() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    first_url = "https://example.test/results?SEARCH_TEXT=JS&ES_STRM=2267"
    alias_url = "https://example.test/results?SEARCH_TEXT=Jewish+Studies&ES_STRM=2267"
    post_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )

    def subject_page(label: str, code: str) -> str:
        return f"""
        <html><body><form id='win0' name='win0' method='post' action='{post_url}'>
          <input type='hidden' name='ICStateNum' value='1'>
          <input type='hidden' name='ICAction' value='None'>
          <input type='hidden' name='ICSID' value='x'>
          <input type='hidden' name='PTS_SELECT$chk$0' value='N'>
          <input type='checkbox' id='PTS_SELECT$0' name='PTS_SELECT$0' value='Y'>
          <label for='PTS_SELECT$0'>Open Classes</label>
          <input type='hidden' name='PTS_SELECT$chk$6' value='N'>
          <input type='checkbox' id='PTS_SELECT$6' name='PTS_SELECT$6' value='Y'>
          <label for='PTS_SELECT$6'>{label}</label>
          <ul><li class='ps_grid-row psc_rowact'><p hidden>{code} 495</p>
          <a href='/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?CRSE_ID=1&amp;CRSE_OFFER_NBR=1&amp;STRM=2267&amp;ACAD_CAREER=UGRD&amp;CLASS_NBR=1'>Internship</a>
          </li></ul>
        </form></body></html>
        """

    first = subject_page("GEN S/General Studies", "GEN S")
    alias = subject_page("JS/Jewish Studies", "JS")
    filtered = alias.replace("name='PTS_SELECT$6' value='Y'", "name='PTS_SELECT$6' value='Y' checked")
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(first_url, first),
            _response(alias_url, alias),
            _response(post_url, filtered),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )
    _pages, result = client.scrape_subject(
        term="Fall 2026", term_code="2267", subject="JS"
    )
    assert result.complete
    assert [course.course_code for course in result.courses] == ["JS 495"]
    assert fake.requests[2][2]["params"]["SEARCH_TEXT"] == "Jewish Studies"


@pytest.mark.parametrize(
    ("subject", "search_text"),
    (("CLASS", "CLASSICS"), ("STAT", "STATISTICS")),
)
def test_problematic_subject_uses_specific_primary_search_text(
    subject: str,
    search_text: str,
) -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = f"https://example.test/results?SEARCH_TEXT={search_text}&ES_STRM=2267"
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, _fixture("no_results_live.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    _pages, result = client.scrape_subject(
        term="Fall 2026", term_code="2267", subject=subject
    )

    assert result.complete
    assert fake.requests[1][2]["params"]["SEARCH_TEXT"] == search_text


def test_subject_alias_no_results_confirms_zero_offering_subject() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    first_url = "https://example.test/results?SEARCH_TEXT=JS&ES_STRM=2267"
    alias_url = "https://example.test/results?SEARCH_TEXT=Jewish+Studies&ES_STRM=2267"
    post_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )
    unrelated = f"""
    <html><body><form id='win0' name='win0' method='post' action='{post_url}'>
      <input type='hidden' name='ICStateNum' value='1'>
      <input type='hidden' name='ICAction' value='None'>
      <input type='hidden' name='ICSID' value='x'>
      <input type='hidden' name='PTS_SELECT$chk$0' value='N'>
      <input type='checkbox' id='PTS_SELECT$0' name='PTS_SELECT$0' value='Y'>
      <label for='PTS_SELECT$0'>Open Classes</label>
      <input type='hidden' name='PTS_SELECT$chk$6' value='N'>
      <input type='checkbox' id='PTS_SELECT$6' name='PTS_SELECT$6' value='Y'>
      <label for='PTS_SELECT$6'>GEN S/General Studies</label>
      <ul><li class='ps_grid-row psc_rowact'><p hidden>GEN S 100</p>
      <a href='/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?CRSE_ID=1&amp;CRSE_OFFER_NBR=1&amp;STRM=2267&amp;ACAD_CAREER=UGRD&amp;CLASS_NBR=1'>Unrelated course</a>
      </li></ul>
    </form></body></html>
    """
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(first_url, unrelated),
            _response(alias_url, _fixture("no_results_live.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    pages, result = client.scrape_subject(
        term="Fall 2026", term_code="2267", subject="JS"
    )

    assert result.complete
    assert result.courses == ()
    assert result.initial_result_count == 0
    assert result.filtered_result_count == 0
    assert not result.exact_facet_applied
    assert "Jewish+Studies" in pages.search_url
    assert fake.requests[2][2]["params"]["SEARCH_TEXT"] == "Jewish Studies"


def test_true_no_results_page_is_complete_zero_offering_subject() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=STS&ES_STRM=2267"
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, _fixture("no_results.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    observed: list[str] = []
    _pages, result = client.scrape_subject(
        term="Fall 2026",
        term_code="2267",
        subject="STS",
        page_observer=lambda kind, _html: observed.append(kind),
    )

    assert result.complete
    assert result.courses == ()
    assert result.initial_result_count == 0
    assert result.filtered_result_count == 0
    assert not result.exact_facet_applied
    assert observed == ["initial"]
    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "GET"]


def test_live_fluid_no_results_page_is_complete_zero_offering_subject() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=BQS&ES_STRM=2267"
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, _fixture("no_results_live.html")),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    observed: list[str] = []
    _pages, result = client.scrape_subject(
        term="Fall 2026",
        term_code="2267",
        subject="BQS",
        page_observer=lambda kind, _html: observed.append(kind),
    )

    assert result.complete
    assert result.courses == ()
    assert result.initial_result_count == 0
    assert result.filtered_result_count == 0
    assert not result.exact_facet_applied
    assert observed == ["initial"]
    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "GET"]


def test_zero_exact_subject_rows_without_no_results_message_still_require_status_proof() -> None:
    landing_url = "https://example.test/landing?Page=SSR_CLSRCH_MAIN_FL"
    result_url = "https://example.test/results?SEARCH_TEXT=STS&ES_STRM=2267"
    unrelated = """
    <html><body><form id='win0'>
      <ul><li class='ps_grid-row psc_rowact'><p hidden>GEN S 100</p>
      <a href='/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?CRSE_ID=1&amp;CRSE_OFFER_NBR=1&amp;STRM=2267&amp;ACAD_CAREER=UGRD&amp;CLASS_NBR=1'>Unrelated course</a>
      </li></ul>
    </form></body></html>
    """
    fake = FakeSession(
        [
            _response(landing_url, _fixture("landing_terms.html")),
            _response(result_url, unrelated),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
            in_run_state_recovery_attempts=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    with pytest.raises(PeopleSoftSessionError, match="cannot safely prove"):
        client.scrape_subject(term="Fall 2026", term_code="2267", subject="STS")


def test_scrape_subject_retries_recoverable_state_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSession([])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            max_retries=0,
            in_run_state_recovery_attempts=1,
            state_circuit_breaker_threshold=3,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )
    pages = RawSubjectPages(
        subject="CS",
        term="Fall 2026",
        term_code="2267",
        search_url="https://example.test/results?SEARCH_TEXT=CS&ES_STRM=2267",
        initial_html=_fixture("filtered_all_statuses_cs.html"),
        filtered_html=_fixture("filtered_all_statuses_cs.html"),
        exact_facet_applied=True,
    )
    attempts = 0

    def fake_fetch_subject_pages(**kwargs: object) -> RawSubjectPages:
        nonlocal attempts
        assert kwargs["subject"] == "CS"
        attempts += 1
        if attempts == 1:
            raise PeopleSoftSessionError("stale component state")
        return pages

    monkeypatch.setattr(client, "fetch_subject_pages", fake_fetch_subject_pages)

    _pages, result = client.scrape_subject(
        term="Fall 2026",
        term_code="2267",
        subject="CS",
        require_detail_urls=True,
    )

    assert attempts == 2
    assert result.complete is True
    assert [course.course_code for course in result.courses] == [
        "CS 150",
        "CS 155",
        "CS 160",
    ]


def test_state_circuit_breaker_opens_after_configured_consecutive_failures() -> None:
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            state_circuit_breaker_threshold=2,
        ),
        http_session=FakeSession([]),  # type: ignore[arg-type]
    )

    client.record_state_failure(
        context="first invalid detail",
        error=PeopleSoftSessionError("first"),
    )
    with pytest.raises(PeopleSoftCircuitBreakerOpen, match="2 consecutive times"):
        client.record_state_failure(
            context="second invalid detail",
            error=PeopleSoftSessionError("second"),
        )

    client.record_state_success()
    client.record_state_failure(
        context="counter reset",
        error=PeopleSoftSessionError("third"),
    )


def test_partitioned_detail_preparation_rehydrates_and_canonicalizes_course_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = CourseSearchHit(
        term="Fall 2026",
        term_code="2267",
        subject="ART",
        catalog_number="100",
        course_code="ART 100",
        title="Art 100",
        detail_url="https://stale.example/detail?CRSE_ID=1",
        crse_id="000001",
        crse_offer_nbr="1",
        acad_career="UGRD",
        class_number="4381",
        section_count=8,
        source_row_index=0,
        raw_text="ART 100",
    )
    second = first.model_copy(
        update={
            "catalog_number": "101",
            "course_code": "ART 101",
            "crse_id": "000002",
            "class_number": "4392",
            "section_count": 4,
            "source_row_index": 1,
        }
    )
    partitioned = SubjectScrapeResult(
        term="Fall 2026",
        term_code="2267",
        subject="ART",
        fetched_at="2026-08-20T00:00:00+00:00",
        search_url="https://example.test/search/ART",
        initial_result_count=75,
        filtered_result_count=75,
        exact_facet_applied=True,
        initial_result_cap_warning=True,
        filtered_result_cap_warning=True,
        complete=True,
        partitioned=True,
        partition_facets=("Course Career",),
        partition_leaf_count=2,
        courses=(first, second),
    )
    fresh_first = first.model_copy(
        update={
            "detail_url": "https://fresh.example/detail?CRSE_ID=000001",
            "section_count": 1,
        }
    )
    fresh_result = partitioned.model_copy(
        update={
            "partitioned": False,
            "partition_facets": (),
            "partition_leaf_count": 0,
            "initial_result_cap_warning": False,
            "filtered_result_cap_warning": False,
            "courses": (fresh_first,),
        }
    )
    pages = RawSubjectPages(
        subject="ART",
        term="Fall 2026",
        term_code="2267",
        search_url="https://example.test/search/ART",
        initial_html="<html></html>",
        filtered_html="<html></html>",
        exact_facet_applied=True,
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0),
        http_session=FakeSession([]),  # type: ignore[arg-type]
    )
    rehydrate_calls = 0

    def fake_rehydrate(**kwargs: object) -> RawSubjectPages:
        nonlocal rehydrate_calls
        assert kwargs["reset_session"] is False
        rehydrate_calls += 1
        return pages

    monkeypatch.setattr(client, "rehydrate_subject_context", fake_rehydrate)
    monkeypatch.setattr(client, "parse_subject_pages", lambda _pages: fresh_result)

    refreshed = client.prepare_partitioned_detail_result(partitioned)

    assert rehydrate_calls == 1
    assert refreshed.partitioned is True
    assert len(refreshed.courses) == 2
    assert refreshed.courses[0].section_count == 8
    assert refreshed.courses[0].detail_url is not None
    assert "SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL" in refreshed.courses[0].detail_url
    assert "CRSE_ID=000001" in refreshed.courses[0].detail_url
    assert refreshed.courses[1].detail_url is not None
    assert "CRSE_ID=000002" in refreshed.courses[1].detail_url


def test_recovery_rehydration_does_not_clear_failure_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            in_run_state_recovery_attempts=0,
            state_circuit_breaker_threshold=3,
        ),
        http_session=FakeSession([]),  # type: ignore[arg-type]
    )
    pages = RawSubjectPages(
        subject="CS",
        term="Fall 2026",
        term_code="2267",
        search_url="https://example.test/search/CS",
        initial_html="<html></html>",
        filtered_html="<html></html>",
        exact_facet_applied=True,
    )
    monkeypatch.setattr(client, "fetch_subject_pages", lambda **_kwargs: pages)
    client.record_state_failure(
        context="first failed detail",
        error=PeopleSoftSessionError("first"),
    )

    client.rehydrate_subject_context(
        term="Fall 2026",
        term_code="2267",
        subject="CS",
        reset_session=False,
        mark_success=False,
    )

    client.record_state_failure(
        context="second failed detail",
        error=PeopleSoftSessionError("second"),
    )
    with pytest.raises(PeopleSoftCircuitBreakerOpen, match="3 consecutive times"):
        client.record_state_failure(
            context="third failed detail",
            error=PeopleSoftSessionError("third"),
        )
