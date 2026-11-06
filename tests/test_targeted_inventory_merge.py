"""Offline targeted course replacement and safety regression tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from classcatalog.dataset.targeted_sync import load_keys, merge, offering_key, plan
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH

KEY = "CS|038524|1|UGRD"


def _fixture(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    base = dict(term="Spring 2027", term_code="2273", subject="CS",
                course_code="CS 250", catalog_number="250", crse_id="038524",
                crse_offer_nbr="1", acad_career="UGRD", seat_updated_at=None)
    old = tuple(sample.model_copy(update=dict(base, id=f"old-{n}", schedule_number=n))
                for n in ("8480", "10095", "11623"))
    new = tuple(sample.model_copy(update=dict(base, id=f"new-{n}", schedule_number=n))
                for n in ("11623", "12262"))
    summer = sample.model_copy(update={
        "id": "summer-keep", "term": "Summer 2027", "term_code": "2275",
        "schedule_number": "57001", "course_code": "MATH 151", "subject": "MATH",
        "catalog_number": "151",
    })
    active = tmp_path / "sections.json"
    active.write_text(json.dumps([s.model_dump(mode="json") for s in (*old, summer)]), encoding="utf-8")
    production = tmp_path / "one-course-production"
    production.mkdir()
    incoming = production / "sections.json"
    incoming.write_text(json.dumps([s.model_dump(mode="json") for s in new]), encoding="utf-8")
    (production / "validation-report.json").write_text(
        json.dumps({"status": "passed", "term": "Spring 2027"}), encoding="utf-8"
    )
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps([KEY]), encoding="utf-8")
    return active, incoming, keys, summer


def test_plan_only_replaces_requested_course_and_retains_other_semesters(tmp_path: Path):
    active, incoming, keys, summer = _fixture(tmp_path)
    old_hash = active.read_bytes()
    preview = merge(active_path=active, incoming_path=incoming, keys_path=keys, term="Spring 2027")
    assert preview["applied"] is False
    assert preview["removed_physical_classes"] == ["2273::10095", "2273::8480"]
    assert preview["added_physical_classes"] == ["2273::12262"]
    assert active.read_bytes() == old_hash
    result = merge(active_path=active, incoming_path=incoming, keys_path=keys,
                   term="Spring 2027", apply=True)
    assert result["applied"] is True
    assert Path(result["backup_dir"]).exists()
    sections = json.loads(active.read_text())
    assert {s["schedule_number"] for s in sections if s["term"] == "Spring 2027"} == {"11623", "12262"}
    assert any(s["id"] == summer.id for s in sections)
    assert len(json.loads((Path(result["backup_dir"]) / "sections.json").read_text())) == 4


def test_targets_dont_match_entire_term_or_extra_course(tmp_path: Path):
    active, incoming, keys, _ = _fixture(tmp_path)
    # The validated incoming build explicitly targets Spring 2027.
    # Reject this wrong-term invocation at the earliest report guard.
    with pytest.raises(ValueError, match="passing validation report"):
        merge(active_path=active, incoming_path=incoming, keys_path=keys, term="Fall 2026")
    incoming_sections = json.loads(incoming.read_text())
    incoming_sections[0]["crse_id"] = "OTHER"
    incoming.write_text(json.dumps(incoming_sections))
    with pytest.raises(ValueError, match="Incoming courses do not exactly match"):
        merge(active_path=active, incoming_path=incoming, keys_path=keys, term="Spring 2027")


def test_missing_course_key_is_never_silently_erased(tmp_path: Path):
    active, incoming, keys, _ = _fixture(tmp_path)
    keys.write_text(json.dumps([KEY, "CS|999999|1|UGRD"]))
    with pytest.raises(ValueError, match="do not exactly match requested"):
        merge(active_path=active, incoming_path=incoming, keys_path=keys, term="Spring 2027")


def test_invalid_build_report_is_rejected(tmp_path: Path):
    active, incoming, keys, _ = _fixture(tmp_path)
    (incoming.parent / "validation-report.json").write_text(json.dumps({"status": "failed", "term": "Spring 2027"}))
    with pytest.raises(ValueError, match="passing validation"):
        merge(active_path=active, incoming_path=incoming, keys_path=keys, term="Spring 2027")


def test_hidden_overlay_is_cleared_after_validated_targeted_replace(tmp_path: Path):
    active, incoming, keys, _ = _fixture(tmp_path)
    hidden = tmp_path / "hidden_classes.json"
    hidden.write_text(json.dumps({"version": 1, "hidden": [{
        "key": "2273::8480", "section_ids": ["old-8480"],
        "class_number": "8480", "course_code": "CS 250", "term": "Spring 2027",
        "crse_id": "038524", "hidden_at": "2026-11-06T00:00:00+00:00",
        "verified_at": "2026-11-06T00:00:00+00:00"
    }]}))
    report = merge(active_path=active, incoming_path=incoming, keys_path=keys,
                   term="Spring 2027", apply=True)
    assert report["existing_hidden_overlays_to_clear"] == ["2273::8480"]
    assert json.loads(hidden.read_text())["hidden"] == []
    assert len(json.loads((Path(report["backup_dir"]) / "hidden_classes.json").read_text())["hidden"]) == 1
