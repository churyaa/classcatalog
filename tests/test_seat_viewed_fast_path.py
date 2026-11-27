"""Fast, offline regression tests for exact-class waitlist refresh and fairness.

No SDSU requests are made; PeopleSoft CA responses and worker session are mocked.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.seats import SeatRefreshService

SOURCE_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
    "&CRSE_ID=032312&CRSE_OFFER_NBR=3&STRM=2273&CLASS_NBR=11659"
)


def make_service(tmp_path: Path, *, count: int = 3) -> SeatRefreshService:
    sample = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None,
    ).sections[0]
    numbers = ("3976", "3983", "3991", "4010", "4020")[:count]
    sections = tuple(sample.model_copy(update={
        "id": f"chem201-fast-{number}",
        "term": "Spring 2027", "term_code": "2273",
        "subject": "CHEM", "catalog_number": "201", "course_code": "CHEM 201",
        "schedule_number": number, "source_url": SOURCE_URL,
        "crse_id": "032312", "crse_offer_nbr": "3", "acad_career": "UGRD",
        "seat_status": SeatStatus.WAITLIST,
        "seats_available": 0, "seat_capacity": 32, "seats_enrolled": 32,
        "waitlist_available": 998, "waitlist_capacity": 999,
        "seat_updated_at": None, "linked_components": (),
    }) for number in numbers)
    return SeatRefreshService(
        CourseRepository(sections),
        cache_path=tmp_path / "fast-seat-cache.json",
        instructor_cache_path=tmp_path / "fast-instructor-cache.json",
        preferred_term="Spring 2027", request_delay_seconds=0,
    )


class FakeSession:
    def __init__(self, *, actual_class: str = "3991", fail: bool = False) -> None:
        self.calls = []
        self.actual_class = actual_class
        self.fail = fail
        self.context = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bootstrap(self):
        return None

    def rehydrate_subject_context(self, *, term, term_code, subject):
        self.context = (term, term_code, subject)

    def fetch_class_detail_tab_page(self, url: str, number: str, tab: str):
        assert url == SOURCE_URL
        assert tab == "CA"
        self.calls.append(number)
        assert self.context == ("Spring 2027", "2273", "CHEM")
        if self.fail:
            raise RuntimeError("PeopleSoft cannot open that exact class")
        return SimpleNamespace(html=self.actual_class)

    def fetch_course_info_page(self, _url):
        raise AssertionError("A requested class should not require a complete option-grid fetch")


def stub_ca(monkeypatch):
    monkeypatch.setattr(
        "classcatalog.seats.parse_class_information_page",
        lambda number: SimpleNamespace(
            class_number=number, selected_tab_value="CA",
            course_label="CHEM 201 General Chemistry",
            status=SeatStatus.WAITLIST,
            seats_available=0, seat_capacity=32, seats_enrolled=32,
            waitlist_available=989, waitlist_capacity=999, waitlist_total=10,
        ),
    )


def test_direct_viewed_3991_is_verified_and_persisted_without_grid(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    stub_ca(monkeypatch)
    client = FakeSession()
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")

    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "verified"
    assert client.calls == ["3991"]
    record = service.seat_records(("3991",))["Spring 2027::3991"]
    assert record["waitlist_available"] == 989
    assert record["waitlist_capacity"] == 999
    assert record["updated_at"] is not None
    # Verifying one class must not falsely stamp its lecture/lab as fresh.
    for number in ("3976", "3983"):
        section = next(row for row in service.repository.sections if row.schedule_number == number)
        assert section.seat_updated_at is None
    assert SOURCE_URL not in service._source_last_success
    throughput = service.public_status()["seat_refresh_throughput"]
    assert throughput["viewed_class_verified_last_300s"] == 1
    assert throughput["verified_viewed_classes_last_60s"] == 1
    assert throughput["section_updates_last_60s"] == 1
    service._flush_cache(force=True)
    assert (tmp_path / "fast-seat-cache.json").is_file()


def test_wrong_class_modal_cannot_stale_to_fresh(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    stub_ca(monkeypatch)
    client = FakeSession(actual_class="3976")
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "unverified"
    assert client.calls == ["3991"]
    row = next(s for s in service.repository.sections if s.schedule_number == "3991")
    assert row.seat_updated_at is None
    assert row.waitlist_available == 998
    assert service._linked_modal_retry_after[(SOURCE_URL, "3991")] > 0
    assert service.public_status()["seat_refresh_throughput"]["viewed_class_unverified_last_300s"] == 1


def test_network_error_does_not_mark_class_fresh(tmp_path):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    client = FakeSession(fail=True)
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "unverified"
    assert next(s for s in service.repository.sections if s.schedule_number == "3991").seat_updated_at is None


def test_no_explicit_view_never_attempts_hidden_class(tmp_path):
    service = make_service(tmp_path)
    client = FakeSession()
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "not_applicable"
    assert client.calls == []


def test_retry_cooldown_prevents_repeated_bad_modal(tmp_path):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    client = FakeSession(fail=True)
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "unverified"
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "not_applicable"
    assert client.calls == ["3991"]


def test_hidden_modal_candidates_are_only_explicitly_viewed(tmp_path, monkeypatch):
    service = make_service(tmp_path, count=5)
    service.register_interest(("3991",))
    stub_ca(monkeypatch)
    client = FakeSession()
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")
    cache_rows, updates = {}, {}
    service._refresh_linked_component_seats(
        client,
        SimpleNamespace(html="", canonical_url=SOURCE_URL),
        service._sources[SOURCE_URL], parsed_term_code="2273",
        cache_rows=cache_rows, updates=updates,
        refreshed_at="2026-11-26T09:30:00+00:00",
    )
    assert client.calls == ["3991"]
    assert list(updates) == [("2273", "3991")]


def test_worker_uses_fast_path_before_full_course_grid(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    stub_ca(monkeypatch)
    client = FakeSession()
    service._session_factory = lambda: client
    queue = [SOURCE_URL]

    def next_source(_iteration):
        if queue:
            return queue.pop(0)
        service._stop.set()
        return None

    monkeypatch.setattr(service, "_next_source", next_source)
    service._run()
    assert client.calls == ["3991"]
    assert service.seat_records(("3991",))["Spring 2027::3991"]["waitlist_available"] == 989
    assert (tmp_path / "fast-seat-cache.json").is_file()
    timing = service.public_status()["seat_refresh_throughput"]["worker_stage_timing_last_300s"]
    assert timing["viewed_class_attempts"] == 1
    assert timing["course_page_attempts"] == 0


def test_worker_http_retries_and_timeout_are_bounded_for_seat_refresh(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    monkeypatch.delenv("CLASSCATALOG_SEAT_HTTP_GET_RETRIES", raising=False)
    monkeypatch.delenv("CLASSCATALOG_SEAT_HTTP_TIMEOUT_SECONDS", raising=False)
    client = service._default_session_factory()
    try:
        assert client.config.timeout_seconds == 20.0
        assert client.config.max_retries == 1
    finally:
        client.close()
    monkeypatch.setenv("CLASSCATALOG_SEAT_HTTP_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("CLASSCATALOG_SEAT_HTTP_GET_RETRIES", "2")
    client = service._default_session_factory()
    try:
        assert client.config.timeout_seconds == 25.0
        assert client.config.max_retries == 2
    finally:
        client.close()


def test_worker_does_not_idle_when_stop_requested(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    client = FakeSession()
    service._session_factory = lambda: client

    def stop_on_next_source(_iteration):
        service._stop.set()
        return None

    monkeypatch.setattr(service, "_next_source", stop_on_next_source)
    monkeypatch.setattr(
        service._wake, "wait",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("A stopped worker must not sleep for five seconds")
        ),
    )
    service._run()


def test_admin_manual_full_refresh_cannot_be_replaced_by_one_modal(tmp_path):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    assert service.request_course_refresh("CHEM 201") == 1
    selected_url = service._next_source(0)
    assert selected_url == SOURCE_URL
    assert service._next_selected_was_manual is True
    # The worker must perform the normal full-grid path for an Admin request.
    assert service._refresh_viewed_class_fast(FakeSession(), SOURCE_URL) == "not_applicable"


def test_right_class_number_but_wrong_course_label_cannot_refresh(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    service.register_interest(("3991",))
    monkeypatch.setattr(
        "classcatalog.seats.parse_class_information_page",
        lambda number: SimpleNamespace(
            class_number=number, selected_tab_value="CA",
            course_label="PHYS 201 Different Course",
            status=SeatStatus.WAITLIST, seats_available=0, seat_capacity=32,
            seats_enrolled=32, waitlist_available=989, waitlist_capacity=999,
            waitlist_total=10,
        ),
    )
    client = FakeSession()
    client.rehydrate_subject_context(term="Spring 2027", term_code="2273", subject="CHEM")
    assert service._refresh_viewed_class_fast(client, SOURCE_URL) == "unverified"
    assert service.seat_records(("3991",))["Spring 2027::3991"]["updated_at"] is None
