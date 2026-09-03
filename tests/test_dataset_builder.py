from __future__ import annotations

import json
from datetime import time
from pathlib import Path

from classcatalog.dataset.builder import (
    DatasetBuildConfig,
    _needs_option_recovery,
    _recovered_option_data,
    build_production_dataset,
)
from classcatalog.filters import SearchFilters
from classcatalog.models import GradingType, InstructionMode, SeatStatus, Weekday
from classcatalog.repository import CourseRepository
from classcatalog.scraping.models import (
    CourseClassOption,
    CourseDetailOutput,
    CourseInfoRecord,
    CourseSearchHit,
    DetailCourseStatus,
    ScrapeCheckpoint,
    ScrapeRunOutput,
    SdsuCourseSectionRecord,
    SubjectCheckpointState,
    SubjectCheckpointStatus,
    SubjectDetailOutput,
    SubjectScrapeError,
    SubjectScrapeResult,
)
from classcatalog.scraping.progress import write_model


def _hit(
    *,
    subject: str = "CS",
    catalog_number: str = "150",
    crse_id: str = "038518",
    class_number: str = "3213",
) -> CourseSearchHit:
    return CourseSearchHit(
        term="Fall 2026",
        term_code="2267",
        subject=subject,
        catalog_number=catalog_number,
        course_code=f"{subject} {catalog_number}",
        title="Introduction to Computer Programming",
        detail_url=(
            "https://example.invalid/detail?"
            f"CRSE_ID={crse_id}&CRSE_OFFER_NBR=1&ACAD_CAREER=UGRD&"
            f"CLASS_NBR={class_number}"
        ),
        crse_id=crse_id,
        crse_offer_nbr="1",
        acad_career="UGRD",
        class_number=class_number,
        section_count=1,
        source_row_index=0,
        raw_text=f"{subject} {catalog_number} Introduction to Computer Programming",
    )


def _subject_output(*, complete: bool = True) -> SubjectDetailOutput:
    hit = _hit()
    result = SubjectScrapeResult(
        term="Fall 2026",
        term_code="2267",
        subject="CS",
        fetched_at="2026-08-23T00:00:00+00:00",
        search_url="https://example.invalid/search",
        initial_result_count=1,
        filtered_result_count=1,
        exact_facet_applied=True,
        initial_result_cap_warning=False,
        filtered_result_cap_warning=False,
        complete=True,
        courses=(hit,),
    )
    course_info = CourseInfoRecord(
        term="Fall 2026",
        term_code="2267",
        subject="CS",
        catalog_number="150",
        course_code="CS 150",
        title="Introduction to Computer Programming",
        description="Computing methodology and problem solving.",
        units=3.0,
        units_min=3.0,
        units_max=3.0,
        units_text="3.00",
        grading=GradingType.LETTER,
        grading_text="Letter",
        components="Lecture",
        course_career="Undergraduate",
        selected_class_number="3213",
        selected_section_number="01",
        source_url="https://example.invalid/course",
        options_start=1,
        options_end=1,
        options_total=1,
        options_complete=True,
        options=(
            CourseClassOption(
                option_number=1,
                status=SeatStatus.OPEN,
                raw_status="Open",
                session="Regular Academic Session",
                component="Lecture",
                class_number="3213",
                section_number="01",
                meeting_dates="08/24/2026 - 12/11/2026",
                days_times_text="Monday Wednesday 10:00AM to 11:15AM",
                days=(Weekday.MON, Weekday.WED),
                start_time=time(10, 0),
                end_time=time(11, 15),
                location="San Diego State University (GMCS 301)",
                instructor="Ada Lovelace",
                open_seats=5,
                seat_capacity=40,
                seats_enrolled=35,
                source_row_index=0,
            ),
        ),
    )
    section = SdsuCourseSectionRecord(
        term="Fall 2026",
        term_code="2267",
        course_code="CS 150",
        subject="CS",
        catalog_number="150",
        title="Introduction to Computer Programming",
        description="Computing methodology and problem solving.",
        class_number="3213",
        section_number=None,
        component="Lecture",
        units=3.0,
        units_min=3.0,
        units_max=3.0,
        units_text="3.00",
        grading=GradingType.LETTER,
        grading_text="Letter",
        prerequisite_text="MATH 150",
        enrollment_requirements=("MATH 150",),
        instruction_mode=InstructionMode.IN_PERSON,
        instruction_mode_text="In-Person",
        seat_status=SeatStatus.OPEN,
        seats_available=5,
        seat_capacity=40,
        seats_enrolled=35,
        waitlist_capacity=10,
        waitlist_total=0,
        waitlist_available=10,
        campus="San Diego Campus",
        location="San Diego State University",
        instructor="Ada Lovelace",
        bookstore_url="https://example.invalid/books",
        course_source_url="https://example.invalid/course",
    )
    detail_status = DetailCourseStatus.COMPLETE if complete else DetailCourseStatus.PARTIAL
    detail = CourseDetailOutput(
        course_key="CS|038518|1|UGRD",
        course=hit,
        status=detail_status,
        started_at="2026-08-23T00:00:00+00:00",
        updated_at="2026-08-23T00:01:00+00:00",
        completed_at="2026-08-23T00:01:00+00:00" if complete else None,
        attempts=1,
        course_info=course_info,
        sections=(section,),
    )
    return SubjectDetailOutput(
        run_id="run-1",
        started_at="2026-08-23T00:00:00+00:00",
        updated_at="2026-08-23T00:01:00+00:00",
        completed_at="2026-08-23T00:01:00+00:00" if complete else None,
        term="Fall 2026",
        term_code="2267",
        subject="CS",
        detail_limit=9999,
        search_result=result,
        detail_target_course_keys=("CS|038518|1|UGRD",),
        course_details=(detail,),
        complete=complete,
        detail_courses_targeted=1,
        detail_courses_attempted=1,
        detail_courses_complete=1 if complete else 0,
        detail_courses_partial=0 if complete else 1,
    )


