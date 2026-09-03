from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import ADMIN_SESSION_COOKIE, create_app
from classcatalog.ratings import RmpCandidate
from classcatalog.repository import SAMPLE_DATA_PATH, CourseRepository

ADMIN_PASSWORD = "correct-horse-battery-staple"


def _client(
    repository: CourseRepository,
    *,
    data_path: Path = SAMPLE_DATA_PATH,
    catalog_path: Path | None = None,
    ratings_path: Path | None = None,
    rmp_client: object | None = None,
    base_url: str = "http://testserver",
) -> TestClient:
    return TestClient(
        create_app(
            repository,
            data_path=data_path,
            catalog_path=catalog_path,
            ratings_path=ratings_path,
            admin_password=ADMIN_PASSWORD,
            rmp_client=rmp_client,
        ),
        base_url=base_url,
    )


def _login(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


class FakeAdminRmpClient:
    def __init__(self) -> None:
        self.lookups: list[int] = []

    def lookup_legacy_id(self, legacy_id: int) -> RmpCandidate | None:
        self.lookups.append(legacy_id)
        if legacy_id != 123456:
            return None
        return RmpCandidate(
            node_id="maya-profile",
            legacy_id=legacy_id,
            name="May Chen",
            department="Computer Science",
            rating=4.8,
            difficulty=2.2,
            num_reviews=55,
            would_take_again_percent=94.0,
        )


def _repository_with_unmatched_maya() -> CourseRepository:
    sections = CourseRepository.from_json(
        SAMPLE_DATA_PATH,
        catalog_path=None,
        ratings_path=None,
    ).sections
    return CourseRepository(
        tuple(
            section.model_copy(update={"professor": None})
            if section.instructor == "Maya Chen"
            else section
            for section in sections
        )
    )


def test_admin_health_requires_authentication(tmp_path: Path) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository)

    response = client.get("/api/admin/health")

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin authentication required."


def test_admin_session_reports_enabled_but_hidden_until_authenticated() -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository)

    assert client.get("/api/admin/session").json() == {
        "enabled": True,
        "authenticated": False,
    }
    _login(client)
    assert client.get("/api/admin/session").json() == {
        "enabled": True,
        "authenticated": True,
    }


