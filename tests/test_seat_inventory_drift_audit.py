"""Read-only inventory audit tests: never use mismatched numbers as replacements."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts.audit_seat_inventory_drift import (
    TARGETS, assess_detail, assess_hits, build_report, inventory, safe_public_detail_url,
)


def _data(tmp_path):
    p = tmp_path / "active.json"
    p.write_text(json.dumps([
        {
            "term": "Spring 2027", "term_code": "2273", "subject": "CFD",
            "course_code": "CFD 278", "crse_id": "033748", "crse_offer_nbr": "1",
            "acad_career": "UGRD", "schedule_number": "4267",
            "source_url": "https://example.test/saved",
        },
        {
            "term": "Spring 2027", "term_code": "2273", "subject": "ECL",
            "course_code": "ECL 503", "crse_id": "037828", "crse_offer_nbr": "1",
            "acad_career": "UGRD", "schedule_number": "11264",
        },
        {
            "term": "Spring 2027", "term_code": "2273", "subject": "ECL",
            "course_code": "ECL 503", "crse_id": "037828", "crse_offer_nbr": "1",
            "acad_career": "UGRD", "schedule_number": "11309",
        },
        {"term": "Fall 2026", "course_code": "CFD 278", "schedule_number": "99999"},
    ]), encoding="utf-8")
    return p


def test_default_offline_report_never_modifies_inventory(tmp_path):
    data = _data(tmp_path)
    before = data.read_bytes()
    report = build_report(data, term="Spring 2027", courses=TARGETS, live=False)
    assert report["auto_modifications"] is False
    assert report["mode"] == "offline_inventory_only"
    assert report["entries"][1]["course_code"] == "CFD 278"
    ecl = next(item for item in report["entries"] if item["course_code"] == "ECL 503")
    assert ecl["expected_class_numbers"] == ["11264", "11309"]
    assert data.read_bytes() == before


def test_exact_stable_identity_required_for_detail(tmp_path):
    item = next(x for x in inventory(_data(tmp_path)) if x["course_code"] == "CFD 278")
    def result(crse_id="033748", offer="1", complete=True):
        return SimpleNamespace(
            term="Spring 2027", term_code="2273", subject="CFD", complete=complete,
            courses=(SimpleNamespace(
                course_code="CFD 278", subject="CFD", term="Spring 2027", term_code="2273",
                crse_id=crse_id, crse_offer_nbr=offer, acad_career="UGRD",
                detail_url="https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL?TEST=1"
            ),)
        )
    assert assess_hits(item, result())[0] == "unique_identity_found"
    assert assess_hits(item, result(crse_id="WRONG"))[0] == "identity_not_found_unverified"
    assert assess_hits(item, result(complete=False))[0] == "incomplete_subject_unverified"


def test_mismatched_detail_requires_manual_review(tmp_path):
    item = next(x for x in inventory(_data(tmp_path)) if x["course_code"] == "CFD 278")
    parsed = SimpleNamespace(
        course_code="CFD 278", term="Spring 2027", term_code="2273",
        options_complete=True, options=(SimpleNamespace(class_number="12410"),),
    )
    assessment = assess_detail(item, parsed, "<html></html>")
    assert assessment["assessment"] == "different_class_numbers_requires_manual_review"
    assert assessment["observed_class_numbers"] == ["12410"]
    assert assessment["expected_but_not_observed"] == ["4267"]
    assert item["expected_class_numbers"] == ["4267"]


def test_does_not_follow_external_or_insecure_detail_url():
    assert safe_public_detail_url("https://cmsweb.cms.sdsu.edu/a/SSR_CS_WRAP_FL.GBL?x=1")
    assert not safe_public_detail_url("http://cmsweb.cms.sdsu.edu/a/SSR_CS_WRAP_FL.GBL")
    assert not safe_public_detail_url("https://sdsu.edu.evil.example/a/SSR_CS_WRAP_FL.GBL")
    assert not safe_public_detail_url("https://evil.example/a/SSR_CS_WRAP_FL.GBL")
