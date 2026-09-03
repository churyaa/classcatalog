from __future__ import annotations

from datetime import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class InstructionMode(StrEnum):
    IN_PERSON = "in_person"
    ONLINE_SYNCHRONOUS = "online_synchronous"
    ONLINE_ASYNCHRONOUS = "online_asynchronous"
    HYBRID = "hybrid"
    OTHER = "other"


class SeatStatus(StrEnum):
    OPEN = "open"
    WAITLIST = "waitlist"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class GradingType(StrEnum):
    LETTER = "letter"
    CREDIT_NO_CREDIT = "credit_no_credit"
    LETTER_OR_CREDIT_NO_CREDIT = "letter_or_credit_no_credit"
    OTHER = "other"


class ProgramClassification(StrEnum):
    MAJOR_PREP = "major_prep"
    MAJOR_COURSE = "major_course"
    ELECTIVE = "elective"


class SortBy(StrEnum):
    COURSE_A_Z = "alphabetical"
    COURSE_Z_A = "alphabetical_desc"
    PROFESSOR_RATING_LOW_TO_HIGH = "professor_rating_asc"
    PROFESSOR_RATING_HIGH_TO_LOW = "professor_rating"
    CLASS_DIFFICULTY_LOW_TO_HIGH = "difficulty"
    CLASS_DIFFICULTY_HIGH_TO_LOW = "difficulty_desc"
    PROFESSOR_DIFFICULTY_LOW_TO_HIGH = "professor_difficulty_asc"
    PROFESSOR_DIFFICULTY_HIGH_TO_LOW = "professor_difficulty"
    REVIEWS_LOW_TO_HIGH = "reviews_asc"
    REVIEWS_HIGH_TO_LOW = "reviews"
    TAKE_AGAIN_LOW_TO_HIGH = "take_again_asc"
    TAKE_AGAIN_HIGH_TO_LOW = "take_again"

    # Backward-compatible aliases for existing API links and tests.
    ALPHABETICAL = "alphabetical"
    PROFESSOR_RATING = "professor_rating"
    DIFFICULTY_LOW_TO_HIGH = "difficulty"
    DIFFICULTY_HIGH_TO_LOW = "difficulty_desc"
    DIFFICULTY = "difficulty"
    REVIEWS = "reviews"


class Meeting(BaseModel):
    model_config = ConfigDict(frozen=True)

    days: tuple[Weekday, ...] = ()
    start_time: time | None = None
    end_time: time | None = None
    location: str | None = None
    meeting_dates: str | None = None
    instructor: str | None = None


class ProfessorMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    external_id: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    difficulty: float | None = Field(default=None, ge=0, le=5)
    would_take_again_percent: float | None = Field(default=None, ge=0, le=100)
    num_reviews: int = Field(default=0, ge=0)
    attendance_required: bool | None = None
    textbook_required: bool | None = None
    profile_url: str | None = None
    match_confidence: float | None = Field(default=None, ge=0, le=1)


class ProgramTag(BaseModel):
    model_config = ConfigDict(frozen=True)

    program: str
    catalog_year: str
    classification: ProgramClassification


class CourseComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    section_number: str = ""
    schedule_number: str
    option_number: int | None = Field(default=None, ge=1)
    component: str | None = None
    instruction_mode: InstructionMode
    instruction_mode_text: str | None = None
    seat_status: SeatStatus
    seats_available: int | None = Field(default=None, ge=0)
    seat_capacity: int | None = Field(default=None, ge=0)
    seats_enrolled: int | None = Field(default=None, ge=0)
    seat_updated_at: str | None = None
    waitlist_available: int | None = Field(default=None, ge=0)
    campus: str
    location: str | None = None
    instructor: str | None = None
    meetings: tuple[Meeting, ...] = ()
    professor: ProfessorMetrics | None = None


class CourseSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    term: str
    term_code: str | None = None
    course_code: str
    subject: str
    catalog_number: str
    section_number: str
    schedule_number: str
    option_number: int | None = Field(default=None, ge=1)
    source_course_key: str | None = None
    option_group_indices: tuple[int, ...] = ()
    option_primary_group_indices: tuple[int, ...] = ()
    option_group_ids: tuple[str, ...] = ()
    option_primary_group_ids: tuple[str, ...] = ()
    crse_id: str | None = None
    crse_offer_nbr: str | None = None
    acad_career: str | None = None
    component: str | None = None
    title: str
    description: str | None = None
    units: float = Field(ge=0)
    units_min: float | None = Field(default=None, ge=0)
    units_max: float | None = Field(default=None, ge=0)
    units_text: str | None = None
    class_difficulty: float | None = Field(default=None, ge=0, le=5)
    grading: GradingType
    grading_text: str | None = None
    requirement_tags: tuple[str, ...] = ()
    program_tags: tuple[ProgramTag, ...] = ()
    prerequisite_text: str | None = None
    prerequisite_groups: tuple[tuple[str, ...], ...] = ()
    prerequisite_manual_review: bool = False
    enrollment_requirements: tuple[str, ...] = ()
    class_notes: tuple[str, ...] = ()
    instruction_mode: InstructionMode
    instruction_mode_text: str | None = None
    seat_status: SeatStatus
    seats_available: int | None = Field(default=None, ge=0)
    seat_capacity: int | None = Field(default=None, ge=0)
    seats_enrolled: int | None = Field(default=None, ge=0)
    seat_updated_at: str | None = None
    waitlist_capacity: int | None = Field(default=None, ge=0)
    waitlist_total: int | None = Field(default=None, ge=0)
    waitlist_available: int | None = Field(default=None, ge=0)
    campus: str
    location: str | None = None
    instructor: str | None = None
    meetings: tuple[Meeting, ...] = ()
    professor: ProfessorMetrics | None = None
    bookstore_url: str | None = None
    materials_description: str | None = None
    textbook_required: bool | None = None
    source_url: str | None = None
    source_updated_at: str | None = None
    linked_components: tuple[CourseComponent, ...] = ()




class SectionCoverageAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    course_section_listings: int = Field(ge=0)
    physical_sections: int = Field(ge=0)
    displayed_options: int = Field(ge=0)
    standalone_physical_sections: int = Field(ge=0)
    grouped_component_physical_sections: int = Field(ge=0)
    physical_option_memberships: int = Field(ge=0)
    duplicate_option_memberships: int = Field(ge=0)
    accounted_physical_sections: int = Field(ge=0)
    unaccounted_physical_sections: int = Field(ge=0)
    represented_course_section_listings: int = Field(ge=0)
    unaccounted_course_section_listings: int = Field(ge=0)
    unaccounted_examples: tuple[str, ...] = ()

class SearchResponse(BaseModel):
    filtered_total: int
    unfiltered_total: int
    page: int
    page_size: int
    total_pages: int
    items: tuple[CourseSection, ...]


class CourseLookupOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_code: str
    title: str = ""


class SearchOptions(BaseModel):
    terms: tuple[str, ...]
    courses: tuple[CourseLookupOption, ...] = ()
    campuses: tuple[str, ...] = ()
    requirements: tuple[str, ...]
    programs: tuple[str, ...]
    catalog_years: tuple[str, ...]
    classifications: tuple[ProgramClassification, ...]
    gradings: tuple[GradingType, ...]
    instruction_modes: tuple[InstructionMode, ...]
    seat_statuses: tuple[SeatStatus, ...]
