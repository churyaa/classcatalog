from __future__ import annotations

from datetime import time

from classcatalog.filters import SearchFilters, matches
from classcatalog.models import InstructionMode, Meeting, ProfessorMetrics, SeatStatus, SortBy, Weekday
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


repository = CourseRepository.from_json(SAMPLE_DATA_PATH)


def test_term_filter_reduces_count() -> None:
    all_results = repository.search(SearchFilters())
    summer = repository.search(SearchFilters(terms=("Summer 2026",)))
    assert all_results.filtered_total == 13
    assert summer.filtered_total == 4
    assert summer.filtered_total < all_results.filtered_total


def test_filters_and_across_groups() -> None:
    results = repository.search(
        SearchFilters(
            terms=("Fall 2026",),
            instruction_modes=(InstructionMode.IN_PERSON, InstructionMode.HYBRID),
            seat_statuses=(SeatStatus.OPEN,),
        )
    )
    assert {item.course_code for item in results.items} == {"CS 160", "ECON 101"}


def test_course_sort_supports_both_directions() -> None:
    ascending = repository.search(SearchFilters(sort_by=SortBy.COURSE_A_Z))
    descending = repository.search(SearchFilters(sort_by=SortBy.COURSE_Z_A))
    ascending_codes = [item.course_code for item in ascending.items]
    descending_codes = [item.course_code for item in descending.items]
    assert descending_codes == list(reversed(ascending_codes))


def test_professor_rating_sorts_in_both_directions() -> None:
    high_to_low = repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_RATING_HIGH_TO_LOW)
    )
    low_to_high = repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_RATING_LOW_TO_HIGH)
    )
    descending_ratings = [
        item.professor.rating
        for item in high_to_low.items
        if item.professor is not None and item.professor.rating is not None
    ]
    ascending_ratings = [
        item.professor.rating
        for item in low_to_high.items
        if item.professor is not None and item.professor.rating is not None
    ]
    assert descending_ratings == sorted(descending_ratings, reverse=True)
    assert ascending_ratings == sorted(ascending_ratings)


def test_class_difficulty_sorts_in_both_directions() -> None:
    low_to_high = repository.search(
        SearchFilters(sort_by=SortBy.CLASS_DIFFICULTY_LOW_TO_HIGH)
    )
    high_to_low = repository.search(
        SearchFilters(sort_by=SortBy.CLASS_DIFFICULTY_HIGH_TO_LOW)
    )
    ascending_values = [
        item.class_difficulty
        for item in low_to_high.items
        if item.class_difficulty is not None
    ]
    descending_values = [
        item.class_difficulty
        for item in high_to_low.items
        if item.class_difficulty is not None
    ]
    assert ascending_values == sorted(ascending_values)
    assert descending_values == sorted(descending_values, reverse=True)


def test_professor_difficulty_sorts_in_both_directions() -> None:
    low_to_high = repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_DIFFICULTY_LOW_TO_HIGH)
    )
    high_to_low = repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_DIFFICULTY_HIGH_TO_LOW)
    )
    ascending_values = [
        item.professor.difficulty
        for item in low_to_high.items
        if item.professor is not None and item.professor.difficulty is not None
    ]
    descending_values = [
        item.professor.difficulty
        for item in high_to_low.items
        if item.professor is not None and item.professor.difficulty is not None
    ]
    assert ascending_values == sorted(ascending_values)
    assert descending_values == sorted(descending_values, reverse=True)


def test_reviews_sort_in_both_directions() -> None:
    high_to_low = repository.search(SearchFilters(sort_by=SortBy.REVIEWS_HIGH_TO_LOW))
    low_to_high = repository.search(SearchFilters(sort_by=SortBy.REVIEWS_LOW_TO_HIGH))
    descending_values = [
        item.professor.num_reviews
        for item in high_to_low.items
        if item.professor is not None
    ]
    ascending_values = [
        item.professor.num_reviews
        for item in low_to_high.items
        if item.professor is not None
    ]
    assert descending_values == sorted(descending_values, reverse=True)
    assert ascending_values == sorted(ascending_values)


def test_take_again_sorts_in_both_directions() -> None:
    high_to_low = repository.search(SearchFilters(sort_by=SortBy.TAKE_AGAIN_HIGH_TO_LOW))
    low_to_high = repository.search(SearchFilters(sort_by=SortBy.TAKE_AGAIN_LOW_TO_HIGH))
    descending_values = [
        item.professor.would_take_again_percent
        for item in high_to_low.items
        if item.professor is not None and item.professor.would_take_again_percent is not None
    ]
    ascending_values = [
        item.professor.would_take_again_percent
        for item in low_to_high.items
        if item.professor is not None and item.professor.would_take_again_percent is not None
    ]
    assert descending_values == sorted(descending_values, reverse=True)
    assert ascending_values == sorted(ascending_values)


