from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from classcatalog.models import SeatStatus
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH
from classcatalog.scraping.models import CourseClassOption, CourseInfoRecord
from classcatalog.seats import SeatRefreshService


COURSE_INFO_URL = (
    "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c/"
    "SSR_STUDENT_FL.SSR_CRSE_INFO_FL.GBL?Page=SSR_CRSE_INFO_FL"
)


def _repository_with_live_source() -> CourseRepository:
    base = CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None).sections[0]
    section = base.model_copy(
        update={
            "source_url": COURSE_INFO_URL,
            "term": "Fall 2026",
            "course_code": "MATH 150",
            "subject": "MATH",
            "catalog_number": "150",
            "title": "Calculus I",
            "term_code": "2267",
            "schedule_number": "6528",
            "section_number": "01",
            "crse_id": "038518",
            "crse_offer_nbr": "1",
            "acad_career": "UGRD",
            "seat_status": SeatStatus.OPEN,
            "seats_available": 12,
            "seat_capacity": 30,
            "seats_enrolled": 18,
        }
    )
    duplicate = section.model_copy(update={"id": f"{section.id}-cross-listing"})
    return CourseRepository((section, duplicate))


def _parsed_course() -> CourseInfoRecord:
    return CourseInfoRecord(
        term="Fall 2026",
        term_code="2267",
        subject="MATH",
        catalog_number="150",
        course_code="MATH 150",
        title="Calculus I",
        units=4,
        units_min=4,
        units_max=4,
        units_text="4 units",
        grading="letter",
        source_url=COURSE_INFO_URL,
        options=(
            CourseClassOption(
                option_number=1,
                option_group_index=1,
                status=SeatStatus.WAITLIST,
                class_number="6528",
                open_seats=0,
                seat_capacity=30,
                seats_enrolled=30,
                source_row_index=1,
            ),
        ),
    )


class FakeClient:
    def reset_public_session(self) -> None:
        pass

    def bootstrap(self) -> str:
        return "ok"

    def fetch_course_info_page(self, url: str):
        assert url == COURSE_INFO_URL
        return SimpleNamespace(
            html="<html></html>",
            canonical_url=COURSE_INFO_URL,
            expansion_count=0,
        )


def test_refresh_updates_all_duplicate_listings_and_persists_cache(monkeypatch, tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    cache = tmp_path / "seat_cache.json"
    service = SeatRefreshService(repository, cache_path=cache, request_delay_seconds=0)
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed_course())

    service._refresh_source(FakeClient(), COURSE_INFO_URL)
    service._flush_cache(force=True)

    assert all(section.seat_status is SeatStatus.WAITLIST for section in repository.sections)
    assert all(section.seats_available == 0 for section in repository.sections)
    assert all(section.seats_enrolled == 30 for section in repository.sections)
    assert cache.is_file()
    status = service.admin_status()
    assert status["cached_sections"] == 1
    assert status["successful_course_refreshes"] == 1
    assert status["failed_course_refreshes"] == 0


def test_cached_seats_are_applied_immediately_on_restart(monkeypatch, tmp_path: Path) -> None:
    first_repository = _repository_with_live_source()
    cache = tmp_path / "seat_cache.json"
    first = SeatRefreshService(first_repository, cache_path=cache, request_delay_seconds=0)
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed_course())
    first._refresh_source(FakeClient(), COURSE_INFO_URL)
    first._flush_cache(force=True)

    restarted_repository = _repository_with_live_source()
    assert restarted_repository.sections[0].seats_available == 12
    restarted = SeatRefreshService(restarted_repository, cache_path=cache, request_delay_seconds=0)

    assert restarted.repository.sections[0].seats_available == 0
    assert restarted.repository.sections[0].seat_status is SeatStatus.WAITLIST


