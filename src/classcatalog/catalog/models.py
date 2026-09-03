from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from classcatalog.models import ProgramClassification


class CatalogSourceKind(StrEnum):
    CATALOG_INDEX = "catalog_index"
    PROGRAM = "program"
    REQUIREMENT = "requirement"
    DEPARTMENT = "department"


class CatalogSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: CatalogSourceKind
    url: str
    title: str | None = None
    fetched_at: str | None = None
    fixture_path: str | None = None


class ProgramCourseMapping(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_code: str
    classification: ProgramClassification
    group: str | None = None
    source_url: str | None = None
    notes: tuple[str, ...] = ()


class CatalogProgram(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    catalog_year: str
    source_url: str
    department_url: str | None = None
    degree_type: str | None = None
    mappings: tuple[ProgramCourseMapping, ...] = ()
    unmapped_course_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @computed_field
    @property
    def mapped_course_count(self) -> int:
        return len({mapping.course_code for mapping in self.mappings})


class CatalogRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    catalog_year: str
    source_url: str
    course_codes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @computed_field
    @property
    def display_name(self) -> str:
        if self.code and self.code.casefold() not in self.name.casefold():
            return f"{self.code}: {self.name}"
        return self.name


class CatalogMappings(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    institution: str = "San Diego State University"
    catalog_year: str
    catoid: int | None = None
    generated_at: str
    source_index_url: str
    sources: tuple[CatalogSource, ...] = ()
    programs: tuple[CatalogProgram, ...] = ()
    requirements: tuple[CatalogRequirement, ...] = ()
    warnings: tuple[str, ...] = ()


class CatalogProgramSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    catalog_year: str
    source_url: str
    degree_type: str | None = None
    mapped_course_count: int = Field(ge=0)
    major_prep_count: int = Field(ge=0)
    major_course_count: int = Field(ge=0)
    elective_count: int = Field(ge=0)
    scheduled_course_count: int = Field(ge=0)


class CatalogStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    loaded: bool
    institution: str | None = None
    catalog_years: tuple[str, ...] = ()
    programs: int = Field(default=0, ge=0)
    requirements: int = Field(default=0, ge=0)
    mapped_course_codes: int = Field(default=0, ge=0)
    generated_at: str | None = None
    source_index_url: str | None = None
    data_file: str | None = None
    warnings: tuple[str, ...] = ()


class StudentProfileSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    program: str
    catalog_year: str
    source_url: str
    completed_courses: tuple[str, ...] = ()
    mapped_course_count: int = Field(ge=0)
    scheduled_course_count: int = Field(ge=0)
    completed_mapped_course_count: int = Field(ge=0)
    required_course_count: int = Field(ge=0)
    completed_required_course_count: int = Field(ge=0)
    remaining_scheduled_course_count: int = Field(ge=0)
    major_prep_courses: tuple[str, ...] = ()
    major_course_courses: tuple[str, ...] = ()
    elective_courses: tuple[str, ...] = ()
    remaining_scheduled_courses: tuple[str, ...] = ()
    note: str = (
        "Planning summary only. Course choices, units, grades, residency, and non-course "
        "conditions still require the official SDSU degree evaluation and advisor review."
    )

class CatalogProgramListItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    catalog_year: str
    source_url: str
    degree_type: str | None = None
    mapped_course_count: int = Field(ge=0)
    major_prep_count: int = Field(ge=0)
    major_course_count: int = Field(ge=0)
    elective_count: int = Field(ge=0)
    warning_count: int = Field(default=0, ge=0)


class CatalogProgramListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=1)
    items: tuple[CatalogProgramListItem, ...] = ()


class CatalogProgramRequirementGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    classification: ProgramClassification
    group: str
    course_codes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class CatalogProgramDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    catalog_year: str
    source_url: str
    department_url: str | None = None
    degree_type: str | None = None
    mapped_course_count: int = Field(ge=0)
    groups: tuple[CatalogProgramRequirementGroup, ...] = ()
    unmapped_course_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class CatalogRequirementSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    display_name: str
    catalog_year: str
    source_url: str
    course_count: int = Field(ge=0)


class CatalogRequirementListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total: int = Field(ge=0)
    items: tuple[CatalogRequirementSummary, ...] = ()


class CatalogRequirementDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    display_name: str
    catalog_year: str
    source_url: str
    course_codes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