def test_missing_professor_metrics_sort_after_known_values() -> None:
    sections = repository.search(SearchFilters()).items
    unknown = sections[0].model_copy(update={"id": "unknown-professor", "professor": None})
    custom_repository = CourseRepository((*sections, unknown))

    ascending = custom_repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_RATING_LOW_TO_HIGH)
    )
    descending = custom_repository.search(
        SearchFilters(sort_by=SortBy.PROFESSOR_RATING_HIGH_TO_LOW)
    )
    assert ascending.items[-1].id == "unknown-professor"
    assert descending.items[-1].id == "unknown-professor"

    unknown_class = sections[0].model_copy(
        update={"id": "unknown-class-difficulty", "class_difficulty": None}
    )
    class_repository = CourseRepository((*sections, unknown_class))
    class_ascending = class_repository.search(
        SearchFilters(sort_by=SortBy.CLASS_DIFFICULTY_LOW_TO_HIGH)
    )
    class_descending = class_repository.search(
        SearchFilters(sort_by=SortBy.CLASS_DIFFICULTY_HIGH_TO_LOW)
    )
    assert class_ascending.items[-1].id == "unknown-class-difficulty"
    assert class_descending.items[-1].id == "unknown-class-difficulty"


def test_pagination_returns_at_most_fifty_classes() -> None:
    templates = repository.search(SearchFilters()).items
    sections = tuple(
        templates[index % len(templates)].model_copy(
            update={
                "id": f"generated-{index}",
                "section_number": f"{index:03}",
                "schedule_number": f"{10000 + index}",
            }
        )
        for index in range(65)
    )
    custom_repository = CourseRepository(sections)

    first = custom_repository.search(SearchFilters(page=1, page_size=100))
    second = custom_repository.search(SearchFilters(page=2, page_size=50))
    past_last = custom_repository.search(SearchFilters(page=99, page_size=50))

    assert first.page_size == 50
    assert first.total_pages == 2
    assert len(first.items) == 50
    assert second.page == 2
    assert len(second.items) == 15
    assert past_last.page == 2
    assert len(past_last.items) == 15


def test_completed_courses_are_removed_from_search_results() -> None:
    results = repository.search(SearchFilters(completed_courses=("CS 150",)))

    assert "CS 150" not in {item.course_code for item in results.items}


def test_prerequisite_metadata_never_filters_search_results() -> None:
    template = repository.search(SearchFilters()).items[0]
    section = template.model_copy(
        update={
            "id": "prerequisite-metadata-is-informational",
            "course_code": "TEST 401",
            "subject": "TEST",
            "catalog_number": "401",
            "prerequisite_text": "CS 150 and consent of instructor.",
            "prerequisite_groups": (("CS 150",),),
            "prerequisite_manual_review": True,
        }
    )

    results = CourseRepository((section,)).search(SearchFilters())

    assert [item.course_code for item in results.items] == ["TEST 401"]


def test_time_window_excludes_async_and_outside_meetings() -> None:
    results = repository.search(
        SearchFilters(
            terms=("Summer 2026",),
            days=(Weekday.MON,),
            time_from=time(9, 0),
            time_to=time(12, 0),
        )
    )
    assert {item.course_code for item in results.items} == {"CS 150"}


def test_search_can_match_course_description() -> None:
    results = repository.search(SearchFilters(query="computational problem solving"))
    assert [item.course_code for item in results.items] == ["CS 150"]


def test_variable_unit_filter_uses_range_overlap() -> None:
    template = repository.search(SearchFilters()).items[0]
    variable = template.model_copy(
        update={
            "id": "variable-units",
            "course_code": "TEST 500",
            "subject": "TEST",
            "catalog_number": "500",
            "units": 1.0,
            "units_min": 1.0,
            "units_max": 3.0,
            "units_text": "1.00 - 3.00",
        }
    )
    custom_repository = CourseRepository((variable,))

    overlaps = custom_repository.search(SearchFilters(units_min=2.5, units_max=4.0))
    below = custom_repository.search(SearchFilters(units_max=0.5))

    assert [item.id for item in overlaps.items] == ["variable-units"]
    assert below.filtered_total == 0


