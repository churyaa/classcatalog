from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrontendCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class FrontendCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: FrontendCheckStatus
    details: str
    duration_ms: float = Field(ge=0)


class FrontendCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    sections: int = Field(ge=0)
    courses: int = Field(ge=0)
    physical_sections: int = Field(ge=0)
    active_subjects: int = Field(ge=0)
    catalog_programs: int = Field(ge=0)
    catalog_requirements: int = Field(ge=0)


class FrontendValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: FrontendCheckStatus
    base_url: str
    generated_at: str
    counts: FrontendCounts
    checks: tuple[FrontendCheck, ...]
    browser_console_errors: tuple[str, ...] = ()
    page_errors: tuple[str, ...] = ()
    screenshots: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