def _checkpoint(subject_path: Path, aggregate_path: Path) -> ScrapeCheckpoint:
    return ScrapeCheckpoint(
        run_id="run-1",
        started_at="2026-08-23T00:00:00+00:00",
        updated_at="2026-08-23T00:01:00+00:00",
        completed_at="2026-08-23T00:01:00+00:00",
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS",),
        detail_limit=9999,
        output_path=str(aggregate_path),
        subject_output_dir=str(subject_path.parent),
        subjects=(
            SubjectCheckpointState(
                subject="CS",
                status=SubjectCheckpointStatus.COMPLETE,
                output_path=str(subject_path),
                updated_at="2026-08-23T00:01:00+00:00",
                completed_course_keys=("CS|038518|1|UGRD",),
            ),
        ),
    )


def _aggregate(output: SubjectDetailOutput) -> ScrapeRunOutput:
    return ScrapeRunOutput(
        started_at=output.started_at,
        completed_at=output.completed_at,
        term=output.term,
        term_code=output.term_code,
        requested_subjects=(output.subject,),
        completed_subjects=(output.subject,) if output.complete else (),
        results=(output.search_result,),
        detail_courses_attempted=1,
        detail_courses_complete=1 if output.complete else 0,
        detail_courses_partial=0 if output.complete else 1,
    )


def test_builds_api_data_and_reports_inventory_drift(tmp_path: Path) -> None:
    subject_dir = tmp_path / "subjects"
    subject_path = subject_dir / "cs.json"
    aggregate_path = tmp_path / "deep.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    discovery_path = tmp_path / "discovery.json"
    api_path = tmp_path / "api" / "sections.json"
    output_dir = tmp_path / "production"

    output = _subject_output()
    write_model(subject_path, output)
    write_model(aggregate_path, _aggregate(output))
    write_model(checkpoint_path, _checkpoint(subject_path, aggregate_path))

    extra = _hit(subject="MATH", catalog_number="150", crse_id="099999", class_number="9999")
    discovery = _aggregate(output).model_copy(
        update={
            "requested_subjects": ("CS", "MATH"),
            "completed_subjects": ("CS", "MATH"),
            "results": (
                output.search_result,
                output.search_result.model_copy(
                    update={"subject": "MATH", "courses": (extra,)}
                ),
            ),
        }
    )
    write_model(discovery_path, discovery)

    result = build_production_dataset(
        DatasetBuildConfig(
            checkpoint_path=checkpoint_path,
            discovery_run_path=discovery_path,
            output_dir=output_dir,
            api_data_path=api_path,
        )
    )

    assert result.exit_code == 0
    assert result.report.status == "passed"
    assert result.report.counts.discovered_courses == 1
    assert result.report.counts.course_section_listings == 1
    assert result.report.counts.unique_physical_sections == 1
    assert result.report.inventory_diff.status == "drift_detected"
    assert [item.course_code for item in result.report.inventory_diff.only_in_discovery] == [
        "MATH 150"
    ]
    assert api_path.is_file()

    repository = CourseRepository.from_json(api_path)
    assert repository.total == 1
    response = repository.search(SearchFilters())
    section = response.items[0]
    assert section.section_number == "01"
    assert section.units_text == "3.00"
    assert section.term_code == "2267"

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "passed"
    assert len(manifest["files"]) == 5


