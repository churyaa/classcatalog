from __future__ import annotations

import json
from pathlib import Path

import pytest

from classcatalog.dataset.install import (
    active_term_counts,
    install_term_sections,
    plan_term_retirement,
    retire_term_sections,
)
from classcatalog.models import (
    CourseSection,
    GradingType,
    InstructionMode,
    SeatStatus,
)


def _section(
    *,
    term: str,
    term_code: str,
    course_code: str,
    schedule_number: str,
    identifier: str | None = None,
    title: str = "Test Course",
) -> CourseSection:
    subject, catalog_number = course_code.split(" ", maxsplit=1)
    return CourseSection(
        id=identifier or f"{term_code}:{course_code}:{schedule_number}",
        term=term,
        term_code=term_code,
        course_code=course_code,
        subject=subject,
        catalog_number=catalog_number,
        section_number="01",
        schedule_number=schedule_number,
        title=title,
        units=3.0,
        grading=GradingType.LETTER,
        instruction_mode=InstructionMode.IN_PERSON,
        seat_status=SeatStatus.OPEN,
        campus="San Diego Campus",
    )


def _write(path: Path, sections: tuple[CourseSection, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([section.model_dump(mode="json") for section in sections], indent=2),
        encoding="utf-8",
    )


def _read(path: Path) -> tuple[CourseSection, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(CourseSection.model_validate(item) for item in raw)


def test_install_preserves_other_terms_and_replaces_target_term(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    incoming_path = tmp_path / "spring.json"

    fall = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
    )
    old_spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
        title="Old Spring Version",
    )
    new_spring = old_spring.model_copy(update={"title": "Updated Spring Version"})
    added_spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="BIOL 100",
        schedule_number="2002",
    )

    _write(active_path, (fall, old_spring))
    _write(incoming_path, (new_spring, added_spring))

    summary = install_term_sections(incoming_path, active_path)

    assert summary.term == "Spring 2027"
    assert summary.replaced_sections == 1
    assert summary.preserved_sections == 1
    assert summary.incoming_sections == 2
    assert summary.active_sections == 3
    assert summary.active_terms == ("Fall 2026", "Spring 2027")

    active = _read(active_path)
    assert {(section.term, section.course_code) for section in active} == {
        ("Fall 2026", "CS 150"),
        ("Spring 2027", "MATH 150"),
        ("Spring 2027", "BIOL 100"),
    }
    math = next(section for section in active if section.course_code == "MATH 150")
    assert math.title == "Updated Spring Version"


def test_rescraping_same_term_replaces_instead_of_appending(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    incoming_path = tmp_path / "spring.json"

    fall = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
    )
    spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
    )
    _write(active_path, (fall, spring))
    _write(incoming_path, (spring,))

    first = install_term_sections(incoming_path, active_path)
    second = install_term_sections(incoming_path, active_path)

    assert first.active_sections == 2
    assert second.replaced_sections == 1
    assert second.active_sections == 2
    assert len(_read(active_path)) == 2


def test_install_rejects_duplicate_logical_class_listings(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    incoming_path = tmp_path / "spring.json"

    first = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
        identifier="first-id",
    )
    duplicate = first.model_copy(update={"id": "second-id", "title": "Duplicate"})
    _write(incoming_path, (first, duplicate))

    with pytest.raises(ValueError, match="Duplicate logical class listing"):
        install_term_sections(incoming_path, active_path)

    assert not active_path.exists()


def test_retirement_preview_does_not_modify_active_data(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    fall = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
    )
    spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
    )
    _write(active_path, (fall, spring))
    before = active_path.read_bytes()

    plan = plan_term_retirement(active_path, "Fall 2026")

    assert plan.removed_sections == 1
    assert plan.remaining_terms == ("Spring 2027",)
    assert active_path.read_bytes() == before


def test_retire_term_removes_only_requested_term(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    fall = _section(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        schedule_number="1001",
    )
    spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
    )
    _write(active_path, (fall, spring))

    plan = retire_term_sections(active_path, "Fall 2026")

    assert plan.removed_sections == 1
    active = _read(active_path)
    assert [section.term for section in active] == ["Spring 2027"]
    assert active_term_counts(active_path) == (("Spring 2027", 1, 1),)


def test_retiring_final_term_is_refused_by_default(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    spring = _section(
        term="Spring 2027",
        term_code="2273",
        course_code="MATH 150",
        schedule_number="2001",
    )
    _write(active_path, (spring,))

    with pytest.raises(ValueError, match="final active term"):
        retire_term_sections(active_path, "Spring 2027")

    assert len(_read(active_path)) == 1
