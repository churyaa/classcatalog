from __future__ import annotations

from datetime import time
from enum import StrEnum

from classcatalog.models import GradingType, InstructionMode, SeatStatus, Weekday
from pydantic import BaseModel, ConfigDict, Field


class CourseSearchHit(BaseModel):
    """A result-list record before the course-detail page has been parsed."""

    model_config = ConfigDict(frozen=True)

    term: str
    term_code: str
    subject: str
    catalog_number: str
    course_code: str
    title: str
    detail_url: str | None = None
    crse_id: str | None = None
    crse_offer_nbr: str | None = None
    acad_career: str | None = None
    class_number: str | None = None
    section_count: int | None = Field(default=None, ge=0)
    source_row_index: int = Field(ge=0)
    raw_text: str


class SubjectScrapeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    term: str
    term_code: str
    subject: str
    fetched_at: str
    search_url: str
    initial_result_count: int = Field(ge=0)
    filtered_result_count: int = Field(ge=0)
    exact_facet_applied: bool
    initial_result_cap_warning: bool
    filtered_result_cap_warning: bool
    complete: bool
    partitioned: bool = False
    partition_facets: tuple[str, ...] = ()
    partition_leaf_count: int = Field(default=0, ge=0)
    unresolved_partition_count: int = Field(default=0, ge=0)
    courses: tuple[CourseSearchHit, ...] = ()


class SubjectScrapeError(BaseModel):
    model_config = ConfigDict(frozen=True)

    term: str
    subject: str
    error_type: str
    message: str


class ScrapeRunOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    started_at: str
    completed_at: str | None = None
    term: str
    term_code: str
    requested_subjects: tuple[str, ...]
    completed_subjects: tuple[str, ...] = ()
    results: tuple[SubjectScrapeResult, ...] = ()
    errors: tuple[SubjectScrapeError, ...] = ()
    detail_courses_attempted: int = Field(default=0, ge=0)
    detail_courses_complete: int = Field(default=0, ge=0)
    detail_courses_partial: int = Field(default=0, ge=0)
    detail_warnings: int = Field(default=0, ge=0)


class CourseClassOption(BaseModel):
    """One class option listed on an SDSU Course Information page."""

    model_config = ConfigDict(frozen=True)

    option_number: int | None = Field(default=None, ge=1)
    option_group_index: int | None = Field(default=None, ge=1)
    status: SeatStatus
    raw_status: str | None = None
    session: str | None = None
    component: str | None = None
    class_number: str
    section_number: str | None = None
    meeting_dates: str | None = None
    days_times_text: str | None = None
    days: tuple[Weekday, ...] = ()
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = None
    instructor: str | None = None
    open_seats: int | None = Field(default=None, ge=0)
    seat_capacity: int | None = Field(default=None, ge=0)
    seats_enrolled: int | None = Field(default=None, ge=0)
    source_row_index: int = Field(ge=0)


class CourseInfoRecord(BaseModel):
    """Typed data parsed from ``SSR_CRSE_INFO_FL``."""

    model_config = ConfigDict(frozen=True)

    term: str
    term_code: str | None = None
    subject: str
    catalog_number: str
    course_code: str
    title: str
    description: str | None = None
    units: float | None = Field(default=None, ge=0)
    units_min: float = Field(ge=0)
    units_max: float = Field(ge=0)
    units_text: str
    grading: GradingType
    grading_text: str | None = None
    components: str | None = None
    course_career: str | None = None
    selected_class_number: str | None = None
    selected_section_number: str | None = None
    source_url: str | None = None
    options_start: int | None = Field(default=None, ge=1)
    options_end: int | None = Field(default=None, ge=1)
    options_total: int | None = Field(default=None, ge=0)
    options_complete: bool = True
    options: tuple[CourseClassOption, ...] = ()


class ClassMeetingRecord(BaseModel):
    """One meeting row from the Class Information > Meeting Information tab."""

    model_config = ConfigDict(frozen=True)

    meeting_dates: str | None = None
    days: tuple[Weekday, ...] = ()
    start_time: time | None = None
    end_time: time | None = None
    room: str | None = None
    instructor: str | None = None


class ClassInformationRecord(BaseModel):
    """Parsed data from one or more PeopleSoft Class Information modal tabs."""

    model_config = ConfigDict(frozen=True)

    class_number: str
    section_number: str | None = None
    component: str | None = None
    course_label: str | None = None
    status: SeatStatus = SeatStatus.UNKNOWN
    raw_status: str | None = None
    selected_tab: str | None = None
    selected_tab_value: str | None = None

    enrollment_requirements: tuple[str, ...] = ()
    class_notes: tuple[str, ...] = ()
    raw_class_notes_label: str | None = None

    units: float | None = Field(default=None, ge=0)
    units_min: float | None = Field(default=None, ge=0)
    units_max: float | None = Field(default=None, ge=0)
    units_text: str | None = None
    grading: GradingType | None = None
    grading_text: str | None = None
    instruction_mode: InstructionMode | None = None
    instruction_mode_text: str | None = None
    location: str | None = None
    campus: str | None = None

    meetings: tuple[ClassMeetingRecord, ...] = ()

    seat_capacity: int | None = Field(default=None, ge=0)
    seats_enrolled: int | None = Field(default=None, ge=0)
    seats_available: int | None = Field(default=None, ge=0)
    waitlist_capacity: int | None = Field(default=None, ge=0)
    waitlist_total: int | None = Field(default=None, ge=0)
    waitlist_available: int | None = Field(default=None, ge=0)

    bookstore_url: str | None = None
    materials_description: str | None = None
    textbook_required: bool | None = None


