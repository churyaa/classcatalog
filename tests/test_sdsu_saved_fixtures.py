from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from classcatalog.scraping.facet_parser import SubjectFacetNotFound, find_subject_facet
from classcatalog.scraping.parser import has_no_results_message, parse_result_rows

LIVE_ROOT = Path("fixtures/sdsu/live")

RUN_LIVE_FIXTURE_AUDIT = (
        os.getenv("CLASSCATALOG_TEST_LIVE_FIXTURES", "")
        .strip()
        .casefold()
        in {"1", "true", "yes", "on"}
)

METADATA_FILES = (
    tuple(sorted(LIVE_ROOT.glob("**/metadata.json")))
    if RUN_LIVE_FIXTURE_AUDIT
    else ()
)


@pytest.mark.skipif(
    not RUN_LIVE_FIXTURE_AUDIT,
    reason="Set CLASSCATALOG_TEST_LIVE_FIXTURES=1 to audit saved live SDSU fixtures.",
)
@pytest.mark.skipif(
    RUN_LIVE_FIXTURE_AUDIT and not METADATA_FILES,
    reason="Run the scraper with --save-fixtures first.",
    )

def test_saved_live_fixtures_remain_parseable() -> None:
    def is_search_fixture(filename: str) -> bool:
        if filename in {
            "initial.html",
            "filtered.html",
            "all-statuses.html",
        }:
            return True

        return (
            filename.startswith("fallback-")
            and (
                filename.endswith("-initial.html")
                or filename.endswith("-all-statuses.html")
                or filename.endswith("-filtered.html")
            )
        )

    for metadata_path in METADATA_FILES:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        subject = str(metadata["subject"])
        term = str(metadata["term"])
        declared_files = tuple(str(name) for name in metadata.get("files", ()))

        assert declared_files
        assert "initial.html" in declared_files

        # Every file declared by metadata must physically exist, including
        # detail shell/grouplet/course-info fixtures.
        for filename in declared_files:
            assert (metadata_path.parent / filename).is_file()

        search_files = tuple(
            filename
            for filename in declared_files
            if is_search_fixture(filename)
        )

        assert search_files, f"{subject}: no saved search-stage fixtures"

        authoritative_state_found = False

        for filename in search_files:
            fixture_path = metadata_path.parent / filename
            html = fixture_path.read_text(encoding="utf-8")

            no_results = has_no_results_message(html)

            if no_results:
                authoritative_state_found = True
                continue

            hits = parse_result_rows(
                html,
                term=term,
                term_code="0000",
                base_url="https://cmsweb.cms.sdsu.edu/",
            )

            try:
                find_subject_facet(html, subject)
                facet_matches = True
            except SubjectFacetNotFound:
                facet_matches = False

            subject_only_hits = bool(hits) and all(
                hit.subject == subject
                for hit in hits
            )

            if facet_matches or subject_only_hits:
                authoritative_state_found = True

            # A true filtered fixture gets the strongest validation.
            if (
                filename == "filtered.html"
                or filename.endswith("-filtered.html")
            ):
                filtered_hits = parse_result_rows(
                    html,
                    term=term,
                    term_code="0000",
                    base_url="https://cmsweb.cms.sdsu.edu/",
                    expected_subject=subject,
                    strict_subject=True,
                )
                assert all(
                    hit.subject == subject
                    for hit in filtered_hits
                )

        assert authoritative_state_found, (
            f"{subject}: saved search history contains no matching subject "
            "facet, subject-only result set, or authoritative no-results state"
        )
