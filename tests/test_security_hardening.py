from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from classcatalog.main import create_app
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import is_course_info_url


def _repository() -> CourseRepository:
    return CourseRepository.from_json(
        SAMPLE_DATA_PATH,
        catalog_path=None,
        ratings_path=None,
    )


def test_api_docs_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSCATALOG_ENV", raising=False)
    with TestClient(create_app(_repository(), seat_refresh_enabled=False)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_api_docs_are_available_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSCATALOG_ENV", "development")
    with TestClient(create_app(_repository(), seat_refresh_enabled=False)) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_course_info_url_requires_https_and_a_real_sdsu_host() -> None:
    valid = (
        "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
        "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
    )
    assert is_course_info_url(valid)

    invalid = (
        valid.replace("https://", "http://"),
        valid.replace("cmsweb.cms.sdsu.edu", "evilsdsu.edu"),
        valid.replace("cmsweb.cms.sdsu.edu", "sdsu.edu.evil.example"),
        valid.replace("cmsweb.cms.sdsu.edu", "example.com"),
        valid.replace("SSR_CRSE_INFO_FL.GBL", "SSR_CLSRCH_MAIN_FL.GBL"),
    )
    assert all(not is_course_info_url(url) for url in invalid)


def test_deploy_script_polls_health_instead_of_assuming_three_seconds() -> None:
    script_path = Path(__file__).parents[1] / "ops" / "deploy.sh"
    script = script_path.read_text(encoding="utf-8")

    assert 'HEALTH_URL="http://127.0.0.1:8000/api/health"' in script
    assert 'for attempt in $(seq 1 "$STARTUP_ATTEMPTS")' in script
    assert 'sleep "$STARTUP_DELAY_SECONDS"' in script
    assert "sleep 3" not in script
    assert "journalctl -u classcatalog -n 100" in script
