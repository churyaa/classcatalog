from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ApiCheckStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    SKIPPED = "skipped"


class ApiValidationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: ApiCheckStatus
    message: str
    duration_ms: float = Field(ge=0)
    details: dict[str, object] = Field(default_factory=dict)


class ApiValidationCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_section_listings: int = Field(ge=0)
    logical_courses: int = Field(ge=0)
    unique_physical_sections: int = Field(ge=0)
    subjects: int = Field(ge=0)
    terms: int = Field(ge=0)
    pages_at_50: int = Field(ge=1)


class ApiPerformanceMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    requests: int = Field(ge=0)
    total_ms: float = Field(ge=0)
    median_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)


class ApiValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    generated_at: str
    status: str
    data_path: str | None = None
    counts: ApiValidationCounts
    checks: tuple[ApiValidationCheck, ...] = ()
    performance: ApiPerformanceMetrics
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)
    skipped: int = Field(ge=0)
