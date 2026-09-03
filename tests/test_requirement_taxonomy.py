from __future__ import annotations

from classcatalog.catalog.models import CatalogMappings, CatalogRequirement
from classcatalog.catalog.repository import CatalogRepository
from classcatalog.models import CourseSection, GradingType, InstructionMode, SeatStatus
from classcatalog.requirements import REQUIREMENT_ORDER


def _section(course_code: str) -> CourseSection:
    subject, number = course_code.rsplit(" ", 1)
    return CourseSection(
        id=course_code,
        term="Fall 2026",
        course_code=course_code,
        subject=subject,
        catalog_number=number,
        section_number="1",
        schedule_number="1",
        title="Example",
        units=3,
        grading=GradingType.LETTER,
        instruction_mode=InstructionMode.IN_PERSON,
        seat_status=SeatStatus.OPEN,
        campus="SDSU",
    )


def test_current_ge_records_collapse_into_requested_filter_taxonomy() -> None:
    requirements = (
        CatalogRequirement(code="GWAR", name="Graduation Writing Assessment Requirement", catalog_year="2026-2027", source_url="https://example.test", course_codes=("RWS 305W",)),
        CatalogRequirement(code="AI", name="American Institutions", catalog_year="2026-2027", source_url="https://example.test", course_codes=("POLS 102",)),
        CatalogRequirement(code="GE 6", name="Ethnic Studies (3 units)", catalog_year="2026-2027", source_url="https://example.test", course_codes=("AFRAS 101",)),
        CatalogRequirement(code="GE 1A", name="English Composition", catalog_year="2026-2027", source_url="https://example.test", course_codes=("RWS 100",)),
        CatalogRequirement(code="GE 1B", name="Critical Thinking", catalog_year="2026-2027", source_url="https://example.test", course_codes=("RWS 200",)),
        CatalogRequirement(code="GE 1C", name="Oral Communication", catalog_year="2026-2027", source_url="https://example.test", course_codes=("COMM 103",)),
        CatalogRequirement(code="GE 2", name="Mathematical Concepts and Quantitative Reasoning (3 units)", catalog_year="2026-2027", source_url="https://example.test", course_codes=("MATH 120",)),
        CatalogRequirement(code="GE 2", name="or 5. Mathematical Concepts and Quantitative Reasoning or Physical and Biological Sciences", catalog_year="2026-2027", source_url="https://example.test", course_codes=("ANTH 360",)),
        CatalogRequirement(code="GE 3A", name="Arts", catalog_year="2026-2027", source_url="https://example.test", course_codes=("ART 100",)),
        CatalogRequirement(code="GE 3B", name="Humanities", catalog_year="2026-2027", source_url="https://example.test", course_codes=("HUM 101",)),
        CatalogRequirement(code="GE 3", name="Arts and Humanities", catalog_year="2026-2027", source_url="https://example.test", course_codes=("AAS 326",)),
        CatalogRequirement(code="GE 4", name="Social and Behavioral Sciences (6 units)", catalog_year="2026-2027", source_url="https://example.test", course_codes=("SOC 101",)),
        CatalogRequirement(code="GE 4", name="Social and Behavioral Science", catalog_year="2026-2027", source_url="https://example.test", course_codes=("AAS 380",)),
        CatalogRequirement(code="GE 5A", name="Physical Science", catalog_year="2026-2027", source_url="https://example.test", course_codes=("ASTR 101",)),
        CatalogRequirement(code="GE 5B", name="Biological Science", catalog_year="2026-2027", source_url="https://example.test", course_codes=("BIOL 100",)),
        CatalogRequirement(code="GE 5C", name="Laboratory", catalog_year="2026-2027", source_url="https://example.test", course_codes=("BIOL 100L",)),
    )
    catalog = CatalogRepository(
        CatalogMappings(
            catalog_year="2026-2027",
            generated_at="2026-08-23T00:00:00+00:00",
            source_index_url="https://example.test",
            requirements=requirements,
        )
    )

    assert catalog.requirement_labels == REQUIREMENT_ORDER
    anth = catalog.enrich_section(_section("ANTH 360"))
    assert anth.requirement_tags == (
        "Cultural Diversity",
        "EXPLORATIONS - PHYS, BIO, MATH/QUANT",
    )
    aas = catalog.enrich_section(_section("AAS 326"))
    assert aas.requirement_tags == (
        "Cultural Diversity",
        "EXPLORATIONS - ARTS & HUMANITIES",
    )
    social = catalog.enrich_section(_section("AAS 380"))
    assert "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES" in social.requirement_tags


def test_legacy_section_tags_are_rewritten_instead_of_exposed_as_extra_filters() -> None:
    catalog = CatalogRepository.empty()
    section = _section("COMM 103").model_copy(
        update={"requirement_tags": ("GE A1: Oral Communication", "American Institutions")}
    )
    enriched = catalog.enrich_section(section)
    assert enriched.requirement_tags == (
        "American Institutions",
        "1C Oral Communication",
    )
