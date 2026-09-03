from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import ADMIN_SESSION_COOKIE, create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


ADMIN_PASSWORD = "correct-horse-battery-staple"


def _client(
    repository: CourseRepository,
    *,
    data_path: Path = SAMPLE_DATA_PATH,
    catalog_path: Path | None = None,
    ratings_path: Path | None = None,
    base_url: str = "http://testserver",
) -> TestClient:
    return TestClient(
        create_app(
            repository,
            data_path=data_path,
            catalog_path=catalog_path,
            ratings_path=ratings_path,
            admin_password=ADMIN_PASSWORD,
        ),
        base_url=base_url,
    )


def _login(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


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
