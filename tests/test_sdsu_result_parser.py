from __future__ import annotations

from pathlib import Path

import pytest

from classcatalog.scraping.parser import (
    CourseCodeParseError,
    ResultParseError,
    count_result_rows,
    has_no_results_message,
    has_result_cap_warning,
    normalize_people_soft_response,
    parse_result_rows,
    parse_subject_catalog,
    parse_term_codes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sdsu"
BASE_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL?SEARCH_TEXT=CS&ES_STRM=2267"
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_single_word_subject_and_catalog() -> None:
    assert parse_subject_catalog("CS  150") == ("CS", "150")



def test_parses_current_seg_subject_and_catalog() -> None:
    assert parse_subject_catalog("SEG 360") == ("SEG", "360")

def test_parses_multiword_subject_and_catalog() -> None:
    assert parse_subject_catalog("CIV E  101") == ("CIV E", "101")


def test_uses_longest_known_multiword_subject() -> None:
    assert parse_subject_catalog("M S E  355A") == ("M S E", "355A")


def test_rejects_unknown_subject_instead_of_guessing() -> None:
    with pytest.raises(CourseCodeParseError):
        parse_subject_catalog("NOTREAL 101")


def test_discovers_term_codes_from_options() -> None:
    mapping = parse_term_codes(_fixture("landing_terms.html"))
    assert mapping == {
        "Summer 2026": "2261",
        "Fall 2026": "2267",
        "Spring 2027": "2272",
    }


def test_detects_institutional_result_cap_warning() -> None:
    assert has_result_cap_warning(_fixture("initial_cs.html"))
    assert not has_result_cap_warning(_fixture("filtered_cs.html"))


def test_detects_no_results_message() -> None:
    assert has_no_results_message(_fixture("no_results.html"))


def test_detects_live_fluid_no_results_message() -> None:
    html = _fixture("no_results_live.html")
    assert has_no_results_message(html)
    assert count_result_rows(html) == 0


def test_ignores_generic_hidden_no_results_widget_text() -> None:
    html = """
    <html><body>
      <div class='pts_message psc_hidden'>No results to display</div>
      <div id='PTS_SRCH_PTS_INDEXTIME'>12 courses displayed with keyword(s): CS</div>
    </body></html>
    """
    assert not has_no_results_message(html)


def test_no_results_message_does_not_override_course_rows() -> None:
    html = """
    <html><body>
      <div id='PTS_SRCH_PTS_INDEXTIME'>No results were returned.</div>
      <ul><li class='ps_grid-row psc_rowact'><p hidden>CS 150</p></li></ul>
    </body></html>
    """
    assert not has_no_results_message(html)


def test_extracts_html_from_people_soft_ajax_xml() -> None:
    normalized = normalize_people_soft_response(_fixture("partial_response.xml"))
    assert "ps_grid-row" in normalized
    assert "CS  150" in normalized


def test_counts_people_soft_result_rows() -> None:
    assert count_result_rows(_fixture("initial_cs.html")) == 2
    assert count_result_rows(_fixture("filtered_cs.html")) == 3


def test_parses_and_deduplicates_filtered_course_rows() -> None:
    hits = parse_result_rows(
        _fixture("filtered_cs.html"),
        term="Fall 2026",
        term_code="2267",
        base_url=BASE_URL,
        expected_subject="CS",
        strict_subject=True,
    )
    assert [hit.course_code for hit in hits] == ["CS 150", "CS 160"]


def test_extracts_title_and_detail_identifiers() -> None:
    hits = parse_result_rows(
        _fixture("filtered_cs.html"),
        term="Fall 2026",
        term_code="2267",
        base_url=BASE_URL,
    )
    first = hits[0]
    assert first.title == "Introduction to Computer Programming"
    assert first.crse_id == "012345"
    assert first.crse_offer_nbr == "1"
    assert first.acad_career == "UGRD"
    assert first.class_number == "12345"
    assert first.section_count == 3
    assert first.detail_url is not None and first.detail_url.startswith("https://")


def test_non_strict_expected_subject_filters_mixed_initial_results() -> None:
    hits = parse_result_rows(
        _fixture("initial_cs.html"),
        term="Fall 2026",
        term_code="2267",
        base_url=BASE_URL,
        expected_subject="CS",
    )
    assert [hit.course_code for hit in hits] == ["CS 150"]



def test_non_strict_soc_search_can_ignore_unrelated_seg_result() -> None:
    html = """
    <ul>
      <li class="ps_grid-row psc_rowact"><p hidden>SOC 101</p></li>
      <li class="ps_grid-row psc_rowact"><p hidden>SEG 360</p></li>
    </ul>
    """
    hits = parse_result_rows(
        html,
        term="Fall 2026",
        term_code="2267",
        base_url=BASE_URL,
        expected_subject="SOC",
    )
    assert [hit.course_code for hit in hits] == ["SOC 101"]

def test_strict_expected_subject_rejects_mixed_results() -> None:
    with pytest.raises(ResultParseError, match="contained 'CSP 600'"):
        parse_result_rows(
            _fixture("initial_cs.html"),
            term="Fall 2026",
            term_code="2267",
            base_url=BASE_URL,
            expected_subject="CS",
            strict_subject=True,
        )


def test_live_class_options_suffix_is_removed_and_counted() -> None:
    html = """
    <ul>
      <li class="ps_grid-row psc_rowact">
        <p hidden>CS  150</p>
        <a class="result-title"
           href="SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?CRSE_ID=038518&amp;CLASS_NBR=3213">
          CS 150 Introductory Computer Programming 2 Class Options Available
        </a>
      </li>
    </ul>
    """
    hits = parse_result_rows(
        html,
        term="Fall 2026",
        term_code="2267",
        base_url=BASE_URL,
    )
    assert len(hits) == 1
    assert hits[0].title == "Introductory Computer Programming"
    assert hits[0].section_count == 2
