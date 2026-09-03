from __future__ import annotations

from classcatalog.catalog.models import (
    CatalogMappings,
    CatalogProgram,
    CatalogRequirement,
    ProgramCourseMapping,
)
from classcatalog.catalog.repository import CatalogRepository
from classcatalog.models import ProgramClassification
from classcatalog.repository import CourseRepository

from classcatalog.repository import SAMPLE_DATA_PATH


def _catalog() -> CatalogRepository:
    mappings = CatalogMappings(
        catalog_year="2026-2027",
        generated_at="2026-08-23T00:00:00+00:00",
        source_index_url="https://catalog.sdsu.edu/index.php?catoid=12",
        programs=(
            CatalogProgram(
                name="Computer Science, B.S.",
                catalog_year="2026-2027",
                source_url="https://catalog.sdsu.edu/preview_program.php?catoid=12&poid=11846",
                mappings=(
                    ProgramCourseMapping(
                        course_code="CS 150",
                        classification=ProgramClassification.MAJOR_PREP,
                    ),
                    ProgramCourseMapping(
                        course_code="CS 210",
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
    return CatalogRepository(mappings)


def test_catalog_repository_enriches_schedule_sections() -> None:
    catalog = _catalog()
    source = CourseRepository.from_json(SAMPLE_DATA_PATH).sections
    cs_source = next(section for section in source if section.course_code == "CS 150")
    comm_source = next(section for section in source if section.course_code == "COMM 103")
    repository = CourseRepository((cs_source, comm_source), catalog=catalog)

    cs = repository.sections[0]
    comm = repository.sections[1]
    assert cs.program_tags[0].program == "Computer Science, B.S."
    assert cs.program_tags[0].classification is ProgramClassification.MAJOR_PREP
    assert comm.requirement_tags == ("1C Oral Communication",)
    assert repository.options().programs == ("Computer Science, B.S.",)
    assert "1C Oral Communication" in repository.options().requirements


def test_program_and_profile_summaries_only_claim_mapped_schedule_courses() -> None:
    catalog = _catalog()
    program = catalog.program_summary(
        "Computer Science, B.S.",
        "2026-2027",
        scheduled_course_codes=("CS 150", "COMM 103"),
    )
    assert program is not None
    assert program.mapped_course_count == 2
    assert program.scheduled_course_count == 1

    profile = catalog.student_profile_summary(
        "Computer Science, B.S.",
        "2026-2027",
        scheduled_course_codes=("CS 150", "CS 210"),
        completed_courses=("CS 150",),
    )
    assert profile is not None
    assert profile.completed_mapped_course_count == 1
    assert profile.remaining_scheduled_courses == ("CS 210",)
    assert "official SDSU degree evaluation" in profile.note
