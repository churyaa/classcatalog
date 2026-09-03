from __future__ import annotations

import json
from pathlib import Path

from classcatalog.api_validation.models import ApiCheckStatus
from classcatalog.api_validation.validator import (
    ApiValidationConfig,
    validate_api_data_file,
)
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


repository = CourseRepository.from_json(SAMPLE_DATA_PATH)


def _production_sized_fixture(count: int) -> list[dict[str, object]]:
    templates = repository.sections
    rows: list[dict[str, object]] = []
    for index in range(count):
        template = templates[index % len(templates)]
        units = template.units
        section = template.model_copy(
            update={
                "id": f"production-{index}",
                "term": "Fall 2026",
                "term_code": "2267",
                "course_code": f"TEST {1000 + index}",
                "subject": "TEST",
                "catalog_number": str(1000 + index),
                "section_number": f"{(index % 99) + 1:02}",
                "schedule_number": str(20000 + index),
                "crse_id": f"{index:06}",
                "crse_offer_nbr": "1",
                "acad_career": "UGRD",
                "units_min": units,
                "units_max": units,
            }
        )
        rows.append(section.model_dump(mode="json"))
    return rows


def test_api_validation_exercises_filters_and_every_page(tmp_path: Path) -> None:
    data_path = tmp_path / "sections.json"
    data_path.write_text(
        json.dumps(_production_sized_fixture(123)),
        encoding="utf-8",
    )

    result = validate_api_data_file(
        data_path,
        config=ApiValidationConfig(
            data_path=data_path,
            expected_sections=123,
            expected_courses=123,
            expected_physical_sections=123,
            expected_subjects=1,
            full_pagination=True,
        ),
    )

    assert result.exit_code == 0
    assert result.report.status == "passed"
    assert result.report.counts.course_section_listings == 123
    assert result.report.counts.pages_at_50 == 3
    assert result.report.errors == 0
    assert all(
        check.status in {ApiCheckStatus.PASS, ApiCheckStatus.SKIPPED}
        for check in result.report.checks
    )
    by_name = {check.name: check for check in result.report.checks}
    assert by_name["full_pagination"].details["listings"] == 123
    assert by_name["program_filters"].status is ApiCheckStatus.PASS
    assert result.report.performance.requests >= 20


def test_api_validation_reports_expected_count_mismatch(tmp_path: Path) -> None:
    data_path = tmp_path / "sections.json"
    data_path.write_text(
        json.dumps(_production_sized_fixture(3)),
        encoding="utf-8",
    )

    result = validate_api_data_file(
        data_path,
        config=ApiValidationConfig(
            data_path=data_path,
            expected_sections=4,
            full_pagination=False,
        ),
    )

    assert result.exit_code == 1
    assert result.report.status == "failed"
    assert result.report.errors == 1
    expected_check = next(
        check for check in result.report.checks if check.name == "expected_counts"
    )
    assert expected_check.status is ApiCheckStatus.FAIL
