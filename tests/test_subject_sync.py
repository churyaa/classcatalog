"""Regression tests for CS-style subject sync (place in tests/)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from classcatalog.dataset.subject_sync import load_subjects, merge, plan
from classcatalog.models import Meeting
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def fixture(tmp_path: Path):
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    common = dict(
        term="Spring 2027", term_code="2273", subject="CS", course_code="CS 250",
        catalog_number="250", crse_id="038524", acad_career="UGRD",
        option_group_ids=(), option_primary_group_ids=(),
        option_group_indices=(), option_primary_group_indices=(), linked_components=(),
        professor=None, seat_updated_at=None,
    )
    old100 = sample.model_copy(update={
        **common, "id": "cs-old-100", "schedule_number": "100", "crse_offer_nbr": "1",
        "source_course_key": "CS|038524|1|UGRD", "section_number": "01",
        "instructor": "Example Instructor", "meetings": (Meeting(meeting_dates="01/21/2027 - 05/05/2027"),),
    })
    old200 = sample.model_copy(update={
        **common, "id": "cs-old-200", "schedule_number": "200", "crse_offer_nbr": "1",
        "source_course_key": "CS|038524|1|UGRD", "section_number": "02",
    })
    new100 = sample.model_copy(update={
        **common, "id": "cs-new-100", "schedule_number": "100", "crse_offer_nbr": "2",
        "source_course_key": "CS|038524|2|UGRD", "section_number": "",
        "instructor": None, "meetings": (),
    })
    new300 = sample.model_copy(update={
        **common, "id": "cs-new-300", "schedule_number": "300", "crse_offer_nbr": "2",
        "source_course_key": "CS|038524|2|UGRD", "section_number": "03",
    })
    other = sample.model_copy(update={
        **common, "id": "math-keep", "subject": "MATH", "course_code": "MATH 151",
        "catalog_number": "151", "schedule_number": "401",
    })
    summer = sample.model_copy(update={
        **common, "id": "summer-keep", "subject": "CS", "schedule_number": "501",
        "term": "Summer 2027", "term_code": "2275",
    })
    active = tmp_path / "sections.json"
    active.write_text(json.dumps([r.model_dump(mode="json") for r in (old100, old200, other, summer)]))
    built = tmp_path / "built"
    built.mkdir()
    incoming = built / "sections.json"
    incoming.write_text(json.dumps([r.model_dump(mode="json") for r in (new100, new300)]))
    (built / "validation-report.json").write_text(json.dumps({"status": "passed", "term": "Spring 2027"}))
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({"subjects": [{"subject": "CS", "status": "complete"}]}))
    return active, incoming, checkpoint


def test_dry_run_preserves_baseline_and_outside_scope(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    before = active.read_bytes()
    report = merge(active_path=active, incoming_path=incoming, checkpoint_path=checkpoint,
                   term="Spring 2027", expected_subjects=1)
    assert report["applied"] is False
    assert report["retained_physical_classes"] == 1
    assert report["added_physical_classes"] == 1
    assert report["removed_physical_classes"] == 1
    assert report["stable_ids_preserved"] == 1
    assert report["metadata_carried_forward"] == {
        "instructor": 1, "meetings": 1, "section_number": 1,
    }
    assert active.read_bytes() == before


def test_apply_requires_exact_hash_and_explicit_removal_limit(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    params = dict(active_path=active, incoming_path=incoming,
                  checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    report = merge(**params)
    with pytest.raises(ValueError, match="expected-active-sha256"):
        merge(**params, apply=True)
    with pytest.raises(ValueError, match="max-removals"):
        merge(**params, apply=True, expected_active_sha256=report["active_sha256_before"])
    result = merge(**params, apply=True,
                   expected_active_sha256=report["active_sha256_before"], max_removals=1)
    assert result["applied"] is True
    assert (Path(result["backup_dir"]) / "sections.json").is_file()
    rows = json.loads(active.read_text())
    assert {s["id"] for s in rows} == {"cs-old-100", "cs-new-300", "math-keep", "summer-keep"}
    retained = next(s for s in rows if s["schedule_number"] == "100")
    assert retained["crse_offer_nbr"] == "2"
    assert retained["source_course_key"] == "CS|038524|2|UGRD"
    assert retained["instructor"] == "Example Instructor"
    assert retained["meetings"]
    assert retained["section_number"] == "01"


def test_preview_reports_reused_class_number_and_blocks_apply(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["course_code"] = "CS 999"
    rows[0]["acad_career"] = "GRAD"
    incoming.write_text(json.dumps(rows))
    kwargs = dict(active_path=active, incoming_path=incoming,
                  checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    before = active.read_bytes()
    preview = merge(**kwargs)
    assert preview["applied"] is False
    assert preview["identity_conflict_count"] == 1
    assert preview["identity_conflicts"][0]["schedule_number"] == "100"
    assert preview["identity_conflicts"][0]["before"]["acad_career"] == "UGRD"
    assert preview["identity_conflicts"][0]["after"]["acad_career"] == "GRAD"
    assert preview["stable_ids_preserved"] == 0
    assert preview["metadata_carried_forward"] == {
        "instructor": 0, "meetings": 0, "section_number": 0,
    }
    assert active.read_bytes() == before
    with pytest.raises(ValueError, match="identity conflicts need manual review"):
        merge(**kwargs, apply=True,
              expected_active_sha256=preview["active_sha256_before"],
              max_removals=1)
    assert active.read_bytes() == before


def test_academic_career_change_alone_is_reported(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["acad_career"] = "GLBL"
    incoming.write_text(json.dumps(rows))
    report = merge(active_path=active, incoming_path=incoming,
                   checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    assert report["identity_conflict_count"] == 1
    assert report["identity_conflicts"][0]["before"]["course_code"] == "CS 250"
    assert report["identity_conflicts"][0]["after"]["course_code"] == "CS 250"
    assert report["identity_conflicts"][0]["before"]["acad_career"] == "UGRD"
    assert report["identity_conflicts"][0]["after"]["acad_career"] == "GLBL"


def test_subject_checkpoint_must_be_complete(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    checkpoint.write_text(json.dumps({"subjects": [{"subject": "CS", "status": "partial"}]}))
    with pytest.raises(ValueError, match="Incomplete subject"):
        load_subjects(checkpoint, 1)


def test_course_id_reuse_is_a_conflict_even_if_course_code_matches(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["crse_id"] = "999999"
    incoming.write_text(json.dumps(rows))
    preview = merge(active_path=active, incoming_path=incoming,
                    checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    assert preview["identity_conflict_count"] == 1
    assert preview["unapproved_identity_conflict_count"] == 1
    assert preview["stable_ids_preserved"] == 0
    assert any(item["schedule_number"] == "100" for item in preview["metadata_regression_details"])


def test_career_rekey_approval_exact_identity_and_preserves_opaque_id(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["acad_career"] = "GLBL"
    rows[0]["source_course_key"] = "CS|038524|2|GLBL"
    incoming.write_text(json.dumps(rows))
    args = dict(active_path=active, incoming_path=incoming,
                checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    preview = merge(**args)
    assert preview["identity_conflict_count"] == 1
    assert preview["unapproved_identity_conflict_count"] == 1
    conflict = preview["identity_conflicts"][0]
    approvals_path = tmp_path / "reviewed.json"
    approvals_path.write_text(json.dumps([{**conflict, "review_note": "Verified the same physical course and career change manually", "restore_historical_section_number": False}]))
    approved = merge(**args, approved_rekeys_path=approvals_path)
    assert approved["identity_conflict_count"] == 1
    assert approved["approved_identity_rekey_count"] == 1
    assert approved["unapproved_identity_conflict_count"] == 0
    assert approved["stable_ids_preserved"] == 1
    # A changed career is not a license to reuse old meeting metadata.
    assert approved["metadata_carried_forward"]["meetings"] == 0
    assert approved["metadata_regressions_remaining"]["meetings"] == 1
    with pytest.raises(ValueError, match="Unresolved missing metadata"):
        merge(**args, approved_rekeys_path=approvals_path, apply=True,
              expected_active_sha256=approved["active_sha256_before"], max_removals=1)
    staged = merge(**args, approved_rekeys_path=approvals_path, apply=True,
                   expected_active_sha256=approved["active_sha256_before"], max_removals=1,
                   max_metadata_regressions=3)
    assert staged["applied"] is True
    rows = json.loads(active.read_text())
    retained = next(r for r in rows if r["schedule_number"] == "100")
    assert retained["id"] == "cs-old-100"
    assert retained["acad_career"] == "GLBL"
    assert retained["source_course_key"] == "CS|038524|2|GLBL"
    assert retained["instructor"] is None
    assert not retained["meetings"]


def test_approval_refuses_wildcards_mismatch_or_unreviewed(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["acad_career"] = "GLBL"
    incoming.write_text(json.dumps(rows))
    args = dict(active_path=active, incoming_path=incoming,
                checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    preview = merge(**args)
    conflict = preview["identity_conflicts"][0]
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps([{**conflict, "review_note": "UNREVIEWED", "restore_historical_section_number": False}]))
    with pytest.raises(ValueError, match="UNREVIEWED"):
        merge(**args, approved_rekeys_path=path)
    bad = {**conflict, "review_note": "Verified the physical class number and source records", "restore_historical_section_number": False}
    bad["before"] = {**bad["before"], "crse_id": "wrong"}
    path.write_text(json.dumps([bad]))
    with pytest.raises(ValueError, match="Approval does not match"):
        merge(**args, approved_rekeys_path=path)


def test_approval_does_not_waive_changed_crse_id(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["crse_id"] = "999999"
    rows[0]["acad_career"] = "GLBL"
    incoming.write_text(json.dumps(rows))
    args = dict(active_path=active, incoming_path=incoming,
                checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    preview = merge(**args)
    conflict = preview["identity_conflicts"][0]
    path = tmp_path / "reviewed.json"
    path.write_text(json.dumps([{**conflict, "review_note": "Reviewed this class using saved PeopleSoft documents", "restore_historical_section_number": False}]))
    with pytest.raises(ValueError, match="cannot waive"):
        merge(**args, approved_rekeys_path=path)


def test_explicit_reviewed_section_number_carry_on_career_change(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["acad_career"] = "GLBL"
    rows[0]["source_course_key"] = "CS|038524|2|GLBL"
    incoming.write_text(json.dumps(rows))
    args = dict(active_path=active, incoming_path=incoming,
                checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    conflict = merge(**args)["identity_conflicts"][0]
    approval = {**conflict, "review_note": "Verified same class and historical section label in source records",
                "restore_historical_section_number": True}
    approvals_path = tmp_path / "reviewed.json"
    approvals_path.write_text(json.dumps([approval]))
    preview = merge(**args, approved_rekeys_path=approvals_path)
    assert preview["approved_identity_rekey_count"] == 1
    assert preview["stable_ids_preserved"] == 1
    assert preview["reviewed_career_rekey_section_numbers_carried"] == 1
    assert preview["metadata_regressions_remaining"] == {"instructor": 1, "meetings": 1}
    assert preview["metadata_carried_forward"]["section_number"] == 0
    # Staging still blocked because instructor and meetings must NOT be copied across careers.
    with pytest.raises(ValueError, match="Unresolved missing metadata"):
        merge(**args, approved_rekeys_path=approvals_path, apply=True,
              expected_active_sha256=preview["active_sha256_before"], max_removals=1)
    result = merge(**args, approved_rekeys_path=approvals_path, apply=True,
                   expected_active_sha256=preview["active_sha256_before"],
                   max_removals=1, max_metadata_regressions=2)
    assert result["applied"] is True
    retained = next(r for r in json.loads(active.read_text()) if r["schedule_number"] == "100")
    assert retained["id"] == "cs-old-100"
    assert retained["section_number"] == "01"
    assert retained["acad_career"] == "GLBL"
    assert retained["instructor"] is None and not retained["meetings"]


def test_restoring_historical_section_number_requires_valid_old_and_new(tmp_path: Path):
    active, incoming, checkpoint = fixture(tmp_path)
    rows = json.loads(incoming.read_text())
    rows[0]["acad_career"] = "GLBL"
    rows[0]["section_number"] = "10"
    incoming.write_text(json.dumps(rows))
    args = dict(active_path=active, incoming_path=incoming,
                checkpoint_path=checkpoint, term="Spring 2027", expected_subjects=1)
    conflict = merge(**args)["identity_conflicts"][0]
    approvals_path = tmp_path / "reviewed.json"
    approvals_path.write_text(json.dumps([{**conflict,
        "review_note": "Reviewed same career change and source section numbers",
        "restore_historical_section_number": True}]))
    with pytest.raises(ValueError, match="Historical section number can only be restored"):
        merge(**args, approved_rekeys_path=approvals_path)
