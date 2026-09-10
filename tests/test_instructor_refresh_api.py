from __future__ import annotations

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


class FakeInstructorSeatService:
    enabled = True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def request_tba_instructor_refresh(self) -> tuple[int, int]:
        return (7, 11)

    def public_status(self) -> dict[str, object]:
        return {"enabled": True, "running": True}

    def admin_status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "running": True,
            "tba_instructor_physical_sections": 11,
            "tba_instructor_course_pages": 7,
            "instructor_cache_records": 3,
        }


def _client() -> TestClient:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    return TestClient(
        create_app(
            repository,
            data_path=SAMPLE_DATA_PATH,
            admin_password="secret-password",
            seat_refresh_service=FakeInstructorSeatService(),
        )
    )


def test_admin_tba_instructor_refresh_requires_authentication() -> None:
    client = _client()

    assert client.post("/api/admin/instructors/refresh-tba").status_code == 401


def test_admin_can_queue_only_tba_instructor_pages() -> None:
    client = _client()
    assert client.post("/api/admin/login", json={"password": "secret-password"}).status_code == 200

    response = client.post("/api/admin/instructors/refresh-tba")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "accepted": True,
        "queued_course_pages": 7,
        "tba_physical_sections": 11,
    }