def test_program_and_catalog_year_filters_work_without_classification() -> None:
    computer_science = repository.search(
        SearchFilters(program="Computer Science, B.S.")
    )
    biology = repository.search(SearchFilters(program="Biology, B.S."))
    catalog_year = repository.search(SearchFilters(catalog_year="2026-2027"))

    assert computer_science.filtered_total == 12
    assert {item.course_code for item in biology.items} == {"BIOL 100"}
    assert catalog_year.filtered_total == 13


def test_course_code_query_matches_course_code_prefix_without_description_noise() -> None:
    template = repository.search(SearchFilters()).items[0]
    cs_150 = template.model_copy(
        update={
            "id": "cs-150",
            "course_code": "CS 150",
            "subject": "CS",
            "catalog_number": "150",
            "title": "Introduction to Computer Science",
            "description": "Computing methodology.",
        }
    )
    cs_151 = template.model_copy(
        update={
            "id": "cs-151",
            "course_code": "CS 151",
            "subject": "CS",
            "catalog_number": "151",
            "title": "Computer Programming II",
            "description": "Further programming concepts.",
        }
    )
    cs_160 = template.model_copy(
        update={
            "id": "cs-160",
            "course_code": "CS 160",
            "subject": "CS",
            "catalog_number": "160",
            "title": "Intermediate Computer Programming",
            "description": "Programming methodology.",
        }
    )
    mentions_code = template.model_copy(
        update={
            "id": "mentions-cs-15",
            "course_code": "MATH 120",
            "subject": "MATH",
            "catalog_number": "120",
            "title": "Mathematical Foundations",
            "description": "Preparation that may be useful alongside CS 150 and CS 151.",
        }
    )
    custom_repository = CourseRepository((cs_150, cs_151, cs_160, mentions_code))

    prefix_results = custom_repository.search(SearchFilters(query=" cs   15 "))
    full_results = custom_repository.search(SearchFilters(query="CS 150"))

    assert {item.id for item in prefix_results.items} == {"cs-150", "cs-151"}
    assert {item.id for item in full_results.items} == {"cs-150"}




def test_major_filter_can_be_disabled_while_program_remains_selected() -> None:
    major_only = repository.search(
        SearchFilters(program="Computer Science, B.S.", major_only=True)
    )
    all_courses = repository.search(
        SearchFilters(program="Computer Science, B.S.", major_only=False)
    )
    assert major_only.filtered_total < all_courses.filtered_total
    assert all_courses.filtered_total == repository.total


def test_multiple_requirement_filters_use_and_logic() -> None:
    template = repository.sections[0]
    both = template.model_copy(update={
        "requirement_tags": ("Ethnic Studies", "3B Humanities"),
    })
    ethnic_only = template.model_copy(update={
        "requirement_tags": ("Ethnic Studies",),
    })
    filters = SearchFilters(requirements=("Ethnic Studies", "3B Humanities"))
    assert matches(both, filters) is True
    assert matches(ethnic_only, filters) is False


def test_campus_filter_uses_canonical_sdsu_locations() -> None:
    template = repository.sections[0]
    san_diego = template.model_copy(update={"id": "sd", "campus": "San Diego Campus"})
    imperial = template.model_copy(update={
        "id": "iv",
        "course_code": "TEST 499",
        "subject": "TEST",
        "catalog_number": "499",
        "campus": "Imperial Valley Campus",
    })
    custom = CourseRepository((san_diego, imperial))
    results = custom.search(SearchFilters(campuses=("San Diego Campus",)))
    assert [item.id for item in results.items] == ["sd"]
    assert custom.options().campuses == ("San Diego Campus", "Imperial Valley Campus")


def test_linked_option_components_are_grouped_and_keep_meeting_locations() -> None:
    template = repository.sections[0]
    common = {
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "A E 460A",
        "subject": "A E",
        "catalog_number": "460A",
        "crse_id": "AE460A",
        "crse_offer_nbr": "1",
        "acad_career": "UGRD",
        "option_number": 1,
        "campus": "San Diego Campus",
    }
    discussion = template.model_copy(update={
        **common,
        "id": "ae-disc",
        "component": "Discussion",
        "schedule_number": "3699",
        "meetings": (
            Meeting(days=(Weekday.MON,), start_time=time(8,0), end_time=time(8,50), location="M 265"),
            Meeting(days=(Weekday.WED,), start_time=time(8,0), end_time=time(8,50), location="AH 3110"),
        ),
    })
    lab = template.model_copy(update={
        **common,
        "id": "ae-lab",
        "component": "Laboratory",
        "schedule_number": "3701",
        "meetings": (Meeting(days=(Weekday.MON,), start_time=time(10,0), end_time=time(10,50), location="M 265"),),
    })
    tech = template.model_copy(update={
        **common,
        "id": "ae-tech",
        "component": "Tech Acts",
        "schedule_number": "3700",
        "meetings": (Meeting(days=(Weekday.MON, Weekday.WED), start_time=time(9,0), end_time=time(9,50), location="M 265"),),
    })
    results = CourseRepository((discussion, lab, tech)).search(SearchFilters(query="A E 460A"))
    assert results.filtered_total == 1
    item = results.items[0]
    assert item.option_number == 1
    assert [component.schedule_number for component in item.linked_components] == ["3699", "3701", "3700"]
    assert item.linked_components[0].meetings[0].location == "M 265"
    assert item.linked_components[0].meetings[1].location == "AH 3110"


