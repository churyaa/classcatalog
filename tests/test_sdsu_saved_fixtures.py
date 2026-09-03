from __future__ import annotations

import json
from pathlib import Path

import pytest

from classcatalog.scraping.facet_parser import SubjectFacetNotFound, find_subject_facet
from classcatalog.scraping.parser import has_no_results_message, parse_result_rows

LIVE_ROOT = Path("fixtures/sdsu/live")
METADATA_FILES = tuple(sorted(LIVE_ROOT.glob("**/metadata.json")))


@pytest.mark.skipif(not METADATA_FILES, reason="Run the scraper with --save-fixtures first.")
def test_saved_live_fixtures_remain_parseable() -> None:
    for metadata_path in METADATA_FILES:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        subject = str(metadata["subject"])
        term = str(metadata["term"])
        initial_path = metadata_path.parent / "initial.html"
        filtered_path = metadata_path.parent / "filtered.html"
        assert initial_path.is_file()
        assert filtered_path.is_file()

        initial_html = initial_path.read_text(encoding="utf-8")
        filtered_html = filtered_path.read_text(encoding="utf-8")
        try:
            find_subject_facet(initial_html, subject)
        except SubjectFacetNotFound:
            initial_hits = parse_result_rows(
                initial_html,
                term=term,
                term_code="0000",
                base_url="https://cmsweb.cms.sdsu.edu/",
            )
            assert has_no_results_message(initial_html) or all(
                hit.subject == subject for hit in initial_hits
            )

        hits = parse_result_rows(
            filtered_html,
            term=term,
            term_code="0000",
            base_url="https://cmsweb.cms.sdsu.edu/",
            expected_subject=subject,
            strict_subject=True,
        )
        assert all(hit.subject == subject for hit in hits)
