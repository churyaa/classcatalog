from __future__ import annotations

from classcatalog.filters import SearchFilters, matches, sort_sections
from classcatalog.models import SeatStatus, SortBy
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _result_signature(items):
    return [
        (
            item.id,
            item.option_number,
            tuple(component.id for component in item.linked_components),
        )
        for item in items
    ]


def test_search_cache_matches_authoritative_grouping_and_builds_once(monkeypatch) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH)
    filters = SearchFilters(terms=("Fall 2026",), sort_by=SortBy.COURSE_A_Z)
    matched = [section for section in repository.sections if matches(section, filters)]
    expected = sort_sections(repository._grouped_sections(matched), filters.sort_by)

    original = repository._grouped_sections
    calls = 0

    def counted(matched_sections=None):
        nonlocal calls
        calls += 1
        return original(matched_sections)

    monkeypatch.setattr(repository, "_grouped_sections", counted)

    first = repository.search(filters)
    second = repository.search(SearchFilters(terms=("Summer 2026",)))

    assert calls == 1
    assert _result_signature(first.items) == _result_signature(expected)
    assert first.filtered_total == len(expected)
    assert second.unfiltered_total == first.unfiltered_total


def test_displayed_options_reuses_authoritative_group_cache(monkeypatch) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH)
    original = repository._grouped_sections
    calls = 0

    def counted(matched_sections=None):
        nonlocal calls
        calls += 1
        return original(matched_sections)

    monkeypatch.setattr(repository, "_grouped_sections", counted)

    fall = repository.displayed_options(term="Fall 2026")
    summer = repository.displayed_options(term="Summer 2026")

    assert calls == 1
    assert fall
    assert summer
    assert all(item.term == "Fall 2026" for item in fall)
    assert all(item.term == "Summer 2026" for item in summer)


def test_cached_search_reflects_live_seat_updates_without_regrouping(monkeypatch) -> None:
    repository = CourseRepository.from_json(SAMPLE_DATA_PATH)
    original = repository._grouped_sections
    calls = 0

    def counted(matched_sections=None):
        nonlocal calls
        calls += 1
        return original(matched_sections)

    monkeypatch.setattr(repository, "_grouped_sections", counted)

    initial = repository.search(SearchFilters())
    target = initial.items[0]
    source = next(section for section in repository.sections if section.id == target.id)
    new_status = SeatStatus.CLOSED if source.seat_status is not SeatStatus.CLOSED else SeatStatus.OPEN

    changed = repository.apply_seat_updates(
        {
            repository._physical_key(source): {
                "seat_status": new_status,
                "seats_available": 7,
                "seat_capacity": 30,
                "seats_enrolled": 23,
            }
        }
    )
    refreshed = repository.search(
        SearchFilters(query=source.course_code, seat_statuses=(new_status,))
    )

    assert changed == 1
    assert calls == 1
    updated = next(item for item in refreshed.items if item.id == source.id)
    assert updated.seat_status is new_status
    assert updated.seats_available == 7
    assert updated.seat_capacity == 30
    assert updated.seats_enrolled == 23


def test_cached_search_reflects_live_instructor_updates_without_regrouping(monkeypatch) -> None:
    template = CourseRepository.from_json(SAMPLE_DATA_PATH).sections[0]
    section = template.model_copy(
        update={
            "id": "cache-instructor-test",
            "instructor": "TBA",
            "professor": None,
        }
    )
    repository = CourseRepository((section,))
    original = repository._grouped_sections
    calls = 0

    def counted(matched_sections=None):
        nonlocal calls
        calls += 1
        return original(matched_sections)

    monkeypatch.setattr(repository, "_grouped_sections", counted)

    repository.search(SearchFilters())
    changed = repository.apply_instructor_updates(
        {repository._physical_key(section): "Ada Lovelace"}
    )
    refreshed = repository.search(SearchFilters())

    assert changed == 1
    assert calls == 1
    assert refreshed.items[0].instructor == "Ada Lovelace"


def test_professor_sort_cache_is_invalidated_after_instructor_update() -> None:
    template = CourseRepository.from_json(SAMPLE_DATA_PATH).sections[0]
    section = template.model_copy(
        update={
            "id": "cache-professor-sort-test",
            "instructor": "TBA",
            "professor": None,
        }
    )
    repository = CourseRepository((section,))

    repository.search(SearchFilters(sort_by=SortBy.PROFESSOR_RATING_HIGH_TO_LOW))
    assert SortBy.PROFESSOR_RATING_HIGH_TO_LOW in repository._sort_index_cache

    repository.apply_instructor_updates(
        {repository._physical_key(section): "Ada Lovelace"}
    )

    assert SortBy.PROFESSOR_RATING_HIGH_TO_LOW not in repository._sort_index_cache
