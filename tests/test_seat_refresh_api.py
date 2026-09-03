from __future__ import annotations

from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


class FakeSeatService:
    enabled = True

    def __init__(self) -> None:
        self.interests: tuple[str, ...] = ()
        self.manual_refreshes = 0
        self.course_refreshes: tuple[str, ...] = ()
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    def register_interest(self, schedules) -> int:
        self.interests = tuple(schedules)
        return 2

    def public_status(self):
        return {
            "enabled": True,
            "running": True,
            "refreshable_course_pages": 100,
            "cached_sections": 50,
            "last_success_at": "2026-08-25T12:00:00+00:00",
            "age_seconds": 30.0,
            "browser_poll_seconds": 60,
        }

    def admin_status(self):
        return {
            **self.public_status(),
            "priority_sources": 2,
            "successful_course_refreshes": 20,
            "failed_course_refreshes": 1,
            "requests_completed": 21,
            "manual_refresh_pending": self.manual_refreshes,
            "refreshable_course_codes": ["CS 210"],
            "seat_failure_retention_limit": 100,
            "seat_failures": [
                {
                    "course_code": "DANCE 190",
                    "course_codes": ["DANCE 190"],
                    "source_url": "https://cmsweb.cms.sdsu.edu/example",
                    "first_failed_at": "2026-08-27T03:12:52+00:00",
                    "last_failed_at": "2026-08-27T03:12:52+00:00",
                    "error_type": "ResultParseError",
                    "detail": "No expected class numbers.",
                    "occurrences": 1,
                    "resolved": False,
                }
            ],
        }

    def seat_records(self, schedules):
        return {
            "Summer 2026::1234": {
                "schedule_number": "1234",
                "term": "Summer 2026",
                "seat_status": "open",
                "seats_available": 4,
                "seat_capacity": 30,
                "seats_enrolled": 26,
                "updated_at": "2026-08-25T12:00:00+00:00",
            }
        }

    def request_full_refresh(self) -> int:
        self.manual_refreshes = 100
        return 100

    def request_course_refresh(self, course_code: str) -> int:
        self.course_refreshes += (course_code,)
        return 2 if course_code == "CS 210" else 0


def _client(service: FakeSeatService, *, admin_password: str | None = None) -> TestClient:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    return TestClient(
        create_app(
            repository,
            data_path=SAMPLE_DATA_PATH,
            admin_password=admin_password,
            seat_refresh_service=service,
        )
    )



def test_seat_service_uses_fastapi_lifespan() -> None:
    service = FakeSeatService()
    client = _client(service)

    assert service.starts == 0
    assert service.stops == 0
    with client:
        assert service.starts == 1
        assert service.stops == 0
        assert client.get("/api/seats/status").status_code == 200
    assert service.stops == 1

def test_public_seat_status_and_lookup_are_lightweight() -> None:
    service = FakeSeatService()
    client = _client(service)

    status = client.get("/api/seats/status")
    assert status.status_code == 200
    assert status.json()["browser_poll_seconds"] == 60

    response = client.get("/api/seats", params=[("schedule_number", "1234")])
    assert response.status_code == 200
    assert response.json()["records"]["Summer 2026::1234"]["seats_available"] == 4
    assert service.interests == ("1234",)


def test_admin_manual_seat_refresh_requires_authentication() -> None:
    service = FakeSeatService()
    client = _client(service, admin_password="secret-password")

    assert client.post("/api/admin/seats/refresh").status_code == 401
    assert client.post("/api/admin/login", json={"password": "secret-password"}).status_code == 200
    response = client.post("/api/admin/seats/refresh")
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "queued_course_pages": 100}


def test_admin_can_queue_one_course_seat_refresh() -> None:
    service = FakeSeatService()
    client = _client(service, admin_password="secret-password")

    assert client.post(
        "/api/admin/seats/refresh/course",
        json={"course_code": "CS 210"},
    ).status_code == 401
    assert client.post("/api/admin/login", json={"password": "secret-password"}).status_code == 200

    response = client.post(
        "/api/admin/seats/refresh/course",
        json={"course_code": " cs   210 "},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "accepted": True,
        "course_code": "CS 210",
        "queued_course_pages": 2,
    }
    assert service.course_refreshes == ("CS 210",)


def test_admin_specific_seat_refresh_rejects_invalid_or_unknown_course() -> None:
    service = FakeSeatService()
    client = _client(service, admin_password="secret-password")
    client.post("/api/admin/login", json={"password": "secret-password"})

    invalid = client.post(
        "/api/admin/seats/refresh/course",
        json={"course_code": "CS/210"},
    )
    unknown = client.post(
        "/api/admin/seats/refresh/course",
        json={"course_code": "CS 999"},
    )

    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "Select a valid course code."
    assert unknown.status_code == 404
    assert "not in the active schedule" in unknown.json()["detail"]


def test_admin_health_includes_seat_refresh_diagnostics() -> None:
    service = FakeSeatService()
    client = _client(service, admin_password="secret-password")
    client.post("/api/admin/login", json={"password": "secret-password"})

    payload = client.get("/api/admin/health").json()

    assert payload["seat_refresh"]["running"] is True
    assert payload["seat_refresh"]["refreshable_course_pages"] == 100
    assert payload["seat_refresh"]["failed_course_refreshes"] == 1
    assert payload["seat_refresh"]["seat_failures"][0]["course_code"] == "DANCE 190"
    assert payload["seat_refresh"]["seat_failure_retention_limit"] == 100