def test_strict_build_does_not_publish_incomplete_subject(tmp_path: Path) -> None:
    subject_dir = tmp_path / "subjects"
    subject_path = subject_dir / "cs.json"
    aggregate_path = tmp_path / "deep.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    output_dir = tmp_path / "production"

    output = _subject_output(complete=False)
    write_model(subject_path, output)
    write_model(aggregate_path, _aggregate(output))
    write_model(checkpoint_path, _checkpoint(subject_path, aggregate_path))

    result = build_production_dataset(
        DatasetBuildConfig(
            checkpoint_path=checkpoint_path,
            output_dir=output_dir,
        )
    )

    assert result.exit_code == 1
    assert result.report.errors >= 2
    assert not (output_dir / "sections.json").exists()
    assert (output_dir / "validation-report.json").is_file()


def test_incomplete_discovery_snapshot_is_rejected(tmp_path: Path) -> None:
    subject_dir = tmp_path / "subjects"
    subject_path = subject_dir / "cs.json"
    aggregate_path = tmp_path / "deep.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    discovery_path = tmp_path / "discovery.json"
    output_dir = tmp_path / "production"

    output = _subject_output()
    write_model(subject_path, output)
    write_model(aggregate_path, _aggregate(output))
    write_model(checkpoint_path, _checkpoint(subject_path, aggregate_path))
    incomplete = _aggregate(output).model_copy(
        update={
            "completed_subjects": (),
            "errors": (
                SubjectScrapeError(
                    term="Fall 2026",
                    subject="CS",
                    error_type="TestError",
                    message="Incomplete discovery fixture",
                ),
            ),
        }
    )
    write_model(discovery_path, incomplete)

    result = build_production_dataset(
        DatasetBuildConfig(
            checkpoint_path=checkpoint_path,
            discovery_run_path=discovery_path,
            output_dir=output_dir,
        )
    )

    assert result.exit_code == 1
    assert result.report.inventory_diff.status == "invalid"
    assert any(
        issue.code == "discovery_snapshot_incomplete"
        for issue in result.report.issues
    )
    assert not (output_dir / "sections.json").exists()



def test_option_recovery_skips_normal_single_component_courses() -> None:
    detail = _subject_output().course_details[0]
    assert _needs_option_recovery(detail) is False

    duplicate_lecture = detail.sections[0].model_copy(
        update={"class_number": "3214", "option_number": None}
    )
    same_component = detail.model_copy(
        update={"sections": (detail.sections[0], duplicate_lecture)}
    )
    assert _needs_option_recovery(same_component) is False


def test_option_recovery_runs_only_for_missing_linked_component_options() -> None:
    detail = _subject_output().course_details[0]
    lecture = detail.sections[0].model_copy(
        update={"option_number": 1, "component": "Discussion"}
    )
    activity = detail.sections[0].model_copy(
        update={
            "class_number": "9999",
            "option_number": None,
            "component": "Activity",
            "units": 0.0,
            "units_min": 0.0,
            "units_max": 0.0,
            "units_text": "0.00",
        }
    )
    linked = detail.model_copy(update={"sections": (lecture, activity)})
    assert _needs_option_recovery(linked) is True

    complete = linked.model_copy(
        update={
            "sections": (
                lecture,
                activity.model_copy(update={"option_number": 1}),
            )
        }
    )
    assert _needs_option_recovery(complete) is False

def test_rebuild_recovers_secondary_component_option_numbers_from_saved_course_html(
    tmp_path: Path,
) -> None:
    subject_dir = tmp_path / "subjects"
    subject_path = subject_dir / "cs.json"
    aggregate_path = tmp_path / "deep.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    api_path = tmp_path / "api" / "sections.json"
    output_dir = tmp_path / "production"

    output = _subject_output()
    original_detail = output.course_details[0]
    original_section = original_detail.sections[0]

    fixture = (
        Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-course-info.html"
    ).read_text(encoding="utf-8")
    fixture = fixture.replace(
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Lrg Lect - 3212</a>',
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Discussion - 3212</a>'
        '<br><a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$294$$0">Activity - 9999</a>',
        1,
    )
    course_html = tmp_path / "detail-cs150-course-info.html"
    course_html.write_text(fixture, encoding="utf-8")

    discussion = original_section.model_copy(
        update={
            "class_number": "3212",
            "section_number": None,
            "option_number": None,
            "component": "Discussion",
            "units": 3.0,
            "units_min": 3.0,
            "units_max": 3.0,
        }
    )
    activity = original_section.model_copy(
        update={
            "class_number": "9999",
            "section_number": None,
            "option_number": None,
            "component": "Activity",
            "units": 0.0,
            "units_min": 0.0,
            "units_max": 0.0,
            "units_text": "0.00",
            "instructor": "Grace Hopper",
        }
    )
    old_course_info = original_detail.course_info.model_copy(
        update={
            "options": tuple(
                option.model_copy(update={"option_number": None})
                for option in original_detail.course_info.options
            )
        }
    )
    detail = original_detail.model_copy(
        update={
            "course_info": old_course_info,
            "sections": (discussion, activity),
            "fixture_paths": (str(course_html),),
        }
    )
    output = output.model_copy(update={"course_details": (detail,)})

    write_model(subject_path, output)
    write_model(aggregate_path, _aggregate(output))
    write_model(checkpoint_path, _checkpoint(subject_path, aggregate_path))

    result = build_production_dataset(
        DatasetBuildConfig(
            checkpoint_path=checkpoint_path,
            output_dir=output_dir,
            api_data_path=api_path,
        )
    )

    assert result.exit_code == 0
    raw_sections = json.loads(api_path.read_text(encoding="utf-8"))
    by_schedule = {item["schedule_number"]: item for item in raw_sections}
    assert by_schedule["3212"]["option_number"] == 1
    assert by_schedule["9999"]["option_number"] == 1

    repository = CourseRepository.from_json(api_path)
    response = repository.search(SearchFilters(query="CS 150"))
    assert response.filtered_total == 1
    assert [item.schedule_number for item in response.items[0].linked_components] == [
        "3212",
        "9999",
    ]


