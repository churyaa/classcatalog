from __future__ import annotations

import json
from pathlib import Path

from classcatalog.catalog.coverage import (
    AcademicPlan,
    load_expected_programs,
    normalize_program_name,
    validate_program_coverage,
)


def test_normalize_program_name_handles_degree_punctuation_and_ampersand() -> None:
    assert normalize_program_name("English & Comparative Literature, B.A.") == (
        "english and comparative literature ba"
    )


def test_coverage_matches_dropdown_name_when_degree_is_omitted() -> None:
    report = validate_program_coverage(
        [AcademicPlan(plan_code="773801", name="COMPUTER SCIENCE")],
        ["Computer Science, B.S."],
    )
    assert report.matched_count == 1
    assert report.matches[0].method == "unique_without_degree"
    assert report.missing == ()


def test_coverage_preserves_explicit_degree_when_multiple_degree_variants_exist() -> None:
    report = validate_program_coverage(
        [AcademicPlan(plan_code="770501", name="ASTRONOMY - BA")],
        ["Astronomy, B.A.", "Astronomy, B.S."],
    )
    assert report.matched_count == 1
    assert report.matches[0].catalog_name == "Astronomy, B.A."


def test_coverage_supports_explicit_catalog_aliases() -> None:
    report = validate_program_coverage(
        [
            AcademicPlan(
                plan_code="112204",
                name="WRITING AND RHETORIC",
                catalog_names=("Rhetoric and Writing Studies, B.A.",),
            )
        ],
        ["Rhetoric and Writing Studies, B.A."],
    )
    assert report.matched_count == 1
    assert report.unexpected == ()


def test_coverage_reports_ambiguous_degree_less_name() -> None:
    report = validate_program_coverage(
        [AcademicPlan(plan_code="1", name="EXAMPLE")],
        ["Example, B.A.", "Example, B.S."],
    )
    assert report.matched_count == 0
    assert len(report.ambiguous) == 1


def test_load_expected_programs_reads_registry_object(tmp_path: Path) -> None:
    path = tmp_path / "programs.json"
    path.write_text(
        json.dumps(
            {
                "catalog_year": "2026-2027",
                "plans": [
                    {
                        "plan_code": "441001",
                        "name": "AEROSPACE ENGINEERING",
                        "catalog_names": ["Aerospace Engineering, B.S."],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plans = load_expected_programs(path)
    assert plans == (
        AcademicPlan(
            plan_code="441001",
            name="AEROSPACE ENGINEERING",
            catalog_names=("Aerospace Engineering, B.S.",),
        ),
    )


def test_coverage_reports_raw_pages_and_unique_titles_separately() -> None:
    report = validate_program_coverage(
        [AcademicPlan(plan_code="1", name="EXAMPLE")],
        ["Example, B.A.", "Example, B.A."],
    )
    assert report.scraped_count == 2
    assert report.unique_scraped_count == 1
    assert report.matched_catalog_count == 1
