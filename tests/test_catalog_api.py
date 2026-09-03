from __future__ import annotations

from fastapi.testclient import TestClient

from classcatalog.catalog.models import (
    CatalogMappings,
    CatalogProgram,
    CatalogRequirement,
    ProgramCourseMapping,
)
from classcatalog.catalog.repository import CatalogRepository
from classcatalog.main import create_app
from classcatalog.models import ProgramClassification
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _client() -> TestClient:
    catalog = CatalogRepository(
        CatalogMappings(
            catalog_year="2026-2027",
            generated_at="2026-08-23T00:00:00+00:00",
            source_index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            programs=(
                CatalogProgram(
                    name="Computer Science, B.S.",
                    catalog_year="2026-2027",
                    source_url=(
                        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11846"
                    ),
                    mappings=(
                        ProgramCourseMapping(
                            course_code="CS 150",
                            classification=ProgramClassification.MAJOR_PREP,
                        ),
                        ProgramCourseMapping(
                            course_code="CS 160",
                            classification=ProgramClassification.MAJOR_COURSE,
                        ),
                    ),
                ),
            ),
            requirements=(
                CatalogRequirement(
                    code="GE A1",
                    name="Oral Communication",
                    catalog_year="2026-2027",
                    source_url="https://catalog.sdsu.edu/content.php?catoid=12&navoid=1",
                    course_codes=("COMM 103",),
                ),
            ),
        )
    )
    sections = tuple(
        section.model_copy(update={"program_tags": (), "requirement_tags": ()})
        for section in CourseRepository.from_json(SAMPLE_DATA_PATH).sections
    )
    repository = CourseRepository(sections, catalog=catalog)
    return TestClient(create_app(repository))


def test_catalog_status_and_health_expose_overlay_counts() -> None:
    client = _client()
    status = client.get("/api/catalog/status").json()
    health = client.get("/api/health").json()

    assert status["loaded"] is True
    assert status["programs"] == 1
    assert status["requirements"] == 1
    assert health["catalog_loaded"] is True
    assert health["catalog_programs"] == 1


def test_program_and_student_profile_endpoints() -> None:
    client = _client()
    program = client.get(
        "/api/catalog/program",
        params={"program": "Computer Science, B.S.", "catalog_year": "2026-2027"},
    )
    profile = client.get(
        "/api/profile/summary",
        params=[
            ("program", "Computer Science, B.S."),
            ("catalog_year", "2026-2027"),
            ("completed_course", "CS 150"),
        ],
    )

    assert program.status_code == 200
    assert program.json()["mapped_course_count"] == 2
    assert profile.status_code == 200
    assert profile.json()["completed_mapped_course_count"] == 1
    assert profile.json()["required_course_count"] == 2
    assert profile.json()["completed_required_course_count"] == 1
    assert "CS 160" in profile.json()["remaining_scheduled_courses"]


def test_catalog_overlay_activates_program_and_requirement_filters() -> None:
    client = _client()
    options = client.get("/api/options").json()
    program_results = client.get(
        "/api/classes",
        params={
            "program": "Computer Science, B.S.",
            "catalog_year": "2026-2027",
            "classification": "major_prep",
        },
    ).json()
    requirement_results = client.get(
        "/api/classes",
        params={"requirement": "1C Oral Communication"},
    ).json()

    assert "Computer Science, B.S." in options["programs"]
    assert "1C Oral Communication" in options["requirements"]
    assert {course["course_code"] for course in options["courses"]} >= {"CS 150", "CS 160"}
    assert any(course["course_code"] == "CS 150" and course["title"] for course in options["courses"])
    assert {item["course_code"] for item in program_results["items"]} == {"CS 150"}
    assert {item["course_code"] for item in requirement_results["items"]} == {
        "COMM 103"
    }


def test_program_profile_can_be_kept_without_filtering_to_major_courses() -> None:
    client = _client()
    major_only = client.get(
        "/api/classes",
        params={
            "program": "Computer Science, B.S.",
            "catalog_year": "2026-2027",
            "major_only": "true",
        },
    ).json()
    all_courses = client.get(
        "/api/classes",
        params={
            "program": "Computer Science, B.S.",
            "catalog_year": "2026-2027",
            "major_only": "false",
        },
    ).json()

    assert {item["course_code"] for item in major_only["items"]} == {"CS 150", "CS 160"}
    assert all_courses["filtered_total"] > major_only["filtered_total"]


def test_completed_course_is_excluded_from_class_search() -> None:
    client = _client()
    response = client.get(
        "/api/classes",
        params=[("completed_course", "CS 150")],
    )

    assert response.status_code == 200
    assert "CS 150" not in {item["course_code"] for item in response.json()["items"]}


def test_computer_science_progress_counts_elective_slots_not_every_option() -> None:
    fixed_prep = tuple(
        ProgramCourseMapping(
            course_code=f"PREP {index}",
            classification=ProgramClassification.MAJOR_PREP,
            group="Preparation for the Major",
        )
        for index in range(1, 17)
    )
    fixed_major = tuple(
        ProgramCourseMapping(
            course_code=f"MAJOR {index}",
            classification=ProgramClassification.MAJOR_COURSE,
            group="Major",
        )
        for index in range(1, 7)
    )
    elective_options = tuple(
        ProgramCourseMapping(
            course_code=f"ELECT {index}",
            classification=ProgramClassification.ELECTIVE,
            group="Upper Division Major Electives",
        )
        for index in range(1, 36)
    )
    catalog = CatalogRepository(
        CatalogMappings(
            catalog_year="2026-2027",
            generated_at="2026-08-23T00:00:00+00:00",
            source_index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            programs=(
                CatalogProgram(
                    name="Computer Science, B.S.",
                    catalog_year="2026-2027",
                    source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11969",
                    mappings=fixed_prep + fixed_major + elective_options,
                ),
            ),
        )
    )

    summary = catalog.student_profile_summary(
        "Computer Science, B.S.",
        "2026-2027",
        scheduled_course_codes=(),
        completed_courses=("PREP 1", *(f"ELECT {index}" for index in range(1, 9))),
    )

    assert summary is not None
    assert summary.mapped_course_count == 57
    assert summary.required_course_count == 28
    assert summary.completed_mapped_course_count == 9
    assert summary.completed_required_course_count == 7


def test_location_options_and_campus_query_are_exposed_by_api() -> None:
    client = _client()
    options = client.get("/api/options").json()
    response = client.get("/api/classes", params={"campus": "San Diego Campus"})

    assert options["campuses"] == ["San Diego Campus", "Imperial Valley Campus"]
    assert response.status_code == 200
    assert response.json()["filtered_total"] > 0
    assert all(item["campus"] == "San Diego Campus" for item in response.json()["items"])