def test_orphan_secondary_component_is_not_a_standalone_result_when_course_has_numbered_options() -> None:
    template = repository.sections[0]
    common = {
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "acad_career": "UGRD",
        "campus": "San Diego Campus",
    }
    discussion = template.model_copy(update={
        **common,
        "id": "math-disc",
        "option_number": 1,
        "component": "Discussion",
        "schedule_number": "6522",
    })
    linked_activity = template.model_copy(update={
        **common,
        "id": "math-activity-linked",
        "option_number": 1,
        "component": "Activity",
        "schedule_number": "6529",
    })
    orphan_activity = template.model_copy(update={
        **common,
        "id": "math-activity-orphan",
        "option_number": None,
        "component": "Activity",
        "schedule_number": "6530",
        "units": 0.0,
    })
    response = CourseRepository((discussion, linked_activity, orphan_activity)).search(SearchFilters(query="MATH 150"))
    assert response.filtered_total == 1
    assert [component.schedule_number for component in response.items[0].linked_components] == ["6522", "6529"]


def test_linked_components_keep_their_own_professor_metrics() -> None:
    template = repository.sections[0]
    common = {
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "acad_career": "UGRD",
        "option_number": 1,
        "campus": "San Diego Campus",
    }
    discussion = template.model_copy(update={
        **common,
        "id": "math-disc-prof",
        "component": "Discussion",
        "schedule_number": "6522",
        "instructor": "Mark Dunster",
        "professor": ProfessorMetrics(provider="test", rating=4.5, num_reviews=20),
    })
    activity = template.model_copy(update={
        **common,
        "id": "math-act-prof",
        "component": "Activity",
        "schedule_number": "6529",
        "instructor": "Ariana Johnson",
        "professor": ProfessorMetrics(provider="test", rating=4.1, num_reviews=12),
    })
    item = CourseRepository((discussion, activity)).search(SearchFilters(query="MATH 150")).items[0]
    assert item.linked_components[0].professor is not None
    assert item.linked_components[0].professor.rating == 4.5
    assert item.linked_components[1].professor is not None
    assert item.linked_components[1].professor.rating == 4.1


def test_orphan_zero_unit_activity_is_suppressed_even_when_offering_identity_differs() -> None:
    template = repository.sections[0]
    common = {
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "acad_career": "UGRD",
        "campus": "San Diego Campus",
    }
    discussion = template.model_copy(update={
        **common,
        "id": "math-disc-stable",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "option_number": 1,
        "component": "Discussion",
        "schedule_number": "6522",
        "units": 4.0,
    })
    linked_activity = template.model_copy(update={
        **common,
        "id": "math-activity-stable",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "option_number": 1,
        "component": "Activity",
        "schedule_number": "6529",
        "units": 0.0,
    })
    orphan_activity = template.model_copy(update={
        **common,
        "id": "math-activity-different-offering",
        "crse_id": "MATH150-ACT",
        "crse_offer_nbr": "99",
        "option_number": None,
        "component": "Activity",
        "schedule_number": "6532",
        "units": 0.0,
    })
    response = CourseRepository((discussion, linked_activity, orphan_activity)).search(
        SearchFilters(query="MATH 150")
    )
    assert response.filtered_total == 1
    assert response.items[0].schedule_number == "6522"
    assert [component.schedule_number for component in response.items[0].linked_components] == ["6522", "6529"]


