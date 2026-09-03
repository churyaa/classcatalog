from __future__ import annotations

from pathlib import Path

import pytest

from classcatalog.scraping.main import (
    _atomic_write,
    _parse_subjects,
    _resume_slice,
    _scrape_exit_code,
    _write_run,
)


def test_cli_safe_default_is_cs_only() -> None:
    assert _parse_subjects([], all_subjects=False) == ("CS",)


def test_cli_parses_comma_separated_and_multiword_subjects() -> None:
    assert _parse_subjects(["CS,MATH", "CIV E"], all_subjects=False) == (
        "CS",
        "MATH",
        "CIV E",
    )



def test_cli_maps_legacy_segs_to_current_seg() -> None:
    assert _parse_subjects(["SEGS", "SOC"], all_subjects=False) == ("SEG", "SOC")

def test_cli_rejects_unknown_subject() -> None:
    with pytest.raises(ValueError, match="NOTREAL"):
        _parse_subjects(["NOTREAL"], all_subjects=False)


def test_resume_slice_starts_at_named_subject() -> None:
    assert _resume_slice(("CS", "MATH", "STAT"), "MATH") == ("MATH", "STAT")


def test_atomic_write_replaces_target_without_leaving_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "results.json"
    _atomic_write(target, '{"ok": true}')
    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert not target.with_suffix(".json.tmp").exists()


def test_run_output_persists_detail_completion_accounting(tmp_path: Path) -> None:
    import json

    target = tmp_path / "results.json"
    _write_run(
        target,
        started_at="2026-08-17T00:00:00+00:00",
        completed_at="2026-08-17T00:05:00+00:00",
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS",),
        completed_subjects=["CS"],
        results=[],
        errors=[],
        detail_courses_attempted=10,
        detail_courses_complete=9,
        detail_courses_partial=1,
        detail_warnings=2,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["detail_courses_attempted"] == 10
    assert payload["detail_courses_complete"] == 9
    assert payload["detail_courses_partial"] == 1
    assert payload["detail_warnings"] == 2


def test_cli_defaults_use_conservative_people_soft_throttling() -> None:
    from classcatalog.scraping.main import _build_parser

    args = _build_parser().parse_args([])

    assert args.delay == 2.0
    assert args.jitter == 0.75
    assert args.max_retries == 4
    assert args.post_recovery_cooldown == 20.0
    assert args.stateful_post_recovery_attempts == 1


def test_scrape_exit_code_is_nonzero_for_incomplete_subject() -> None:
    from classcatalog.scraping.models import SubjectScrapeResult

    incomplete = SubjectScrapeResult(
        term="Fall 2026",
        term_code="2267",
        subject="MUSIC",
        fetched_at="2026-08-19T00:00:00+00:00",
        search_url="https://example.test/results",
        initial_result_count=75,
        filtered_result_count=75,
        exact_facet_applied=True,
        initial_result_cap_warning=True,
        filtered_result_cap_warning=True,
        complete=False,
        partitioned=True,
        partition_facets=("Course Career", "Class Component"),
        partition_leaf_count=1,
        unresolved_partition_count=1,
    )

    assert _scrape_exit_code([incomplete], []) == 1


def test_scrape_exit_code_is_zero_for_complete_subjects_without_errors() -> None:
    from classcatalog.scraping.models import SubjectScrapeResult

    complete = SubjectScrapeResult(
        term="Fall 2026",
        term_code="2267",
        subject="ENS",
        fetched_at="2026-08-19T00:00:00+00:00",
        search_url="https://example.test/results",
        initial_result_count=75,
        filtered_result_count=75,
        exact_facet_applied=True,
        initial_result_cap_warning=True,
        filtered_result_cap_warning=True,
        complete=True,
        partitioned=True,
        partition_facets=("Course Career",),
        partition_leaf_count=2,
        unresolved_partition_count=0,
    )

    assert _scrape_exit_code([complete], []) == 0
