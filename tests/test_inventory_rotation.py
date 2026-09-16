from __future__ import annotations

import json
from pathlib import Path

import pytest

from classcatalog.dataset.lifecycle import (
    apply_inventory_rotation,
    plan_inventory_rotation,
    rotation_report_payload,
)
from classcatalog.models import CourseSection, GradingType, InstructionMode, SeatStatus


def _section(
    *,
    term: str,
    term_code: str,
    course_code: str,
    schedule_number: str,
    instructor: str = "Example Professor",
    source_url: str | None = None,
) -> CourseSection:
    subject, catalog_number = course_code.split(" ", maxsplit=1)
    return CourseSection(
        id=f"{term_code}:{course_code}:{schedule_number}",
        term=term,
        term_code=term_code,
        course_code=course_code,
        subject=subject,
        catalog_number=catalog_number,
        section_number="01",
        schedule_number=schedule_number,
        title=f"{course_code} Test Course",
        units=3.0,
        grading=GradingType.LETTER,
        instruction_mode=InstructionMode.IN_PERSON,
        seat_status=SeatStatus.OPEN,
        instructor=instructor,
        source_url=source_url,
        campus="San Diego Campus",
    )


def _write_sections(path: Path, sections: tuple[CourseSection, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([section.model_dump(mode="json") for section in sections], indent=2),
        encoding="utf-8",
    )


def _read_terms(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return sorted({str(item["term"]) for item in payload})


def test_rotation_preview_validates_candidate_without_modifying_active_data(tmp_path: Path) -> None:
    active_path = tmp_path / "sections.json"
    incoming_path = tmp_path / "fall-2027.json"
    fall_2026 = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
    )
    spring_2027 = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
    )
    fall_2027 = _section(
        term="Fall 2027",
        term_code="2277",
        course_code="CS 160",
        schedule_number="3001",
    )
    new_subject = _section(
        term="Fall 2027",
        term_code="2277",
        course_code="STAT 119",
        schedule_number="3002",
    )
    _write_sections(active_path, (fall_2026, spring_2027))
    _write_sections(incoming_path, (fall_2027, new_subject))
    before = active_path.read_bytes()

    plan = plan_inventory_rotation(
        incoming_path,
        active_path,
        "Fall 2026",
        validate_api=False,
    )

    assert active_path.read_bytes() == before
    assert plan.active_terms_before == ("Fall 2026", "Spring 2027")
    assert plan.candidate_terms == ("Spring 2027", "Fall 2027")
    assert plan.retired_sections == 1
    assert plan.incoming_sections == 2
    assert plan.candidate_sections == 3
    assert plan.coverage_status == "passed"
    assert plan.api_validation_status == "skipped"
    assert plan.subjects_added == ("STAT",)
    assert plan.courses_added == ("CS 160", "STAT 119")
    assert plan.courses_removed == ("CS 150",)


def test_rotation_apply_backs_up_active_data_and_prunes_runtime_caches(tmp_path: Path) -> None:
    active_path = tmp_path / "sections.json"
    incoming_path = tmp_path / "fall-2027.json"
    backup_path = tmp_path / "backups" / "before.json"
    seat_cache = tmp_path / "seat_cache.json"
    instructor_cache = tmp_path / "instructor_cache.json"

    fall_2026 = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
        source_url="https://cmsweb.cms.sdsu.edu/old",
    )
    spring_2027 = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
        source_url="https://cmsweb.cms.sdsu.edu/spring",
    )
    fall_2027 = _section(
        term="Fall 2027",
        term_code="2277",
        course_code="CS 160",
        schedule_number="3001",
        source_url="https://cmsweb.cms.sdsu.edu/new",
    )
    _write_sections(active_path, (fall_2026, spring_2027))
    _write_sections(incoming_path, (fall_2027,))

    seat_cache.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-09-16T00:00:00+00:00",
                "sections": {
                    "2267::1001": {"term": "2267", "schedule_number": "1001"},
                    "2273::2001": {"term": "2273", "schedule_number": "2001"},
                },
                "sources": {
                    fall_2026.source_url: {"last_success_at": "2026-09-16T00:00:00+00:00"},
                    spring_2027.source_url: {"last_success_at": "2026-09-16T00:00:00+00:00"},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    instructor_cache.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sections": {
                    "2267::1001": {
                        "term": "2267",
                        "schedule_number": "1001",
                        "instructor": "Old Professor",
                    },
                    "2273::2001": {
                        "term": "2273",
                        "schedule_number": "2001",
                        "instructor": "Spring Professor",
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = apply_inventory_rotation(
        incoming_path,
        active_path,
        "Fall 2026",
        backup_path=backup_path,
        seat_cache_path=seat_cache,
        instructor_cache_path=instructor_cache,
        validate_api=False,
    )

    assert _read_terms(active_path) == ["Fall 2027", "Spring 2027"]
    assert _read_terms(backup_path) == ["Fall 2026", "Spring 2027"]
    assert len(summary.backup_sha256) == 64
    assert summary.seat_cache.removed_records == 1
    assert summary.seat_cache.removed_sources == 1
    assert summary.instructor_cache.removed_records == 1

    seat_payload = json.loads(seat_cache.read_text(encoding="utf-8"))
    assert set(seat_payload["sections"]) == {"2273::2001"}
    assert set(seat_payload["sources"]) == {spring_2027.source_url}
    instructor_payload = json.loads(instructor_cache.read_text(encoding="utf-8"))
    assert set(instructor_payload["sections"]) == {"2273::2001"}


def test_rotation_refuses_to_retire_the_same_term_as_incoming(tmp_path: Path) -> None:
    active_path = tmp_path / "sections.json"
    incoming_path = tmp_path / "fall-2027.json"
    current = _section(
        term="Fall 2027",
        term_code="2277",
        course_code="CS 150",
        schedule_number="1001",
    )
    replacement = current.model_copy(update={"title": "Updated"})
    _write_sections(active_path, (current,))
    _write_sections(incoming_path, (replacement,))

    with pytest.raises(ValueError, match="incoming term and retired term are the same"):
        plan_inventory_rotation(
            incoming_path,
            active_path,
            "Fall 2027",
            validate_api=False,
        )


def test_rotation_report_is_json_serializable(tmp_path: Path) -> None:
    active_path = tmp_path / "sections.json"
    incoming_path = tmp_path / "fall-2027.json"
    _write_sections(
        active_path,
        (
            _section(
                term="Fall 2026",
                term_code="2267",
                course_code="CS 150",
                schedule_number="1001",
            ),
            _section(
                term="Spring 2027",
                term_code="2273",
                course_code="MATH 150",
                schedule_number="2001",
            ),
        ),
    )
    _write_sections(
        incoming_path,
        (
            _section(
                term="Fall 2027",
                term_code="2277",
                course_code="CS 160",
                schedule_number="3001",
            ),
        ),
    )
    plan = plan_inventory_rotation(
        incoming_path,
        active_path,
        "Fall 2026",
        validate_api=False,
    )
    payload = rotation_report_payload(plan)
    rendered = json.dumps(payload)
    assert "Fall 2027" in rendered
    assert "Fall 2026" in rendered
