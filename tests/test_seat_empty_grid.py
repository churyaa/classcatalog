"""Offline tests for exact SDSU 'No scheduled classes' course-grid evidence.

These tests never contact SDSU or modify the production sections dataset.
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
from classcatalog.seats import (
    SeatInventoryMismatchError,
    SeatRefreshService,
    _has_explicit_no_scheduled_classes_message,
)

URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL&STRM=2273"
)
EMPTY_HTML = (
    '<html><body><div id="SSR_CLSRCH_F_WK_SSR_MSG_TEXT">'
    'No scheduled classes</div></body></html>'
)
GENERIC_HTML = (
    '<html><body><div id="OTHER_MESSAGE">No scheduled classes</div>'
    '<div>No results to display</div></body></html>'
)


def _service(tmp_path: Path) -> SeatRefreshService:
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    section = sample.model_copy(update={
        "id": "empty-fin-11525",
        "course_code": "FIN 496", "subject": "FIN", "catalog_number": "496",
        "term": "Spring 2027", "term_code": "2273",
        "schedule_number": "11525", "section_number": "02",
        "source_url": URL,
        "seat_status": SeatStatus.OPEN,
        "seat_updated_at": None, "seats_available": 10, "seat_capacity": 20,
        "crse_id": None, "crse_offer_nbr": None,
    })
    return SeatRefreshService(
        CourseRepository((section,)),
        cache_path=tmp_path / "seat_cache.json",
        instructor_cache_path=tmp_path / "instructor_cache.json",
        preferred_term="Spring 2027", request_delay_seconds=0,
    )


def _parsed(*, term="Spring 2027", course="FIN 496", complete=True, number=None):
    options = () if number is None else (
        SimpleNamespace(
            class_number=number, instructor="Jane Doe", status=SeatStatus.OPEN,
            open_seats=5, seat_capacity=20, seats_enrolled=15,
        ),
    )
    return SimpleNamespace(
        course_code=course, term=term, term_code="2273",
        options_complete=complete, options=options,
    )


class StubClient:
    def __init__(self, html=EMPTY_HTML):
        self.html = html
        self.fetches = 0
        self.resets = 0
        self.bootstraps = 0

    def fetch_course_info_page(self, url):
        self.fetches += 1
        return SimpleNamespace(
            html=self.html, canonical_url=url, expansion_count=0,
        )

    def reset_public_session(self):
        self.resets += 1

    def bootstrap(self):
        self.bootstraps += 1


def _mock_parsed(monkeypatch, parsed):
    monkeypatch.setattr(
        "classcatalog.seats.parse_course_info_page",
        lambda *_args, **_kwargs: parsed,
    )
    monkeypatch.setattr(
        "classcatalog.seats.find_class_number_actions", lambda _html: (),
    )


def test_fin_496_explicit_empty_grid_is_classified_without_recovery(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_parsed(monkeypatch, _parsed())
    client = StubClient()

    service._refresh_source(client, URL)

    assert client.fetches == 1
    assert client.resets == client.bootstraps == 0
    assert 29 * 60 < service._source_retry_after[URL] - time.time() <= 1801
    assert service._source_failure_streak[URL] == 1
    section = service.repository.seat_snapshot(("11525",))[("2273", "11525")]
    assert section.seat_updated_at is None
    assert section.seats_available == 10  # Do not overwrite historical seat data.
    assert URL not in service._source_last_success
    assert service.missing_section_keys() == set()
    assert service.unlisted_source_urls() == set()
    assert service.missing_number_source_urls() == set()

    admin = service.admin_status()
    assert admin["inventory_mismatch_sources_since_start"] == 0
    assert admin["inventory_empty_course_sources_since_start"] == 1
    assert admin["unverified_empty_course_sources_since_start"] == 1
    failure = admin["seat_failures"][0]
    assert failure["error_type"] == "SeatUnverifiedEmptyGridError"
    assert failure["empty_recovery_status"] == "missing_recovery_metadata"
    assert failure["inventory_reason"] == "no_scheduled_classes"
    assert failure["expected_class_numbers"] == ["11525"]
    assert failure["observed_class_numbers"] == []
    assert failure["resolved"] is False

    service._flush_cache(force=True)
    cached = json.loads((tmp_path / "seat_cache.json").read_text(encoding="utf-8"))
    assert URL not in cached["sources"]
    restarted = _service(tmp_path)
    assert restarted.missing_section_keys() == set()
    assert restarted._last_success_at is None


def test_empty_grid_requires_exact_sdsu_status_field():
    assert _has_explicit_no_scheduled_classes_message(EMPTY_HTML)
    assert _has_explicit_no_scheduled_classes_message(
        '<span id="SSR_CLSRCH_F_WK_SSR_MSG_TEXT">  NO  scheduled\n classes </span>'
    )
    assert not _has_explicit_no_scheduled_classes_message(GENERIC_HTML)
    assert not _has_explicit_no_scheduled_classes_message(
        '<div id="SSR_CLSRCH_F_WK_SSR_MSG_TEXT">No results to display</div>'
    )
    assert not _has_explicit_no_scheduled_classes_message(
        '<div id="SSR_CLSRCH_F_WK_SSR_MSG_TEXT">1 - 1 of 1 options</div>'
    )


@pytest.mark.parametrize(
    "parsed,html",
    [
        (_parsed(term="Fall 2026"), EMPTY_HTML),
        (_parsed(term=""), EMPTY_HTML),
        (_parsed(course="JMS 527"), EMPTY_HTML),
        (_parsed(complete=False), EMPTY_HTML),
        (_parsed(), GENERIC_HTML),
        (_parsed(), "<html><body></body></html>"),
        (_parsed(number="12272"), EMPTY_HTML),
    ],
)
def test_unsafe_empty_states_use_existing_recovery_path(tmp_path, monkeypatch, parsed, html):
    service = _service(tmp_path)
    _mock_parsed(monkeypatch, parsed)
    source = service._sources[URL]
    loaded = SimpleNamespace(html=html, canonical_url=URL)
    with pytest.raises(ResultParseError) as caught:
        service._parse_expected_source_page(loaded, source)
    # A different *visible* class number is an independently verified
    # mismatch even if the page also contains misleading empty-state text.
    if parsed.options and parsed.course_code == "FIN 496" and parsed.term == "Spring 2027":
        assert isinstance(caught.value, SeatInventoryMismatchError)
        assert caught.value.reason == "different_class_numbers"
        return
    assert not isinstance(caught.value, SeatInventoryMismatchError)
    client = StubClient(html)
    service._refresh_source(client, URL)
    assert client.fetches == 2
    assert client.resets == client.bootstraps == 1
    assert 0 < service._source_retry_after[URL] - time.time() <= 61
    assert service.admin_status()["inventory_empty_course_sources_since_start"] == 0
    assert service.missing_section_keys() == set()


def test_empty_grid_backoff_and_manual_bypass(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _mock_parsed(monkeypatch, _parsed())
    client = StubClient()
    for expected_streak, minimum_seconds in ((1, 29*60), (2, 59*60), (3, 119*60)):
        service._refresh_source(client, URL)
        assert service._source_failure_streak[URL] == expected_streak
        assert service._source_retry_after[URL] - time.time() >= minimum_seconds
    service.register_interest(("11525",))
    assert service._next_source(0) is None
    assert service.request_course_refresh("FIN 496") == 1
    assert service._next_source(1) == URL


def test_correct_class_returns_and_clears_empty_grid_signal(tmp_path, monkeypatch):
    service = _service(tmp_path)
    client = StubClient()
    _mock_parsed(monkeypatch, _parsed())
    service._refresh_source(client, URL)
    assert service.missing_section_keys() == set()

    _mock_parsed(monkeypatch, _parsed(number="11525"))
    client.html = "<html></html>"
    service._refresh_source(client, URL)

    row = service.repository.seat_snapshot(("11525",))[("2273", "11525")]
    assert row.seats_available == 5
    assert row.seat_updated_at is not None
    assert service.missing_section_keys() == set()
    assert URL not in service._source_retry_after
    assert service.admin_status()["inventory_empty_course_sources_since_start"] == 0
    assert service.admin_status()["seat_failures"][0]["resolved"] is True