def test_orphan_zero_unit_activity_is_suppressed_even_when_internal_term_code_differs() -> None:
    template = repository.sections[0]
    common = {
        "term": "Fall 2026",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "acad_career": "UGRD",
        "campus": "San Diego Campus",
    }
    discussion = template.model_copy(update={
        **common,
        "id": "math-disc-term-code",
        "term_code": "2267",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "option_number": 1,
        "component": "Discussion",
        "schedule_number": "6522",
        "units": 4.0,
    })
    linked_activity = template.model_copy(update={
        **common,
        "id": "math-linked-term-code",
        "term_code": "2267",
        "crse_id": "MATH150",
        "crse_offer_nbr": "1",
        "option_number": 1,
        "component": "Activity",
        "schedule_number": "6529",
        "units": 0.0,
    })
    orphan_activity = template.model_copy(update={
        **common,
        "id": "math-orphan-term-code",
        "term_code": "",
        "crse_id": "MATH150-ACT",
        "crse_offer_nbr": "99",
        "option_number": None,
        "component": "Activity",
        "schedule_number": "6531",
        "units": 0.0,
    })
    response = CourseRepository((discussion, linked_activity, orphan_activity)).search(
        SearchFilters(query="MATH 150")
    )
    assert response.filtered_total == 1
    assert response.items[0].schedule_number == "6522"
    assert [component.schedule_number for component in response.items[0].linked_components] == ["6522", "6529"]


def test_all_orphan_zero_unit_secondary_components_are_hidden_without_parent_identity() -> None:
    template = repository.sections[0]
    stragglers = []
    for index, schedule_number in enumerate(("6532", "6531", "7803", "6526", "7700", "6530", "6527")):
        stragglers.append(template.model_copy(update={
            "id": f"orphan-activity-{schedule_number}",
            "term": "Fall 2026",
            "term_code": "" if index % 2 else f"legacy-{index}",
            "course_code": "MATH 150",
            "subject": "MATH",
            "catalog_number": "150",
            "crse_id": f"orphan-{schedule_number}",
            "crse_offer_nbr": str(90 + index),
            "option_number": None,
            "component": "Activity",
            "schedule_number": schedule_number,
            "units": 0.0,
            "units_min": 0.0,
            "units_max": 0.0,
            "units_text": "0.00",
            "campus": "San Diego Campus",
        }))

    response = CourseRepository(tuple(stragglers)).search(SearchFilters(query="MATH 150"))

    assert response.filtered_total == 0
    assert response.items == ()


def test_numbered_components_group_across_inconsistent_sdsu_offering_ids() -> None:
    template = repository.sections[0]
    discussion = template.model_copy(update={
        "id": "math-discussion-option-1",
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "source_course_key": "MATH|150|UGRD",
        "option_group_indices": (1,),
        "crse_id": "MATH150-DISC",
        "crse_offer_nbr": "1",
        "option_number": 1,
        "component": "Discussion",
        "schedule_number": "6522",
        "units": 4.0,
        "units_min": 4.0,
        "units_max": 4.0,
        "units_text": "4.00",
    })
    activity = template.model_copy(update={
        "id": "math-activity-option-1",
        "term": "Fall 2026",
        "term_code": "legacy-other-code",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "source_course_key": "MATH|150|UGRD",
        "option_group_indices": (1,),
        "crse_id": "MATH150-ACT-6533",
        "crse_offer_nbr": "99",
        "option_number": 1,
        "component": "Activity",
        "schedule_number": "6533",
        "units": 0.0,
        "units_min": 0.0,
        "units_max": 0.0,
        "units_text": "0.00",
    })

    response = CourseRepository((discussion, activity)).search(SearchFilters(query="MATH 150"))

    assert response.filtered_total == 1
    assert response.items[0].schedule_number == "6522"
    assert [component.schedule_number for component in response.items[0].linked_components] == ["6522", "6533"]


def test_numbered_secondary_only_option_is_not_a_standalone_result() -> None:
    template = repository.sections[0]
    activity = template.model_copy(update={
        "id": "math-activity-only-option",
        "term": "Fall 2026",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "option_number": 7,
        "component": "Activity",
        "schedule_number": "7805",
        "units": 0.0,
        "units_min": 0.0,
        "units_max": 0.0,
        "units_text": "0.00",
    })

    response = CourseRepository((activity,)).search(SearchFilters(query="MATH 150"))

    assert response.filtered_total == 0
    assert response.items == ()


