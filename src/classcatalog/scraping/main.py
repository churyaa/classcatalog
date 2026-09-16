from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from classcatalog.models import SeatStatus
from classcatalog.scraping.class_detail import (
    assemble_sdsu_course_section,
    ensure_all_course_option_sections,
    merge_class_information_records,
    parse_class_information_page,
)
from classcatalog.scraping.class_post import find_class_number_actions
from classcatalog.scraping.constants import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_IN_RUN_STATE_RECOVERY_ATTEMPTS,
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POST_RECOVERY_COOLDOWN_SECONDS,
    DEFAULT_STATE_CIRCUIT_BREAKER_THRESHOLD,
    DEFAULT_STATEFUL_POST_RECOVERY_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
)
from classcatalog.scraping.logging import EventLogger, configure_logging
from classcatalog.scraping.models import (
    ClassInformationRecord,
    CourseClassOption,
    CourseDetailOutput,
    CourseInfoRecord,
    DetailCourseStatus,
    ScrapeCheckpoint,
    ScrapeRunOutput,
    SubjectCheckpointState,
    SubjectCheckpointStatus,
    SubjectDetailOutput,
    SubjectScrapeError,
    SubjectScrapeResult,
    SdsuCourseSectionRecord,
)
from classcatalog.scraping.parser import has_no_results_message, parse_course_info_page
from classcatalog.scraping.progress import (
    atomic_write,
    build_subject_output,
    checkpoint_state_from_subject_output,
    course_detail_key,
    detail_target_hits,
    failed_checkpoint_state,
    read_model,
    refresh_subject_output,
    replace_checkpoint_state,
    replace_course_detail,
    subject_output_requires_search_refresh,
    utc_now,
    write_model,
)
from classcatalog.scraping.session import (
    PeopleSoftCircuitBreakerOpen,
    PeopleSoftSessionError,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
    is_recoverable_people_soft_state_error,
)
from classcatalog.subjects import SUBJECT_ABBREVIATIONS, normalize_subject_input

DEFAULT_RESULTS_DIRECTORY: Final[Path] = Path("results")
DEFAULT_FIXTURE_DIRECTORY: Final[Path] = Path("fixtures/sdsu/live")
CLASS_DETAIL_TAB_PROBES: Final[tuple[str, ...]] = ("CD", "MI", "CA", "TI")


@dataclass(frozen=True, slots=True)
class DetailScrapeStats:
    attempted: int = 0
    complete: int = 0
    partial: int = 0
    warnings: int = 0


@dataclass(frozen=True, slots=True)
class DetailScrapeOutcome:
    stats: DetailScrapeStats
    courses: tuple[CourseDetailOutput, ...] = ()


class _WarningCaptureLogger:
    def __init__(self, delegate: EventLogger) -> None:
        self._delegate = delegate
        self.warning_events: list[str] = []

    def info(self, event: str, **values: object) -> None:
        self._delegate.info(event, **values)

    def warning(self, event: str, **values: object) -> None:
        self.warning_events.append(event)
        self._delegate.warning(event, **values)

    def error(self, event: str, **values: object) -> None:
        self._delegate.error(event, **values)


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    completed_subjects: tuple[str, ...]
    results: tuple[SubjectScrapeResult, ...]
    errors: tuple[SubjectScrapeError, ...]
    detail_courses_attempted: int
    detail_courses_complete: int
    detail_courses_partial: int
    detail_warnings: int
    incomplete_subjects: int


