from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


ADMIN_PASSWORD = "announcement-test-password"


def _client(tmp_path: Path) -> TestClient:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    return TestClient(
        create_app(
            repository,
            data_path=SAMPLE_DATA_PATH,
            announcements_path=tmp_path / "announcements.json",
            admin_password=ADMIN_PASSWORD,
            seat_refresh_enabled=False,
        )
    )


def test_announcements_are_public_but_writes_require_admin(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/announcements").json() == {"messages": []}

    denied = client.post(
        "/api/admin/announcements",
        json={"title": "Hello", "message": "Registration opens soon."},
    )
    assert denied.status_code == 401

    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 200
    sent = client.post(
        "/api/admin/announcements",
        json={"title": "Hello", "message": "Registration opens soon."},
    )
    assert sent.status_code == 200
    record = sent.json()["announcement"]
    assert record["title"] == "Hello"

    public = client.get("/api/announcements").json()["messages"]
    assert [item["id"] for item in public] == [record["id"]]
    assert public[0]["message"] == "Registration opens soon."

    deleted = client.delete(f"/api/admin/announcements/{record['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/announcements").json() == {"messages": []}


def test_announcement_payload_is_bounded(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/admin/login", json={"password": ADMIN_PASSWORD}).status_code == 200
    response = client.post(
        "/api/admin/announcements",
        json={"title": "x" * 121, "message": "ok"},
    )
    assert response.status_code == 422
