from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from classcatalog.scraping.main import _build_parser, _parse_subjects, _subject_inventory_document
from classcatalog.scraping.parser import parse_result_rows, parse_term_codes
from classcatalog.scraping.session import PeopleSoftSessionError, SdsuHttpConfig, SdsuPeopleSoftSession


def _response(url: str, body: str, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")  # noqa: SLF001 - test fixture
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


def test_scrape_term_has_no_stale_semester_default() -> None:
    args = _build_parser().parse_args([])
    assert args.term is None


def test_live_subjects_extend_the_static_all_subject_seed() -> None:
    selected = _parse_subjects(
        [],
        all_subjects=True,
        discovered_subjects=("CS", "NEW"),
    )
    assert "CS" in selected
    assert "NEW" in selected


def test_explicit_new_subject_is_allowed_when_live_discovery_verified_it() -> None:
    assert _parse_subjects(
        ["NEW"],
        all_subjects=False,
        discovered_subjects=("NEW",),
    ) == ("NEW",)


def test_subject_inventory_document_reports_new_live_codes_without_calling_them_removed() -> None:
    payload = _subject_inventory_document(
        term="Fall 2027",
        term_code="2277",
        discovered_subjects=("CS", "NEW"),
    )
    assert payload["new_subjects"] == ["NEW"]
    assert "CS" in payload["effective_subjects"]
    assert "NEW" in payload["effective_subjects"]
    assert "seed_subjects_not_observed" in payload


def test_parser_accepts_verified_expected_subject_not_in_static_seed() -> None:
    html = """
    <ul>
      <li class="ps_grid-row psc_rowact">
        <p hidden>NEW 101</p>
        <a class="result-title">NEW 101 New Subject Course</a>
      </li>
    </ul>
    """
    hits = parse_result_rows(
        html,
        term="Fall 2027",
        term_code="2277",
        base_url="https://example.test/results",
        expected_subject="NEW",
        strict_subject=True,
    )
    assert [hit.course_code for hit in hits] == ["NEW 101"]


def test_term_parser_ignores_ambiguous_nearby_codes_in_fallback_markup() -> None:
    html = """
    <div>
      Fall 2027 javascript:setTerm('2277');
      Spring 2028 javascript:setTerm('2283');
    </div>
    """
    assert parse_term_codes(html) == {}


def test_confirmed_term_conflict_fails_closed_until_explicit_code_is_supplied() -> None:
    landing = """
    <select>
      <option value="2272">Spring 2027</option>
    </select>
    """
    fake = FakeSession([_response("https://example.test/landing", landing)])
    client = SdsuPeopleSoftSession(
        SdsuHttpConfig(
            landing_url="https://example.test/landing",
            results_url="https://example.test/results",
            delay_seconds=0,
            max_retries=0,
        ),
        http_session=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(PeopleSoftSessionError, match="conflicts with the confirmed"):
        client.resolve_term_code("Spring 2027")
    assert client.resolve_term_code("Spring 2027", "2273") == "2273"


def test_live_subject_discovery_reads_subject_facet_codes() -> None:
    landing = "<html><body>landing</body></html>"
    results = """
    <html><body>
      <form id="win0">
        <fieldset>
          <legend>Subject</legend>
          <input type="checkbox" id="PTS_SELECT$5" name="PTS_SELECT$5" value="Y" />
          <label class="ps-label" for="PTS_SELECT$5">CS/Computer Science</label>
          <input type="checkbox" id="PTS_SELECT$6" name="PTS_SELECT$6" value="Y" />
          <label class="ps-label" for="PTS_SELECT$6">NEW/New Studies</label>
        </fieldset>
      </form>
    </body></html>
    """
    fake = FakeSession(
        [
            _response("https://example.test/landing", landing),
            _response("https://example.test/results", results),
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
    assert client.discover_subjects(term_code="2277", probes=("N",)) == ("CS", "NEW")


def test_dataset_builder_output_directory_follows_checkpoint_term(tmp_path: Path) -> None:
    import json
    from argparse import Namespace

    from classcatalog.dataset.main import _resolve_output_dir

    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"term": "Fall 2027"}), encoding="utf-8")
    args = Namespace(
        output_dir=None,
        checkpoint=checkpoint,
        deep_run=None,
        discovery_run=None,
        subject_output_dir=None,
    )
    assert _resolve_output_dir(args) == Path("results/fall-2027-production")
