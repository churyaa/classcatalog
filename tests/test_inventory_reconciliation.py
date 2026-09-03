from __future__ import annotations

from pathlib import Path

from classcatalog.dataset.builder import _build_inventory_diff
from classcatalog.dataset.models import InventoryCourseSnapshot, ValidationIssue
from classcatalog.scraping.models import CourseSearchHit, ScrapeRunOutput, SubjectScrapeResult
from classcatalog.scraping.progress import write_model


def _hit(
    *,
    subject: str,
    catalog_number: str,
    title: str,
    crse_id: str,
    offer: str,
    career: str = "UGRD",
    class_number: str = "1000",
) -> CourseSearchHit:
    return CourseSearchHit(
        term="Fall 2026",
        term_code="2267",
        subject=subject,
        catalog_number=catalog_number,
        course_code=f"{subject} {catalog_number}",
        title=title,
        detail_url=(
            "https://example.invalid/detail?"
            f"CRSE_ID={crse_id}&CRSE_OFFER_NBR={offer}&ACAD_CAREER={career}&"
            f"CLASS_NBR={class_number}"
        ),
        crse_id=crse_id,
        crse_offer_nbr=offer,
        acad_career=career,
        class_number=class_number,
        section_count=1,
        source_row_index=0,
        raw_text=f"{subject} {catalog_number} {title}",
    )


def _snapshot(hit: CourseSearchHit) -> InventoryCourseSnapshot:
    return InventoryCourseSnapshot(
        course_key="|".join(
            (
                hit.subject,
                hit.crse_id or "",
                hit.crse_offer_nbr or "",
                hit.acad_career or "",
            )
        ),
        subject=hit.subject,
        catalog_number=hit.catalog_number,
        course_code=hit.course_code,
        title=hit.title,
        crse_id=hit.crse_id,
        crse_offer_nbr=hit.crse_offer_nbr,
        acad_career=hit.acad_career,
        class_number=hit.class_number,
        section_count=hit.section_count,
        detail_url=hit.detail_url,
    )


def _run(hits: tuple[CourseSearchHit, ...]) -> ScrapeRunOutput:
    subjects = tuple(dict.fromkeys(hit.subject for hit in hits))
    results = tuple(
        SubjectScrapeResult(
            term="Fall 2026",
            term_code="2267",
            subject=subject,
            fetched_at="2026-08-20T00:00:00+00:00",
            search_url="https://example.invalid/search",
            initial_result_count=sum(hit.subject == subject for hit in hits),
            filtered_result_count=sum(hit.subject == subject for hit in hits),
            exact_facet_applied=True,
            initial_result_cap_warning=False,
            filtered_result_cap_warning=False,
            complete=True,
            courses=tuple(hit for hit in hits if hit.subject == subject),
        )
        for subject in subjects
    )
    return ScrapeRunOutput(
        started_at="2026-08-20T00:00:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        term="Fall 2026",
        term_code="2267",
        requested_subjects=subjects,
        completed_subjects=subjects,
        results=results,
    )


def test_inventory_diff_ignores_offer_numbers_and_classifies_true_drift(
    tmp_path: Path,
) -> None:
    discovery_hits = (
        _hit(
            subject="A E",
            catalog_number="340",
            title="Fluid Mechanics",
            crse_id="038493",
            offer="1",
        ),
        _hit(
            subject="R A",
            catalog_number="795",
            title="Capstone Development I",
            crse_id="038812",
            offer="2",
            career="GRAD",
        ),
        _hit(
            subject="HUM",
            catalog_number="343",
            title="Urban Humanities",
            crse_id="042439",
            offer="1",
        ),
        _hit(
            subject="CS",
            catalog_number="150",
            title="Introduction to Computer Programming",
            crse_id="038518",
            offer="1",
        ),
    )
    deep_hits = (
        _hit(
            subject="A E",
            catalog_number="340",
            title="Fluid Mechanics",
            crse_id="038493",
            offer="2",
        ),
        _hit(
            subject="R A",
            catalog_number="795",
            title="Capstone Development I",
            crse_id="042739",
            offer="2",
            career="GRAD",
        ),
        _hit(
            subject="P A",
            catalog_number="792",
            title="Problem Analysis",
            crse_id="002854",
            offer="1",
            career="GRAD",
        ),
        _hit(
            subject="CS",
            catalog_number="150",
            title="Introduction to Computer Programming",
            crse_id="038518",
            offer="1",
        ),
    )
    discovery_path = tmp_path / "discovery.json"
    write_model(discovery_path, _run(discovery_hits))
    deep_inventory = {item.course_key: item for item in map(_snapshot, deep_hits)}
    issues: list[ValidationIssue] = []

    diff = _build_inventory_diff(
        deep_inventory=deep_inventory,
        discovery_path=discovery_path,
        issues=issues,
    )

    assert diff.status == "drift_detected"
    assert diff.matching_courses == 2
    assert [item.course_code for item in diff.only_in_discovery] == ["HUM 343"]
    assert [item.course_code for item in diff.only_in_deep] == ["P A 792"]
    assert [item.discovery.course_code for item in diff.rekeyed_courses] == ["R A 795"]
    assert [item.discovery.course_code for item in diff.offering_number_changes] == [
        "A E 340"
    ]
    assert diff.offering_number_changes[0].discovery_offer_numbers == ("1",)
    assert diff.offering_number_changes[0].deep_offer_numbers == ("2",)
    assert not diff.changed_courses
    assert sum(issue.code == "inventory_drift_detected" for issue in issues) == 1
