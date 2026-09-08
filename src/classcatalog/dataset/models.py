from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from classcatalog.models import GradingType, SectionCoverageAudit


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: ValidationSeverity
    code: str
    message: str
    subject: str | None = None
    course_key: str | None = None
    class_number: str | None = None
    path: str | None = None


class FieldCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    total: int = Field(ge=0)
    present: int = Field(ge=0)
    missing: int = Field(ge=0)
    percent_present: float = Field(ge=0, le=100)
    examples: tuple[str, ...] = ()


class InventoryCourseSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_key: str
    subject: str
    catalog_number: str
    course_code: str
    title: str
    crse_id: str | None = None
    crse_offer_nbr: str | None = None
    acad_career: str | None = None
    class_number: str | None = None
    section_count: int | None = Field(default=None, ge=0)
    detail_url: str | None = None


class InventoryCourseChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_key: str
    discovery: InventoryCourseSnapshot
    deep: InventoryCourseSnapshot
    changed_fields: tuple[str, ...]


class InventoryOfferingNumberChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    logical_course_key: str
    discovery: InventoryCourseSnapshot
    deep: InventoryCourseSnapshot
    discovery_offer_numbers: tuple[str, ...] = ()
    deep_offer_numbers: tuple[str, ...] = ()


class InventoryCourseRekey(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_identity_key: str
    discovery: InventoryCourseSnapshot
    deep: InventoryCourseSnapshot
    changed_fields: tuple[str, ...]


class InventoryDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    discovery_path: str | None = None
    discovery_courses: int | None = Field(default=None, ge=0)
    deep_courses: int = Field(ge=0)
    matching_courses: int = Field(ge=0)
    only_in_discovery: tuple[InventoryCourseSnapshot, ...] = ()
    only_in_deep: tuple[InventoryCourseSnapshot, ...] = ()
    rekeyed_courses: tuple[InventoryCourseRekey, ...] = ()
    offering_number_changes: tuple[InventoryOfferingNumberChange, ...] = ()
    changed_courses: tuple[InventoryCourseChange, ...] = ()
    preferred_inventory: str = "deep_subject_outputs"
    identity_strategy: str = "subject|crse_id|crse_offer_nbr|acad_career"
    note: str | None = None


class DatasetCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_subjects: int = Field(ge=0)
    subject_files: int = Field(ge=0)
    complete_subjects: int = Field(ge=0)
    discovered_courses: int = Field(ge=0)
    complete_course_details: int = Field(ge=0)
    course_section_listings: int = Field(ge=0)
    unique_physical_sections: int = Field(ge=0)
    cross_listed_physical_sections: int = Field(ge=0)
    duplicate_course_section_listings_removed: int = Field(ge=0)


class DatasetBuildReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 2
    generated_at: str
    status: str
    term: str | None = None
    term_code: str | None = None
    source_run_id: str | None = None
    checkpoint_path: str | None = None
    subject_output_dir: str
    deep_run_path: str | None = None
    discovery_run_path: str | None = None
    counts: DatasetCounts
    inventory_diff: InventoryDiff
    section_coverage_audit: SectionCoverageAudit | None = None
    field_coverage: tuple[FieldCoverage, ...] = ()
    grading_counts: dict[str, int] = Field(default_factory=dict)
    instruction_mode_counts: dict[str, int] = Field(default_factory=dict)
    seat_status_counts: dict[str, int] = Field(default_factory=dict)
    issues: tuple[ValidationIssue, ...] = ()
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)
    output_files: dict[str, str] = Field(default_factory=dict)
    api_data_installed_to: str | None = None


class ProductionCourseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    term: str
    term_code: str
    subject: str
    catalog_number: str
    course_code: str
    crse_id: str | None = None
    crse_offer_nbr: str | None = None
    acad_career: str | None = None
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
    prerequisite_texts: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    course_section_listings: int = Field(ge=0)
    unique_physical_sections: int = Field(ge=0)
    source_url: str | None = None
    source_updated_at: str | None = None


class ManifestFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes: int = Field(ge=0)
    sha256: str


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 2
    generated_at: str
    status: str
    term: str | None = None
    term_code: str | None = None
    source_run_id: str | None = None
    counts: DatasetCounts
    inventory_status: str
    files: tuple[ManifestFile, ...]