def test_recovery_preserves_many_to_many_enrollment_option_memberships(tmp_path: Path) -> None:
    output = _subject_output()
    detail = output.course_details[0]
    section = detail.sections[0]

    fixture = (
        Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-course-info.html"
    ).read_text(encoding="utf-8")
    fixture = fixture.replace(
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Lrg Lect - 3212</a>',
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Discussion - 3212</a>'
        '<br><a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$294$$0">Activity - 9001</a>',
        1,
    )
    fixture = fixture.replace(
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1">2</a>',
        'id="SSR_CLSRCH_F_WK_SSR_OPTION_DESCR$306$$1">1</a>',
        1,
    )
    fixture = fixture.replace(
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1">Lrg Lect - 3213</a>',
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$1">Discussion - 3212</a>'
        '<br><a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$294$$1">Activity - 9002</a>',
        1,
    )
    course_html = tmp_path / "detail-math-course-info.html"
    course_html.write_text(fixture, encoding="utf-8")

    discussion = section.model_copy(update={
        "class_number": "3212",
        "component": "Discussion",
        "option_number": None,
        "option_group_indices": (),
    })
    activity_one = section.model_copy(update={
        "class_number": "9001",
        "component": "Activity",
        "option_number": None,
        "option_group_indices": (),
        "units": 0.0,
        "units_min": 0.0,
        "units_max": 0.0,
        "units_text": "0.00",
    })
    activity_two = activity_one.model_copy(update={"class_number": "9002"})
    legacy_detail = detail.model_copy(update={
        "sections": (discussion, activity_one, activity_two),
        "fixture_paths": (str(course_html),),
    })

    recovered = _recovered_option_data(legacy_detail)

    assert recovered.group_indices["3212"] == (1, 2)
    assert recovered.group_indices["9001"] == (1,)
    assert recovered.group_indices["9002"] == (2,)
    assert recovered.group_ids["3212"] == ("3212+9001", "3212+9002")
    assert recovered.group_ids["9001"] == ("3212+9001",)
    assert recovered.group_ids["9002"] == ("3212+9002",)
    assert recovered.primary_group_ids["3212"] == ("3212+9001", "3212+9002")


def test_recovery_runs_for_single_secondary_component_detail(tmp_path: Path) -> None:
    output = _subject_output()
    detail = output.course_details[0]
    section = detail.sections[0]

    fixture = (
        Path(__file__).parent / "fixtures" / "sdsu" / "detail-3213-course-info.html"
    ).read_text(encoding="utf-8")
    fixture = fixture.replace(
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Lrg Lect - 3212</a>',
        'id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1$294$$0">Discussion - 3212</a>'
        '<br><a id="SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_2$294$$0">Activity - 9999</a>',
        1,
    )
    course_html = tmp_path / "detail-single-secondary-course-info.html"
    course_html.write_text(fixture, encoding="utf-8")

    activity = section.model_copy(update={
        "class_number": "9999",
        "component": "Activity",
        "option_number": None,
        "option_group_indices": (),
        "option_primary_group_indices": (),
        "units": 0.0,
        "units_min": 0.0,
        "units_max": 0.0,
        "units_text": "0.00",
    })
    legacy_detail = detail.model_copy(update={
        "sections": (activity,),
        "fixture_paths": (str(course_html),),
    })

    recovered = _recovered_option_data(legacy_detail)

    assert recovered.group_indices["9999"] == (1,)
    assert recovered.primary_group_indices.get("9999", ()) == ()
    assert recovered.primary_group_indices["3212"] == (1,)
