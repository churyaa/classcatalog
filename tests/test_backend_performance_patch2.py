"""Regression tests for per-term indexed searches and request-compiled filters."""
from __future__ import annotations

from datetime import time

import classcatalog.filters as filters_module
from classcatalog.filters import (
    SearchFilters,
    matches,
    prepare_search_terms,
    search_haystack,
)
from classcatalog.models import SeatStatus, SortBy, Weekday
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _sample() -> CourseRepository:
    return CourseRepository.from_json(SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None)


def _identity(items):
    return [
        (item.id, item.option_number, tuple(part.id for part in item.linked_components))
        for item in items
    ]


def _legacy_search_result(repository: CourseRepository, selected: SearchFilters):
    """Authoritative pre-Patch-2 all-section filter + cached grouping/sort path."""
    matched = [section for section in repository.sections if matches(section, selected)]
    group_indices = repository._selected_group_indices(matched)
    ordered = tuple(
        index for index in repository._sorted_group_indices(selected.sort_by)
        if index in group_indices
    )
    page_size = min(max(selected.page_size, 1), 50)
    total_pages = max(1, (len(ordered) + page_size - 1) // page_size)
    page = min(max(selected.page, 1), total_pages)
    indices = ordered[(page - 1) * page_size:page * page_size]
    return len(ordered), total_pages, _identity(repository._materialize_group_indices(indices))


def test_compiled_filter_matches_original_for_several_filter_combinations():
    repository = _sample()
    selected_filters = (
        SearchFilters(),
        SearchFilters(terms=("Fall 2026",)),
        SearchFilters(terms=("Summer 2026", "Fall 2026")),
        SearchFilters(query="CS 240"),
        SearchFilters(query='"Computer Science"'),
        SearchFilters(query='CS "course"'),
        SearchFilters(query="CS 240A"),
        SearchFilters(query="  "),
        SearchFilters(campuses=("San Diego Campus", "Imperial Valley Campus")),
        SearchFilters(completed_courses=("CS 101", "MATH 120")),
        SearchFilters(requirements=("Math",)),
        SearchFilters(days=(Weekday.MON,), excluded_days=(Weekday.FRI,)),
        SearchFilters(time_from=time(8, 0), time_to=time(17, 0)),
        SearchFilters(seat_statuses=(SeatStatus.OPEN,)),
        SearchFilters(rating_min=3.0, reviews_min=10),
    )
    for selected in selected_filters:
        prepared = prepare_search_terms(selected)
        for section in repository.sections:
            assert matches(section, selected) == matches(
                section, selected, prepared=prepared,
                search_text=search_haystack(section),
            ), (selected, section.id)


def test_indexed_search_matches_original_results_with_filters_and_pagination():
    repository = _sample()
    selected_filters = (
        SearchFilters(terms=("Fall 2026",)),
        SearchFilters(terms=("Summer 2026",)),
        SearchFilters(terms=("Fall 2026", "Summer 2026"), page_size=3, page=2),
        SearchFilters(terms=("Fall 2026",), query="CS"),
        SearchFilters(terms=("Fall 2026",), seat_statuses=(SeatStatus.OPEN,)),
        SearchFilters(terms=("Fall 2026",), sort_by=SortBy.COURSE_Z_A),
        SearchFilters(terms=("Fall 2026",), page=9999),
        SearchFilters(query='"introductory"', page_size=5),
        SearchFilters(),
    )
    for selected in selected_filters:
        total, pages, items = _legacy_search_result(repository, selected)
        result = repository.search(selected)
        assert (result.filtered_total, result.total_pages) == (total, pages), selected
        assert _identity(result.items) == items, selected


def test_term_index_skips_other_semesters_and_never_counts_duplicate_terms():
    seed = _sample().sections[0]
    fall = seed.model_copy(update={
        "id": "patch2-fall", "term": "Fall 2026", "term_code": "2267",
        "schedule_number": "92401", "course_code": "PATCH 201",
        "subject": "PATCH", "catalog_number": "201",
    })
    spring = seed.model_copy(update={
        "id": "patch2-spring", "term": "Spring 2027", "term_code": "2273",
        "schedule_number": "92402", "course_code": "PATCH 202",
        "subject": "PATCH", "catalog_number": "202",
    })
    winter = seed.model_copy(update={
        "id": "patch2-winter", "term": "Winter 2027", "term_code": "2271",
        "schedule_number": "92403", "course_code": "PATCH 203",
        "subject": "PATCH", "catalog_number": "203",
    })
    repository = CourseRepository((fall, spring, winter))
    original_index = repository._section_indices_by_term
    assert tuple(original_index) == ("Fall 2026", "Spring 2027", "Winter 2027")
    first = repository.search(SearchFilters(terms=("Spring 2027",)))
    metrics = repository.search_performance()
    assert metrics["last_candidate_sections"] == 1
    assert metrics["last_matched_sections"] == 1
    assert metrics["calls"] == 1
    assert first.filtered_total == 1
    assert first.items[0].id == spring.id

    many = repository.search(SearchFilters(
        terms=("Spring 2027", "Spring 2027", "Winter 2027"),
    ))
    assert repository.search_performance()["last_candidate_sections"] == 2
    assert many.filtered_total == 2
    assert {row.id for row in many.items} == {spring.id, winter.id}


def test_query_tokenization_happens_once_per_search_not_per_section(monkeypatch):
    repository = _sample()
    calls = 0
    original = filters_module._query_terms

    def counted(query):
        nonlocal calls
        calls += 1
        return original(query)

    monkeypatch.setattr(filters_module, "_query_terms", counted)
    repository.search(SearchFilters(query="CS 240"))
    assert calls == 1


def test_live_instructor_update_refreshes_precomputed_search_text():
    seed = _sample().sections[0]
    section = seed.model_copy(update={
        "id": "patch2-instructor", "schedule_number": "92330",
        "instructor": "TBA", "professor": None,
    })
    repository = CourseRepository((section,))
    selected = SearchFilters(query='"Patchtwo Unique Instructor"')
    assert repository.search(selected).filtered_total == 0
    assert repository.apply_instructor_updates({
        repository._physical_key(section): "Patchtwo Unique Instructor"
    }) == 1
    result = repository.search(selected)
    assert result.filtered_total == 1
    assert result.items[0].instructor == "Patchtwo Unique Instructor"
    assert "patchtwo unique instructor" in repository._search_haystacks[0]


def test_seat_updates_preserve_search_text_and_live_status_filter():
    repository = _sample()
    first = repository.search(SearchFilters()).items[0]
    source = next(section for section in repository.sections if section.id == first.id)
    cache = repository._search_haystacks
    status = SeatStatus.CLOSED if source.seat_status is not SeatStatus.CLOSED else SeatStatus.OPEN
    assert repository.apply_seat_updates({repository._physical_key(source): {
        "seat_status": status, "seats_available": 0,
    }}) == 1
    assert repository._search_haystacks is cache
    result = repository.search(SearchFilters(
        query=source.course_code, terms=(source.term,), seat_statuses=(status,),
    ))
    assert any(row.id == source.id for row in result.items)


def test_replace_inventory_rebuilds_term_and_search_text_index():
    seed = _sample().sections[0]
    fall = seed.model_copy(update={"id": "patch2-fall2", "term": "Fall 2026", "term_code": "2267"})
    spring = seed.model_copy(update={
        "id": "patch2-spring2", "term": "Spring 2027", "term_code": "2273",
        "instructor": "Patchtwo Replacement Instructor",
    })
    repository = CourseRepository((fall,))
    repository.replace_inventory((spring,))
    assert repository.search(SearchFilters(terms=("Fall 2026",))).filtered_total == 0
    assert repository.search(SearchFilters(terms=("Spring 2027",))).filtered_total == 1
    assert repository.search(SearchFilters(query='"Patchtwo Replacement Instructor"')).filtered_total == 1
    assert repository.displayed_options(term="Fall 2026") == ()
    assert len(repository.displayed_options(term="Spring 2027")) == 1


def test_search_metrics_are_lightweight_and_exclude_user_query_text():
    repository = _sample()
    result = repository.search(SearchFilters(query="Private Student Query"))
    metrics = repository.search_performance()
    assert metrics["calls"] == 1
    assert metrics["last_candidate_sections"] == repository.total
    assert metrics["last_matched_sections"] >= 0
    assert metrics["last_ms"] >= 0
    assert metrics["max_ms"] >= metrics["last_ms"]
    assert metrics["total_ms"] >= metrics["last_ms"] - 0.001
    assert "Private Student Query" not in str(metrics)
    assert result.filtered_total == 0