def test_repeated_visible_option_numbers_do_not_collapse_distinct_class_choices() -> None:
    template = repository.sections[0]
    sections = []
    for group_index in range(1, 21):
        discussion = template.model_copy(update={
            "id": f"math-disc-{group_index}",
            "term": "Fall 2026",
            "term_code": "2267",
            "course_code": "MATH 150",
            "subject": "MATH",
            "catalog_number": "150",
            "source_course_key": "MATH|150|UGRD",
            "option_number": ((group_index - 1) % 5) + 1,
            "option_group_indices": (group_index,),
            "component": "Discussion",
            "schedule_number": str(6500 + group_index),
            "units": 4.0,
            "units_min": 4.0,
            "units_max": 4.0,
            "units_text": "4.00",
        })
        activity = template.model_copy(update={
            "id": f"math-act-{group_index}",
            "term": "Fall 2026",
            "term_code": "2267",
            "course_code": "MATH 150",
            "subject": "MATH",
            "catalog_number": "150",
            "source_course_key": "MATH|150|UGRD",
            "option_number": ((group_index - 1) % 5) + 1,
            "option_group_indices": (group_index,),
            "component": "Activity",
            "schedule_number": str(7500 + group_index),
            "units": 0.0,
            "units_min": 0.0,
            "units_max": 0.0,
            "units_text": "0.00",
        })
        sections.extend((discussion, activity))

    response = CourseRepository(tuple(sections)).search(
        SearchFilters(query="MATH 150", page_size=50)
    )

    assert response.filtered_total == 20
    assert [item.option_number for item in response.items] == list(range(1, 21))
    assert all(len(item.linked_components) == 2 for item in response.items)


def test_one_physical_primary_can_belong_to_multiple_enrollment_options() -> None:
    template = repository.sections[0]
    discussion = template.model_copy(update={
        "id": "math-shared-disc",
        "term": "Fall 2026",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "source_course_key": "MATH|150|UGRD",
        "option_number": 1,
        "option_group_indices": (1, 2),
        "component": "Discussion",
        "schedule_number": "6528",
        "units": 4.0,
    })
    activity_one = template.model_copy(update={
        "id": "math-act-one",
        "term": "Fall 2026",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "source_course_key": "MATH|150|UGRD",
        "option_number": 1,
        "option_group_indices": (1,),
        "component": "Activity",
        "schedule_number": "6533",
        "units": 0.0,
    })
    activity_two = activity_one.model_copy(update={
        "id": "math-act-two",
        "option_group_indices": (2,),
        "schedule_number": "6534",
    })

    response = CourseRepository((discussion, activity_one, activity_two)).search(
        SearchFilters(query="MATH 150")
    )

    assert response.filtered_total == 2
    assert [item.option_number for item in response.items] == [1, 2]
    assert [
        [component.schedule_number for component in item.linked_components]
        for item in response.items
    ] == [["6528", "6533"], ["6528", "6534"]]


def test_coverage_audit_accounts_for_standalone_and_grouped_physical_sections() -> None:
    template = repository.sections[0]
    standalone = template.model_copy(update={
        "id": "audit-standalone",
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "CS 100",
        "subject": "CS",
        "catalog_number": "100",
        "component": "Lecture",
        "schedule_number": "1001",
        "option_number": None,
        "option_group_indices": (),
        "source_course_key": None,
    })
    discussion = template.model_copy(update={
        "id": "audit-discussion",
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "component": "Discussion",
        "schedule_number": "6528",
        "option_number": 1,
        "option_group_indices": (1,),
        "source_course_key": "MATH|150|UGRD",
        "units": 4.0,
    })
    activity = discussion.model_copy(update={
        "id": "audit-activity",
        "component": "Activity",
        "schedule_number": "6533",
        "units": 0.0,
    })

    audit = CourseRepository((standalone, discussion, activity)).coverage_audit()

    assert audit.status == "passed"
    assert audit.course_section_listings == 3
    assert audit.physical_sections == 3
    assert audit.displayed_options == 2
    assert audit.accounted_physical_sections == 3
    assert audit.unaccounted_physical_sections == 0
    assert audit.standalone_physical_sections == 1
    assert audit.grouped_component_physical_sections == 2


def test_coverage_audit_reports_shared_primary_as_duplicate_option_membership() -> None:
    template = repository.sections[0]
    discussion = template.model_copy(update={
        "id": "audit-shared-discussion",
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "component": "Discussion",
        "schedule_number": "6528",
        "option_number": 1,
        "option_group_indices": (1, 2),
        "source_course_key": "MATH|150|UGRD",
        "units": 4.0,
    })
    activity_one = discussion.model_copy(update={
        "id": "audit-activity-one",
        "component": "Activity",
        "schedule_number": "6533",
        "option_group_indices": (1,),
        "units": 0.0,
    })
    activity_two = activity_one.model_copy(update={
        "id": "audit-activity-two",
        "schedule_number": "6534",
        "option_group_indices": (2,),
    })

    audit = CourseRepository((discussion, activity_one, activity_two)).coverage_audit()

    assert audit.status == "passed"
    assert audit.physical_sections == 3
    assert audit.displayed_options == 2
    assert audit.accounted_physical_sections == 3
    assert audit.physical_option_memberships == 4
    assert audit.duplicate_option_memberships == 1


