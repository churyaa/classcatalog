from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from classcatalog.scraping.class_post import (
    ClassNumberActionNotFound,
    build_class_number_post,
    find_class_number_action,
    find_class_number_actions,
)
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession

FIXTURE = Path(__file__).parent / "fixtures" / "sdsu" / "class-number-post-page.html"
COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "CRSE_ID=038518&STRM=2267&CLASS_NBR=3213&SEC=05"
)
POST_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL"
)
GROUPLET_URL = "https://cmsweb.cms.sdsu.edu/psc/CSDPRD_newwin/EMPLOYEE/SA/c/grouplet"


def _fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


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


def test_finds_both_dynamic_class_number_actions() -> None:
    actions = find_class_number_actions(_fixture())

    assert [action.class_number for action in actions] == ["3212", "3213"]
    assert actions[0].component == "Lrg Lect"
    assert actions[0].source_row_index == 0
    assert actions[0].action_id == "SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0"
    assert actions[1].action_id == "SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1"


def test_finds_action_by_visible_class_number() -> None:
    action = find_class_number_action(_fixture(), "3213")
    assert action.source_row_index == 1
    assert action.action_id.endswith("$$1")


def test_rejects_unknown_class_number() -> None:
    with pytest.raises(ClassNumberActionNotFound, match="9999"):
        find_class_number_action(_fixture(), "9999")


def test_builds_post_from_people_soft_form_state() -> None:
    action = find_class_number_action(_fixture(), "3212")
    post = build_class_number_post(
        _fixture(),
        current_url=COURSE_INFO_URL,
        action=action,
    )
    fields = dict(post.fields)

    assert post.action_url == POST_URL
    assert fields["ICStateNum"] == "6"
    assert fields["ICSID"] == "TEST-SESSION-ID"
    assert fields["ICAction"] == action.action_id


def test_session_refreshes_state_then_posts_class_action() -> None:
    response_html = "<html><body><div id='class-detail'>Class 3212 detail</div></body></html>"
    fake = FakeSession(
        [
            _response(COURSE_INFO_URL, _fixture()),
            _response(POST_URL, response_html),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    page = client.fetch_class_number_page(
        COURSE_INFO_URL,
        "3212",
        referer=GROUPLET_URL,
    )

    assert [request[0] for request in fake.requests] == ["GET", "POST"]
    assert fake.requests[0][1] == COURSE_INFO_URL
    assert fake.requests[0][2]["headers"] == {"Referer": GROUPLET_URL}

    post_method, post_url, post_kwargs = fake.requests[1]
    assert post_method == "POST"
    assert post_url == POST_URL
    assert post_kwargs["headers"]["Referer"] == COURSE_INFO_URL
    assert post_kwargs["headers"]["Origin"] == "https://cmsweb.cms.sdsu.edu"
    assert dict(post_kwargs["data"])["ICAction"] == (
        "SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0"
    )
    assert page.class_number == "3212"
    assert page.action_id.endswith("$$0")
    assert page.post_url == POST_URL
    assert page.response_url == POST_URL
    assert page.html == response_html


def test_class_post_does_not_hardcode_dynamic_action_id() -> None:
    changed = _fixture().replace("$294$$0", "$811$$0")
    fake = FakeSession(
        [
            _response(COURSE_INFO_URL, changed),
            _response(POST_URL, "<div>fresh action accepted</div>"),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    page = client.fetch_class_number_page(COURSE_INFO_URL, "3212")

    assert page.action_id == "SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$811$$0"
    assert dict(fake.requests[1][2]["data"])["ICAction"] == page.action_id


def test_deduplicates_repeated_lecture_actions_and_keeps_linked_labs() -> None:
    html = """
    <html><body>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Lrg Lect - 6403</a>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$295$$0">Laboratory - 7795</a>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1">Lrg Lect - 6403</a>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$295$$1">Laboratory - 12612</a>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$2">Lrg Lect - 8913</a>
      <a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$295$$2">Laboratory - 8967</a>
    </body></html>
    """

    actions = find_class_number_actions(html)

    assert [action.class_number for action in actions] == [
        "6403",
        "7795",
        "12612",
        "8913",
        "8967",
    ]
    assert actions[0].action_id.endswith("$$0")
    assert actions[0].component_index == 1
    assert actions[1].component == "Laboratory"
    assert actions[1].component_index == 2
    assert actions[2].source_row_index == 1