def _scrape_exit_code(
    results: list[SubjectScrapeResult],
    errors: list[SubjectScrapeError],
    subject_outputs: list[SubjectDetailOutput] | None = None,
) -> int:
    detail_incomplete = (
        subject_outputs is not None and any(not output.complete for output in subject_outputs)
    )
    return (
        1
        if errors or any(not result.complete for result in results) or detail_incomplete
        else 0
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _parse_subjects(
    values: list[str],
    *,
    all_subjects: bool,
    discovered_subjects: tuple[str, ...] = (),
) -> tuple[str, ...]:
    live_subjects = tuple(
        dict.fromkeys(
            " ".join(normalize_subject_input(subject).split())
            for subject in discovered_subjects
            if str(subject).strip()
        )
    )
    known = set(SUBJECT_ABBREVIATIONS) | set(live_subjects)
    if all_subjects:
        selected = [*SUBJECT_ABBREVIATIONS]
        selected.extend(sorted(subject for subject in live_subjects if subject not in selected))
    elif values:
        selected = []
        for value in values:
            selected.extend(
                normalize_subject_input(item)
                for item in value.split(",")
                if item.strip()
            )
    else:
        selected = ["CS"]

    unknown = [subject for subject in selected if subject not in known]
    if unknown:
        raise ValueError(f"Unknown SDSU subject abbreviation(s): {', '.join(unknown)}")
    return tuple(dict.fromkeys(selected))


def _resume_slice(subjects: tuple[str, ...], resume_from: str | None) -> tuple[str, ...]:
    if resume_from is None:
        return subjects
    normalized = normalize_subject_input(resume_from)
    try:
        index = subjects.index(normalized)
    except ValueError as exc:
        raise ValueError(
            f"--resume-from {normalized!r} is not present in the selected subject list."
        ) from exc
    return subjects[index:]


def _atomic_write(path: Path, payload: str) -> None:
    atomic_write(path, payload)


def _write_run(
    output_path: Path,
    *,
    started_at: str,
    completed_at: str | None,
    term: str,
    term_code: str,
    requested_subjects: tuple[str, ...],
    completed_subjects: list[str],
    results: list[SubjectScrapeResult],
    errors: list[SubjectScrapeError],
    detail_courses_attempted: int = 0,
    detail_courses_complete: int = 0,
    detail_courses_partial: int = 0,
    detail_warnings: int = 0,
) -> None:
    run = ScrapeRunOutput(
        started_at=started_at,
        completed_at=completed_at,
        term=term,
        term_code=term_code,
        requested_subjects=requested_subjects,
        completed_subjects=tuple(completed_subjects),
        results=tuple(results),
        errors=tuple(errors),
        detail_courses_attempted=detail_courses_attempted,
        detail_courses_complete=detail_courses_complete,
        detail_courses_partial=detail_courses_partial,
        detail_warnings=detail_warnings,
    )
    _atomic_write(output_path, run.model_dump_json(indent=2))


def _save_fixture(
    root: Path,
    *,
    term: str,
    subject: str,
    kind: str,
    html: str,
) -> Path:
    directory = root / _slug(term) / _slug(subject)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.html"
    path.write_text(html, encoding="utf-8")
    metadata_path = directory / "metadata.json"
    metadata = {
        "term": term,
        "subject": subject,
        "files": sorted(item.name for item in directory.glob("*.html")),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return path


def _save_json_fixture(
    root: Path,
    *,
    term: str,
    subject: str,
    kind: str,
    payload: str,
) -> Path:
    directory = root / _slug(term) / _slug(subject)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.json"
    _atomic_write(path, payload)
    return path


def _default_output(term: str) -> Path:
    return DEFAULT_RESULTS_DIRECTORY / f"{_slug(term)}-course-stubs.json"


def _default_checkpoint(term: str) -> Path:
    return DEFAULT_RESULTS_DIRECTORY / _slug(term) / "checkpoint.json"


def _default_subject_output_dir(term: str) -> Path:
    return DEFAULT_RESULTS_DIRECTORY / _slug(term) / "subjects"


def _default_subject_inventory_output(term: str) -> Path:
    return DEFAULT_RESULTS_DIRECTORY / _slug(term) / "subject-inventory.json"


def _subject_inventory_document(
    *,
    term: str,
    term_code: str,
    discovered_subjects: tuple[str, ...],
) -> dict[str, object]:
    discovered = tuple(sorted(dict.fromkeys(discovered_subjects)))
    seed = tuple(SUBJECT_ABBREVIATIONS)
    effective = (*seed, *(subject for subject in discovered if subject not in seed))
    return {
        "term": term,
        "term_code": term_code,
        "seed_subjects": list(seed),
        "discovered_subjects": list(discovered),
        "effective_subjects": list(effective),
        "new_subjects": sorted(set(discovered) - set(seed)),
        # A seed subject can legitimately have no classes in one term. Do not call it
        # removed merely because it was not observed in this term's live facet probes.
        "seed_subjects_not_observed": sorted(set(seed) - set(discovered)),
    }


def _write_subject_inventory(path: Path, payload: dict[str, object]) -> None:
    _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _subject_output_path(root: Path, subject: str) -> Path:
    return root / f"{_slug(subject)}.json"

def _load_course_keys_file(path: Path | None) -> frozenset[str] | None:
    if path is None:
        return None

    raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, list):
        raise ValueError("--course-keys-file must contain a JSON array.")

    keys: set[str] = set()

    for item in raw:
        if isinstance(item, str):
            key = item.strip()
        elif isinstance(item, dict):
            key = str(item.get("course_key") or "").strip()
        else:
            key = ""

        if key:
            keys.add(key)

    if not keys:
        raise ValueError(
            "--course-keys-file did not contain any course_key values."
        )

    return frozenset(keys)


def _filter_result_to_course_keys(
        result: SubjectScrapeResult,
        course_keys: frozenset[str] | None,
) -> SubjectScrapeResult:
    if course_keys is None:
        return result

    matching = tuple(
        hit
        for hit in result.courses
        if course_detail_key(hit) in course_keys
    )

    return result.model_copy(
        update={
            "courses": matching,
            "filtered_result_count": len(matching),
        }
    )

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape SDSU public Fluid class-search result stubs with an exact subject-facet POST."
        )
    )
    parser.add_argument(
        "--term",
        help=(
            "Exact SDSU term label, for example 'Fall 2027'. Required for a scrape; "
            "there is intentionally no stale semester default."
        ),
    )
    parser.add_argument(
        "--term-code",
        help=(
            "Verified four-digit PeopleSoft STRM code; discovered from the landing page "
            "if omitted."
        ),
    )
    parser.add_argument(
        "--subjects",
        nargs="+",
        default=[],
        metavar="SUBJECT",
        help='Subjects separated by spaces or commas, for example: --subjects CS MATH "CIV E"',
    )
    parser.add_argument(
        "--all-subjects",
        action="store_true",
        help=(
            "Process the static subject seed plus live-discovered SDSU subject codes. "
            "Without this option, the safe default is CS only."
        ),
    )
    parser.add_argument(
        "--known-subjects-only",
        action="store_true",
        help=(
            "With --all-subjects, explicitly skip live Subject-facet discovery and use "
            "only the checked-in seed. Intended as an upstream-recovery escape hatch."
        ),
    )
    parser.add_argument(
        "--subject-inventory-output",
        type=Path,
        help=(
            "Where to save the live subject inventory manifest. Defaults to "
            "results/<term>/subject-inventory.json when discovery runs."
        ),
    )
    parser.add_argument(
        "--resume-from",
        help="Start at this subject in the selected order for a new run.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume from the atomic checkpoint. Completed subjects and completed "
            "course details are skipped; partial/failed course details are retried."
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Checkpoint JSON path; defaults to results/<term>/checkpoint.json.",
    )
    parser.add_argument(
        "--subject-output-dir",
        type=Path,
        help=(
            "Directory for incremental per-subject JSON output; defaults to "
            "results/<term>/subjects/."
        ),
    )
    parser.add_argument("--save-fixtures", action="store_true")
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURE_DIRECTORY)
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=None,
        help=(
            "For each subject, follow up to this many course details. Defaults to 0 "
            "for a new run; when --resume is used and omitted, the checkpoint value "
            "is reused."
        ),
    )
    parser.add_argument(
        "--course-keys-file",
        type=Path,
        help=(
            "Optional JSON file containing specific course_key values to deep-scrape. "
            "When provided, only matching discovered courses are targeted."
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=(
            "Minimum seconds between every PeopleSoft request. The default is intentionally "
            "conservative to reduce 502/503 throttling."
        ),
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=DEFAULT_JITTER_SECONDS,
        help="Random extra delay added to paced requests and recovery sleeps.",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Number of safe GET retries using 2/5/10/20-second backoff.",
    )
    parser.add_argument(
        "--post-recovery-cooldown",
        type=float,
        default=DEFAULT_POST_RECOVERY_COOLDOWN_SECONDS,
        help=(
            "Cooldown after a transient stateful POST failure before reopening fresh "
            "PeopleSoft course/class state."
        ),
    )
    parser.add_argument(
        "--stateful-post-recovery-attempts",
        type=int,
        default=DEFAULT_STATEFUL_POST_RECOVERY_ATTEMPTS,
        help=(
            "Logical recovery attempts for read-only PeopleSoft class/tab POST operations. "
            "The original stale POST body is never replayed."
        ),
    )
    parser.add_argument(
        "--in-run-state-recovery-attempts",
        type=int,
        default=DEFAULT_IN_RUN_STATE_RECOVERY_ATTEMPTS,
        help=(
            "Fresh-session retries for structurally invalid PeopleSoft subject/detail "
            "pages, such as a missing subject facet or missing detail grouplet."
        ),
    )
    parser.add_argument(
        "--state-circuit-breaker-threshold",
        type=int,
        default=DEFAULT_STATE_CIRCUIT_BREAKER_THRESHOLD,
        help=(
            "Stop the run after this many consecutive unrecovered PeopleSoft state "
            "failures. Set 0 to disable the circuit breaker."
        ),
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--json-logs", action="store_true")
    parser.add_argument(
        "--list-terms",
        action="store_true",
        help="Bootstrap the landing page, print discovered term labels/codes, and exit.",
    )
    parser.add_argument(
        "--list-subjects",
        action="store_true",
        help=(
            "Resolve --term, discover live Subject facet codes across all class statuses, "
            "write the subject inventory manifest, print it, and exit."
        ),
    )
    return parser


def _save_detail_fixtures(
    client: SdsuPeopleSoftSession,
    result: SubjectScrapeResult,
    *,
    fixture_root: Path,
    limit: int,
    logger: EventLogger,
) -> DetailScrapeStats:
    if limit <= 0:
        return DetailScrapeStats()
    attempted = 0
    complete = 0
    partial = 0
    warning_count = 0

    def detail_warning(event: str, **values: object) -> None:
        nonlocal warning_count
        warning_count += 1
        logger.warning(event, **values)

    for hit in result.courses:
        if hit.detail_url is None:
            continue
        if attempted >= limit:
            break
        attempted += 1
        course_warning_start = warning_count
        try:
            pages = client.fetch_detail_pages(hit.detail_url, referer=result.search_url)
        except Exception as exc:  # noqa: BLE001 - detail probes should not discard result stubs
            if is_recoverable_people_soft_state_error(exc):
                raise
            detail_warning(
                "detail_probe_failed",
                subject=result.subject,
                course=hit.course_code,
                error=str(exc),
            )
            partial += 1
            continue
        detail_key = _slug(hit.class_number or hit.course_code)
        shell_path = _save_fixture(
            fixture_root,
            term=result.term,
            subject=result.subject,
            kind=f"detail-{detail_key}-shell",
            html=pages.shell_html,
        )
        grouplet_path = _save_fixture(
            fixture_root,
            term=result.term,
            subject=result.subject,
            kind=f"detail-{detail_key}-grouplet",
            html=pages.grouplet_html,
        )
        course_info_path = _save_fixture(
            fixture_root,
            term=result.term,
            subject=result.subject,
            kind=f"detail-{detail_key}-course-info",
            html=pages.course_info_html,
        )

        parsed = None
        parsed_path: Path | None = None
        parsed_options: int | None = None
        class_detail_paths: list[str] = []
        section_records_by_class = {}
        try:
            parsed = parse_course_info_page(
                pages.course_info_html,
                source_url=pages.course_info_url,
            )
            parsed_path = _save_json_fixture(
                fixture_root,
                term=result.term,
                subject=result.subject,
                kind=f"detail-{detail_key}-course-info",
                payload=parsed.model_dump_json(indent=2),
            )
            parsed_options = len(parsed.options)
            if not parsed.options:
                raise PeopleSoftSessionError(
                    "Course Information loaded for a scheduled course but exposed zero "
                    f"class options ({hit.course_code}). This usually means the current "
                    "PeopleSoft search/detail component state is stale."
                )
            if pages.course_info_expansion_count:
                logger.info(
                    "course_option_grid_expanded",
                    course=hit.course_code,
                    expansion_posts=pages.course_info_expansion_count,
                    displayed_options=parsed_options,
                    options_start=parsed.options_start,
                    options_end=parsed.options_end,
                    options_total=parsed.options_total,
                    complete=parsed.options_complete,
                )
            if not parsed.options_complete:
                detail_warning(
                    "course_option_grid_truncated",
                    course=hit.course_code,
                    displayed_options=parsed_options,
                    options_start=parsed.options_start,
                    options_end=parsed.options_end,
                    options_total=parsed.options_total,
                    message=(
                        "PeopleSoft returned only part of the enrollment-option grid; "
                        "additional option-page retrieval is required before this course "
                        "can be considered complete."
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - keep the raw fixture for diagnosis
            if is_recoverable_people_soft_state_error(exc):
                raise
            detail_warning(
                "course_info_parse_failed",
                course=hit.course_code,
                course_info_path=str(course_info_path),
                error=str(exc),
            )

        if parsed is not None and not parsed.options_complete:
            class_actions = ()
            logger.info(
                "class_detail_skipped_incomplete_option_grid",
                course=hit.course_code,
                displayed_options=parsed_options,
                options_total=parsed.options_total,
            )
        else:
            try:
                class_actions = find_class_number_actions(pages.course_info_html)
            except Exception as exc:  # noqa: BLE001 - retain higher-level fixtures
                if is_recoverable_people_soft_state_error(exc):
                    raise
                class_actions = ()
                detail_warning(
                    "class_action_parse_failed",
                    course=hit.course_code,
                    course_info_path=str(course_info_path),
                    error=str(exc),
                )

        for class_action in class_actions:
            try:
                class_page = client.fetch_class_number_page(
                    pages.course_info_url,
                    class_action.class_number,
                    referer=pages.grouplet_url,
                )
                class_kind = (
                    f"detail-{detail_key}-class-{_slug(class_action.class_number)}"
                )
                class_path = _save_fixture(
                    fixture_root,
                    term=result.term,
                    subject=result.subject,
                    kind=class_kind,
                    html=class_page.html,
                )
                class_detail_paths.append(str(class_path))
                partial_records = []
                class_parsed_path: Path | None = None
                try:
                    class_record = parse_class_information_page(class_page.html)
                    if class_record.class_number != class_action.class_number:
                        detail_warning(
                            "class_detail_record_mismatch",
                            course=hit.course_code,
                            expected_class_number=class_action.class_number,
                            parsed_class_number=class_record.class_number,
                            path=str(class_path),
                        )
                    else:
                        partial_records.append(class_record)
                        class_parsed_path = _save_json_fixture(
                            fixture_root,
                            term=result.term,
                            subject=result.subject,
                            kind=f"{class_kind}-enrollment",
                            payload=class_record.model_dump_json(indent=2),
                        )
                except Exception as exc:  # noqa: BLE001 - raw modal remains useful
                    detail_warning(
                        "class_detail_parse_failed",
                        course=hit.course_code,
                        class_number=class_action.class_number,
                        path=str(class_path),
                        error=str(exc),
                    )

                tab_paths: list[str] = []
                try:
                    tab_pages = client.fetch_class_detail_tab_pages_from_class_page(
                        class_page,
                        list(CLASS_DETAIL_TAB_PROBES),
                    )
                except Exception as exc:  # noqa: BLE001 - preserve enrollment/fallback data
                    if is_recoverable_people_soft_state_error(exc):
                        raise
                    tab_pages = ()
                    detail_warning(
                        "class_detail_tab_walk_failed",
                        course=hit.course_code,
                        class_number=class_action.class_number,
                        error=str(exc),
                    )

                for tab_page in tab_pages:
                    tab_kind = f"{class_kind}-{_slug(tab_page.tab_label)}"
                    tab_path = _save_fixture(
                        fixture_root,
                        term=result.term,
                        subject=result.subject,
                        kind=tab_kind,
                        html=tab_page.html,
                    )
                    tab_paths.append(str(tab_path))
                    try:
                        tab_record = parse_class_information_page(tab_page.html)
                        if tab_record.class_number != class_action.class_number:
                            detail_warning(
                                "class_detail_tab_record_mismatch",
                                course=hit.course_code,
                                expected_class_number=class_action.class_number,
                                parsed_class_number=tab_record.class_number,
                                tab_value=tab_page.tab_value,
                                path=str(tab_path),
                            )
                        else:
                            partial_records.append(tab_record)
                            _save_json_fixture(
                                fixture_root,
                                term=result.term,
                                subject=result.subject,
                                kind=tab_kind,
                                payload=tab_record.model_dump_json(indent=2),
                            )
                    except Exception as exc:  # noqa: BLE001 - raw tab fixture remains useful
                        detail_warning(
                            "class_detail_tab_parse_failed",
                            course=hit.course_code,
                            class_number=class_action.class_number,
                            tab_value=tab_page.tab_value,
                            path=str(tab_path),
                            error=str(exc),
                        )
                    logger.info(
                        "class_detail_tab_fixture_saved",
                        course=hit.course_code,
                        class_number=class_action.class_number,
                        tab=tab_page.tab_label,
                        tab_value=tab_page.tab_value,
                        action_id=tab_page.action_id,
                        path=str(tab_path),
                        navigation="same_class_modal_state",
                    )

                complete_path: Path | None = None
                section_path: Path | None = None
                if partial_records:
                    option = (
                        next(
                            (
                                item
                                for item in parsed.options
                                if item.class_number == class_action.class_number
                            ),
                            None,
                        )
                        if parsed is not None
                        else None
                    )
                    if parsed is not None and option is None:
                        option = CourseClassOption(
                            status=SeatStatus.UNKNOWN,
                            component=class_action.component,
                            class_number=class_action.class_number,
                            source_row_index=class_action.source_row_index,
                        )
                    try:
                        combined = merge_class_information_records(
                            *partial_records,
                            section_number=(
                                option.section_number if option is not None else None
                            ),
                        )
                        complete_path = _save_json_fixture(
                            fixture_root,
                            term=result.term,
                            subject=result.subject,
                            kind=f"{class_kind}-complete",
                            payload=combined.model_dump_json(indent=2),
                        )
                        if parsed is not None and option is not None:
                            section_record = assemble_sdsu_course_section(
                                parsed,
                                option,
                                combined,
                            )
                            section_records_by_class[class_action.class_number] = section_record
                            section_path = _save_json_fixture(
                                fixture_root,
                                term=result.term,
                                subject=result.subject,
                                kind=f"{class_kind}-section",
                                payload=section_record.model_dump_json(indent=2),
                            )
                    except Exception as exc:  # noqa: BLE001 - retain partial records
                        detail_warning(
                            "class_detail_merge_failed",
                            course=hit.course_code,
                            class_number=class_action.class_number,
                            error=str(exc),
                        )

                logger.info(
                    "class_detail_fixture_saved",
                    course=hit.course_code,
                    class_number=class_action.class_number,
                    action_id=class_page.action_id,
                    path=str(class_path),
                    parsed_path=(
                        str(class_parsed_path) if class_parsed_path is not None else None
                    ),
                    complete_path=str(complete_path) if complete_path is not None else None,
                    section_path=str(section_path) if section_path is not None else None,
                    tab_paths=tab_paths,
                    response_url=class_page.response_url,
                )
            except Exception as exc:  # noqa: BLE001 - isolate individual class clicks
                if is_recoverable_people_soft_state_error(exc):
                    raise
                detail_warning(
                    "class_detail_probe_failed",
                    course=hit.course_code,
                    class_number=class_action.class_number,
                    error=str(exc),
                )

        # Course Information is the canonical list of class options. Never silently
        # drop one just because its Class Information modal/tab state was stale.
        ordered_section_records = list(section_records_by_class.values())
        fallback_class_numbers: tuple[str, ...] = ()
        expected_classes: tuple[str, ...] = ()
        if parsed is not None and parsed.options_complete:
            expected_action_rows = tuple(
                (
                    action.class_number,
                    action.component,
                    action.source_row_index,
                )
                for action in class_actions
            )
            ordered_sections, fallback_class_numbers = ensure_all_course_option_sections(
                parsed,
                ordered_section_records,
                expected_classes=expected_action_rows,
            )
            ordered_section_records = list(ordered_sections)
            action_by_class = {action.class_number: action for action in class_actions}
            option_by_class = {}
            for item in parsed.options:
                option_by_class.setdefault(item.class_number, item)

            for class_number in fallback_class_numbers:
                fallback_section = next(
                    record
                    for record in ordered_section_records
                    if record.class_number == class_number
                )
                option = option_by_class.get(class_number)
                action = action_by_class.get(class_number)
                if option is None:
                    option = CourseClassOption(
                        status=SeatStatus.UNKNOWN,
                        component=action.component if action is not None else None,
                        class_number=class_number,
                        source_row_index=(
                            action.source_row_index if action is not None else 0
                        ),
                    )
                fallback_info = ClassInformationRecord(
                    class_number=option.class_number,
                    section_number=option.section_number,
                    component=(
                        action.component
                        if action is not None and action.component is not None
                        else option.component
                    ),
                    course_label=f"{parsed.course_code} {parsed.title}",
                    status=option.status,
                    raw_status=option.raw_status,
                    selected_tab="Course Information fallback",
                )
                fallback_kind = (
                    f"detail-{detail_key}-class-{_slug(option.class_number)}"
                )
                _save_json_fixture(
                    fixture_root,
                    term=result.term,
                    subject=result.subject,
                    kind=f"{fallback_kind}-complete",
                    payload=fallback_info.model_dump_json(indent=2),
                )
                _save_json_fixture(
                    fixture_root,
                    term=result.term,
                    subject=result.subject,
                    kind=f"{fallback_kind}-section",
                    payload=fallback_section.model_dump_json(indent=2),
                )
                detail_warning(
                    "section_fallback_from_course_option",
                    course=hit.course_code,
                    class_number=option.class_number,
                    reason=(
                        "Class Information tabs did not produce a complete matching "
                        "record; retained the class using Course Information data."
                    ),
                )

            if class_actions:
                expected_classes = tuple(action.class_number for action in class_actions)
            else:
                expected_classes = tuple(
                    dict.fromkeys(option.class_number for option in parsed.options)
                )
            actual_classes = tuple(record.class_number for record in ordered_section_records)
            duplicate_actual_classes = tuple(
                class_number
                for class_number in dict.fromkeys(actual_classes)
                if actual_classes.count(class_number) > 1
            )
            if duplicate_actual_classes:
                raise RuntimeError(
                    "Section aggregation produced duplicate physical class numbers: "
                    + ", ".join(duplicate_actual_classes)
                )
            missing_classes = tuple(
                class_number
                for class_number in expected_classes
                if class_number not in actual_classes
            )
            if missing_classes:
                raise RuntimeError(
                    "Section aggregation lost canonical physical class(es): "
                    + ", ".join(missing_classes)
                )
            logger.info(
                "section_membership_verified",
                course=hit.course_code,
                expected_classes=expected_classes,
                actual_classes=actual_classes,
                enrollment_options=parsed_options,
                option_grid_total=parsed.options_total,
                option_grid_complete=parsed.options_complete,
            )

        sections_path: Path | None = None
        if ordered_section_records:
            sections_path = _save_json_fixture(
                fixture_root,
                term=result.term,
                subject=result.subject,
                kind=f"detail-{detail_key}-sections",
                payload=json.dumps(
                    [record.model_dump(mode="json") for record in ordered_section_records],
                    indent=2,
                ),
            )

        logger.info(
            "detail_fixture_saved",
            course=hit.course_code,
            shell_path=str(shell_path),
            grouplet_path=str(grouplet_path),
            course_info_path=str(course_info_path),
            parsed_path=str(parsed_path) if parsed_path is not None else None,
            parsed_options=parsed_options,
            class_detail_paths=class_detail_paths,
            sections_path=str(sections_path) if sections_path is not None else None,
            parsed_sections=len(ordered_section_records),
            grouplet_url=pages.grouplet_url,
            course_info_url=pages.course_info_url,
        )
        course_is_complete = (
            parsed is not None
            and parsed.options_complete
            and bool(expected_classes)
            and expected_classes
            == tuple(record.class_number for record in ordered_section_records)
            and not fallback_class_numbers
            and warning_count == course_warning_start
        )
        if course_is_complete:
            complete += 1
        else:
            partial += 1

    return DetailScrapeStats(
        attempted=attempted,
        complete=complete,
        partial=partial,
        warnings=warning_count,
    )


def _course_fixture_directory(
    fixture_root: Path,
    *,
    term: str,
    subject: str,
) -> Path:
    return fixture_root / _slug(term) / _slug(subject)


def _clear_course_json_fixtures(
    fixture_root: Path,
    *,
    term: str,
    subject: str,
    detail_key: str,
) -> None:
    directory = _course_fixture_directory(
        fixture_root,
        term=term,
        subject=subject,
    )
    if not directory.exists():
        return
    for path in directory.glob(f"detail-{detail_key}*.json"):
        path.unlink(missing_ok=True)


def _read_course_detail_fixtures(
    fixture_root: Path,
    *,
    term: str,
    subject: str,
    detail_key: str,
) -> tuple[CourseInfoRecord | None, tuple[SdsuCourseSectionRecord, ...], tuple[str, ...]]:
    directory = _course_fixture_directory(
        fixture_root,
        term=term,
        subject=subject,
    )
    course_info_path = directory / f"detail-{detail_key}-course-info.json"
    sections_path = directory / f"detail-{detail_key}-sections.json"

    course_info: CourseInfoRecord | None = None
    if course_info_path.exists():
        course_info = CourseInfoRecord.model_validate_json(
            course_info_path.read_text(encoding="utf-8")
        )

    sections: tuple[SdsuCourseSectionRecord, ...] = ()
    if sections_path.exists():
        payload = json.loads(sections_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON list in {sections_path}.")
        sections = tuple(SdsuCourseSectionRecord.model_validate(item) for item in payload)

    fixture_paths = tuple(
        str(path)
        for path in sorted(directory.glob(f"detail-{detail_key}*"))
        if path.is_file()
    )
    return course_info, sections, fixture_paths


def _resume_has_pending_detail_targets(
    result: SubjectScrapeResult,
    *,
    limit: int,
    existing: tuple[CourseDetailOutput, ...],
) -> bool:
    """Return whether a resumed subject still needs any network detail work."""

    if limit <= 0:
        return False
    existing_by_key = {record.course_key: record for record in existing}
    return any(
        (prior := existing_by_key.get(course_detail_key(hit))) is None
        or prior.status is not DetailCourseStatus.COMPLETE
        for hit in detail_target_hits(result, limit=limit)
    )


def _save_detail_fixtures_resumable(
    client: SdsuPeopleSoftSession,
    result: SubjectScrapeResult,
    *,
    fixture_root: Path,
    limit: int,
    logger: EventLogger,
    existing: tuple[CourseDetailOutput, ...] = (),
    progress_callback: Callable[[CourseDetailOutput], None] | None = None,
) -> DetailScrapeOutcome:
    targets = detail_target_hits(result, limit=limit)
    existing_by_key = {record.course_key: record for record in existing}
    records: list[CourseDetailOutput] = []

    for hit in targets:
        key = course_detail_key(hit)
        prior = existing_by_key.get(key)
        if prior is not None and prior.status is DetailCourseStatus.COMPLETE:
            records.append(prior)
            logger.info(
                "detail_course_resumed_complete",
                subject=result.subject,
                course=hit.course_code,
                course_key=key,
                attempts=prior.attempts,
            )
            continue

        attempt_started = utc_now()
        attempts = (prior.attempts if prior is not None else 0) + 1
        in_progress = CourseDetailOutput(
            course_key=key,
            course=hit,
            status=DetailCourseStatus.IN_PROGRESS,
            started_at=prior.started_at if prior is not None else attempt_started,
            updated_at=attempt_started,
            attempts=attempts,
        )
        if progress_callback is not None:
            progress_callback(in_progress)

        detail_key = _slug(hit.class_number or hit.course_code)
        try:
            recovery_attempts = max(
                int(
                    getattr(
                        getattr(client, "config", None),
                        "in_run_state_recovery_attempts",
                        0,
                    )
                ),
                0,
            )
            single_course_result = result.model_copy(update={"courses": (hit,)})
            stats: DetailScrapeStats | None = None
            capture_logger: _WarningCaptureLogger | None = None
            for recovery_index in range(recovery_attempts + 1):
                _clear_course_json_fixtures(
                    fixture_root,
                    term=result.term,
                    subject=result.subject,
                    detail_key=detail_key,
                )
                capture_logger = _WarningCaptureLogger(logger)
                try:
                    stats = _save_detail_fixtures(
                        client,
                        single_course_result,
                        fixture_root=fixture_root,
                        limit=1,
                        logger=capture_logger,
                    )
                    record_success = getattr(client, "record_state_success", None)
                    if callable(record_success):
                        record_success()
                    break
                except PeopleSoftCircuitBreakerOpen:
                    raise
                except Exception as state_error:  # noqa: BLE001 - classify state shape
                    if not is_recoverable_people_soft_state_error(state_error):
                        raise
                    if recovery_index >= recovery_attempts:
                        record_failure = getattr(client, "record_state_failure", None)
                        if callable(record_failure):
                            record_failure(
                                context=(
                                    f"deep detail {result.subject} "
                                    f"{hit.course_code}"
                                ),
                                error=state_error,
                            )
                        raise
                    logger.warning(
                        "detail_session_state_recovery",
                        subject=result.subject,
                        course=hit.course_code,
                        course_key=key,
                        recovery_attempt=recovery_index + 1,
                        error=str(state_error),
                    )
                    rehydrate = getattr(client, "rehydrate_subject_context", None)
                    if not callable(rehydrate):
                        raise
                    recovery_pages = rehydrate(
                        term=result.term,
                        term_code=result.term_code,
                        subject=result.subject,
                        mark_success=False,
                    )
                    logger.info(
                        "detail_session_rehydrated",
                        subject=result.subject,
                        course=hit.course_code,
                        exact_facet=recovery_pages.exact_facet_applied,
                        search_url=recovery_pages.search_url,
                    )
            else:
                raise AssertionError("detail state recovery loop exited unexpectedly")

            if stats is None or capture_logger is None:
                raise AssertionError("detail state recovery completed without statistics")

            course_info, sections, fixture_paths = _read_course_detail_fixtures(
                fixture_root,
                term=result.term,
                subject=result.subject,
                detail_key=detail_key,
            )
            status = (
                DetailCourseStatus.COMPLETE
                if stats.complete == 1 and stats.partial == 0
                else DetailCourseStatus.PARTIAL
            )
            warning_events = tuple(capture_logger.warning_events)
            if len(warning_events) != stats.warnings:
                warning_events = tuple(
                    (*warning_events, *(
                        "detail_warning"
                        for _ in range(stats.warnings - len(warning_events))
                    ))
                )
            completed_at = utc_now()
            record = CourseDetailOutput(
                course_key=key,
                course=hit,
                status=status,
                started_at=in_progress.started_at,
                updated_at=completed_at,
                completed_at=completed_at,
                attempts=attempts,
                warning_events=warning_events,
                error=(
                    None
                    if status is DetailCourseStatus.COMPLETE
                    else "Detail scrape completed with incomplete or fallback data."
                ),
                course_info=course_info,
                sections=sections,
                fixture_paths=fixture_paths,
            )
        except PeopleSoftCircuitBreakerOpen:
            raise
        except Exception as exc:  # noqa: BLE001 - record and resume the course later
            completed_at = utc_now()
            record = CourseDetailOutput(
                course_key=key,
                course=hit,
                status=DetailCourseStatus.FAILED,
                started_at=in_progress.started_at,
                updated_at=completed_at,
                completed_at=completed_at,
                attempts=attempts,
                warning_events=("detail_course_failed",),
                error=str(exc),
            )
            logger.error(
                "detail_course_failed",
                subject=result.subject,
                course=hit.course_code,
                course_key=key,
                error=str(exc),
            )

        records.append(record)
        if progress_callback is not None:
            progress_callback(record)

    stats = DetailScrapeStats(
        attempted=len(records),
        complete=sum(
            record.status is DetailCourseStatus.COMPLETE for record in records
        ),
        partial=sum(
            record.status is not DetailCourseStatus.COMPLETE for record in records
        ),
        warnings=sum(len(record.warning_events) for record in records),
    )
    return DetailScrapeOutcome(stats=stats, courses=tuple(records))


def _progress_summary(
    requested_subjects: tuple[str, ...],
    subject_outputs: dict[str, SubjectDetailOutput],
    errors_by_subject: dict[str, SubjectScrapeError],
) -> ProgressSummary:
    ordered_outputs = tuple(
        subject_outputs[subject]
        for subject in requested_subjects
        if subject in subject_outputs
    )
    ordered_errors = tuple(
        errors_by_subject[subject]
        for subject in requested_subjects
        if subject in errors_by_subject
    )
    return ProgressSummary(
        completed_subjects=tuple(
            output.subject for output in ordered_outputs if output.complete
        ),
        results=tuple(output.search_result for output in ordered_outputs),
        errors=ordered_errors,
        detail_courses_attempted=sum(
            output.detail_courses_attempted for output in ordered_outputs
        ),
        detail_courses_complete=sum(
            output.detail_courses_complete for output in ordered_outputs
        ),
        detail_courses_partial=sum(
            output.detail_courses_partial for output in ordered_outputs
        ),
        detail_warnings=sum(output.detail_warnings for output in ordered_outputs),
        incomplete_subjects=len(requested_subjects)
        - sum(output.complete for output in ordered_outputs),
    )


def _write_progress_run(
    output_path: Path,
    *,
    checkpoint: ScrapeCheckpoint,
    subject_outputs: dict[str, SubjectDetailOutput],
    errors_by_subject: dict[str, SubjectScrapeError],
    completed_at: str | None,
) -> ProgressSummary:
    summary = _progress_summary(
        checkpoint.requested_subjects,
        subject_outputs,
        errors_by_subject,
    )
    _write_run(
        output_path,
        started_at=checkpoint.started_at,
        completed_at=completed_at,
        term=checkpoint.term,
        term_code=checkpoint.term_code,
        requested_subjects=checkpoint.requested_subjects,
        completed_subjects=list(summary.completed_subjects),
        results=list(summary.results),
        errors=list(summary.errors),
        detail_courses_attempted=summary.detail_courses_attempted,
        detail_courses_complete=summary.detail_courses_complete,
        detail_courses_partial=summary.detail_courses_partial,
        detail_warnings=summary.detail_warnings,
    )
    return summary


def _new_checkpoint(
    *,
    term: str,
    term_code: str,
    requested_subjects: tuple[str, ...],
    detail_limit: int,
    output_path: Path,
    subject_output_dir: Path,
) -> ScrapeCheckpoint:
    started_at = utc_now()
    states = tuple(
        SubjectCheckpointState(
            subject=subject,
            status=SubjectCheckpointStatus.PENDING,
            output_path=str(_subject_output_path(subject_output_dir, subject)),
            updated_at=started_at,
        )
        for subject in requested_subjects
    )
    return ScrapeCheckpoint(
        run_id=str(uuid4()),
        started_at=started_at,
        updated_at=started_at,
        term=term,
        term_code=term_code,
        requested_subjects=requested_subjects,
        detail_limit=detail_limit,
        output_path=str(output_path),
        subject_output_dir=str(subject_output_dir),
        subjects=states,
    )


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _validate_resume_checkpoint(
    checkpoint: ScrapeCheckpoint,
    *,
    term: str,
    term_code: str,
    requested_subjects: tuple[str, ...],
    detail_limit: int,
    output_path: Path,
    subject_output_dir: Path,
) -> None:
    if checkpoint.term != term:
        raise ValueError(
            f"Checkpoint term is {checkpoint.term!r}, not requested term {term!r}."
        )
    if checkpoint.term_code != term_code:
        raise ValueError(
            "Checkpoint term code is "
            f"{checkpoint.term_code!r}, not resolved code {term_code!r}."
        )
    if checkpoint.requested_subjects != requested_subjects:
        raise ValueError(
            "The selected subject list does not match the checkpoint. Resume with the "
            "same --subjects/--all-subjects selection, or omit both to reuse it."
        )
    if checkpoint.detail_limit != detail_limit:
        raise ValueError(
            f"Checkpoint detail limit is {checkpoint.detail_limit}, not {detail_limit}. "
            "Omit --detail-limit while resuming to reuse the saved value."
        )
    if not _same_path(Path(checkpoint.output_path), output_path):
        raise ValueError(
            f"Checkpoint aggregate output is {checkpoint.output_path!r}, not {str(output_path)!r}."
        )
    if not _same_path(Path(checkpoint.subject_output_dir), subject_output_dir):
        raise ValueError(
            "Checkpoint per-subject output directory is "
            f"{checkpoint.subject_output_dir!r}, not {str(subject_output_dir)!r}."
        )


def _load_subject_outputs_from_checkpoint(
    checkpoint: ScrapeCheckpoint,
    *,
    logger: EventLogger,
) -> tuple[dict[str, SubjectDetailOutput], ScrapeCheckpoint]:
    outputs: dict[str, SubjectDetailOutput] = {}
    current = checkpoint
    for state in checkpoint.subjects:
        path = Path(state.output_path)
        if not path.exists():
            if state.status is not SubjectCheckpointStatus.PENDING:
                logger.warning(
                    "resume_subject_output_missing",
                    subject=state.subject,
                    path=str(path),
                    previous_status=state.status.value,
                )
                pending = state.model_copy(
                    update={
                        "status": SubjectCheckpointStatus.PENDING,
                        "updated_at": utc_now(),
                        "completed_course_keys": (),
                        "partial_course_keys": (),
                        "error": None,
                    }
                )
                current = replace_checkpoint_state(current, pending)
            continue
        try:
            output = read_model(path, SubjectDetailOutput)
            if output.run_id != checkpoint.run_id:
                raise ValueError(
                    f"run_id {output.run_id!r} does not match checkpoint "
                    f"{checkpoint.run_id!r}"
                )
            if output.term != checkpoint.term or output.term_code != checkpoint.term_code:
                raise ValueError("term metadata does not match the checkpoint")
            if output.subject != state.subject:
                raise ValueError(
                    f"subject {output.subject!r} does not match state {state.subject!r}"
                )
            if output.detail_limit != checkpoint.detail_limit:
                raise ValueError("detail limit does not match the checkpoint")
        except Exception as exc:  # noqa: BLE001 - stale/corrupt output should be retried
            logger.warning(
                "resume_subject_output_invalid",
                subject=state.subject,
                path=str(path),
                error=str(exc),
            )
            pending = state.model_copy(
                update={
                    "status": SubjectCheckpointStatus.PENDING,
                    "updated_at": utc_now(),
                    "completed_course_keys": (),
                    "partial_course_keys": (),
                    "error": None,
                }
            )
            current = replace_checkpoint_state(current, pending)
            continue
        refreshed_output = refresh_subject_output(output, error=output.error)
        if refreshed_output != output:
            logger.info(
                "resume_subject_output_recalculated",
                subject=state.subject,
                previous_complete=output.complete,
                restored_complete=refreshed_output.complete,
                detail_courses_targeted=refreshed_output.detail_courses_targeted,
                detail_courses_complete=refreshed_output.detail_courses_complete,
                path=str(path),
            )
            output = refreshed_output
            write_model(path, output)

        requires_search_refresh = subject_output_requires_search_refresh(output)
        outputs[state.subject] = output
        reconciled = checkpoint_state_from_subject_output(
            output,
            output_path=path,
            force_status=(
                SubjectCheckpointStatus.PENDING
                if requires_search_refresh
                else None
            ),
        )
        if requires_search_refresh:
            reconciled = reconciled.model_copy(update={"error": None})
            logger.warning(
                "resume_deep_search_refresh_required",
                subject=state.subject,
                courses=len(output.search_result.courses),
                persisted_target_keys=len(output.detail_target_course_keys),
                detail_courses_targeted=output.detail_courses_targeted,
                reason=(
                    "The persisted deep-run target list is incomplete for the saved "
                    "course inventory; the subject search will be rebuilt."
                ),
            )
        if reconciled != state:
            logger.info(
                "resume_checkpoint_reconciled",
                subject=state.subject,
                previous_status=state.status.value,
                restored_status=reconciled.status.value,
                path=str(path),
            )
            current = replace_checkpoint_state(current, reconciled)
    return outputs, current


def run(args: argparse.Namespace) -> int:
    logger = configure_logging(json_output=bool(args.json_logs))
    config = SdsuHttpConfig(
        timeout_seconds=float(args.timeout),
        delay_seconds=max(float(args.delay), 0.0),
        jitter_seconds=max(float(args.jitter), 0.0),
        max_retries=max(int(args.max_retries), 0),
        post_recovery_cooldown_seconds=max(float(args.post_recovery_cooldown), 0.0),
        stateful_post_recovery_attempts=max(
            int(args.stateful_post_recovery_attempts),
            0,
        ),
        in_run_state_recovery_attempts=max(
            int(args.in_run_state_recovery_attempts),
            0,
        ),
        state_circuit_breaker_threshold=max(
            int(args.state_circuit_breaker_threshold),
            0,
        ),
    )
    logger.info(
        "http_policy",
        delay_seconds=config.delay_seconds,
        jitter_seconds=config.jitter_seconds,
        max_get_retries=config.max_retries,
        get_backoff_seconds=config.get_backoff_seconds,
        post_recovery_cooldown_seconds=config.post_recovery_cooldown_seconds,
        stateful_post_recovery_attempts=config.stateful_post_recovery_attempts,
        in_run_state_recovery_attempts=config.in_run_state_recovery_attempts,
        state_circuit_breaker_threshold=config.state_circuit_breaker_threshold,
    )

    with SdsuPeopleSoftSession(config) as client:
        if args.list_terms:
            try:
                discovered = client.discovered_term_codes()
            except Exception as exc:  # noqa: BLE001 - concise CLI diagnostic
                logger.error("term_discovery_failed", error=str(exc))
                return 1
            print(json.dumps(discovered, indent=2, sort_keys=True))
            return 0

        if args.term is None or not str(args.term).strip():
            logger.error(
                "invalid_arguments",
                error="--term is required for scraping or --list-subjects.",
            )
            return 2
        term = str(args.term).strip()
        try:
            term_code = client.resolve_term_code(term, args.term_code)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            logger.error("term_resolution_failed", term=term, error=str(exc))
            return 1

        if args.list_subjects:
            try:
                discovered_subjects = client.discover_subjects(term_code=term_code)
            except Exception as exc:  # noqa: BLE001 - concise CLI diagnostic
                logger.error("subject_inventory_discovery_failed", term=term, error=str(exc))
                return 1
            document = _subject_inventory_document(
                term=term,
                term_code=term_code,
                discovered_subjects=discovered_subjects,
            )
            inventory_path = args.subject_inventory_output or _default_subject_inventory_output(term)
            _write_subject_inventory(inventory_path, document)
            print(json.dumps(document, indent=2, sort_keys=True))
            return 0

        checkpoint_path = args.checkpoint or _default_checkpoint(term)
        resume_checkpoint: ScrapeCheckpoint | None = None

        if args.resume and args.resume_from is not None:
            logger.error(
                "invalid_arguments",
                error="--resume and --resume-from cannot be used together.",
            )
            return 2

        if args.resume:
            if not checkpoint_path.exists():
                logger.error(
                    "checkpoint_not_found",
                    checkpoint=str(checkpoint_path),
                    error="No checkpoint exists at the requested path.",
                )
                return 2
            try:
                resume_checkpoint = read_model(checkpoint_path, ScrapeCheckpoint)
            except Exception as exc:  # noqa: BLE001 - malformed checkpoint boundary
                logger.error(
                    "checkpoint_load_failed",
                    checkpoint=str(checkpoint_path),
                    error=str(exc),
                )
                return 2

        try:
            requested_course_keys = _load_course_keys_file(args.course_keys_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "course_keys_file_invalid",
                path=str(args.course_keys_file),
                error=str(exc),
            )
            return 2
        discovered_subjects: tuple[str, ...] = ()
        if resume_checkpoint is None and args.all_subjects and not args.known_subjects_only:
            try:
                discovered_subjects = client.discover_subjects(term_code=term_code)
            except Exception as exc:  # noqa: BLE001 - preflight must fail closed
                logger.error("subject_inventory_discovery_failed", term=term, error=str(exc))
                return 1
            document = _subject_inventory_document(
                term=term,
                term_code=term_code,
                discovered_subjects=discovered_subjects,
            )
            inventory_path = args.subject_inventory_output or _default_subject_inventory_output(term)
            _write_subject_inventory(inventory_path, document)
            logger.info(
                "subject_inventory_discovered",
                term=term,
                discovered=len(discovered_subjects),
                effective=len(document["effective_subjects"]),
                new_subjects=document["new_subjects"],
                manifest=str(inventory_path),
            )

        try:
            if resume_checkpoint is not None and not args.subjects:
                requested_subjects = resume_checkpoint.requested_subjects
            else:
                requested_subjects = _parse_subjects(
                    list(args.subjects),
                    all_subjects=bool(args.all_subjects),
                    discovered_subjects=discovered_subjects,
                )
                if resume_checkpoint is None:
                    requested_subjects = _resume_slice(
                        requested_subjects,
                        args.resume_from,
                    )

            if requested_course_keys is not None:
                detail_limit = len(requested_course_keys)
            elif resume_checkpoint is not None and args.detail_limit is None:
                detail_limit = resume_checkpoint.detail_limit
            else:
                detail_limit = max(int(args.detail_limit or 0), 0)

            output_path = (
                args.output
                or (
                    Path(resume_checkpoint.output_path)
                    if resume_checkpoint is not None
                    else _default_output(term)
                )
            )
            subject_output_dir = (
                args.subject_output_dir
                or (
                    Path(resume_checkpoint.subject_output_dir)
                    if resume_checkpoint is not None
                    else _default_subject_output_dir(term)
                )
            )
        except ValueError as exc:
            logger.error("invalid_arguments", error=str(exc))
            return 2

        subject_output_dir.mkdir(parents=True, exist_ok=True)
        if resume_checkpoint is None:
            checkpoint = _new_checkpoint(
                term=term,
                term_code=term_code,
                requested_subjects=requested_subjects,
                detail_limit=detail_limit,
                output_path=output_path,
                subject_output_dir=subject_output_dir,
            )
            subject_outputs: dict[str, SubjectDetailOutput] = {}
            errors_by_subject: dict[str, SubjectScrapeError] = {}
            write_model(checkpoint_path, checkpoint)
            logger.info(
                "checkpoint_created",
                checkpoint=str(checkpoint_path),
                run_id=checkpoint.run_id,
                subjects=len(requested_subjects),
                detail_limit=detail_limit,
                subject_output_dir=str(subject_output_dir),
            )
        else:
            try:
                _validate_resume_checkpoint(
                    resume_checkpoint,
                    term=term,
                    term_code=term_code,
                    requested_subjects=requested_subjects,
                    detail_limit=detail_limit,
                    output_path=output_path,
                    subject_output_dir=subject_output_dir,
                )
            except ValueError as exc:
                logger.error(
                    "checkpoint_mismatch",
                    checkpoint=str(checkpoint_path),
                    error=str(exc),
                )
                return 2
            checkpoint = resume_checkpoint
            subject_outputs, checkpoint = _load_subject_outputs_from_checkpoint(
                checkpoint,
                logger=logger,
            )
            errors_by_subject = {
                state.subject: state.error
                for state in checkpoint.subjects
                if state.error is not None
            }
            write_model(checkpoint_path, checkpoint)
            logger.info(
                "checkpoint_resumed",
                checkpoint=str(checkpoint_path),
                run_id=checkpoint.run_id,
                subjects=len(requested_subjects),
                subjects_with_output=len(subject_outputs),
                detail_limit=detail_limit,
                subject_output_dir=str(subject_output_dir),
            )

        _write_progress_run(
            output_path,
            checkpoint=checkpoint,
            subject_outputs=subject_outputs,
            errors_by_subject=errors_by_subject,
            completed_at=None,
        )

        for position, subject in enumerate(requested_subjects, start=1):
            subject_path = _subject_output_path(subject_output_dir, subject)
            existing_output = subject_outputs.get(subject)
            requires_search_refresh = (
                existing_output is not None
                and subject_output_requires_search_refresh(existing_output)
            )
            if (
                args.resume
                and existing_output is not None
                and existing_output.complete
                and not requires_search_refresh
            ):
                logger.info(
                    "subject_resumed_complete",
                    subject=subject,
                    position=position,
                    total=len(requested_subjects),
                    courses=len(existing_output.search_result.courses),
                    detail_courses_complete=existing_output.detail_courses_complete,
                    output=str(subject_path),
                )
                continue

            logger.info(
                "subject_started",
                subject=subject,
                position=position,
                total=len(requested_subjects),
                term=term,
                term_code=term_code,
                resumed=bool(args.resume and existing_output is not None),
            )

            if existing_output is not None:
                in_progress_state = checkpoint_state_from_subject_output(
                    existing_output,
                    output_path=subject_path,
                    force_status=SubjectCheckpointStatus.IN_PROGRESS,
                )
            else:
                in_progress_state = SubjectCheckpointState(
                    subject=subject,
                    status=SubjectCheckpointStatus.IN_PROGRESS,
                    output_path=str(subject_path),
                    updated_at=utc_now(),
                )
            checkpoint = replace_checkpoint_state(checkpoint, in_progress_state)
            write_model(checkpoint_path, checkpoint)

            current_output = existing_output
            pages = None
            reused_persisted_search = False
            try:
                if (
                    args.resume
                    and existing_output is not None
                    and existing_output.search_result.complete
                    and not requires_search_refresh
                ):
                    result = existing_output.search_result
                    reused_persisted_search = True
                    logger.info(
                        "subject_search_resumed",
                        subject=subject,
                        courses=len(result.courses),
                        fetched_at=result.fetched_at,
                        output=str(subject_path),
                    )
                else:

                    def observe_page(kind: str, html: str) -> None:
                        if not args.save_fixtures:
                            return
                        path = _save_fixture(
                            args.fixtures_dir,
                            term=term,
                            subject=subject,
                            kind=kind,
                            html=html,
                        )
                        logger.info(
                            "fixture_saved",
                            subject=subject,
                            kind=kind,
                            path=str(path),
                        )

                    pages, result = client.scrape_subject(
                        term=term,
                        term_code=term_code,
                        subject=subject,
                        page_observer=observe_page,
                        require_detail_urls=detail_limit > 0,
                    )
                    if pages.partition_pages:
                        for leaf_number, partition in enumerate(
                            pages.partition_pages,
                            start=1,
                        ):
                            path_label = " / ".join(
                                f"{group}={label}" for group, label in partition.filters
                            ) or "unpartitioned"
                            logger.info(
                                "subject_partition_leaf",
                                subject=subject,
                                leaf=leaf_number,
                                filters=path_label,
                                rows=partition.result_count,
                                capped=partition.capped,
                            )
                            if args.save_fixtures:
                                filter_slug = "-".join(
                                    f"{_slug(group)}-{_slug(label)}"
                                    for group, label in partition.filters
                                ) or "unpartitioned"
                                partition_path = _save_fixture(
                                    args.fixtures_dir,
                                    term=term,
                                    subject=subject,
                                    kind=(
                                        f"partition-{leaf_number:02d}-{filter_slug}"
                                    ),
                                    html=partition.html,
                                )
                                logger.info(
                                    "fixture_saved",
                                    subject=subject,
                                    kind="partition",
                                    path=str(partition_path),
                                )
                        logger.info(
                            "subject_partition_completed",
                            subject=subject,
                            leaves=result.partition_leaf_count,
                            merged_courses=len(result.courses),
                            partition_facets=result.partition_facets,
                            unresolved_partitions=result.unresolved_partition_count,
                            complete=result.complete,
                        )
                result = _filter_result_to_course_keys(
                    result,
                    requested_course_keys,
                )

                pending_detail_work = _resume_has_pending_detail_targets(
                    result,
                    limit=detail_limit,
                    existing=(
                        existing_output.course_details
                        if existing_output is not None
                        else ()
                    ),
                )
                if pending_detail_work and result.partitioned:
                    result = client.prepare_partitioned_detail_result(result)
                    logger.info(
                        "partition_detail_context_rehydrated",
                        subject=subject,
                        courses=len(result.courses),
                        reason=(
                            "Partition traversal can leave PeopleSoft on an empty or "
                            "narrow facet leaf; a fresh exact-subject context and "
                            "canonical detail URLs were prepared before deep scraping."
                        ),
                    )
                elif (
                    pending_detail_work
                    and reused_persisted_search
                ):
                    resume_pages = client.rehydrate_subject_context(
                        term=term,
                        term_code=term_code,
                        subject=subject,
                    )
                    logger.info(
                        "resume_session_rehydrated",
                        subject=subject,
                        exact_facet=resume_pages.exact_facet_applied,
                        search_url=resume_pages.search_url,
                        reason=(
                            "Fresh PeopleSoft session/search state is required before "
                            "retrying unfinished course details."
                        ),
                    )

                current_output = build_subject_output(
                    run_id=checkpoint.run_id,
                    started_at=(
                        existing_output.started_at
                        if existing_output is not None
                        else utc_now()
                    ),
                    detail_limit=detail_limit,
                    result=result,
                    existing=existing_output,
                )
                subject_outputs[subject] = current_output
                errors_by_subject.pop(subject, None)
                write_model(subject_path, current_output)
                checkpoint = replace_checkpoint_state(
                    checkpoint,
                    checkpoint_state_from_subject_output(
                        current_output,
                        output_path=subject_path,
                        force_status=SubjectCheckpointStatus.IN_PROGRESS,
                    ),
                )
                write_model(checkpoint_path, checkpoint)

                def save_course_progress(record: CourseDetailOutput) -> None:
                    nonlocal checkpoint, current_output
                    if current_output is None:
                        raise AssertionError("subject output was not initialized")
                    current_output = replace_course_detail(current_output, record)
                    subject_outputs[subject] = current_output
                    write_model(subject_path, current_output)
                    checkpoint = replace_checkpoint_state(
                        checkpoint,
                        checkpoint_state_from_subject_output(
                            current_output,
                            output_path=subject_path,
                            force_status=SubjectCheckpointStatus.IN_PROGRESS,
                        ),
                    )
                    write_model(checkpoint_path, checkpoint)
                    logger.info(
                        "detail_checkpoint_saved",
                        subject=subject,
                        course=record.course.course_code,
                        course_key=record.course_key,
                        status=record.status.value,
                        attempts=record.attempts,
                        subject_output=str(subject_path),
                        checkpoint=str(checkpoint_path),
                    )

                outcome = _save_detail_fixtures_resumable(
                    client,
                    result,
                    fixture_root=args.fixtures_dir,
                    limit=detail_limit,
                    logger=logger,
                    existing=current_output.course_details,
                    progress_callback=save_course_progress,
                )
                current_output = refresh_subject_output(
                    current_output,
                    course_details=outcome.courses,
                )
                subject_outputs[subject] = current_output
                write_model(subject_path, current_output)
                checkpoint = replace_checkpoint_state(
                    checkpoint,
                    checkpoint_state_from_subject_output(
                        current_output,
                        output_path=subject_path,
                    ),
                )
                write_model(checkpoint_path, checkpoint)

                if (
                    result.complete
                    and not result.courses
                    and result.initial_result_count == 0
                    and result.filtered_result_count == 0
                    and (
                        pages is None
                        or has_no_results_message(pages.initial_html)
                    )
                ):
                    logger.info(
                        "subject_no_offerings",
                        subject=subject,
                        term=term,
                        term_code=term_code,
                        message=(
                            "PeopleSoft returned an explicit no-results page; treating "
                            "this valid subject as having zero offerings for the term."
                        ),
                    )

                logger.info(
                    "subject_output_saved",
                    subject=subject,
                    path=str(subject_path),
                    complete=current_output.complete,
                    detail_courses_targeted=current_output.detail_courses_targeted,
                    detail_courses_complete=current_output.detail_courses_complete,
                    detail_courses_partial=current_output.detail_courses_partial,
                )
                logger.info(
                    "subject_completed",
                    subject=subject,
                    courses=len(result.courses),
                    initial_rows=result.initial_result_count,
                    filtered_rows=result.filtered_result_count,
                    exact_facet=result.exact_facet_applied,
                    complete=current_output.complete,
                    search_complete=result.complete,
                    partitioned=result.partitioned,
                    partition_leaves=result.partition_leaf_count,
                    partition_facets=result.partition_facets,
                    unresolved_partitions=result.unresolved_partition_count,
                    detail_courses_attempted=current_output.detail_courses_attempted,
                    detail_courses_complete=current_output.detail_courses_complete,
                    detail_courses_partial=current_output.detail_courses_partial,
                    detail_warnings=current_output.detail_warnings,
                )
                if not result.complete:
                    logger.warning(
                        "subject_still_capped",
                        subject=subject,
                        message=(
                            "Automatic result-cap partitioning exhausted the known facet "
                            "dimensions while at least one leaf still reports PeopleSoft's "
                            "institutional cap. This subject remains incomplete."
                        ),
                    )
            except Exception as exc:  # noqa: BLE001 - preserve resumable progress
                error = SubjectScrapeError(
                    term=term,
                    subject=subject,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                errors_by_subject[subject] = error
                if current_output is not None:
                    current_output = refresh_subject_output(
                        current_output,
                        error=error,
                    )
                    subject_outputs[subject] = current_output
                    write_model(subject_path, current_output)
                    failed_state = checkpoint_state_from_subject_output(
                        current_output,
                        output_path=subject_path,
                        force_status=SubjectCheckpointStatus.FAILED,
                    )
                else:
                    failed_state = failed_checkpoint_state(
                        subject=subject,
                        output_path=subject_path,
                        error=error,
                    )
                checkpoint = replace_checkpoint_state(checkpoint, failed_state)
                write_model(checkpoint_path, checkpoint)
                logger.error(
                    "subject_failed",
                    subject=subject,
                    error_type=error.error_type,
                    error=error.message,
                    checkpoint=str(checkpoint_path),
                    subject_output=(
                        str(subject_path) if current_output is not None else None
                    ),
                )
                if isinstance(exc, PeopleSoftCircuitBreakerOpen):
                    logger.error(
                        "session_circuit_breaker_open",
                        subject=subject,
                        checkpoint=str(checkpoint_path),
                        error=str(exc),
                        message=(
                            "Repeated PeopleSoft state-shape failures opened the circuit "
                            "breaker. Remaining subjects were left pending for a later "
                            "--resume instead of being processed with an invalid session."
                        ),
                    )
                    return 1
                if args.fail_fast:
                    completed_at = utc_now()
                    _write_progress_run(
                        output_path,
                        checkpoint=checkpoint,
                        subject_outputs=subject_outputs,
                        errors_by_subject=errors_by_subject,
                        completed_at=completed_at,
                    )
                    return 1
            finally:
                _write_progress_run(
                    output_path,
                    checkpoint=checkpoint,
                    subject_outputs=subject_outputs,
                    errors_by_subject=errors_by_subject,
                    completed_at=None,
                )

        completed_at = utc_now()
        summary = _progress_summary(
            requested_subjects,
            subject_outputs,
            errors_by_subject,
        )
        checkpoint = checkpoint.model_copy(
            update={
                "updated_at": completed_at,
                "completed_at": (
                    completed_at
                    if summary.incomplete_subjects == 0 and not summary.errors
                    else None
                ),
            }
        )
        write_model(checkpoint_path, checkpoint)
        summary = _write_progress_run(
            output_path,
            checkpoint=checkpoint,
            subject_outputs=subject_outputs,
            errors_by_subject=errors_by_subject,
            completed_at=completed_at,
        )
        logger.info(
            "scrape_completed",
            output=str(output_path),
            checkpoint=str(checkpoint_path),
            subject_output_dir=str(subject_output_dir),
            subjects_completed=len(summary.completed_subjects),
            incomplete_subjects=summary.incomplete_subjects,
            errors=len(summary.errors),
            courses=sum(len(result.courses) for result in summary.results),
            detail_courses_attempted=summary.detail_courses_attempted,
            detail_courses_complete=summary.detail_courses_complete,
            detail_courses_partial=summary.detail_courses_partial,
            detail_warnings=summary.detail_warnings,
        )
        return _scrape_exit_code(
            list(summary.results),
            list(summary.errors),
            list(subject_outputs.values()),
        )


def main() -> None:
    parser = _build_parser()
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
