"""Offline regression checks for the manual modified-class review feature."""
from __future__ import annotations

from datetime import datetime, time
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.models import Meeting, Weekday
from classcatalog.modification_review import ModificationReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH

OFFICIAL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_CLSRCH_FL.SSR_CRSE_INFO_FL.GBL?STRM=2273"
)


def _fixture(tmp_path: Path):
    old = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    target = old.model_copy(update={
        "id": "modification-spring-test", "term": "Spring 2027", "term_code": "2273",
        "course_code": "CS 574", "subject": "CS", "catalog_number": "574",
        "schedule_number": "57401", "source_url": OFFICIAL, "instructor": "Maya Chen",
        "location": "GMCS 101",
        "meetings": (Meeting(days=(Weekday.MON, Weekday.WED),
                             start_time=time(10, 0), end_time=time(11, 15),
                             location="GMCS 101", instructor="Maya Chen"),),
    })
    other = old.model_copy(update={
        "id": "modification-summer-keep", "term": "Summer 2027", "term_code": "2275",
        "course_code": "CS 570", "subject": "CS", "catalog_number": "570",
        "schedule_number": "57002", "source_url": "https://example.invalid/class",
    })
    path = tmp_path / "sections.json"
    path.write_text(json.dumps([s.model_dump(mode="json") for s in (target, other)]), encoding="utf-8")
    return target, other, path, CourseRepository((target, other))


def _option():
    return SimpleNamespace(
        class_number="57401", instructor="Anita New", location="GMCS 204",
        days=(Weekday.TUE, Weekday.THU), start_time=time(12, 0), end_time=time(13, 15),
        meeting_dates=None,
    )


def _service(tmp_path: Path):
    target, other, path, repo = _fixture(tmp_path)
    grid = SimpleNamespace(course_code=target.course_code, options_complete=True, options=(_option(),))
    details = SimpleNamespace(meetings=(), location="GMCS 204", instruction_mode=None)
    service = ModificationReviewService(
        repo, path, fetch_grid=lambda _s: grid, verify_live=lambda _s: (_option(), details),
    )
    return target, other, path, repo, service


def test_scan_flags_professor_time_location_without_changing_dataset(tmp_path: Path) -> None:
    target, other, path, repo, service = _service(tmp_path)
    original = path.read_bytes()
    service._scan_worker((target,))
    flagged = service.overview()["items"]
    assert len(flagged) == 1
    assert flagged[0]["status"] == "needs_verification"
    assert set(flagged[0]["changes"]) == {"instructor", "location", "meetings"}
    assert flagged[0]["can_apply"] is False
    assert path.read_bytes() == original
    assert repo.sections[1].id == other.id


def test_verification_unlocks_manual_apply_and_saves_backup(tmp_path: Path) -> None:
    target, other, path, repo, service = _service(tmp_path)
    service._scan_worker((target,))
    service._verify_worker(_key(target), target)
    item = service.overview()["items"][0]
    assert item["status"] == "verified" and item["can_apply"] is True
    applied = service.apply_verified(_key(target))
    assert applied["applied"] is True
    assert Path(applied["backup_path"]).exists()
    new_data = json.loads(path.read_text(encoding="utf-8"))
    assert new_data[0]["instructor"] == "Anita New"
    assert new_data[0]["location"] == "GMCS 204"
    assert new_data[0]["meetings"][0]["days"] == ["tue", "thu"]
    assert new_data[1]["id"] == other.id
    assert repo.sections[0].instructor == "Anita New"
    assert service.overview()["total_flagged"] == 0


def test_cannot_apply_unverified_and_missing_source_is_skipped(tmp_path: Path) -> None:
    target, _, path, _, service = _service(tmp_path)
    service._scan_worker((target,))
    try:
        service.apply_verified(_key(target))
    except ValueError as exc:
        assert "verification" in str(exc)
    else:
        raise AssertionError("Unverified application must be rejected")
    assert path.exists()


def test_admin_endpoints_require_authentication(tmp_path: Path) -> None:
    target, _, path, repo, _service_obj = _service(tmp_path)
    with TestClient(create_app(repo, data_path=path, admin_password="test-secret", seat_refresh_enabled=False)) as client:
        assert client.get("/api/admin/modifications").status_code == 401
        assert client.post("/api/admin/modifications/apply", json={"key": _key(target)}).status_code == 401
        assert client.post("/api/admin/login", json={"password": "test-secret"}).status_code == 200
        payload = client.get("/api/admin/modifications")
        assert payload.status_code == 200
        assert "available_terms" in payload.json()
        assert client.post("/api/admin/modifications/apply", json={"key": _key(target)}).status_code == 409