class SdsuCourseSectionRecord(BaseModel):
    """Near-final section record assembled from the course page and all class tabs.

    ``section_number`` stays optional because the public Class Information modal does
    not expose it for every option. The selected class can still inherit a proven SEC
    value from the original PeopleSoft course URL. ``course_source_url`` is deliberately
    named as a course-level entry URL: PeopleSoft opens individual class modals through
    stateful POST actions, so there is no stable direct GET URL for every class option.
    """

    model_config = ConfigDict(frozen=True)

    term: str
    term_code: str | None = None
    course_code: str
    subject: str
    catalog_number: str
    title: str
    description: str | None = None
    class_number: str
    section_number: str | None = None
    option_number: int | None = Field(default=None, ge=1)
    option_group_indices: tuple[int, ...] = ()
    option_primary_group_indices: tuple[int, ...] = ()
    component: str | None = None
    units: float | None = Field(default=None, ge=0)
    units_min: float = Field(ge=0)
    units_max: float = Field(ge=0)
    units_text: str
    grading: GradingType
    grading_text: str | None = None
    prerequisite_text: str | None = None
    enrollment_requirements: tuple[str, ...] = ()
    class_notes: tuple[str, ...] = ()
    instruction_mode: InstructionMode = InstructionMode.OTHER
    instruction_mode_text: str | None = None
    seat_status: SeatStatus = SeatStatus.UNKNOWN
    seats_available: int | None = Field(default=None, ge=0)
    seat_capacity: int | None = Field(default=None, ge=0)
    seats_enrolled: int | None = Field(default=None, ge=0)
    waitlist_capacity: int | None = Field(default=None, ge=0)
    waitlist_total: int | None = Field(default=None, ge=0)
    waitlist_available: int | None = Field(default=None, ge=0)
    campus: str | None = None
    location: str | None = None
    instructor: str | None = None
    meetings: tuple[ClassMeetingRecord, ...] = ()
    bookstore_url: str | None = None
    materials_description: str | None = None
    textbook_required: bool | None = None
    course_source_url: str | None = None


class DetailCourseStatus(StrEnum):
    """Lifecycle state for one course-level detail scrape."""

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class SubjectCheckpointStatus(StrEnum):
    """Checkpoint state for one requested subject."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class CourseDetailOutput(BaseModel):
    """Persisted result for one course-detail target within a subject run."""

    model_config = ConfigDict(frozen=True)

    course_key: str
    course: CourseSearchHit
    status: DetailCourseStatus
    started_at: str
    updated_at: str
    completed_at: str | None = None
    attempts: int = Field(default=1, ge=1)
    warning_events: tuple[str, ...] = ()
    error: str | None = None
    course_info: CourseInfoRecord | None = None
    sections: tuple[SdsuCourseSectionRecord, ...] = ()
    fixture_paths: tuple[str, ...] = ()


class SubjectDetailOutput(BaseModel):
    """Incrementally written search/detail output for one SDSU subject."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1)
    run_id: str
    started_at: str
    updated_at: str
    completed_at: str | None = None
    term: str
    term_code: str
    subject: str
    detail_limit: int = Field(ge=0)
    search_result: SubjectScrapeResult
    detail_target_course_keys: tuple[str, ...] = ()
    course_details: tuple[CourseDetailOutput, ...] = ()
    error: SubjectScrapeError | None = None
    complete: bool = False
    detail_courses_targeted: int = Field(default=0, ge=0)
    detail_courses_attempted: int = Field(default=0, ge=0)
    detail_courses_complete: int = Field(default=0, ge=0)
    detail_courses_partial: int = Field(default=0, ge=0)
    detail_warnings: int = Field(default=0, ge=0)


class SubjectCheckpointState(BaseModel):
    """Small checkpoint record pointing to the full per-subject output file."""

    model_config = ConfigDict(frozen=True)

    subject: str
    status: SubjectCheckpointStatus = SubjectCheckpointStatus.PENDING
    output_path: str
    updated_at: str
    completed_course_keys: tuple[str, ...] = ()
    partial_course_keys: tuple[str, ...] = ()
    error: SubjectScrapeError | None = None


class ScrapeCheckpoint(BaseModel):
    """Atomic, resumable progress state for a multi-subject scraper run."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1)
    run_id: str
    started_at: str
    updated_at: str
    completed_at: str | None = None
    term: str
    term_code: str
    requested_subjects: tuple[str, ...]
    detail_limit: int = Field(ge=0)
    output_path: str
    subject_output_dir: str
    subjects: tuple[SubjectCheckpointState, ...]

