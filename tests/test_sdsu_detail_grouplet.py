from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from classcatalog.scraping.parser import (
    CourseInfoUrlNotFound,
    GroupletUrlNotFound,
    parse_course_info_url,
    parse_grouplet_url,
)
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession


SHELL_URL = "https://cmsweb.cms.sdsu.edu/psc/CSDPRD_1/EMPLOYEE/SA/c/shell"
GROUPLET_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD_newwin/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_START_PAGE_FL.GBL?Page=SSR_START_PAGE_FL&Action=U&"
    "SEC=05&CRSE_ID=038518&CLASS_NBR=3213&STRM=2267&ICDoModal=1&ICGrouplet=1&ICLoc=1"
)
COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&Action=U&"
    "Page=SSR_CS_WRAP_FL&Action=U&CRSE_ID=038518&CRSE_OFFER_NBR=1&STRM=2267&"
    "INSTITUTION=SDCMP&ACAD_CAREER=UGRD&CLASS_NBR=3213&SEC=05&"
    "pts_Portal=EMPLOYEE&pts_PortalHostNode=SA&pts_Market=GBL"
)

SHELL_HTML = f"""
<html><head><script>
var agGroupletList = [];var groupletList = [];
agGroupletList = [['target','5','{GROUPLET_URL}','','0','bGrouplet@1;']];
</script></head><body><div id="divPAGECONTAINER_TGT"></div></body></html>
"""

GROUPLET_HTML = f"""
<link rel='stylesheet' href='/fake.css' />
<script>
try{{
getDefaultURL('{COURSE_INFO_URL}')
initializePage(true, 'psa_tab_SSR_CLSRCH_MAIN_FL');
}}catch(e) {{}}
</script>
<div id='navigation'>Class Search and Enroll</div>
"""


def _response(url: str, body: str) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
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


def test_parse_grouplet_url_from_ag_grouplet_list() -> None:
    assert parse_grouplet_url(SHELL_HTML) == GROUPLET_URL


def test_parse_grouplet_url_rejects_shell_without_lazy_load_url() -> None:
    with pytest.raises(GroupletUrlNotFound, match="grouplet URL"):
        parse_grouplet_url("<html><body>No deferred content</body></html>")


def test_parse_course_info_url_from_get_default_url() -> None:
    assert parse_course_info_url(GROUPLET_HTML) == COURSE_INFO_URL


def test_parse_course_info_url_rejects_unrelated_grouplet() -> None:
    with pytest.raises(CourseInfoUrlNotFound, match="SSR_CRSE_INFO_FL"):
        parse_course_info_url("<script>initializePage(true)</script>")


def test_parse_course_info_url_from_captured_cs_150_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-grouplet.html"
    assert parse_course_info_url(fixture.read_text(encoding="utf-8")) == COURSE_INFO_URL


def test_detail_fetch_follows_grouplet_then_course_info_with_referers() -> None:
    course_info_html = "<div id='actual-detail'>CS 150 final course information</div>"
    fake = FakeSession(
        [
            _response(SHELL_URL, SHELL_HTML),
            _response(GROUPLET_URL, GROUPLET_HTML),
            _response(COURSE_INFO_URL, course_info_html),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    pages = client.fetch_detail_pages(
        "https://example.test/course-detail",
        referer="https://example.test/results",
    )

    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "GET", "GET"]
    assert fake.requests[0][2]["headers"] == {"Referer": "https://example.test/results"}
    assert fake.requests[1][1] == GROUPLET_URL
    assert fake.requests[1][2]["headers"] == {"Referer": SHELL_URL}
    assert fake.requests[2][1] == COURSE_INFO_URL
    assert fake.requests[2][2]["headers"] == {"Referer": GROUPLET_URL}
    assert pages.shell_html == SHELL_HTML
    assert pages.grouplet_html == GROUPLET_HTML
    assert pages.course_info_html == course_info_html
    assert pages.grouplet_url == GROUPLET_URL
    assert pages.course_info_url == COURSE_INFO_URL
