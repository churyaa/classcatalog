from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

from classcatalog.api_validation.models import (
    ApiCheckStatus,
    ApiPerformanceMetrics,
    ApiValidationCheck,
    ApiValidationCounts,
    ApiValidationReport,
)
from classcatalog.catalog.repository import CatalogRepository
from classcatalog.filters import SearchFilters
from classcatalog.main import create_app
from classcatalog.models import (
    CourseSection,
    GradingType,
    InstructionMode,
    ProgramClassification,
    SearchResponse,
    SeatStatus,
    SortBy,
    Weekday,
)
from classcatalog.repository import CourseRepository
from classcatalog.scraping.progress import atomic_write


@dataclass(frozen=True, slots=True)
class ApiValidationConfig:
    data_path: Path | None = None
    catalog_path: Path | None = None
    expected_sections: int | None = None
    expected_courses: int | None = None
    expected_physical_sections: int | None = None
    expected_subjects: int | None = None
    full_pagination: bool = True
    performance_warning_ms: float = 30_000.0


@dataclass(frozen=True, slots=True)
class ApiValidationResult:
    report: ApiValidationReport
    exit_code: int


@dataclass(frozen=True, slots=True)
class _CheckOutcome:
    status: ApiCheckStatus
    message: str
    details: dict[str, object]


CheckCallback = Callable[[], _CheckOutcome]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _pass(message: str, **details: object) -> _CheckOutcome:
    return _CheckOutcome(ApiCheckStatus.PASS, message, details)


def _skip(message: str, **details: object) -> _CheckOutcome:
    return _CheckOutcome(ApiCheckStatus.SKIPPED, message, details)


def _natural_parts(value: str) -> tuple[tuple[int, int | str], ...]:
    parts: list[tuple[int, int | str]] = []
    for part in re.split(r"(\d+)", value.casefold()):
        if not part:
            continue
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def _payload_alphabetical_key(item: dict[str, Any]) -> tuple[object, ...]:
    return (
        _natural_parts(str(item.get("subject", ""))),
        _natural_parts(str(item.get("catalog_number", ""))),
        _natural_parts(str(item.get("section_number", ""))),
    )


def _section_haystack(item: dict[str, Any]) -> str:
    values = (
        item.get("course_code"),
        item.get("title"),
        item.get("description"),
        item.get("instructor"),
    )
    return " ".join(str(value) for value in values if value).casefold()


