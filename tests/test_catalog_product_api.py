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
            generated_at="2026-08-24T00:00:00+00:00",
            source_index_url="https://catalog.sdsu.edu/index.php?catoid=12",
            programs=(
                CatalogProgram(
                    name="Computer Science, B.S.",
                    catalog_year="2026-2027",
                    source_url=(
                        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11969"
                    ),
                    degree_type="B.S.",
                    mappings=(
                        ProgramCourseMapping(
                            course_code="CS 150",
                            classification=ProgramClassification.MAJOR_PREP,
                            group="Preparation for the Major",
                        ),
                        ProgramCourseMapping(
                            course_code="CS 160",
                            classification=ProgramClassification.MAJOR_COURSE,
                            group="Major",
                        ),
                        ProgramCourseMapping(
                            course_code="CS 496",
                            classification=ProgramClassification.ELECTIVE,
                            group="Three Units Selected from",
                            notes=("Advisor approval may be required.",),
                        ),
                    ),
                ),
                CatalogProgram(
                    name="Aerospace Engineering, B.S.",
                    catalog_year="2026-2027",
                    source_url=(
                        "https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11888"
                    ),
                    degree_type="B.S.",
                    mappings=(
                        ProgramCourseMapping(
                            course_code="A E 123",
                            classification=ProgramClassification.MAJOR_PREP,
                            group="Preparation for the Major",
                        ),
                    ),
                ),
            ),
            requirements=(
                CatalogRequirement(
                    code="GE A1",
                    name="Oral Communication",
                    catalog_year="2026-2027",
                    source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11884",
                    course_codes=("COMM 103", "AFRAS 140"),
                    notes=("Complete one approved course.",),
                ),
            ),
        )
    )
    sections = tuple(
        section.model_copy(update={"program_tags": (), "requirement_tags": ()})
        for section in CourseRepository.from_json(SAMPLE_DATA_PATH).sections
    )
    return TestClient(create_app(CourseRepository(sections, catalog=catalog)))


def test_catalog_programs_is_browsable_and_filterable() -> None:
    client = _client()

    all_programs = client.get(
        "/api/catalog/programs",
        params={"catalog_year": "2026-2027"},
    )
    search = client.get(
        "/api/catalog/programs",
        params={"catalog_year": "2026-2027", "q": "computer"},
    )

    assert all_programs.status_code == 200
    assert all_programs.json()["total"] == 2
    assert [item["name"] for item in all_programs.json()["items"]] == [
        "Aerospace Engineering, B.S.",
        "Computer Science, B.S.",
    ]
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["mapped_course_count"] == 3
    assert search.json()["items"][0]["major_prep_count"] == 1
    assert search.json()["items"][0]["major_course_count"] == 1
    assert search.json()["items"][0]["elective_count"] == 1


def test_catalog_program_detail_preserves_requirement_groups() -> None:
    client = _client()

    response = client.get(
        "/api/catalog/program/detail",
        params={"program": "Computer Science, B.S.", "catalog_year": "2026-2027"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Computer Science, B.S."
    assert body["mapped_course_count"] == 3
    assert [group["classification"] for group in body["groups"]] == [
        "major_prep",
        "major_course",
        "elective",
    ]
    assert body["groups"][0]["group"] == "Preparation for the Major"
    assert body["groups"][0]["course_codes"] == ["CS 150"]
    assert body["groups"][2]["notes"] == ["Advisor approval may be required."]


def test_catalog_requirements_list_and_detail_are_exposed() -> None:
    client = _client()

    listing = client.get(
        "/api/catalog/requirements",
        params={"catalog_year": "2026-2027"},
    )
    detail = client.get(
        "/api/catalog/requirement",
        params={"code": "GE A1", "catalog_year": "2026-2027"},
    )

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["display_name"] == "GE A1: Oral Communication"
    assert listing.json()["items"][0]["course_count"] == 2

    assert detail.status_code == 200
    assert detail.json()["course_codes"] == ["AFRAS 140", "COMM 103"]
    assert detail.json()["notes"] == ["Complete one approved course."]


def test_catalog_product_api_returns_404_for_unknown_records() -> None:
    client = _client()

    program = client.get(
        "/api/catalog/program/detail",
        params={"program": "Not A Major", "catalog_year": "2026-2027"},
    )
    requirement = client.get(
        "/api/catalog/requirement",
        params={"code": "GE Z9", "catalog_year": "2026-2027"},
    )

    assert program.status_code == 404
    assert requirement.status_code == 404