def test_admin_login_rejects_wrong_password() -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository)

    response = client.post("/api/admin/login", json={"password": "wrong-password"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid admin password."
    assert ADMIN_SESSION_COOKIE not in client.cookies


def test_admin_login_sets_secure_httponly_session_cookie_over_https() -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository, base_url="https://testserver")

    response = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert f"{ADMIN_SESSION_COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Secure" in set_cookie
    assert "Max-Age=28800" in set_cookie


def test_admin_health_endpoint_reports_coverage_and_data_sources_after_login(tmp_path: Path) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    missing_catalog = tmp_path / "catalog_mappings.json"
    missing_ratings = tmp_path / "professor_ratings.json"
    client = _client(
        repository,
        data_path=SAMPLE_DATA_PATH,
        catalog_path=missing_catalog,
        ratings_path=missing_ratings,
    )
    _login(client)

    response = client.get("/api/admin/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["coverage"]["unaccounted_physical_sections"] == 0
    assert payload["coverage"]["accounted_physical_sections"] == payload["coverage"]["physical_sections"]
    assert payload["inventory"]["course_section_listings"] == repository.total
    assert payload["inventory"]["physical_sections"] == repository.physical_section_total
    assert payload["inventory"]["displayed_options"] == payload["coverage"]["displayed_options"]
    assert payload["professors"]["matched"] <= payload["professors"]["instructors"]
    assert payload["professors"]["unmatched"] == payload["professors"]["instructors"] - payload["professors"]["matched"]
    assert payload["files"]["sections"]["loaded"] is True
    assert payload["files"]["sections"]["name"] == SAMPLE_DATA_PATH.name
    assert payload["files"]["catalog"]["loaded"] is False
    assert payload["files"]["ratings"]["loaded"] is False
    assert payload["recent_errors"] == []


def test_admin_logout_invalidates_session() -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository)
    _login(client)

    response = client.post("/api/admin/logout")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert client.get("/api/admin/health").status_code == 401


def test_admin_endpoints_are_disabled_without_configured_password(monkeypatch) -> None:
    monkeypatch.delenv("CLASSCATALOG_ADMIN_PASSWORD", raising=False)
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = TestClient(create_app(repository, data_path=SAMPLE_DATA_PATH))

    assert client.get("/api/admin/session").json() == {
        "enabled": False,
        "authenticated": False,
    }
    assert client.post("/api/admin/login", json={"password": "anything"}).status_code == 404
    assert client.get("/api/admin/health").status_code == 404


def test_admin_health_counts_missing_fields_by_physical_section() -> None:
    base = CourseRepository.from_json(SAMPLE_DATA_PATH).sections[0]
    duplicate = base.model_copy(
        update={
            "id": f"{base.id}-duplicate",
            "instructor": None,
            "location": None,
            "meetings": (),
        }
    )
    repository = CourseRepository((base, duplicate))
    client = _client(repository)
    _login(client)

    payload = client.get("/api/admin/health").json()

    # The duplicate listing has the same physical schedule number. The populated
    # listing keeps that physical section from being reported as missing.
    assert payload["inventory"]["physical_sections"] == 1
    assert payload["completeness"]["physical_sections_missing_instructor"] == 0
    assert payload["completeness"]["physical_sections_missing_location"] == 0
    assert payload["completeness"]["physical_sections_missing_meetings"] == 0


def test_public_health_does_not_expose_local_file_paths() -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    client = _client(repository)

    payload = client.get("/api/health").json()

    assert "data_file" not in payload
    assert "catalog_data_file" not in payload
    assert "professor_ratings_data_file" not in payload
    assert str(SAMPLE_DATA_PATH) not in str(payload)


def test_admin_can_persist_apply_and_remove_manual_professor_match(tmp_path: Path) -> None:
    ratings_path = tmp_path / "professor_ratings.json"
    repository = _repository_with_unmatched_maya()
    rmp_client = FakeAdminRmpClient()
    client = _client(repository, ratings_path=ratings_path, rmp_client=rmp_client)

    unauthorized = client.post(
        "/api/admin/professors/overrides",
        json={"instructor_name": "Maya Chen", "rmp_profile": "123456"},
    )
    assert unauthorized.status_code == 401
    assert not ratings_path.exists()

    _login(client)
    before = client.get("/api/admin/health").json()["professors"]
    assert before["manual_matching_enabled"] is True
    assert any(item["name"] == "Maya Chen" for item in before["unmatched_instructors"])

    response = client.post(
        "/api/admin/professors/overrides",
        json={
            "instructor_name": "Maya Chen",
            "rmp_profile": "https://www.ratemyprofessors.com/professor/123456",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["saved"] is True
    assert payload["updated_section_listings"] == 2
    assert payload["override"]["rmp_name"] == "May Chen"
    assert payload["override"]["metrics"]["external_id"] == "123456"
    assert rmp_client.lookups == [123456]

    document = json.loads(ratings_path.read_text(encoding="utf-8"))
    assert document["records"][0]["match_source"] == "admin_override"
    assert document["summary"]["manual_overrides"] == 1
    maya_sections = [item for item in repository.sections if item.instructor == "Maya Chen"]
    assert {item.professor.external_id for item in maya_sections if item.professor} == {"123456"}

    after = client.get("/api/admin/health").json()["professors"]
    assert not any(item["name"] == "Maya Chen" for item in after["unmatched_instructors"])
    assert after["manual_overrides"][0]["name"] == "Maya Chen"

    reloaded = CourseRepository.from_json(SAMPLE_DATA_PATH, ratings_path=ratings_path)
    persisted = next(item for item in reloaded.sections if item.instructor == "Maya Chen")
    assert persisted.professor is not None
    assert persisted.professor.external_id == "123456"

    removed = client.delete(
        "/api/admin/professors/overrides",
        params={"instructor_name": "Maya Chen"},
    )
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert all(item.professor is None for item in repository.sections if item.instructor == "Maya Chen")


def test_admin_professor_override_validates_instructor_and_profile(tmp_path: Path) -> None:
    ratings_path = tmp_path / "professor_ratings.json"
    repository = _repository_with_unmatched_maya()
    rmp_client = FakeAdminRmpClient()
    client = _client(repository, ratings_path=ratings_path, rmp_client=rmp_client)
    _login(client)

    invalid_profile = client.post(
        "/api/admin/professors/overrides",
        json={"instructor_name": "Maya Chen", "rmp_profile": "https://example.com/123456"},
    )
    assert invalid_profile.status_code == 400
    assert "ratemyprofessors.com" in invalid_profile.json()["detail"]

    unknown_instructor = client.post(
        "/api/admin/professors/overrides",
        json={"instructor_name": "Not A Scheduled Professor", "rmp_profile": "123456"},
    )
    assert unknown_instructor.status_code == 404
    assert rmp_client.lookups == []


def test_admin_professor_override_does_not_replace_automatic_match(tmp_path: Path) -> None:
    ratings_path = tmp_path / "professor_ratings.json"
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, ratings_path=ratings_path)
    rmp_client = FakeAdminRmpClient()
    client = _client(repository, ratings_path=ratings_path, rmp_client=rmp_client)
    _login(client)

    response = client.post(
        "/api/admin/professors/overrides",
        json={"instructor_name": "Maya Chen", "rmp_profile": "123456"},
    )

    assert response.status_code == 409
    assert "already has an automatic" in response.json()["detail"]
    assert rmp_client.lookups == []
    assert not ratings_path.exists()
