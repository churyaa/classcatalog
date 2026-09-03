from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests

from classcatalog.scraping.course_option_pagination import (
    build_course_option_expand_post,
    find_course_option_expand_action,
)
from classcatalog.scraping.parser import parse_course_option_grid_progress
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession

COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&"
    "CRSE_ID=005282&STRM=2267&CLASS_NBR=8934&SEC=71"
)
POST_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL"
)
SHELL_URL = "https://example.test/detail-shell"
GROUPLET_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD_newwin/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_START_PAGE_FL.GBL?Page=SSR_START_PAGE_FL&ICGrouplet=1"
)


def _course_page(
    *,
    state: int,
    visible_classes: tuple[str, ...],
    total: int,
    display_more: int | None,
) -> str:
    rows = []
    for index, class_number in enumerate(visible_classes):
        rows.append(
            f"""
            <tr id='SSR_CLS_DTLS_VW$0_row_{index}'>
              <td>
                <a id='SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$${index}'
                   href=\"javascript:submitAction_win0(document.win0,'SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$${index}');\">
                  Lrg Lect - {class_number}
                </a>
              </td>
            </tr>
            """
        )
    count = len(visible_classes)
    pager = ""
    if display_more is not None:
        pager = f"""
        <a id='SSR_CLSRCH_F_WK_SSR_CHANGE_BTN'
           href=\"javascript:submitAction_win0(document.win0,'SSR_CLSRCH_F_WK_SSR_CHANGE_BTN');\">
          Display {display_more} More
        </a>
        """
    return f"""
    <html><body>
      <form method='post' action='{POST_URL}'>
        <input type='hidden' name='ICStateNum' value='{state}' />
        <input type='hidden' name='ICSID' value='TEST-SESSION' />
        <input type='hidden' name='ICAction' value='None' />
        <span id='SSR_CLSRCH_F_WK_SSR_MSG_TEXT'>1 - {count} of {total} options</span>
        <table><tbody>{''.join(rows)}</tbody></table>
        {pager}
      </form>
    </body></html>
    """


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


def _shell() -> str:
    return f"<html><script>var agGroupletList = ['{GROUPLET_URL}'];</script></html>"


def _grouplet() -> str:
    return f"<html><script>getDefaultURL('{COURSE_INFO_URL}');</script></html>"


def test_finds_live_shaped_display_more_action() -> None:
    html = _course_page(
        state=2635,
        visible_classes=("6403", "7795"),
        total=3,
        display_more=1,
    )

    action = find_course_option_expand_action(html)

    assert action is not None
    assert action.action_id == "SSR_CLSRCH_F_WK_SSR_CHANGE_BTN"
    assert action.display_count == 1
    assert action.label == "Display 1 More"


def test_builds_display_more_post_from_current_people_soft_state() -> None:
    html = _course_page(
        state=2635,
        visible_classes=("6403", "7795"),
        total=3,
        display_more=1,
    )
    action = find_course_option_expand_action(html)
    assert action is not None

    post = build_course_option_expand_post(
        html,
        current_url=COURSE_INFO_URL,
        action=action,
    )
    fields = dict(post.fields)

    assert post.action_url == POST_URL
    assert fields["ICStateNum"] == "2635"
    assert fields["ICSID"] == "TEST-SESSION"
    assert fields["ICAction"] == "SSR_CLSRCH_F_WK_SSR_CHANGE_BTN"


def test_option_grid_progress_detects_truncation_and_completion() -> None:
    partial = parse_course_option_grid_progress(
        _course_page(
            state=1,
            visible_classes=("6403", "7795"),
            total=3,
            display_more=1,
        )
    )
    complete = parse_course_option_grid_progress(
        _course_page(
            state=2,
            visible_classes=("6403", "7795", "12612"),
            total=3,
            display_more=None,
        )
    )

    assert (partial.start, partial.end, partial.total, partial.complete) == (1, 2, 3, False)
    assert partial.displayed_rows == 2
    assert (complete.start, complete.end, complete.total, complete.complete) == (1, 3, 3, True)
    assert complete.displayed_rows == 3


def test_explicit_one_of_one_window_is_complete_without_visible_row_wrapper() -> None:
    html = """
    <html><body>
      <span id='SSR_CLSRCH_F_WK_SSR_MSG_TEXT'>1 - 1 of 1 options</span>
    </body></html>
    """

    progress = parse_course_option_grid_progress(html)

    assert progress.displayed_rows == 0
    assert (progress.start, progress.end, progress.total) == (1, 1, 1)
    assert progress.complete is True