def test_coverage_audit_fails_when_physical_secondary_component_is_suppressed() -> None:
    template = repository.sections[0]
    orphan = template.model_copy(update={
        "id": "audit-orphan-activity",
        "term": "Fall 2026",
        "term_code": "2267",
        "course_code": "MATH 150",
        "subject": "MATH",
        "catalog_number": "150",
        "component": "Activity",
        "schedule_number": "6532",
        "option_number": None,
        "option_group_indices": (),
        "source_course_key": None,
        "units": 0.0,
    })

    audit = CourseRepository((orphan,)).coverage_audit()

    assert audit.status == "failed"
    assert audit.physical_sections == 1
    assert audit.displayed_options == 0
    assert audit.accounted_physical_sections == 0
    assert audit.unaccounted_physical_sections == 1
    assert "MATH 150 schedule #6532" in audit.unaccounted_examples[0]


def test_secondary_named_component_can_be_a_legitimate_primary_option() -> None:
    template = repository.sections[0]
    clinical = template.model_copy(update={
        "id": "arp-primary-clinical",
        "term": "Fall 2026",
        "course_code": "ARP 744",
        "subject": "ARP",
        "catalog_number": "744",
        "source_course_key": "ARP|744|UGRD",
        "option_number": 1,
        "option_group_indices": (1,),
        "option_primary_group_indices": (1,),
        "component": "Clinical",
        "schedule_number": "1749",
        "units": 0.0,
        "units_min": 0.0,
        "units_max": 0.0,
        "units_text": "0.00",
    })

    response = CourseRepository((clinical,)).search(SearchFilters(query="ARP 744"))
    audit = CourseRepository((clinical,)).coverage_audit()

    assert response.filtered_total == 1
    assert response.items[0].schedule_number == "1749"
    assert audit.status == "passed"
    assert audit.unaccounted_physical_sections == 0


def test_row_primary_role_beats_secondary_component_name_in_grouped_option() -> None:
    template = repository.sections[0]
    primary_activity = template.model_copy(update={
        "id": "art-primary-activity",
        "term": "Fall 2026",
        "course_code": "ART 100",
        "subject": "ART",
        "catalog_number": "100",
        "source_course_key": "ART|100|UGRD",
        "option_number": 1,
        "option_group_indices": (1,),
        "option_primary_group_indices": (1,),
        "component": "Activity",
        "schedule_number": "4376",
        "units": 0.0,
    })
    linked_lab = template.model_copy(update={
        "id": "art-linked-lab",
        "term": "Fall 2026",
        "course_code": "ART 100",
        "subject": "ART",
        "catalog_number": "100",
        "source_course_key": "ART|100|UGRD",
        "option_number": 1,
        "option_group_indices": (1,),
        "option_primary_group_indices": (),
        "component": "Laboratory",
        "schedule_number": "4377",
        "units": 0.0,
    })

    response = CourseRepository((primary_activity, linked_lab)).search(
        SearchFilters(query="ART 100")
    )

    assert response.filtered_total == 1
    assert response.items[0].schedule_number == "4376"
    assert [c.schedule_number for c in response.items[0].linked_components] == [
        "4376",
        "4377",
    ]


def test_secondary_group_reconciles_across_source_course_keys_when_unambiguous() -> None:
    template = repository.sections[0]
    discussion = template.model_copy(update={
        "id": "cross-key-discussion",
        "term": "Fall 2026",
        "course_code": "A E 341",
        "subject": "A E",
        "catalog_number": "341",
        "source_course_key": "AE|PRIMARY|1|UGRD",
        "option_number": 1,
        "option_group_indices": (7,),
        "option_primary_group_indices": (7,),
        "component": "Discussion",
        "schedule_number": "3668",
        "units": 3.0,
    })
    laboratory = template.model_copy(update={
        "id": "cross-key-lab",
        "term": "Fall 2026",
        "course_code": "A E 341",
        "subject": "A E",
        "catalog_number": "341",
        "source_course_key": "AE|LAB|99|UGRD",
        "option_number": 1,
        "option_group_indices": (7,),
        "option_primary_group_indices": (),
        "component": "Laboratory",
        "schedule_number": "3669",
        "units": 0.0,
    })

    repo = CourseRepository((discussion, laboratory))
    response = repo.search(SearchFilters(query="A E 341"))
    audit = repo.coverage_audit()

    assert response.filtered_total == 1
    assert [c.schedule_number for c in response.items[0].linked_components] == [
        "3668",
        "3669",
    ]
    assert audit.status == "passed"
    assert audit.unaccounted_physical_sections == 0


