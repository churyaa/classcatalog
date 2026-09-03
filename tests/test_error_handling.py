from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.errors import RecentErrorStore, public_error_for_path
from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


ADMIN_PASSWORD = "error-history-test-password"


def _login(client: TestClient) -> None:
    response = client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200


def test_unhandled_class_api_error_returns_clean_message_and_is_retained(monkeypatch) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)
    original_search = repository.search

    def fail_search(_filters):
        raise RuntimeError("internal database detail that public users must not see")

    monkeypatch.setattr(repository, "search", fail_search)
    client = TestClient(
        create_app(repository, admin_password=ADMIN_PASSWORD),
        raise_server_exceptions=False,
    )

    response = client.get("/api/classes")

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert response.json() == {
        "detail": "Class results are temporarily unavailable. Please try again.",
        "error": {
            "code": "class_results_unavailable",
            "message": "Class results are temporarily unavailable. Please try again.",
            "request_id": response.headers["x-request-id"],
        }
    }
    assert "internal database detail" not in response.text

    monkeypatch.setattr(repository, "search", original_search)
    _login(client)
    recent = client.get("/api/admin/health").json()["recent_errors"]

    assert recent[0]["request_id"] == response.headers["x-request-id"]
    assert recent[0]["method"] == "GET"
    assert recent[0]["path"] == "/api/classes"
    assert recent[0]["status_code"] == 500
    assert recent[0]["exception_type"] == "RuntimeError"
    assert "internal database detail" in recent[0]["detail"]


def test_seat_api_error_uses_seat_specific_public_message(monkeypatch) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)

    def fail_snapshot(_schedules):
        raise TimeoutError("SDSU request timed out")

    monkeypatch.setattr(repository, "seat_snapshot", fail_snapshot)
    client = TestClient(create_app(repository), raise_server_exceptions=False)

    response = client.get("/api/seats", params={"schedule_number": "1234"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "seat_data_unavailable"
    assert response.json()["error"]["message"] == "Seat data is temporarily unavailable."
    assert "timed out" not in response.text


def test_malformed_ratings_cache_does_not_break_class_search_and_appears_in_admin(tmp_path: Path) -> None:
    ratings_path = tmp_path / "professor_ratings.json"
    ratings_path.write_text("{not valid json", encoding="utf-8")
    repository = CourseRepository.from_json(
        SAMPLE_DATA_PATH,
        catalog_path=None,
        ratings_path=ratings_path,
    )

    assert repository.ratings_record_count == 0
    assert repository.ratings_load_error is not None

    client = TestClient(
        create_app(
            repository,
            ratings_path=ratings_path,
            admin_password=ADMIN_PASSWORD,
        ),
        raise_server_exceptions=False,
    )
    assert client.get("/api/classes").status_code == 200

    status = client.get("/api/ratings/status")
    assert status.status_code == 503
    assert status.json()["error"]["message"] == "Professor ratings could not be loaded."
    assert "JSONDecodeError" not in status.text

    _login(client)
    recent = client.get("/api/admin/health").json()["recent_errors"]
    assert any(item["method"] == "STARTUP" for item in recent)
    assert any(item["code"] == "professor_ratings_unavailable" for item in recent)


def test_malformed_catalog_falls_back_to_class_search_with_clean_catalog_error(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog_mappings.json"
    catalog_path.write_text("not json", encoding="utf-8")
    repository = CourseRepository.from_json(
        SAMPLE_DATA_PATH,
        catalog_path=catalog_path,
        ratings_path=None,
    )
    client = TestClient(create_app(repository, catalog_path=catalog_path), raise_server_exceptions=False)

    assert repository.catalog_load_error is not None
    assert client.get("/api/classes").status_code == 200
    response = client.get("/api/catalog/status")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Catalog information is temporarily unavailable."
    assert "JSONDecodeError" not in response.text


def test_recent_error_store_is_bounded_and_newest_first() -> None:
    store = RecentErrorStore(max_entries=2)
    for index in range(3):
        store.record(
            request_id=f"request-{index}",
            method="GET",
            path="/api/classes",
            status_code=500,
            code="class_results_unavailable",
            public_message="Class results are temporarily unavailable. Please try again.",
            exception_type="RuntimeError",
            detail=f"failure {index}",
        )

    assert [item["request_id"] for item in store.snapshot()] == ["request-2", "request-1"]

    store.record(
        request_id="request-3",
        method="GET",
        path="/api/classes",
        status_code=500,
        code="class_results_unavailable",
        public_message="Class results are temporarily unavailable. Please try again.",
        exception_type="RuntimeError",
        detail="failure 1",
    )
    assert store.snapshot()[0]["request_id"] == "request-3"
    assert store.snapshot()[0]["occurrences"] == 2


def test_public_error_messages_cover_supplemental_resources() -> None:
    assert public_error_for_path("/api/seats")[1] == "Seat data is temporarily unavailable."
    assert public_error_for_path("/api/ratings/status")[1] == "Professor ratings could not be loaded."