def test_interest_prioritizes_the_source_without_triggering_network(tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    assert service.register_interest(("6528",)) == 1
    status = service.admin_status()
    assert status["priority_sources"] == 1
    assert status["requests_completed"] == 0


def test_manual_refresh_queues_each_course_page_once(tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    assert service.request_full_refresh() == 1
    assert service.request_full_refresh() == 1
    assert service.admin_status()["manual_refresh_pending"] == 1


def test_specific_course_refresh_queues_all_of_its_course_pages(tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    assert service.request_course_refresh(" math   150 ") == 1
    assert service.request_course_refresh("UNKNOWN 999") == 0
    assert service.admin_status()["manual_refresh_pending"] == 1
    assert service.admin_status()["refreshable_course_codes"] == ["MATH 150"]


class BrokenSeatClient:
    def fetch_course_info_page(self, _url: str):
        raise RuntimeError("simulated broken course page")


def test_failures_are_retained_aggregated_logged_and_marked_recovered(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    with caplog.at_level("ERROR", logger="classcatalog.seats"):
        service._refresh_source(BrokenSeatClient(), COURSE_INFO_URL)
        service._refresh_source(BrokenSeatClient(), COURSE_INFO_URL)

    failures = service.admin_status()["seat_failures"]
    assert len(failures) == 1
    assert failures[0]["course_code"] == "MATH 150"
    assert failures[0]["course_codes"] == ("MATH 150",)
    assert failures[0]["source_url"] == COURSE_INFO_URL
    assert failures[0]["error_type"] == "RuntimeError"
    assert failures[0]["detail"] == "simulated broken course page"
    assert failures[0]["occurrences"] == 2
    assert failures[0]["resolved"] is False
    assert "seat_refresh_failed course=MATH 150" in caplog.text

    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed_course())
    service._refresh_source(FakeClient(), COURSE_INFO_URL)

    recovered = service.admin_status()["seat_failures"]
    assert len(recovered) == 1
    assert recovered[0]["occurrences"] == 2
    assert recovered[0]["resolved"] is True
    assert service.admin_status()["seat_failure_retention_limit"] == 100


class RecoveryClient(FakeClient):
    def __init__(self) -> None:
        self.direct_attempts = 0
        self.recovery_calls = 0
        self.rehydrate_calls = []

    def rehydrate_subject_context(self, **kwargs):
        self.rehydrate_calls.append(kwargs)
        return SimpleNamespace()

    def fetch_course_info_page(self, url: str):
        from classcatalog.scraping.session import PeopleSoftSessionError
        self.direct_attempts += 1
        raise PeopleSoftSessionError("stale component state")

    def fetch_detail_pages(self, url: str):
        self.recovery_calls += 1
        assert "SSR_CS_WRAP_FL.GBL" in url
        assert "CRSE_ID=038518" in url
        assert "SEC=01" in url
        return SimpleNamespace(
            course_info_html="<html></html>",
            course_info_url=COURSE_INFO_URL + "&resolved=1",
            course_info_expansion_count=0,
        )


def test_stale_direct_url_falls_back_to_reconstructed_course_wrapper(monkeypatch, tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: _parsed_course())
    client = RecoveryClient()

    service._refresh_source(client, COURSE_INFO_URL)

    assert client.direct_attempts == 2
    assert client.recovery_calls == 1
    assert client.rehydrate_calls == [
        {"term": "Fall 2026", "term_code": "2267", "subject": "MATH"}
    ]
    assert repository.sections[0].seat_status is SeatStatus.WAITLIST
    assert service.admin_status()["successful_course_refreshes"] == 1

class ParseFailureRecoveryClient(FakeClient):
    def __init__(self) -> None:
        self.direct_attempts = 0
        self.recovery_calls = 0
        self.reset_calls = 0
        self.bootstrap_calls = 0

    def fetch_course_info_page(self, url: str):
        self.direct_attempts += 1
        return SimpleNamespace(
            html=f"<html>wrong-state-{self.direct_attempts}</html>",
            canonical_url=url,
            expansion_count=0,
        )

    def reset_public_session(self) -> None:
        self.reset_calls += 1

    def bootstrap(self) -> str:
        self.bootstrap_calls += 1
        return "ok"

    def rehydrate_subject_context(self, **kwargs):
        self.rehydrate_calls = getattr(self, "rehydrate_calls", [])
        self.rehydrate_calls.append(kwargs)
        return SimpleNamespace()

    def fetch_detail_pages(self, url: str):
        self.recovery_calls += 1
        assert "Action=U" in url
        assert "INSTITUTION=SDCMP" in url
        assert "CRSE_ID=038518" in url
        assert "SEC=01" in url
        return SimpleNamespace(
            course_info_html="<html>recovered-course-info</html>",
            course_info_url=COURSE_INFO_URL + "&resolved=1",
            course_info_expansion_count=0,
        )


def test_http_200_wrong_people_soft_state_triggers_wrapper_recovery(monkeypatch, tmp_path: Path) -> None:
    from classcatalog.scraping.parser import ResultParseError

    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)
    calls = 0

    def parse(html: str, **_kwargs):
        nonlocal calls
        calls += 1
        if "recovered-course-info" not in html:
            raise ResultParseError("The course-information page is missing its subject/catalog field.")
        return _parsed_course()

    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", parse)
    client = ParseFailureRecoveryClient()

    service._refresh_source(client, COURSE_INFO_URL)

    assert client.direct_attempts == 2
    assert client.reset_calls == 1
    assert client.bootstrap_calls == 1
    assert client.recovery_calls == 1
    assert client.rehydrate_calls == [
        {"term": "Fall 2026", "term_code": "2267", "subject": "MATH"}
    ]
    assert calls == 3
    assert repository.sections[0].seat_status is SeatStatus.WAITLIST
    status = service.admin_status()
    assert status["successful_course_refreshes"] == 1
    assert status["failed_course_refreshes"] == 0
    assert status["last_error"] is None


class SearchStateRequiredRecoveryClient(ParseFailureRecoveryClient):
    def __init__(self) -> None:
        super().__init__()
        self.search_state_ready = False

    def rehydrate_subject_context(self, **kwargs):
        result = super().rehydrate_subject_context(**kwargs)
        self.search_state_ready = True
        return result

    def fetch_detail_pages(self, url: str):
        assert self.search_state_ready, "course wrapper was opened before subject state was primed"
        return super().fetch_detail_pages(url)


def test_recovery_rehydrates_exact_subject_state_before_opening_wrapper(monkeypatch, tmp_path: Path) -> None:
    from classcatalog.scraping.parser import ResultParseError

    repository = _repository_with_live_source()
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    def parse(html: str, **_kwargs):
        if "recovered-course-info" not in html:
            raise ResultParseError("The course-information page is missing its subject/catalog field.")
        return _parsed_course()

    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", parse)
    client = SearchStateRequiredRecoveryClient()

    service._refresh_source(client, COURSE_INFO_URL)

    assert client.search_state_ready is True
    assert client.recovery_calls == 1
    assert repository.sections[0].seats_available == 0
    assert service.admin_status()["successful_course_refreshes"] == 1


def test_positive_open_seats_force_waitlist_status_back_to_open(monkeypatch, tmp_path: Path) -> None:
    repository = _repository_with_live_source()
    repository.apply_seat_updates({
        ("2267", "6528"): {
            "seat_status": SeatStatus.WAITLIST,
            "seats_available": 0,
            "seats_enrolled": 30,
            "seat_capacity": 30,
        }
    })
    service = SeatRefreshService(repository, cache_path=tmp_path / "seat_cache.json", request_delay_seconds=0)

    opened = _parsed_course().model_copy(
        update={
            "options": (
                _parsed_course().options[0].model_copy(
                    update={
                        # Exercise the defensive transition: even if PeopleSoft's
                        # status text lags, positive open seats mean the class is open.
                        "status": SeatStatus.WAITLIST,
                        "open_seats": 3,
                        "seat_capacity": 30,
                        "seats_enrolled": 27,
                    }
                ),
            )
        }
    )
    monkeypatch.setattr("classcatalog.seats.parse_course_info_page", lambda *_args, **_kwargs: opened)

    service._refresh_source(FakeClient(), COURSE_INFO_URL)

    section = repository.sections[0]
    assert section.seat_status is SeatStatus.OPEN
    assert section.seats_available == 3
    assert section.seats_enrolled == 27
    assert section.seat_updated_at is not None
    records = service.seat_records(("6528",))
    assert records["Fall 2026::6528"]["seat_status"] == "open"
    assert records["Fall 2026::6528"]["updated_at"] == section.seat_updated_at


def test_live_seat_defaults_expose_faster_priority_and_stale_threshold(tmp_path: Path) -> None:
    service = SeatRefreshService(
        _repository_with_live_source(),
        cache_path=tmp_path / "seat_cache.json",
        request_delay_seconds=0,
    )

    status = service.public_status()
    assert service.priority_interval_seconds == 180.0
    assert service.browser_poll_seconds == 30
    assert service.stale_after_seconds == 1800
    assert status["browser_poll_seconds"] == 30
    assert status["stale_after_seconds"] == 1800
    assert status["priority_interval_seconds"] == 180.0
    assert status["fresh_cached_sections"] == 0
    assert status["stale_cached_sections"] == 0