def _run_check(
    name: str,
    callback: CheckCallback,
    *,
    performance_warning_ms: float,
) -> ApiValidationCheck:
    started = perf_counter()
    try:
        outcome = callback()
    except AssertionError as exc:
        outcome = _CheckOutcome(
            ApiCheckStatus.FAIL,
            str(exc) or "Validation assertion failed.",
            {},
        )
    except Exception as exc:  # noqa: BLE001 - unexpected failures belong in the report.
        outcome = _CheckOutcome(
            ApiCheckStatus.FAIL,
            f"{type(exc).__name__}: {exc}",
            {},
        )
    duration_ms = round((perf_counter() - started) * 1_000, 2)
    status = outcome.status
    message = outcome.message
    if status is ApiCheckStatus.PASS and duration_ms > performance_warning_ms:
        status = ApiCheckStatus.WARNING
        message = (
            f"{message} The check took {duration_ms:.2f} ms, above the "
            f"{performance_warning_ms:.2f} ms warning threshold."
        )
    return ApiValidationCheck(
        name=name,
        status=status,
        message=message,
        duration_ms=duration_ms,
        details=outcome.details,
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _response_payload(response: Any, *, expected_status: int = 200) -> dict[str, Any]:
    assert response.status_code == expected_status, (
        f"Expected HTTP {expected_status}, received {response.status_code}: "
        f"{response.text[:500]}"
    )
    payload = response.json()
    assert isinstance(payload, dict), "The endpoint did not return a JSON object."
    return payload


def validate_api_sections(
    sections: Sequence[CourseSection],
    *,
    config: ApiValidationConfig | None = None,
    catalog: CatalogRepository | None = None,
) -> ApiValidationResult:
    settings = config or ApiValidationConfig()
    repository = CourseRepository(sections, catalog=catalog)
    app = create_app(
        repository,
        data_path=settings.data_path,
        catalog_path=settings.catalog_path,
    )
    checks: list[ApiValidationCheck] = []
    request_durations: list[float] = []

    def add(name: str, callback: CheckCallback) -> None:
        checks.append(
            _run_check(
                name,
                callback,
                performance_warning_ms=settings.performance_warning_ms,
            )
        )

    def data_invariants() -> _CheckOutcome:
        assert repository.total > 0, "The production API dataset contains no sections."
        ids = [section.id for section in repository.sections]
        assert len(ids) == len(set(ids)), "Normalized section IDs are not unique."
        assert repository.course_total > 0, "No logical courses were detected."
        assert repository.physical_section_total > 0, "No physical class numbers were detected."
        assert repository.physical_section_total <= repository.total, (
            "Unique physical sections cannot exceed API course-section listings."
        )
        return _pass(
            "Production records load and their identifiers are internally consistent.",
            course_section_listings=repository.total,
            logical_courses=repository.course_total,
            physical_sections=repository.physical_section_total,
            subjects=repository.subject_total,
        )

    add("data_invariants", data_invariants)

    def expected_counts() -> _CheckOutcome:
        expected = {
            "course_section_listings": settings.expected_sections,
            "logical_courses": settings.expected_courses,
            "physical_sections": settings.expected_physical_sections,
            "subjects": settings.expected_subjects,
        }
        actual = {
            "course_section_listings": repository.total,
            "logical_courses": repository.course_total,
            "physical_sections": repository.physical_section_total,
            "subjects": repository.subject_total,
        }
        supplied = {key: value for key, value in expected.items() if value is not None}
        if not supplied:
            return _skip("No expected production counts were supplied.", actual=actual)
        for key, value in supplied.items():
            assert actual[key] == value, (
                f"Expected {key}={value}, but the loaded API data reports {actual[key]}."
            )
        return _pass("Loaded API counts match the expected production manifest.", **actual)

    add("expected_counts", expected_counts)

    with TestClient(app) as client:

        def get(path: str, *, params: Any = None) -> Any:
            started = perf_counter()
            response = client.get(path, params=params)
            request_durations.append((perf_counter() - started) * 1_000)
            return response

        def health_endpoint() -> _CheckOutcome:
            payload = _response_payload(get("/api/health"))
            assert payload["status"] == "ok", "The health endpoint did not report ok."
            assert payload["classes"] == repository.total, "Health class count is stale."
            assert payload["courses"] == repository.course_total, "Health course count is stale."
            assert payload["physical_sections"] == repository.physical_section_total, (
                "Health physical-section count is stale."
            )
            assert payload["subjects"] == repository.subject_total, (
                "Health subject count is stale."
            )
            return _pass(
                "The health endpoint reports the production repository counts.",
                **payload,
            )

        add("health_endpoint", health_endpoint)

        def options_endpoint() -> _CheckOutcome:
            payload = _response_payload(get("/api/options"))
            expected = repository.options().model_dump(mode="json")
            assert payload == expected, "The options endpoint differs from repository options."
            assert payload["terms"], "No semester option is available."
            assert payload["gradings"], "No grading options are available."
            assert payload["instruction_modes"], "No instruction-mode options are available."
            assert payload["seat_statuses"], "No seat-status options are available."
            return _pass(
                "The filter-options endpoint matches the production dataset.",
                terms=len(payload["terms"]),
                requirements=len(payload["requirements"]),
                programs=len(payload["programs"]),
                gradings=len(payload["gradings"]),
                instruction_modes=len(payload["instruction_modes"]),
                seat_statuses=len(payload["seat_statuses"]),
            )

        add("options_endpoint", options_endpoint)

        def default_pagination() -> _CheckOutcome:
            expected = repository.search(
                SearchFilters(page=1, page_size=50)
            )
            payload = _response_payload(
                get("/api/classes", params={"page": 1, "page_size": 50})
            )

            assert payload["unfiltered_total"] == expected.unfiltered_total, (
                "The API unfiltered total does not match grouped repository semantics."
            )
            assert payload["filtered_total"] == expected.filtered_total, (
                "The default request unexpectedly changed the grouped result count."
            )
            assert payload["page"] == expected.page, (
                "The first page did not resolve to page 1."
            )
            assert payload["page_size"] == expected.page_size, (
                "The production page size is not 50."
            )
            assert payload["total_pages"] == expected.total_pages, (
                "The total page count is incorrect."
            )

            actual_ids = [item["id"] for item in payload["items"]]
            expected_ids = [item.id for item in expected.items]
            assert actual_ids == expected_ids, (
                "The first API page differs from direct repository search."
            )

            return _pass(
                "Default pagination matches grouped repository search semantics.",
                filtered_total=payload["filtered_total"],
                page_size=payload["page_size"],
                total_pages=payload["total_pages"],
            )

        add("default_pagination", default_pagination)

        def full_pagination() -> _CheckOutcome:
            if not settings.full_pagination:
                return _skip("Full-page traversal was disabled.")

            expected_first = repository.search(
                SearchFilters(page=1, page_size=50)
            )
            total_pages = expected_first.total_pages
            seen: list[str] = []

            for page in range(1, total_pages + 1):
                expected = repository.search(
                    SearchFilters(page=page, page_size=50)
                )
                payload = _response_payload(
                    get(
                        "/api/classes",
                        params={"page": page, "page_size": 50},
                    )
                )

                assert payload["page"] == expected.page, (
                    f"Requested page {page} returned another page."
                )
                assert payload["total_pages"] == expected.total_pages, (
                    "The total page count changed during traversal."
                )
                assert payload["filtered_total"] == expected.filtered_total, (
                    "The filtered total changed during traversal."
                )
                assert payload["unfiltered_total"] == expected.unfiltered_total, (
                    "The unfiltered total changed during traversal."
                )

                actual_keys = [
                    _payload_display_option_key(item)
                    for item in payload["items"]
                ]
                expected_keys = [
                    _section_display_option_key(item)
                    for item in expected.items
                ]

                assert actual_keys == expected_keys, (
                    f"API page {page} differs from direct repository search."
                )

                seen.extend(actual_keys)

            assert len(seen) == expected_first.filtered_total, (
                "Traversing every page did not return every displayed enrollment option."
            )
            assert len(set(seen)) == len(seen), (
                "Full pagination returned duplicate displayed enrollment options."
            )

            return _pass(
                "Every grouped production page was traversed with no omissions or duplicate IDs.",
                pages=total_pages,
                listings=len(seen),
            )

        add("full_pagination", full_pagination)

        def pagination_boundaries() -> _CheckOutcome:
            expected_first = repository.search(
                SearchFilters(page=1, page_size=50)
            )
            total_pages = expected_first.total_pages

            expected_last = repository.search(
                SearchFilters(page=total_pages, page_size=50)
            )
            last = _response_payload(
                get(
                    "/api/classes",
                    params={"page": total_pages, "page_size": 50},
                )
            )

            assert [item["id"] for item in last["items"]] == [
                item.id for item in expected_last.items
            ], "The final API page differs from direct repository search."

            expected_last_count = len(expected_last.items)
            assert len(last["items"]) == expected_last_count, (
                "The final page size is incorrect."
            )

            beyond_page = total_pages + 100
            expected_beyond = repository.search(
                SearchFilters(page=beyond_page, page_size=50)
            )
            beyond = _response_payload(
                get(
                    "/api/classes",
                    params={"page": beyond_page, "page_size": 50},
                )
            )

            assert beyond["page"] == expected_beyond.page == total_pages, (
                "A page beyond the end was not clamped."
            )
            assert [item["id"] for item in beyond["items"]] == [
                item.id for item in expected_beyond.items
            ], "The clamped API page differs from repository semantics."

            assert get(
                "/api/classes",
                params={"page_size": 51},
            ).status_code == 422, (
                "The API accepted a page size above 50."
            )
            assert get(
                "/api/classes",
                params={"page": 0},
            ).status_code == 422, (
                "The API accepted page zero."
            )

            return _pass(
                "Last-page sizing, clamping, and request bounds are correct.",
                total_pages=total_pages,
                last_page_items=expected_last_count,
            )

        add("pagination_boundaries", pagination_boundaries)

        def no_results() -> _CheckOutcome:
            payload = _response_payload(
                get(
                    "/api/classes",
                    params={"q": "__classcatalog_no_match_9fef3b7e__", "page_size": 50},
                )
            )
            assert payload["filtered_total"] == 0, "The impossible query returned matches."
            assert payload["items"] == [], "The impossible query returned result items."
            assert payload["page"] == 1 and payload["total_pages"] == 1, (
                "A zero-result response did not retain the page-1 contract."
            )
            return _pass("Zero-result searches return a stable empty pagination response.")

        add("no_results", no_results)

        def query_search() -> _CheckOutcome:
            counts = Counter(section.course_code for section in repository.sections)
            course_code = min(counts, key=lambda value: (counts[value], value))
            expected = repository.search(
                SearchFilters(query=course_code, page=1, page_size=50)
            )
            payload = _response_payload(
                get("/api/classes", params={"q": course_code, "page_size": 50})
            )
            assert payload["filtered_total"] == expected.filtered_total, (
                "Text-search total differs from direct repository filtering."
            )
            query = course_code.casefold()
            assert all(query in _section_haystack(item) for item in payload["items"]), (
                "Text search returned an item that does not contain the query."
            )
            return _pass(
                "Course/title/instructor text search matches repository semantics.",
                query=course_code,
                matches=payload["filtered_total"],
            )

        add("query_search", query_search)

        def term_filter() -> _CheckOutcome:
            term = Counter(section.term for section in repository.sections).most_common(1)[0][0]
            expected = repository.search(
                SearchFilters(terms=(term,), page=1, page_size=50)
            )
            payload = _response_payload(
                get("/api/classes", params=[("term", term), ("page_size", "50")])
            )
            assert payload["filtered_total"] == expected.filtered_total, (
                "Semester filtering differs from direct repository filtering."
            )
            assert all(item["term"] == term for item in payload["items"]), (
                "The semester filter leaked a different term."
            )
            return _pass(
                "Semester filtering returns only the selected term.",
                term=term,
                matches=payload["filtered_total"],
            )

        add("term_filter", term_filter)

        def enum_filters() -> _CheckOutcome:
            mode = Counter(
                section.instruction_mode for section in repository.sections
            ).most_common(1)[0][0]
            status = Counter(
                section.seat_status for section in repository.sections
            ).most_common(1)[0][0]
            grading = Counter(
                section.grading for section in repository.sections
            ).most_common(1)[0][0]
            cases: tuple[
                tuple[str, InstructionMode | SeatStatus | GradingType, str], ...
            ] = (
                ("instruction_mode", mode, "instruction_mode"),
                ("seat_status", status, "seat_status"),
                ("grading", grading, "grading"),
            )
            totals: dict[str, object] = {}
            for parameter, value, response_field in cases:
                payload = _response_payload(
                    get(
                        "/api/classes",
                        params=[(parameter, value.value), ("page_size", "50")],
                    )
                )
                assert payload["filtered_total"] >= 1, (
                    f"Known {parameter} value {value.value!r} returned no classes."
                )
                assert all(
                    item[response_field] == value.value for item in payload["items"]
                ), f"The {parameter} filter leaked a different value."
                totals[parameter] = payload["filtered_total"]
            return _pass(
                "Format, seat-status, and grading filters return selected values only.",
                **totals,
            )

        add("enum_filters", enum_filters)

        def units_filter() -> _CheckOutcome:
            chosen = repository.sections[0]
            lower = chosen.units_min if chosen.units_min is not None else chosen.units
            upper = chosen.units_max if chosen.units_max is not None else chosen.units
            target = (lower + upper) / 2
            expected = repository.search(
                SearchFilters(
                    query=chosen.course_code,
                    units_min=target,
                    units_max=target,
                    page=1,
                    page_size=50,
                )
            )
            payload = _response_payload(
                get(
                    "/api/classes",
                    params={
                        "q": chosen.course_code,
                        "units_min": target,
                        "units_max": target,
                        "page_size": 50,
                    },
                )
            )
            assert payload["filtered_total"] == expected.filtered_total, (
                "Units filtering differs from direct repository filtering."
            )
            assert payload["filtered_total"] >= 1, (
                "The selected course was lost from an overlapping units query."
            )
            for item in payload["items"]:
                lower = item["units_min"] if item["units_min"] is not None else item["units"]
                upper = item["units_max"] if item["units_max"] is not None else item["units"]
                assert lower <= target <= upper, "Units filtering did not use range overlap."
            return _pass(
                "Fixed and variable-unit ranges obey overlap filtering.",
                query=chosen.course_code,
                target_units=target,
                matches=payload["filtered_total"],
            )

        add("units_filter", units_filter)

        def program_filters() -> _CheckOutcome:
            tagged = next(
                (section for section in repository.sections if section.program_tags),
                None,
            )
            if tagged is None:
                return _skip(
                    "The current SDSU dataset has no normalized program tags yet; program "
                    "filtering remains covered by unit tests."
                )
            tag = tagged.program_tags[0]
            cases: tuple[tuple[str, str], ...] = (
                ("program", tag.program),
                ("catalog_year", tag.catalog_year),
                ("classification", tag.classification.value),
            )
            details: dict[str, object] = {}
            for parameter, value in cases:
                payload = _response_payload(
                    get("/api/classes", params={parameter: value, "page_size": 50})
                )
                assert payload["filtered_total"] >= 1, (
                    f"Known {parameter} value {value!r} returned no classes."
                )
                details[parameter] = payload["filtered_total"]
            return _pass(
                "Major, catalog-year, and classification filters work independently.",
                **details,
            )

        add("program_filters", program_filters)

        def catalog_endpoints() -> _CheckOutcome:
            status = _response_payload(get("/api/catalog/status"))
            if not repository.catalog.loaded:
                assert status["loaded"] is False
                return _skip("No public catalog overlay was supplied.")
            assert status["loaded"] is True, "Catalog status did not report the overlay."
            assert status["programs"] == len(repository.catalog.programs)
            assert status["requirements"] == len(
                repository.catalog.mappings.requirements
                if repository.catalog.mappings is not None
                else ()
            )
            program = None
            for candidate in repository.catalog.programs:
                candidate_summary = repository.catalog.program_summary(
                    candidate.name,
                    candidate.catalog_year,
                    scheduled_course_codes=repository.scheduled_course_codes,
                )
                if candidate_summary is not None and candidate_summary.scheduled_course_count > 0:
                    program = candidate
                    break
            if program is None:
                return _skip(
                    "The catalog overlay loaded, but no program mapping intersects the schedule."
                )
            payload = _response_payload(
                get(
                    "/api/catalog/program",
                    params={
                        "program": program.name,
                        "catalog_year": program.catalog_year,
                    },
                )
            )
            assert payload["scheduled_course_count"] >= 1
            profile = _response_payload(
                get(
                    "/api/profile/summary",
                    params={
                        "program": program.name,
                        "catalog_year": program.catalog_year,
                    },
                )
            )
            assert profile["mapped_course_count"] == payload["mapped_course_count"]
            return _pass(
                "Catalog status, program summary, and Student Profile endpoints work.",
                programs=status["programs"],
                requirements=status["requirements"],
                example_program=program.name,
            )

        add("catalog_endpoints", catalog_endpoints)

        def requirement_filter() -> _CheckOutcome:
            tagged = next(
                (section for section in repository.sections if section.requirement_tags),
                None,
            )
            if tagged is None:
                return _skip(
                    "The current dataset has no public requirement mappings yet."
                )
            requirement = tagged.requirement_tags[0]
            payload = _response_payload(
                get(
                    "/api/classes",
                    params={"requirement": requirement, "page_size": 50},
                )
            )
            assert payload["filtered_total"] >= 1, (
                f"Known requirement {requirement!r} returned no classes."
            )
            assert all(
                requirement in item["requirement_tags"] for item in payload["items"]
            ), "Requirement filtering leaked an untagged result."
            return _pass(
                "Public requirement filtering returns only tagged classes.",
                requirement=requirement,
                matches=payload["filtered_total"],
            )

        add("requirement_filter", requirement_filter)

        def meeting_filters() -> _CheckOutcome:
            day_counts = Counter(
                day
                for section in repository.sections
                for meeting in section.meetings
                for day in meeting.days
            )
            if not day_counts:
                return _skip("No meeting-day rows exist in the production dataset.")
            day = day_counts.most_common(1)[0][0]
            expected_day = repository.search(
                SearchFilters(days=(day,), page=1, page_size=50)
            )
            payload = _response_payload(
                get("/api/classes", params={"day": day.value, "page_size": 50})
            )
            assert payload["filtered_total"] == expected_day.filtered_total, (
                "Day filtering differs from direct repository filtering."
            )
            assert all(
                any(day.value in meeting["days"] for meeting in item["meetings"])
                for item in payload["items"]
            ), "The day filter returned a section without the selected day."

            timed_candidate = next(
                (
                    section
                    for section in repository.sections
                    if section.meetings
                    and all(
                        meeting.start_time is not None and meeting.end_time is not None
                        for meeting in section.meetings
                    )
                ),
                None,
            )
            details: dict[str, object] = {
                "day": day.value,
                "day_matches": payload["filtered_total"],
            }
            if timed_candidate is None:
                return _pass(
                    "Day filtering passed; no fully timed section was available for a window test.",
                    **details,
                )
            starts = [meeting.start_time for meeting in timed_candidate.meetings]
            ends = [meeting.end_time for meeting in timed_candidate.meetings]
            time_from = min(value for value in starts if value is not None)
            time_to = max(value for value in ends if value is not None)
            expected_time = repository.search(
                SearchFilters(
                    query=timed_candidate.course_code,
                    time_from=time_from,
                    time_to=time_to,
                    page=1,
                    page_size=50,
                )
            )
            timed = _response_payload(
                get(
                    "/api/classes",
                    params={
                        "q": timed_candidate.course_code,
                        "time_from": time_from.isoformat(timespec="minutes"),
                        "time_to": time_to.isoformat(timespec="minutes"),
                        "page_size": 50,
                    },
                )
            )
            assert timed["filtered_total"] == expected_time.filtered_total, (
                "Time-window filtering differs from direct repository filtering."
            )
            assert timed["filtered_total"] >= 1, "A known meeting window returned no matches."
            details.update(
                {
                    "time_from": time_from.isoformat(timespec="minutes"),
                    "time_to": time_to.isoformat(timespec="minutes"),
                    "time_matches": timed["filtered_total"],
                }
            )
            return _pass("Meeting-day and time-window filters work.", **details)

        add("meeting_filters", meeting_filters)

        def combined_filters() -> _CheckOutcome:
            seed = repository.sections[0]
            filters = SearchFilters(
                terms=(seed.term,),
                query=seed.course_code,
                gradings=(seed.grading,),
                instruction_modes=(seed.instruction_mode,),
                seat_statuses=(seed.seat_status,),
                page=1,
                page_size=50,
            )
            expected = repository.search(filters)
            payload = _response_payload(
                get(
                    "/api/classes",
                    params=[
                        ("term", seed.term),
                        ("q", seed.course_code),
                        ("grading", seed.grading.value),
                        ("instruction_mode", seed.instruction_mode.value),
                        ("seat_status", seed.seat_status.value),
                        ("page_size", "50"),
                    ],
                )
            )
            assert payload["filtered_total"] == expected.filtered_total, (
                "Combined filters differ from direct repository filtering."
            )
            assert 1 <= payload["filtered_total"] <= repository.total, (
                "A known combined filter unexpectedly returned no rows."
            )
            return _pass(
                "Combined semester, query, grading, format, and seat filters compose correctly.",
                matches=payload["filtered_total"],
            )

        add("combined_filters", combined_filters)

        def sorting() -> _CheckOutcome:
            for sort_by in (SortBy.COURSE_A_Z, SortBy.COURSE_Z_A):
                payload = _response_payload(
                    get(
                        "/api/classes",
                        params={"sort_by": sort_by.value, "page_size": 50},
                    )
                )
                expected = repository.search(
                    SearchFilters(
                        sort_by=sort_by,
                        page=1,
                        page_size=50,
                    )
                )

                assert [item["id"] for item in payload["items"]] == [
                    item.id for item in expected.items
                ], f"Sort {sort_by.value!r} differs from repository order."

                assert payload["filtered_total"] == expected.filtered_total, (
                    f"Sort {sort_by.value!r} differs from repository grouped-result count."
                )

                ordered = sorted(
                    payload["items"],
                    key=_payload_alphabetical_key,
                    reverse=sort_by is SortBy.COURSE_Z_A,
                )
                assert payload["items"] == ordered, (
                    f"Sort {sort_by.value!r} is not naturally ordered."
                )

            metric_sorts = (
                SortBy.PROFESSOR_RATING_LOW_TO_HIGH,
                SortBy.PROFESSOR_RATING_HIGH_TO_LOW,
                SortBy.CLASS_DIFFICULTY_LOW_TO_HIGH,
                SortBy.CLASS_DIFFICULTY_HIGH_TO_LOW,
                SortBy.PROFESSOR_DIFFICULTY_LOW_TO_HIGH,
                SortBy.PROFESSOR_DIFFICULTY_HIGH_TO_LOW,
                SortBy.REVIEWS_LOW_TO_HIGH,
                SortBy.REVIEWS_HIGH_TO_LOW,
                SortBy.TAKE_AGAIN_LOW_TO_HIGH,
                SortBy.TAKE_AGAIN_HIGH_TO_LOW,
            )

            for sort_by in metric_sorts:
                payload = _response_payload(
                    get(
                        "/api/classes",
                        params={"sort_by": sort_by.value, "page_size": 50},
                    )
                )
                expected = repository.search(
                    SearchFilters(
                        sort_by=sort_by,
                        page=1,
                        page_size=50,
                    )
                )

                assert payload["filtered_total"] == expected.filtered_total, (
                    f"Sort {sort_by.value!r} differs from repository grouped-result count."
                )
                assert payload["unfiltered_total"] == expected.unfiltered_total, (
                    f"Sort {sort_by.value!r} changed the unfiltered grouped count."
                )
                assert [item["id"] for item in payload["items"]] == [
                    item.id for item in expected.items
                ], f"Sort {sort_by.value!r} differs from repository order."

            return _pass(
                "All UI sort values match repository grouped-result semantics.",
                sort_modes_checked=12,
            )

        add("sorting", sorting)

        def filtered_pagination() -> _CheckOutcome:
            mode = Counter(
                section.instruction_mode
                for section in repository.sections
            ).most_common(1)[0][0]

            expected_first = repository.search(
                SearchFilters(
                    instruction_modes=(mode,),
                    page=1,
                    page_size=50,
                )
            )
            first = _response_payload(
                get(
                    "/api/classes",
                    params={
                        "instruction_mode": mode.value,
                        "page": 1,
                        "page_size": 50,
                    },
                )
            )

            assert first["filtered_total"] == expected_first.filtered_total, (
                "Filtered pagination count differs from repository semantics."
            )
            assert first["total_pages"] == expected_first.total_pages, (
                "Filtered pagination page count differs from repository semantics."
            )

            seen: list[str] = []

            for page in range(1, first["total_pages"] + 1):
                expected = repository.search(
                    SearchFilters(
                        instruction_modes=(mode,),
                        page=page,
                        page_size=50,
                    )
                )
                payload = _response_payload(
                    get(
                        "/api/classes",
                        params={
                            "instruction_mode": mode.value,
                            "page": page,
                            "page_size": 50,
                        },
                    )
                )

                actual_keys = [
                    _payload_display_option_key(item)
                    for item in payload["items"]
                ]
                expected_keys = [
                    _section_display_option_key(item)
                    for item in expected.items
                ]

                assert actual_keys == expected_keys, (
                    f"Filtered API page {page} differs from repository search."
                )

                assert all(
                    item["instruction_mode"] == mode.value
                    or any(
                        component["instruction_mode"] == mode.value
                        for component in item.get("linked_components", ())
                    )
                    for item in payload["items"]
                ), (
                    "Filtered pagination returned a grouped option with no "
                    "physical component matching the selected instruction mode."
                )

                seen.extend(actual_keys)

            assert len(seen) == first["filtered_total"], (
                "Filtered pagination omitted one or more matching displayed options."
            )
            assert len(set(seen)) == len(seen), (
                "Filtered pagination returned duplicate displayed enrollment options."
            )

            return _pass(
                "A broad grouped production filter paginates without omissions or duplicates.",
                instruction_mode=mode.value,
                pages=first["total_pages"],
                matches=first["filtered_total"],
            )

        add("filtered_pagination", filtered_pagination)

        def frontend_contract() -> _CheckOutcome:
            index = get("/")
            assert index.status_code == 200, "The browser entry page did not load."
            html = index.text
            for marker in (
                'id="query"',
                'id="sort-by"',
                'id="course-list"',
                'id="top-pagination"',
                'id="pagination"',
            ):
                assert marker in html, f"The page is missing required control {marker}."
            javascript = get("/static/app.js")
            assert javascript.status_code == 200, "The browser JavaScript did not load."
            for marker in (
                "function buildParams",
                'params.set("page_size"',
                "function renderPagination",
                "function goToPage",
            ):
                assert marker in javascript.text, (
                    f"The browser JavaScript is missing required behavior {marker!r}."
                )
            return _pass(
                "The browser shell exposes search, filters, and top/bottom pagination.",
                index_bytes=len(index.content),
                javascript_bytes=len(javascript.content),
            )

        add("frontend_contract", frontend_contract)

    errors = sum(check.status is ApiCheckStatus.FAIL for check in checks)
    warnings = sum(check.status is ApiCheckStatus.WARNING for check in checks)
    skipped = sum(check.status is ApiCheckStatus.SKIPPED for check in checks)
    status = "failed" if errors else ("passed_with_warnings" if warnings else "passed")
    total_pages = repository.search(SearchFilters(page=1, page_size=50)).total_pages
    performance = ApiPerformanceMetrics(
        requests=len(request_durations),
        total_ms=round(sum(request_durations), 2),
        median_ms=round(statistics.median(request_durations), 2)
        if request_durations
        else 0.0,
        p95_ms=round(_percentile(request_durations, 0.95), 2),
        max_ms=round(max(request_durations), 2) if request_durations else 0.0,
    )
    report = ApiValidationReport(
        generated_at=_utc_now(),
        status=status,
        data_path=str(settings.data_path) if settings.data_path is not None else None,
        counts=ApiValidationCounts(
            course_section_listings=repository.total,
            logical_courses=repository.course_total,
            unique_physical_sections=repository.physical_section_total,
            subjects=repository.subject_total,
            terms=len(repository.options().terms),
            pages_at_50=total_pages,
        ),
        checks=tuple(checks),
        performance=performance,
        errors=errors,
        warnings=warnings,
        skipped=skipped,
    )
    return ApiValidationResult(report=report, exit_code=0 if errors == 0 else 1)


def _payload_display_option_key(item):
    linked = item.get("linked_components") or ()
    components = tuple(
        sorted(
            (
                str(component.get("schedule_number") or ""),
                str(component.get("component") or ""),
            )
            for component in linked
        )
    )
    return (
        str(item["id"]),
        int(item.get("option_number") or 0),
        components,
    )


def _section_display_option_key(item):
    components = tuple(
        sorted(
            (
                str(component.schedule_number),
                str(component.component or ""),
            )
            for component in item.linked_components
        )
    )
    return (
        str(item.id),
        int(item.option_number or 0),
        components,
    )


def validate_api_data_file(
    path: Path,
    *,
    config: ApiValidationConfig | None = None,
) -> ApiValidationResult:
    settings = config or ApiValidationConfig(data_path=path)
    if settings.data_path is None:
        settings = ApiValidationConfig(
            data_path=path,
            catalog_path=settings.catalog_path,
            expected_sections=settings.expected_sections,
            expected_courses=settings.expected_courses,
            expected_physical_sections=settings.expected_physical_sections,
            expected_subjects=settings.expected_subjects,
            full_pagination=settings.full_pagination,
            performance_warning_ms=settings.performance_warning_ms,
        )
    repository = CourseRepository.from_json(
        path,
        catalog_path=settings.catalog_path,
    )
    return validate_api_sections(
        repository.sections,
        config=settings,
        catalog=repository.catalog,
    )


def render_api_validation_markdown(report: ApiValidationReport) -> str:
    lines = [
        "# ClassCatalog API validation report",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Status: **{report.status.upper()}**",
        f"- Data file: `{report.data_path or 'in-memory candidate'}`",
        f"- Course-section listings: `{report.counts.course_section_listings}`",
        f"- Logical courses: `{report.counts.logical_courses}`",
        f"- Unique physical sections: `{report.counts.unique_physical_sections}`",
        f"- Subjects: `{report.counts.subjects}`",
        f"- Terms: `{report.counts.terms}`",
        f"- Pages at 50: `{report.counts.pages_at_50}`",
        f"- Errors: **{report.errors}**",
        f"- Warnings: **{report.warnings}**",
        f"- Skipped checks: **{report.skipped}**",
        "",
        "## Request performance",
        "",
        f"- Requests: `{report.performance.requests}`",
        f"- Total: `{report.performance.total_ms:.2f} ms`",
        f"- Median: `{report.performance.median_ms:.2f} ms`",
        f"- p95: `{report.performance.p95_ms:.2f} ms`",
        f"- Maximum: `{report.performance.max_ms:.2f} ms`",
        "",
        "## Checks",
        "",
        "| Check | Status | Duration | Result |",
        "|---|---|---:|---|",
    ]
    for check in report.checks:
        message = check.message.replace("|", "\\|")
        lines.append(
            f"| `{check.name}` | {check.status.value} | "
            f"{check.duration_ms:.2f} ms | {message} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_api_validation_report(
    report: ApiValidationReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    atomic_write(
        json_path,
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
    )
    atomic_write(markdown_path, render_api_validation_markdown(report))
