from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from classcatalog.scraping.models import (
    CourseDetailOutput,
    CourseSearchHit,
    DetailCourseStatus,
    ScrapeCheckpoint,
    SubjectCheckpointState,
    SubjectCheckpointStatus,
    SubjectDetailOutput,
    SubjectScrapeError,
    SubjectScrapeResult,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write(path: Path, payload: str) -> None:
    """Atomically replace a UTF-8 text file in the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def write_model(path: Path, model: BaseModel) -> None:
    atomic_write(path, model.model_dump_json(indent=2))


def read_model(path: Path, model_type: type[_ModelT]) -> _ModelT:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def course_detail_key(hit: CourseSearchHit) -> str:
    """Return a stable key for a course-level PeopleSoft detail target.

    ``CLASS_NBR`` is intentionally excluded because the representative class used by
    the result-list URL can change while the underlying course offering remains the
    same. ``CRSE_ID`` + offer number + career are stable when PeopleSoft exposes them.
    """

    stable_course = hit.crse_id or hit.course_code
    return "|".join(
        (
            hit.subject,
            stable_course,
            hit.crse_offer_nbr or "",
            hit.acad_career or "",
        )
    )


def detail_target_hits(
    result: SubjectScrapeResult,
    *,
    limit: int,
) -> tuple[CourseSearchHit, ...]:
    if limit <= 0:
        return ()
    targets: list[CourseSearchHit] = []
    for hit in result.courses:
        if hit.detail_url is None:
            continue
        targets.append(hit)
        if len(targets) >= limit:
            break
    return tuple(targets)


def expected_detail_target_count(
    result: SubjectScrapeResult,
    *,
    limit: int,
) -> int:
    """Return how many course-level details a deep run is expected to target."""

    if limit <= 0:
        return 0
    return min(limit, len(result.courses))


def subject_output_requires_search_refresh(output: SubjectDetailOutput) -> bool:
    """Return whether a persisted search result is unsafe to reuse for deep details.

    A stale PeopleSoft response can still contain course labels while omitting every
    detail link.  Earlier builds then produced an empty target list and incorrectly
    marked that subject complete.  Compare the persisted target keys against the
    number of scheduled courses the run was configured to detail.
    """

    expected = expected_detail_target_count(
        output.search_result,
        limit=output.detail_limit,
    )
    if expected == 0:
        return False
    current_targets = detail_target_hits(
        output.search_result,
        limit=output.detail_limit,
    )
    expected_keys = tuple(course_detail_key(hit) for hit in current_targets)
    return (
        len(current_targets) != expected
        or len(output.detail_target_course_keys) != expected
        or output.detail_target_course_keys != expected_keys
    )


def _ordered_course_details(
    target_keys: tuple[str, ...],
    course_details: Iterable[CourseDetailOutput],
) -> tuple[CourseDetailOutput, ...]:
    by_key = {record.course_key: record for record in course_details}
    return tuple(by_key[key] for key in target_keys if key in by_key)


def refresh_subject_output(
    output: SubjectDetailOutput,
    *,
    course_details: Iterable[CourseDetailOutput] | None = None,
    error: SubjectScrapeError | None = None,
    updated_at: str | None = None,
) -> SubjectDetailOutput:
    """Recalculate status and detail counters for a per-subject output file."""

    timestamp = updated_at or utc_now()
    ordered = _ordered_course_details(
        output.detail_target_course_keys,
        output.course_details if course_details is None else course_details,
    )
    completed = sum(record.status is DetailCourseStatus.COMPLETE for record in ordered)
    partial = sum(
        record.status is not DetailCourseStatus.COMPLETE for record in ordered
    )
    attempted = len(ordered)
    warnings = sum(len(record.warning_events) for record in ordered)
    expected_targets = expected_detail_target_count(
        output.search_result,
        limit=output.detail_limit,
    )
    target_keys_complete = len(output.detail_target_course_keys) == expected_targets
    detail_complete = (
        target_keys_complete
        and attempted == expected_targets
        and completed == expected_targets
    )
    complete = output.search_result.complete and detail_complete and error is None
    return output.model_copy(
        update={
            "updated_at": timestamp,
            "completed_at": timestamp if complete else None,
            "course_details": ordered,
            "error": error,
            "complete": complete,
            "detail_courses_targeted": expected_targets,
            "detail_courses_attempted": attempted,
            "detail_courses_complete": completed,
            "detail_courses_partial": partial,
            "detail_warnings": warnings,
        }
    )


def build_subject_output(
    *,
    run_id: str,
    started_at: str,
    detail_limit: int,
    result: SubjectScrapeResult,
    existing: SubjectDetailOutput | None = None,
) -> SubjectDetailOutput:
    """Create or refresh a per-subject output while retaining reusable course details."""

    targets = detail_target_hits(result, limit=detail_limit)
    target_keys = tuple(course_detail_key(hit) for hit in targets)
    reusable: tuple[CourseDetailOutput, ...] = ()
    if existing is not None:
        reusable = tuple(
            record for record in existing.course_details if record.course_key in target_keys
        )
    output = SubjectDetailOutput(
        run_id=run_id,
        started_at=existing.started_at if existing is not None else started_at,
        updated_at=utc_now(),
        term=result.term,
        term_code=result.term_code,
        subject=result.subject,
        detail_limit=detail_limit,
        search_result=result,
        detail_target_course_keys=target_keys,
        course_details=reusable,
    )
    return refresh_subject_output(output)


def replace_course_detail(
    output: SubjectDetailOutput,
    record: CourseDetailOutput,
) -> SubjectDetailOutput:
    by_key = {item.course_key: item for item in output.course_details}
    by_key[record.course_key] = record
    return refresh_subject_output(output, course_details=by_key.values())


def checkpoint_state_from_subject_output(
    output: SubjectDetailOutput,
    *,
    output_path: Path,
    force_status: SubjectCheckpointStatus | None = None,
) -> SubjectCheckpointState:
    if force_status is not None:
        status = force_status
    elif output.complete:
        status = SubjectCheckpointStatus.COMPLETE
    elif output.error is not None:
        status = SubjectCheckpointStatus.FAILED
    else:
        status = SubjectCheckpointStatus.PARTIAL

    completed_keys = tuple(
        record.course_key
        for record in output.course_details
        if record.status is DetailCourseStatus.COMPLETE
    )
    partial_keys = tuple(
        record.course_key
        for record in output.course_details
        if record.status is not DetailCourseStatus.COMPLETE
    )
    return SubjectCheckpointState(
        subject=output.subject,
        status=status,
        output_path=str(output_path),
        updated_at=output.updated_at,
        completed_course_keys=completed_keys,
        partial_course_keys=partial_keys,
        error=output.error,
    )


def replace_checkpoint_state(
    checkpoint: ScrapeCheckpoint,
    state: SubjectCheckpointState,
    *,
    completed_at: str | None = None,
) -> ScrapeCheckpoint:
    by_subject = {item.subject: item for item in checkpoint.subjects}
    by_subject[state.subject] = state
    ordered = tuple(by_subject[subject] for subject in checkpoint.requested_subjects)
    timestamp = utc_now()
    all_complete = all(item.status is SubjectCheckpointStatus.COMPLETE for item in ordered)
    return checkpoint.model_copy(
        update={
            "updated_at": timestamp,
            "completed_at": completed_at if completed_at is not None else (
                timestamp if all_complete else None
            ),
            "subjects": ordered,
        }
    )


def failed_checkpoint_state(
    *,
    subject: str,
    output_path: Path,
    error: SubjectScrapeError,
) -> SubjectCheckpointState:
    return SubjectCheckpointState(
        subject=subject,
        status=SubjectCheckpointStatus.FAILED,
        output_path=str(output_path),
        updated_at=utc_now(),
        error=error,
    )
