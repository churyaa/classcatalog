"""Manual removal-review checks; never call live SDSU in these tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.removal_review import RemovalReviewService, _key
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _fixture(tmp_path: Path):
    source = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    first = source.model_copy(update={
        "id": "removal-spring-test", "term": "Spring 2027", "term_code": "2273",
        "course_code": "CS 574", "subject": "CS", "catalog_number": "574",
        "schedule_number": "57401", "seat_updated_at": old,
    })
    second = source.model_copy(update={
        "id": "removal-summer-keep", "term": "Summer 2027", "term_code": "2275",
        "course_code": "CS 570", "subject": "CS", "catalog_number": "570",
        "schedule_number": "57002", "seat_updated_at": old,
    })
    data = tmp_path / "sections.json"
    data.write_text(json.dumps([s.model_dump(mode="json") for s in (first, second)]), encoding="utf-8")
    return first, second, data, CourseRepository((first, second))


def test_stale_seats_flagged_but_no_delete_before_verification(tmp_path: Path) -> None:
    target, _, data, repository = _fixture(tmp_path)
    service = RemovalReviewService(repository, data, probe=lambda _section: True)
    rows = service.candidates()["items"]
    assert len(rows) == 2
    match = next(row for row in rows if row["key"] == _key(target))
    assert match["seat_age_hours"] >= 71
    assert match["can_delete"] is False
    with pytest.raises(ValueError, match="confirmation"):
        service.hide_confirmed(_key(target))


def test_two_complete_absence_confirmations_unlock_manual_hide(tmp_path: Path) -> None:
    target, unaffected, data, repo = _fixture(tmp_path)
    probes = []
    def absent(section):
        probes.append(section.schedule_number)
        return True
    service = RemovalReviewService(repo, data, probe=absent)
    service._verify(_key(target), target)
    assert probes == [target.schedule_number, target.schedule_number]
    row = next(x for x in service.candidates()["items"] if x["key"] == _key(target))
    assert row["status"] == "confirmed_missing" and row["can_delete"]
    before = data.read_bytes()
    hidden = service.hide_confirmed(_key(target))
    assert hidden["hidden"] is True
    assert data.read_bytes() == before
    assert len(json.loads(data.read_text(encoding="utf-8"))) == 2
    assert repo.sections[0].id == unaffected.id
    assert service.candidates()["total_flagged"] == 1
    assert len(service.candidates()["hidden_items"]) == 1


def test_present_or_failed_check_never_permits_deletion(tmp_path: Path) -> None:
    target, _, data, repository = _fixture(tmp_path)
    present = RemovalReviewService(repository, data, probe=lambda _section: False)
    present._verify(_key(target), target)
    row = next(x for x in present.candidates()["items"] if x["key"] == _key(target))
    assert row["status"] == "still_present" and not row["can_delete"]

    def failed(_section):
        raise RuntimeError("SDSU returned an invalid page")
    inconclusive = RemovalReviewService(repository, data, probe=failed)
    inconclusive._verify(_key(target), target)
    row = next(x for x in inconclusive.candidates()["items"] if x["key"] == _key(target))
    assert row["status"] == "inconclusive" and not row["can_delete"]


def test_confirmation_expires_if_live_seat_update_changes(tmp_path: Path) -> None:
    target, _, data, repo = _fixture(tmp_path)
    service = RemovalReviewService(repo, data, probe=lambda _section: True)
    service._verify(_key(target), target)
    repo.apply_seat_updates({("2273", "57401"): {"seat_updated_at": datetime.now(timezone.utc).isoformat()}})
    with pytest.raises(ValueError, match="confirmation"):
        service.hide_confirmed(_key(target))


def test_review_api_is_admin_only_and_delete_requires_confirmation(tmp_path: Path) -> None:
    target, _, data, repo = _fixture(tmp_path)
    client = TestClient(create_app(repo, data_path=data, admin_password="test-secret", seat_refresh_enabled=False))
    key = _key(target)
    assert client.get("/api/admin/removals").status_code == 401
    assert client.post("/api/admin/removals/hide", json={"key": key}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "test-secret"}).status_code == 200
    response = client.get("/api/admin/removals")
    assert response.status_code == 200
    assert response.json()["total_flagged"] == 2
    assert client.post("/api/admin/removals/hide", json={"key": key}).status_code == 409
    assert client.post("/api/admin/removals/dismiss", json={"key": key}).status_code == 200
    assert client.get("/api/admin/removals").json()["total_flagged"] == 1


# CLASSCATALOG_REMOVAL_SOURCE_LINK_V1: only official SDSU course pages are linked.
def test_removal_review_exposes_official_sdsu_source_only(tmp_path: Path) -> None:
    target, unaffected, data, repository = _fixture(tmp_path)
    official_url = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_CLSRCH_FL.SSR_CRSE_INFO_FL.GBL?STRM=2273"
    )
    updated_target = target.model_copy(update={"source_url": official_url})
    updated_repository = CourseRepository((updated_target, unaffected))
    service = RemovalReviewService(updated_repository, data, probe=lambda _section: True)
    rows = {item["key"]: item for item in service.candidates()["items"]}
    assert rows[_key(updated_target)]["source_url"] == official_url
    assert rows[_key(unaffected)]["source_url"] is None


def test_removal_review_ui_contains_sdsu_source_link() -> None:
    javascript = (Path(__file__).resolve().parents[1] / "src/classcatalog/static/app.js").read_text(encoding="utf-8")
    assert 'Open SDSU Source</a>' in javascript
    assert 'rel="noopener noreferrer"' in javascript
    assert 'escapeHtml(item.source_url)' in javascript