def test_cross_key_reconciliation_does_not_guess_when_primary_match_is_ambiguous() -> None:
    template = repository.sections[0]
    primaries = []
    for suffix, schedule in (("A", "1001"), ("B", "1002")):
        primaries.append(template.model_copy(update={
            "id": f"ambiguous-primary-{suffix}",
            "term": "Fall 2026",
            "course_code": "ART 100",
            "subject": "ART",
            "catalog_number": "100",
            "source_course_key": f"ART|PRIMARY|{suffix}",
            "option_number": 1,
            "option_group_indices": (1,),
            "option_primary_group_indices": (1,),
            "component": "Activity",
            "schedule_number": schedule,
            "units": 3.0,
        }))
    orphan = template.model_copy(update={
        "id": "ambiguous-linked-lab",
        "term": "Fall 2026",
        "course_code": "ART 100",
        "subject": "ART",
        "catalog_number": "100",
        "source_course_key": "ART|LAB|X",
        "option_number": 1,
        "option_group_indices": (1,),
        "option_primary_group_indices": (),
        "component": "Laboratory",
        "schedule_number": "1999",
        "units": 0.0,
    })

    audit = CourseRepository((*primaries, orphan)).coverage_audit()

    assert audit.status == "failed"
    assert audit.unaccounted_physical_sections == 1
    assert "schedule #1999" in audit.unaccounted_examples[0]


def test_stable_option_signatures_do_not_collapse_nursing_rows_when_local_group_indices_reset() -> None:
    template = repository.sections[0]
    sections = []
    for index in range(1, 9):
        schedule = str(8200 + index)
        source_key = "NURS|202|A" if index <= 5 else "NURS|202|B"
        local_group = index if index <= 5 else index - 5
        group_id = schedule
        section = template.model_copy(update={
            "id": f"nurs-202-{schedule}",
            "term": "Fall 2026",
            "term_code": "2267",
            "course_code": "NURS 202",
            "subject": "NURS",
            "catalog_number": "202",
            "source_course_key": source_key,
            "option_number": local_group,
            "option_group_indices": (local_group,),
            "option_primary_group_indices": (local_group,),
            "option_group_ids": (group_id,),
            "option_primary_group_ids": (group_id,),
            "component": "Laboratory" if index > 5 else "Lecture",
            "schedule_number": schedule,
            "units": 3.0 if index <= 5 else 0.0,
            "units_min": 3.0 if index <= 5 else 0.0,
            "units_max": 3.0 if index <= 5 else 0.0,
            "units_text": "3.00" if index <= 5 else "0.00",
        })
        sections.append(section)

    response = CourseRepository(tuple(sections)).search(
        SearchFilters(query="NURS 202", page_size=50)
    )

    assert response.filtered_total == 8
    assert {item.schedule_number for item in response.items} == {
        str(8200 + index) for index in range(1, 9)
    }


def test_stable_option_signature_links_components_across_source_course_keys() -> None:
    template = repository.sections[0]
    signature = "8301+9301"
    lecture = template.model_copy(update={
        "id": "nurs-lecture-8301",
        "term": "Fall 2026",
        "course_code": "NURS 202",
        "subject": "NURS",
        "catalog_number": "202",
        "source_course_key": "NURS|202|LECT",
        "option_group_indices": (1,),
        "option_primary_group_indices": (1,),
        "option_group_ids": (signature,),
        "option_primary_group_ids": (signature,),
        "component": "Lecture",
        "schedule_number": "8301",
        "units": 3.0,
    })
    lab = template.model_copy(update={
        "id": "nurs-lab-9301",
        "term": "Fall 2026",
        "course_code": "NURS 202",
        "subject": "NURS",
        "catalog_number": "202",
        "source_course_key": "NURS|202|LAB",
        "option_group_indices": (1,),
        "option_primary_group_indices": (),
        "option_group_ids": (signature,),
        "option_primary_group_ids": (),
        "component": "Laboratory",
        "schedule_number": "9301",
        "units": 0.0,
    })

    response = CourseRepository((lecture, lab)).search(SearchFilters(query="NURS 202"))

    assert response.filtered_total == 1
    assert [component.schedule_number for component in response.items[0].linked_components] == [
        "8301",
        "9301",
    ]
