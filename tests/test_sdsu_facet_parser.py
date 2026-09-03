from __future__ import annotations

from pathlib import Path

import pytest

from classcatalog.scraping.facet_parser import (
    FacetParseError,
    SubjectFacetNotFound,
    build_open_classes_only_post,
    build_subject_facet_post,
    find_open_classes_only_facet,
    find_subject_facet,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sdsu"
CURRENT_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL?SEARCH_TEXT=CS&ES_STRM=2267"
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _payload_map(fields: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(fields)


def test_finds_exact_cs_facet_not_csp() -> None:
    facet = find_subject_facet(_fixture("initial_cs.html"), "CS")
    assert facet.index == 6
    assert facet.label == "CS/Computer Science"


def test_finds_multiword_subject_from_aria_label() -> None:
    facet = find_subject_facet(_fixture("initial_civ_e.html"), "CIV E")
    assert facet.index == 14
    assert facet.control_name == "PTS_SELECT$14"


def test_missing_subject_facet_lists_detected_labels() -> None:
    with pytest.raises(SubjectFacetNotFound, match="CSP/Counseling"):
        find_subject_facet(_fixture("initial_cs.html"), "MATH")


def test_ambiguous_subject_facet_is_rejected() -> None:
    html = """
    <form id='win0'>
      <input id='PTS_SELECT$1' name='PTS_SELECT$1' type='checkbox'>
      <label for='PTS_SELECT$1'>CS/Computer Science</label>
      <input id='PTS_SELECT$2' name='PTS_SELECT$2' type='checkbox'>
      <label for='PTS_SELECT$2'>CS/Computer Science</label>
    </form>
    """
    with pytest.raises(FacetParseError, match="More than one exact facet"):
        find_subject_facet(html, "CS")



def test_finds_open_classes_only_facet_dynamically() -> None:
    facet = find_open_classes_only_facet(_fixture("initial_cs.html"))
    assert facet.index == 0
    assert facet.label == "Open Classes Only"
    assert facet.checked is True


def test_finds_live_sdsu_open_classes_label_without_name_attribute() -> None:
    facet = find_open_classes_only_facet(_fixture("initial_cs_live_status.html"))
    assert facet.index == 0
    assert facet.label == "Open Classes"
    assert facet.control_name == "PTS_SELECT$0"
    assert facet.checked is True


def test_live_sdsu_open_classes_post_turns_filter_off() -> None:
    post = build_open_classes_only_post(
        _fixture("initial_cs_live_status.html"),
        current_url=CURRENT_URL,
        enabled=False,
    )
    payload = _payload_map(post.fields)
    assert payload["ICAction"] == "PTS_SELECT$0"
    assert payload["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in payload
    assert payload["PTS_SELECT$chk$1"] == "N"
    assert payload["PTS_SELECT$chk$2"] == "N"
    assert payload["ICStateNum"] == "2"


def test_open_classes_only_post_explicitly_turns_filter_off() -> None:
    post = build_open_classes_only_post(
        _fixture("initial_cs.html"),
        current_url=CURRENT_URL,
        enabled=False,
    )
    payload = _payload_map(post.fields)
    assert payload["ICAction"] == "PTS_SELECT$0"
    assert payload["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in payload
    assert payload["ICStateNum"] == "4"


def test_subject_post_preserves_open_classes_only_off_state() -> None:
    html = _fixture("all_statuses_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    payload = _payload_map(post.fields)
    assert payload["PTS_SELECT$chk$0"] == "N"
    assert "PTS_SELECT$0" not in payload

def test_post_uses_dynamic_target_as_ic_action() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    assert _payload_map(post.fields)["ICAction"] == "PTS_SELECT$6"


def test_post_turns_exact_subject_on() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    payload = _payload_map(post.fields)
    assert payload["PTS_SELECT$chk$6"] == "Y"
    assert payload["PTS_SELECT$6"] == "Y"


def test_post_preserves_open_classes_checkbox() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    payload = _payload_map(post.fields)
    assert payload["PTS_SELECT$chk$0"] == "Y"
    assert payload["PTS_SELECT$0"] == "Y"


def test_post_marks_other_subject_facets_off() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    payload = _payload_map(post.fields)
    assert payload["PTS_SELECT$chk$5"] == "N"
    assert payload["PTS_SELECT$chk$7"] == "N"
    assert "PTS_SELECT$7" not in payload


def test_post_preserves_people_soft_hidden_state() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    payload = _payload_map(post.fields)
    assert payload["ICStateNum"] == "4"
    assert payload["ICSID"] == "abc123"
    assert payload["ICType"] == "Panel"


def test_post_resolves_relative_form_action() -> None:
    html = _fixture("initial_cs.html")
    facet = find_subject_facet(html, "CS")
    post = build_subject_facet_post(html, current_url=CURRENT_URL, facet=facet)
    assert post.action_url == (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
    )


def test_selected_filter_remove_post_uses_breadcrumb_action() -> None:
    from classcatalog.scraping.facet_parser import build_selected_filter_remove_post

    html = """
    <form id='win0' name='win0' method='post' action='/results'>
      <input type='hidden' name='ICStateNum' value='44'>
      <input type='hidden' name='ICAction' value='None'>
      <input type='hidden' name='ICSID' value='abc'>
      <div id='win0divPTS_SRCH_PTS_BREADCRUMB_GB'>
        <table title='Selected Filters'><tr><td>
          <a id='PTS_BREADCRUMB_PTS_IMG$0'
             aria-label='Remove Open Classes Only filter'>
            <span class='ps-text'>Open Classes</span>
          </a>
        </td></tr></table>
      </div>
    </form>
    """
    post = build_selected_filter_remove_post(
        html,
        current_url=CURRENT_URL,
        labels=("Open Classes", "Open Classes Only"),
    )
    payload = _payload_map(post.fields)
    assert payload["ICAction"] == "PTS_BREADCRUMB_PTS_IMG$0"
    assert payload["ICStateNum"] == "44"
