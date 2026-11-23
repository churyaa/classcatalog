"""Tests for update pipeline's local integrity and explicit review gates."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "classcatalog_update_checks.py"
spec = importlib.util.spec_from_file_location("classcatalog_update_checks", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(tmp_path):
    old = [
        {"term_code": "2273", "term": "Spring 2027", "schedule_number": "111", "course_code": "CS 101", "crse_id": "1", "acad_career": "UGRD", "section_number": "01"},
        {"term_code": "2273", "term": "Spring 2027", "schedule_number": "222", "course_code": "CS 101", "crse_id": "1", "acad_career": "UGRD", "section_number": "02"},
        {"term_code": "2265", "term": "Fall 2026", "schedule_number": "333", "course_code": "MATH 101"},
    ]
    incoming = [old[1], {"term_code": "2273", "term": "Spring 2027", "schedule_number": "444", "course_code": "CS 101"}]
    candidate = [old[1], old[2], incoming[1]]
    for name, rows in (("baseline", old), ("incoming", incoming), ("candidate", candidate)):
        (tmp_path / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    report = {
        "applied": False,
        "active_sha256_before": module.sha256(tmp_path / "baseline.json"),
        "incoming_sha256": module.sha256(tmp_path / "incoming.json"),
        "original_sections": 3,
        "candidate_sections": 3,
        "removed_physical_classes": 1,
        "added_physical_classes": 1,
        "removed_list": ["2273::111"],
        "identity_conflict_count": 0,
        "unapproved_identity_conflict_count": 0,
        "metadata_regressions_remaining": 0,
        "metadata_regression_details": [],
    }
    (tmp_path / "preview.json").write_text(json.dumps(report), encoding="utf-8")
    return [str(tmp_path / f"{name}.json") for name in ("baseline", "incoming", "candidate", "preview")]


def test_preview_and_review(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    review = str(tmp_path / "removals.csv")
    assert module.make_review(baseline, incoming, preview, review) == 1
    assert module.verify_candidate(baseline, incoming, candidate, preview, review, "Spring 2027")["candidate_sections"] == 3


def test_removal_approval_gate(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    review = str(tmp_path / "removals.csv")
    module.make_review(baseline, incoming, preview, review)
    with pytest.raises(ValueError, match="unapproved"):
        module.verify_candidate(baseline, incoming, candidate, preview, review, "Spring 2027", True)
    text = Path(review).read_text(encoding="utf-8").replace("PENDING", "APPROVE")
    Path(review).write_text(text, encoding="utf-8")
    assert module.verify_candidate(baseline, incoming, candidate, preview, review, "Spring 2027", True)["approved"]


def test_no_cross_term_loss(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    rows = module.read_json(candidate)
    rows[1]["course_code"] = "BROKEN 999"
    Path(candidate).write_text(json.dumps(rows), encoding="utf-8")
    module.make_review(baseline, incoming, preview, str(tmp_path / "review.csv"))
    with pytest.raises(ValueError, match="non-target"):
        module.verify_candidate(baseline, incoming, candidate, preview, str(tmp_path / "review.csv"), "Spring 2027")


def test_reject_present_removed_section(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    incoming_rows = module.read_json(incoming)
    incoming_rows.append(module.read_json(baseline)[0])
    Path(incoming).write_text(json.dumps(incoming_rows), encoding="utf-8")
    with pytest.raises(ValueError, match="incoming hash"):
        module.make_review(baseline, incoming, preview, str(tmp_path / "review.csv"))


def test_reject_tampered_baseline(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    Path(baseline).write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline hash"):
        module.validate_preview(module.read_json(preview), baseline, incoming)


def test_duplicate_section(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        section = {"term_code": "2273", "schedule_number": "1"}
        module.identity_map([section, section])


def test_reject_wrong_removal_set(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    review = str(tmp_path / "removals.csv")
    module.make_review(baseline, incoming, preview, review)
    text = Path(review).read_text(encoding="utf-8").replace("111", "999").replace("PENDING", "APPROVE")
    Path(review).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        module.verify_candidate(baseline, incoming, candidate, preview, review, "Spring 2027", True)


def test_no_unapproved_identity_conflicts(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    report = module.read_json(preview)
    report["identity_conflict_count"] = 1
    Path(preview).write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="identity conflicts"):
        module.validate_preview(report, baseline, incoming)


def test_reject_unexpected_removal(tmp_path):
    baseline, incoming, candidate, preview = sample(tmp_path)
    rows = module.read_json(candidate)
    rows = [s for s in rows if s["schedule_number"] != "222"]
    Path(candidate).write_text(json.dumps(rows), encoding="utf-8")
    module.make_review(baseline, incoming, preview, str(tmp_path / "review.csv"))
    with pytest.raises(ValueError, match="counts differ"):
        module.verify_candidate(baseline, incoming, candidate, preview, str(tmp_path / "review.csv"), "Spring 2027")
