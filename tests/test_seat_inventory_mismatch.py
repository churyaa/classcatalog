"""Offline regressions for complete-grid class-inventory mismatches.

All PeopleSoft responses are mocked; no external network or live datasets.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.parser import ResultParseError
from classcatalog.seats import SeatInventoryMismatchError, SeatRefreshService

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM=2273"
)


def _service(tmp_path: Path) -> SeatRefreshService:
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    section = sample.model_copy(update={
        "id": "mismatch-jms-11877",
        "course_code": "JMS 527",
        "subject": "JMS",
        "catalog_number": "527",
        "term": "Spring 2027",
        "term_code": "2273",
        "schedule_number": "11877",
        "section_number": "02",
        "source_url": URL,
        "seat_status": SeatStatus.OPEN,
        "seat_updated_at": None,
        "seats_available": 10,
        "seat_capacity": 20,
        # Simplify the mock: unexpected generic responses still exercise the
        # session-reset path, without needing a separate wrapper fixture.
        "crse_id": None,
        "crse_offer_nbr": None,
    })
    return SeatRefreshService(
        CourseRepository((section,)),
        cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        preferred_term="Spring 2027",
        request_delay_seconds=0,
    )


def _parsed(number: str, *, term: str = "Spring 2027", complete: bool = True):
    return SimpleNamespace(
        course_code="JMS 527",
        term=term,
        term_code="2273",  # Derived from URL; NOT proof of the displayed term.
        options_complete=complete,
        options=(SimpleNamespace(
            class_number=number,
            instructor="Jane Doe",
            status=SeatStatus.OPEN,
            open_seats=5,
            seat_capacity=20,
            seats_enrolled=15,
        ),),
    )


class StubClient:
    def __init__(self):
        self.fetches = 0
        self.resets = 0
        self.bootstraps = 0

    def fetch_course_info_page(self, url):
        self.fetches += 1
        return SimpleNamespace(html="<html></html>", canonical_url=url, expansion_count=0)

    def reset_public_session(self):
        self.resets += 1

    def bootstrap(self):
        self.bootstraps += 1


def _mock_page(monkeypatch, parsed):
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: parsed)
    monkeypatch.setattr("classcatalog.seats.find_class_number_actions", lambda _html: ())


def test_jms_527_mismatch_is_safe_fast_flagged_and_persisted(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_page(monkeypatch, _parsed("12272"))
    client = StubClient()
    original = service.repository.seat_snapshot(("11877",))[("2273", "11877")]
    assert original.seat_updated_at is None

    service._refresh_source(client, URL)

    assert client.fetches == 1  # No second request, session reset, or wrapper recovery.
    assert client.resets == 0 and client.bootstraps == 0
    current = service.repository.seat_snapshot(("11877",))[("2273", "11877")]
    assert current.seat_updated_at is None
    assert current.seats_available == 10  # Never copy #12272's 5 seats.
    assert URL not in service._source_last_success
    assert service._source_retry_after[URL] - time.time() > 29 * 60
    assert service.missing_section_keys() == {"2273::11877"}
    assert service.unlisted_source_urls() == {URL}
    assert service.missing_number_source_urls() == {URL}
    admin = service.admin_status()
    assert admin["inventory_mismatch_sources_since_start"] == 1
    assert admin["inventory_drift"]["missing_section_candidates"] == 1
    assert admin["inventory_drift"]["unlisted_class_numbers"] == 1
    failure = admin["seat_failures"][0]
    assert failure["error_type"] == "SeatInventoryMismatchError"
    assert failure["expected_class_numbers"] == ["11877"]
    assert failure["observed_class_numbers"] == ["12272"]
    assert failure["resolved"] is False

    service._flush_cache(force=True)
    payload = json.loads((tmp_path / "seat_cache.json").read_text(encoding="utf-8"))
    assert payload["sources"][URL]["last_success_at"] is None
    assert payload["sources"][URL]["missing_class_numbers"] == ["11877"]
    assert payload["sources"][URL]["unlisted_class_numbers"] == ["12272"]
    restarted = _service(tmp_path)
    assert restarted.missing_section_keys() == {"2273::11877"}
    assert restarted.unlisted_source_urls() == {URL}
    assert restarted._last_success_at is None  # Evidence is not a live seat refresh.


def test_mismatch_backoff_grows_and_admin_manual_refresh_bypasses(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_page(monkeypatch, _parsed("12272"))
    client = StubClient()
    for index, minimum in ((1, 29 * 60), (2, 59 * 60), (3, 119 * 60),
                           (4, 239 * 60), (5, 359 * 60)):
        service._refresh_source(client, URL)
        assert service._source_failure_streak[URL] == index
        assert minimum <= service._source_retry_after[URL] - time.time() <= 6 * 3600 + 1

    # Student interest and routine scheduling respect the cooldown.
    service.register_interest(("11877",))
    assert service._next_source(0) is None
    # Explicit admin queue bypasses every cooldown tier.
    assert service.request_course_refresh("JMS 527") == 1
    assert service._next_source(1) == URL


def test_wrong_semester_and_incomplete_grid_never_flag_inventory(tmp_path, monkeypatch):
    service = _service(tmp_path)
    loaded = SimpleNamespace(html="<html></html>", canonical_url=URL)
    source = service._sources[URL]

    for parsed in (
        _parsed("12272", term="Fall 2026"),
        _parsed("12272", complete=False),
        _parsed("12272", term=""),
        SimpleNamespace(course_code="JMS 527", term="Spring 2027",
                        term_code="2273", options_complete=True, options=()),
    ):
        _mock_page(monkeypatch, parsed)
        with pytest.raises(ResultParseError) as error:
            service._parse_expected_source_page(loaded, source)
        assert not isinstance(error.value, SeatInventoryMismatchError)
    assert service.missing_section_keys() == set()
    assert service.unlisted_source_urls() == set()

    # A wrong term causes the old session recovery path, not a long cooldown.
    _mock_page(monkeypatch, _parsed("12272", term="Fall 2026"))
    client = StubClient()
    service._refresh_source(client, URL)
    assert client.fetches == 2
    assert client.resets == 1 and client.bootstraps == 1
    assert service._source_retry_after[URL] - time.time() < 65
    assert service.admin_status()["inventory_mismatch_sources_since_start"] == 0


def test_mismatch_clears_on_correct_class_and_retains_seats(tmp_path, monkeypatch):
    service = _service(tmp_path)
    client = StubClient()
    _mock_page(monkeypatch, _parsed("12272"))
    service._refresh_source(client, URL)
    assert service.missing_section_keys()
    assert URL in service._source_retry_after

    _mock_page(monkeypatch, _parsed("11877"))
    service._refresh_source(client, URL)
    row = service.repository.seat_snapshot(("11877",))[("2273", "11877")]
    assert row.seats_available == 5
    assert row.seat_updated_at is not None
    assert service.missing_section_keys() == set()
    assert service.unlisted_source_urls() == set()
    assert URL not in service._source_retry_after
    assert URL not in service._source_failure_streak
    assert service.admin_status()["inventory_mismatch_sources_since_start"] == 0
    assert service.admin_status()["seat_failures"][0]["resolved"] is True