def test_one_of_one_window_does_not_require_nonexistent_display_more_action() -> None:
    html = """
    <html><body>
      <span id='SSR_CLSRCH_F_WK_SSR_MSG_TEXT'>1 - 1 of 1 options</span>
    </body></html>
    """
    fake = FakeSession([_response(COURSE_INFO_URL, html)])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, jitter_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    loaded = client._load_course_info_with_option_expansion(  # noqa: SLF001
        COURSE_INFO_URL,
        referer=None,
    )

    assert loaded.expansion_count == 0
    assert loaded.html == html
    assert [method for method, _url, _kwargs in fake.requests] == ["GET"]


def test_fetch_detail_pages_expands_option_grid_before_returning() -> None:
    initial = _course_page(
        state=10,
        visible_classes=("6403", "7795"),
        total=3,
        display_more=1,
    )
    expanded = _course_page(
        state=11,
        visible_classes=("6403", "7795", "12612"),
        total=3,
        display_more=None,
    )
    fake = FakeSession(
        [
            _response(SHELL_URL, _shell()),
            _response(GROUPLET_URL, _grouplet()),
            _response(COURSE_INFO_URL, initial),
            _response(POST_URL, expanded),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, jitter_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    pages = client.fetch_detail_pages(SHELL_URL)

    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "GET", "GET", "POST"]
    assert dict(fake.requests[3][2]["data"])["ICAction"] == (
        "SSR_CLSRCH_F_WK_SSR_CHANGE_BTN"
    )
    assert pages.course_info_url == COURSE_INFO_URL
    assert pages.course_info_expansion_count == 1
    progress = parse_course_option_grid_progress(pages.course_info_html)
    assert progress.complete
    assert progress.total == 3


def test_class_modal_expands_only_when_requested_class_is_beyond_initial_window() -> None:
    initial = _course_page(
        state=20,
        visible_classes=("6403", "7795"),
        total=3,
        display_more=1,
    )
    expanded = _course_page(
        state=21,
        visible_classes=("6403", "7795", "12612"),
        total=3,
        display_more=None,
    )
    class_modal = "<html><body>Class 12612</body></html>"
    fake = FakeSession(
        [
            _response(COURSE_INFO_URL, initial),
            _response(POST_URL, expanded),
            _response(POST_URL, class_modal),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(delay_seconds=0, jitter_seconds=0, max_retries=0),
        http_session=fake,  # type: ignore[arg-type]
    )

    page = client.fetch_class_number_page(COURSE_INFO_URL, "12612")

    assert [method for method, _url, _kwargs in fake.requests] == ["GET", "POST", "POST"]
    assert dict(fake.requests[1][2]["data"])["ICAction"] == (
        "SSR_CLSRCH_F_WK_SSR_CHANGE_BTN"
    )
    assert dict(fake.requests[2][2]["data"])["ICAction"].endswith("$$2")
    assert page.class_number == "12612"
    assert page.course_info_url == COURSE_INFO_URL


def test_failed_display_more_post_recovers_from_fresh_course_state() -> None:
    initial = _course_page(
        state=30,
        visible_classes=("6403", "7795"),
        total=3,
        display_more=1,
    )
    expanded = _course_page(
        state=31,
        visible_classes=("6403", "7795", "12612"),
        total=3,
        display_more=None,
    )
    fake = FakeSession(
        [
            _response(SHELL_URL, _shell()),
            _response(GROUPLET_URL, _grouplet()),
            _response(COURSE_INFO_URL, initial),
            _response(POST_URL, "temporarily unavailable", status=503),
            _response(COURSE_INFO_URL, initial.replace("value='30'", "value='40'")),
            _response(POST_URL, expanded),
        ]
    )
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            delay_seconds=0,
            jitter_seconds=0,
            max_retries=0,
            post_recovery_cooldown_seconds=0,
            stateful_post_recovery_attempts=1,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )

    pages = client.fetch_detail_pages(SHELL_URL)

    assert [method for method, _url, _kwargs in fake.requests] == [
        "GET",
        "GET",
        "GET",
        "POST",
        "GET",
        "POST",
    ]
    assert dict(fake.requests[3][2]["data"])["ICStateNum"] == "30"
    assert dict(fake.requests[5][2]["data"])["ICStateNum"] == "40"
    assert pages.course_info_expansion_count == 1
    assert parse_course_option_grid_progress(pages.course_info_html).complete
