from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from classcatalog.dataset.models import (
    DatasetBuildReport,
    DatasetCounts,
    DatasetManifest,
    FieldCoverage,
    InventoryCourseChange,
    InventoryCourseRekey,
    InventoryCourseSnapshot,
    InventoryDiff,
    InventoryOfferingNumberChange,
    ManifestFile,
    ProductionCourseRecord,
    ValidationIssue,
    ValidationSeverity,
)
from classcatalog.models import CourseSection, InstructionMode, Meeting
from classcatalog.instruction_modes import classify_sdsu_instruction_mode
from classcatalog.repository import CourseRepository
from classcatalog.scraping.class_post import find_class_number_actions
from classcatalog.scraping.models import (
    CourseDetailOutput,
    CourseInfoRecord,
    CourseSearchHit,
    DetailCourseStatus,
    ScrapeCheckpoint,
    ScrapeRunOutput,
    SdsuCourseSectionRecord,
    SubjectDetailOutput,
)
from classcatalog.scraping.parser import parse_course_info_page
from classcatalog.scraping.progress import (
    atomic_write,
    course_detail_key,
    detail_target_hits,
    read_model,
)


@dataclass(frozen=True, slots=True)
class DatasetBuildConfig:
    """Inputs and destinations for one production dataset build."""

    output_dir: Path
    checkpoint_path: Path | None = None
    subject_output_dir: Path | None = None
    deep_run_path: Path | None = None
    discovery_run_path: Path | None = None
    api_data_path: Path | None = None
    strict: bool = True


@dataclass(frozen=True, slots=True)
class DatasetBuildResult:
    report: DatasetBuildReport
    exit_code: int


@dataclass(frozen=True, slots=True)
class _RecoveredOptionData:
    option_numbers: Mapping[str, int]
    group_indices: Mapping[str, tuple[int, ...]]
    primary_group_indices: Mapping[str, tuple[int, ...]]
    group_ids: Mapping[str, tuple[str, ...]]
    primary_group_ids: Mapping[str, tuple[str, ...]]


@dataclass(slots=True)
class _CoverageCounter:
    total: int = 0
    present: int = 0
    examples: list[str] | None = None

    def observe(self, *, present: bool, example: str | None = None) -> None:
        self.total += 1
        if present:
            self.present += 1
            return
        if example is not None:
            if self.examples is None:
                self.examples = []
            if len(self.examples) < 10 and example not in self.examples:
                self.examples.append(example)


@dataclass(frozen=True, slots=True)
class _LoadedInputs:
    checkpoint: ScrapeCheckpoint | None
    checkpoint_path: Path | None
    subject_output_dir: Path
    deep_run_path: Path | None
    expected_subjects: tuple[str, ...]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _path_from_serialized(value: str) -> Path:
    if os.sep == "\\":
        return Path(value)
    return Path(value.replace("\\", os.sep))


def _resolve_inputs(config: DatasetBuildConfig) -> _LoadedInputs:
    checkpoint: ScrapeCheckpoint | None = None
    checkpoint_path = config.checkpoint_path
    subject_output_dir = config.subject_output_dir
    deep_run_path = config.deep_run_path
    expected_subjects: tuple[str, ...] = ()

    if checkpoint_path is not None:
        checkpoint = read_model(checkpoint_path, ScrapeCheckpoint)
        expected_subjects = checkpoint.requested_subjects
        if subject_output_dir is None:
            subject_output_dir = _path_from_serialized(checkpoint.subject_output_dir)
        if deep_run_path is None:
            deep_run_path = _path_from_serialized(checkpoint.output_path)

    if subject_output_dir is None:
        raise ValueError("Provide --checkpoint or --subject-output-dir.")

    return _LoadedInputs(
        checkpoint=checkpoint,
        checkpoint_path=checkpoint_path,
        subject_output_dir=subject_output_dir,
        deep_run_path=deep_run_path,
        expected_subjects=expected_subjects,
    )


def _subject_paths(inputs: _LoadedInputs) -> tuple[tuple[str, Path], ...]:
    if inputs.checkpoint is not None:
        rows: list[tuple[str, Path]] = []
        by_subject = {state.subject: state for state in inputs.checkpoint.subjects}
        for subject in inputs.checkpoint.requested_subjects:
            state = by_subject[subject]
            serialized = _path_from_serialized(state.output_path)
            path = serialized if serialized.is_absolute() else Path.cwd() / serialized
            if not path.is_file():
                path = inputs.subject_output_dir / f"{_slug(subject)}.json"
            rows.append((subject, path))
        return tuple(rows)

    paths = tuple(sorted(inputs.subject_output_dir.glob("*.json")))
    return tuple((path.stem, path) for path in paths)


def _snapshot(hit: CourseSearchHit) -> InventoryCourseSnapshot:
    return InventoryCourseSnapshot(
        course_key=course_detail_key(hit),
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


def _logical_course_key(item: InventoryCourseSnapshot) -> str:
    """Return the unique PeopleSoft course-offering identity."""

    course_identity = item.crse_id or f"catalog:{item.catalog_number}"

    return "|".join(
        (
            item.subject,
            course_identity,
            str(item.crse_offer_nbr or ""),
            item.acad_career or "",
        )
    )


def _course_identity_key(item: InventoryCourseSnapshot) -> str:
    """Return a secondary identity used to detect a PeopleSoft CRSE_ID rekey."""

    return "|".join((item.subject, item.catalog_number, item.acad_career or ""))


def _normalise_inventory_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _group_inventory_by_logical_key(
    inventory: Mapping[str, InventoryCourseSnapshot],
) -> dict[str, tuple[InventoryCourseSnapshot, ...]]:
    grouped: dict[str, list[InventoryCourseSnapshot]] = defaultdict(list)
    for item in inventory.values():
        grouped[_logical_course_key(item)].append(item)
    return {
        key: tuple(sorted(values, key=lambda item: item.course_key))
        for key, values in grouped.items()
    }


def _preferred_inventory_snapshot(
    values: Sequence[InventoryCourseSnapshot],
) -> InventoryCourseSnapshot:
    return sorted(
        values,
        key=lambda item: (
            item.crse_offer_nbr is None,
            item.crse_offer_nbr or "",
            item.course_key,
        ),
    )[0]


def _offer_numbers(
    values: Sequence[InventoryCourseSnapshot],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {item.crse_offer_nbr for item in values if item.crse_offer_nbr is not None},
            key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
        )
    )


def _changed_fields(
    discovery: InventoryCourseSnapshot,
    deep: InventoryCourseSnapshot,
    fields: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        field
        for field in fields
        if getattr(discovery, field) != getattr(deep, field)
    )


def _inventory_from_subjects(
    outputs: Sequence[SubjectDetailOutput],
    issues: list[ValidationIssue],
) -> dict[str, InventoryCourseSnapshot]:
    inventory: dict[str, InventoryCourseSnapshot] = {}
    for output in outputs:
        for hit in output.search_result.courses:
            item = _snapshot(hit)
            existing = inventory.get(item.course_key)
            if existing is not None and existing != item:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="conflicting_deep_course_identity",
                        message=(
                            "The same stable course key appeared with conflicting metadata in "
                            "multiple subject outputs."
                        ),
                        subject=output.subject,
                        course_key=item.course_key,
                    )
                )
                continue
            inventory[item.course_key] = item
    return inventory


def _inventory_from_run(
    run: ScrapeRunOutput,
    issues: list[ValidationIssue],
) -> dict[str, InventoryCourseSnapshot]:
    inventory: dict[str, InventoryCourseSnapshot] = {}
    for result in run.results:
        for hit in result.courses:
            item = _snapshot(hit)
            existing = inventory.get(item.course_key)
            if existing is not None and existing != item:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="conflicting_discovery_course_identity",
                        message=(
                            "The discovery snapshot contains the same stable course key with "
                            "conflicting metadata."
                        ),
                        subject=result.subject,
                        course_key=item.course_key,
                    )
                )
                continue
            inventory[item.course_key] = item
    return inventory


def _build_inventory_diff(
    *,
    deep_inventory: Mapping[str, InventoryCourseSnapshot],
    discovery_path: Path | None,
    issues: list[ValidationIssue],
) -> InventoryDiff:
    if discovery_path is None:
        return InventoryDiff(
            status="not_compared",
            deep_courses=len(deep_inventory),
            matching_courses=0,
            note=(
                "No discovery snapshot was supplied. Pass --discovery-run to identify additions, "
                "removals, PeopleSoft course rekeys, offering-number changes, and metadata drift."
            ),
        )

    if not discovery_path.is_file():
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="discovery_snapshot_missing",
                message=(
                    "The requested discovery snapshot does not exist; inventory comparison "
                    "was skipped."
                ),
                path=str(discovery_path),
            )
        )
        return InventoryDiff(
            status="not_compared",
            discovery_path=str(discovery_path),
            deep_courses=len(deep_inventory),
            matching_courses=0,
            note="The discovery snapshot path did not exist.",
        )

    try:
        discovery_run = read_model(discovery_path, ScrapeRunOutput)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="invalid_discovery_snapshot",
                message=f"The discovery snapshot could not be validated: {exc}",
                path=str(discovery_path),
            )
        )
        return InventoryDiff(
            status="invalid",
            discovery_path=str(discovery_path),
            deep_courses=len(deep_inventory),
            matching_courses=0,
            note="The discovery snapshot was invalid.",
        )

    discovery_inventory = _inventory_from_run(discovery_run, issues)
    discovery_complete = (
        not discovery_run.errors
        and len(discovery_run.completed_subjects) == len(discovery_run.requested_subjects)
        and all(result.complete for result in discovery_run.results)
    )
    if not discovery_complete:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="discovery_snapshot_incomplete",
                message=(
                    "The supplied discovery snapshot is not a complete successful run. "
                    "Use the preserved 127-subject search-only snapshot or omit "
                    "--discovery-run."
                ),
                path=str(discovery_path),
            )
        )
        return InventoryDiff(
            status="invalid",
            discovery_path=str(discovery_path),
            discovery_courses=len(discovery_inventory),
            deep_courses=len(deep_inventory),
            matching_courses=0,
            note="The discovery snapshot was not a complete successful run.",
        )

    discovery_groups = _group_inventory_by_logical_key(discovery_inventory)
    deep_groups = _group_inventory_by_logical_key(deep_inventory)
    discovery_keys = set(discovery_groups)
    deep_keys = set(deep_groups)
    matching_keys = discovery_keys & deep_keys

    offering_number_changes: list[InventoryOfferingNumberChange] = []
    metadata_changes: list[InventoryCourseChange] = []
    reconciled_matches = 0
    # Compare stable course-level inventory metadata only.
    #
    # ``class_number`` is deliberately excluded. Search results choose one
    # representative physical class as the detail target, and that class can
    # differ between otherwise identical snapshots without representing an
    # inventory change.
    metadata_fields = (
        "course_code",
        "catalog_number",
        "title",
        "acad_career",
        "section_count",
    )
    for logical_key in sorted(matching_keys):
        discovery_values = discovery_groups[logical_key]
        deep_values = deep_groups[logical_key]
        discovery = _preferred_inventory_snapshot(discovery_values)
        deep = _preferred_inventory_snapshot(deep_values)
        discovery_offers = _offer_numbers(discovery_values)
        deep_offers = _offer_numbers(deep_values)
        if discovery_offers != deep_offers:
            offering_number_changes.append(
                InventoryOfferingNumberChange(
                    logical_course_key=logical_key,
                    discovery=discovery,
                    deep=deep,
                    discovery_offer_numbers=discovery_offers,
                    deep_offer_numbers=deep_offers,
                )
            )
        changed = _changed_fields(discovery, deep, metadata_fields)
        if changed:
            metadata_changes.append(
                InventoryCourseChange(
                    course_key=logical_key,
                    discovery=discovery,
                    deep=deep,
                    changed_fields=changed,
                )
            )

    discovery_unmatched = {
        key: _preferred_inventory_snapshot(discovery_groups[key])
        for key in discovery_keys - deep_keys
    }
    deep_unmatched = {
        key: _preferred_inventory_snapshot(deep_groups[key])
        for key in deep_keys - discovery_keys
    }

    # Reconcile one-to-one PeopleSoft offer/career remaps before attempting
    # true course rekey detection.
    #
    # A CRSE_ID may legitimately expose multiple simultaneous offerings.
    # For example, MUSIC 102 can have offer 1 and offer 3 at the same time.
    # Therefore we only collapse an unmatched pair into an offer/career
    # remap when that subject + CRSE_ID has exactly one inventory record on
    # each side in the complete snapshots.
    def _crse_identity(item: InventoryCourseSnapshot) -> str:
        course_identity = item.crse_id or f"catalog:{item.catalog_number}"
        return "|".join((item.subject, course_identity))

    discovery_all_by_crse: dict[str, list[InventoryCourseSnapshot]] = defaultdict(list)
    deep_all_by_crse: dict[str, list[InventoryCourseSnapshot]] = defaultdict(list)

    for item in discovery_inventory.values():
        discovery_all_by_crse[_crse_identity(item)].append(item)

    for item in deep_inventory.values():
        deep_all_by_crse[_crse_identity(item)].append(item)

    discovery_unmatched_by_crse: dict[str, list[str]] = defaultdict(list)
    deep_unmatched_by_crse: dict[str, list[str]] = defaultdict(list)

    for logical_key, item in discovery_unmatched.items():
        discovery_unmatched_by_crse[_crse_identity(item)].append(logical_key)

    for logical_key, item in deep_unmatched.items():
        deep_unmatched_by_crse[_crse_identity(item)].append(logical_key)

    for crse_identity in sorted(
        set(discovery_unmatched_by_crse) & set(deep_unmatched_by_crse)
    ):
        # Do not collapse simultaneous offerings. A remap is only
        # unambiguous when the complete snapshots each contain exactly one
        # record for this subject + CRSE_ID.
        if len(discovery_all_by_crse[crse_identity]) != 1:
            continue
        if len(deep_all_by_crse[crse_identity]) != 1:
            continue

        discovery_candidates = discovery_unmatched_by_crse[crse_identity]
        deep_candidates = deep_unmatched_by_crse[crse_identity]

        if len(discovery_candidates) != 1 or len(deep_candidates) != 1:
            continue

        discovery_key = discovery_candidates[0]
        deep_key = deep_candidates[0]

        discovery = discovery_unmatched[discovery_key]
        deep = deep_unmatched[deep_key]

        # Same CRSE_ID, but PeopleSoft moved the course to another offer
        # number and/or academic career.
        if discovery.crse_id != deep.crse_id:
            continue

        discovery_offers = _offer_numbers((discovery,))
        deep_offers = _offer_numbers((deep,))

        if discovery_offers != deep_offers:
            offering_number_changes.append(
                InventoryOfferingNumberChange(
                    logical_course_key=crse_identity,
                    discovery=discovery,
                    deep=deep,
                    discovery_offer_numbers=discovery_offers,
                    deep_offer_numbers=deep_offers,
                )
            )

        changed = _changed_fields(
            discovery,
            deep,
            (
                "course_code",
                "catalog_number",
                "title",
                "acad_career",
                "section_count",
            ),
        )
        if changed:
            metadata_changes.append(
                InventoryCourseChange(
                    course_key=crse_identity,
                    discovery=discovery,
                    deep=deep,
                    changed_fields=changed,
                )
            )

        reconciled_matches += 1
        del discovery_unmatched[discovery_key]
        del deep_unmatched[deep_key]

    # Anything still unmatched may represent an actual PeopleSoft course
    # rekey. Match by logical catalog identity, but require CRSE_ID itself
    # to have changed; offer/career-only moves were handled above.
    discovery_by_identity: dict[str, list[str]] = defaultdict(list)
    deep_by_identity: dict[str, list[str]] = defaultdict(list)

    for logical_key, item in discovery_unmatched.items():
        discovery_by_identity[_course_identity_key(item)].append(logical_key)

    for logical_key, item in deep_unmatched.items():
        deep_by_identity[_course_identity_key(item)].append(logical_key)

    rekeyed: list[InventoryCourseRekey] = []

    for identity_key in sorted(set(discovery_by_identity) & set(deep_by_identity)):
        discovery_candidates = discovery_by_identity[identity_key]
        deep_candidates = deep_by_identity[identity_key]

        if len(discovery_candidates) != 1 or len(deep_candidates) != 1:
            continue

        discovery_key = discovery_candidates[0]
        deep_key = deep_candidates[0]

        discovery = discovery_unmatched[discovery_key]
        deep = deep_unmatched[deep_key]

        if (
            _normalise_inventory_text(discovery.title)
            != _normalise_inventory_text(deep.title)
        ):
            continue

        # A rekey means the PeopleSoft CRSE_ID actually changed.
        if discovery.crse_id == deep.crse_id:
            continue

        changed = _changed_fields(
            discovery,
            deep,
            (
                "crse_id",
                "crse_offer_nbr",
                "class_number",
                "section_count",
                "detail_url",
            ),
        )

        rekeyed.append(
            InventoryCourseRekey(
                course_identity_key=identity_key,
                discovery=discovery,
                deep=deep,
                changed_fields=changed,
            )
        )

        del discovery_unmatched[discovery_key]
        del deep_unmatched[deep_key]

    only_discovery = tuple(
        sorted(
            discovery_unmatched.values(),
            key=lambda item: (item.subject, item.catalog_number, item.course_key),
        )
    )
    only_deep = tuple(
        sorted(
            deep_unmatched.values(),
            key=lambda item: (item.subject, item.catalog_number, item.course_key),
        )
    )

    if only_discovery or only_deep or rekeyed:
        status = "drift_detected"
    elif offering_number_changes or metadata_changes:
        status = "metadata_changed"
    else:
        status = "match"

    if status == "drift_detected":
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="inventory_drift_detected",
                message=(
                    "Discovery and deep PeopleSoft offering inventories differ: "
                    f"{len(only_discovery)} only in discovery, "
                    f"{len(only_deep)} only in deep, {len(rekeyed)} rekeyed courses, "
                    f"{len(offering_number_changes)} offering-number changes, and "
                    f"{len(metadata_changes)} other metadata changes. The exact records are "
                    "in inventory-diff.json."
                ),
                path=str(discovery_path),
            )
        )

    return InventoryDiff(
        status=status,
        discovery_path=str(discovery_path),
        discovery_courses=len(discovery_inventory),
        deep_courses=len(deep_inventory),
        matching_courses=len(matching_keys) + reconciled_matches,
        only_in_discovery=only_discovery,
        only_in_deep=only_deep,
        rekeyed_courses=tuple(rekeyed),
        offering_number_changes=tuple(offering_number_changes),
        changed_courses=tuple(metadata_changes),
        note=(
            "Snapshot reconciliation preserves distinct PeopleSoft offerings by subject, "
            "CRSE_ID, offer number, and academic career. Unambiguous one-to-one offer/career "
            "moves for the same CRSE_ID are reported as metadata drift rather than additions "
            "and removals. Same-code/title records whose CRSE_ID actually changed are reported "
            "separately as PeopleSoft rekeys. The completed deep subject outputs remain the "
            "publication source."
        ),
    )


def _section_id(
    *,
    term_code: str,
    course_key: str,
    class_number: str,
) -> str:
    return _slug(f"sdsu-{term_code}-{course_key}-{class_number}")


def _option_section_number(
    course_info: CourseInfoRecord,
    class_number: str,
) -> str | None:
    for option in course_info.options:
        if option.class_number == class_number and option.section_number:
            return option.section_number
    return None


def _option_number(
    course_info: CourseInfoRecord,
    class_number: str,
) -> int | None:
    current_option_number: int | None = None
    for option in sorted(course_info.options, key=lambda item: item.source_row_index):
        if option.option_number is not None:
            current_option_number = option.option_number
        if option.class_number == class_number:
            return current_option_number
    return None


def _option_group_indices(
    course_info: CourseInfoRecord,
    class_number: str,
) -> tuple[int, ...]:
    values = {
        option.option_group_index
        for option in course_info.options
        if option.class_number == class_number and option.option_group_index is not None
    }
    return tuple(sorted(values))


def _option_primary_group_indices(
    course_info: CourseInfoRecord,
    class_number: str,
) -> tuple[int, ...]:
    """Return option rows where this class is the row's primary class.

    The visible component label is not a reliable role signal: SDSU can legitimately
    use Activity, Laboratory, or Clinical as the primary enrollment class for a
    course.  The Class Selection row itself is authoritative.
    """

    return _option_group_indices(course_info, class_number)


def _needs_option_membership_recovery(detail: CourseDetailOutput) -> bool:
    """Return whether saved HTML can improve row-level option membership.

    Legacy deep outputs sometimes persist only the linked Activity/Lab/Clinical rows
    for a detail target.  Requiring two component types in the *persisted sections*
    therefore misses exactly the records that the coverage audit exposed.  Recover
    whenever a secondary-looking physical section has no row membership, even if it
    is the only component type in that detail record.
    """

    if not detail.sections:
        return False

    components = {
        (section.component or "").strip().casefold()
        for section in detail.sections
        if (section.component or "").strip()
    }
    if len(detail.sections) >= 2 and len(components) >= 2:
        return True

    secondary_tokens = (
        "activity",
        "laboratory",
        "lab",
        "tech acts",
        "tech act",
        "clinical",
    )
    return any(
        not section.option_group_indices
        and any(
            token in (section.component or "").strip().casefold()
            for token in secondary_tokens
        )
        for section in detail.sections
    )


def _fixture_path(value: str) -> Path:
    return _path_from_serialized(value)


def _needs_option_recovery(detail: CourseDetailOutput) -> bool:
    """Return whether saved HTML is needed to recover linked component options.

    Most courses either have a single physical section, only one component type, or
    already carry ``option_number`` on every section.  Opening/parsing a saved HTML
    fixture for those courses is wasted work and is especially expensive on Windows
    filesystems.  Recovery is only useful when a course has multiple component types
    (for example Discussion + Activity) and at least one section is missing its option
    number.
    """

    if len(detail.sections) < 2:
        return False
    if all(section.option_number is not None for section in detail.sections):
        return False

    components = {
        (section.component or "").strip().casefold()
        for section in detail.sections
        if (section.component or "").strip()
    }
    if len(components) < 2:
        return False

    return True


def _recovered_option_data(detail: CourseDetailOutput) -> _RecoveredOptionData:
    """Recover option numbers and row-group memberships from saved HTML.

    A physical class may appear in more than one enrollment option.  Therefore this
    returns *all* option-group indices for each class number rather than collapsing a
    class to one visible option number.  The saved Course Information HTML is the only
    source that preserves that many-to-many row relationship for legacy deep scrapes.
    """

    if not _needs_option_membership_recovery(detail):
        return _RecoveredOptionData(option_numbers={}, group_indices={}, primary_group_indices={}, group_ids={}, primary_group_ids={})

    html_path: Path | None = None
    for value in detail.fixture_paths:
        candidate = _fixture_path(value)
        if candidate.name.endswith('-course-info.html') and candidate.exists():
            html_path = candidate
            break
    if html_path is None:
        return _RecoveredOptionData(option_numbers={}, group_indices={}, primary_group_indices={}, group_ids={}, primary_group_ids={})

    try:
        html = html_path.read_text(encoding='utf-8')
        parsed = parse_course_info_page(
            html,
            source_url=(
                detail.course_info.source_url
                if detail.course_info is not None and detail.course_info.source_url
                else detail.course.detail_url or 'https://catalog.sdsu.edu/'
            ),
        )
        actions = find_class_number_actions(html)
    except (OSError, ValueError):
        return _RecoveredOptionData(option_numbers={}, group_indices={}, primary_group_indices={}, group_ids={}, primary_group_ids={})

    option_number_by_row: dict[int, int] = {}
    group_index_by_row: dict[int, int] = {}
    current_option: int | None = None
    current_group: int | None = None
    for option in sorted(parsed.options, key=lambda item: item.source_row_index):
        if option.option_number is not None:
            current_option = option.option_number
        if option.option_group_index is not None:
            current_group = option.option_group_index
        if current_option is not None:
            option_number_by_row[option.source_row_index] = current_option
        if current_group is not None:
            group_index_by_row[option.source_row_index] = current_group

    option_numbers: dict[str, int] = {}
    group_sets: dict[str, set[int]] = defaultdict(set)
    primary_group_sets: dict[str, set[int]] = defaultdict(set)
    group_id_sets: dict[str, set[str]] = defaultdict(set)
    primary_group_id_sets: dict[str, set[str]] = defaultdict(set)

    # Build a stable enrollment-row signature from the actual class numbers present
    # on each row.  Local PeopleSoft row/group indices reset across detail records,
    # but a class-number set is stable and therefore safe for cross-record grouping.
    classes_by_row: dict[int, set[str]] = defaultdict(set)
    primary_by_row: dict[int, str] = {}
    for option in parsed.options:
        classes_by_row[option.source_row_index].add(option.class_number)
        primary_by_row[option.source_row_index] = option.class_number
    for action in actions:
        classes_by_row[action.source_row_index].add(action.class_number)

    group_id_by_row: dict[int, str] = {}
    for row_index, class_numbers in classes_by_row.items():
        if class_numbers:
            group_id_by_row[row_index] = "+".join(sorted(class_numbers))
            group_id = group_id_by_row[row_index]
            for class_number in class_numbers:
                group_id_sets[class_number].add(group_id)
            primary_class = primary_by_row.get(row_index)
            if primary_class:
                primary_group_id_sets[primary_class].add(group_id)

    # Primary classes parsed from each row are useful even if their class number is
    # not represented by a clickable action in a synthetic/legacy fixture.
    for option in parsed.options:
        if option.option_number is not None:
            option_numbers.setdefault(option.class_number, option.option_number)
        if option.option_group_index is not None:
            group_sets[option.class_number].add(option.option_group_index)
            primary_group_sets[option.class_number].add(option.option_group_index)

    for action in actions:
        option_number = option_number_by_row.get(action.source_row_index)
        if option_number is not None:
            option_numbers.setdefault(action.class_number, option_number)
        group_index = group_index_by_row.get(action.source_row_index)
        if group_index is not None:
            group_sets[action.class_number].add(group_index)

    return _RecoveredOptionData(
        option_numbers=option_numbers,
        group_indices={
            class_number: tuple(sorted(indices))
            for class_number, indices in group_sets.items()
        },
        primary_group_indices={
            class_number: tuple(sorted(indices))
            for class_number, indices in primary_group_sets.items()
        },
        group_ids={
            class_number: tuple(sorted(values))
            for class_number, values in group_id_sets.items()
        },
        primary_group_ids={
            class_number: tuple(sorted(values))
            for class_number, values in primary_group_id_sets.items()
        },
    )


def _recovered_option_numbers(detail: CourseDetailOutput) -> dict[str, int]:
    """Backward-compatible helper retained for focused tests/callers."""

    return dict(_recovered_option_data(detail).option_numbers)


def _normalise_section(
    *,
    detail: CourseDetailOutput,
    source: SdsuCourseSectionRecord,
    course_info: CourseInfoRecord,
    recovered_option_data: _RecoveredOptionData | None = None,
) -> CourseSection:
    section_number = (
        source.section_number
        or _option_section_number(course_info, source.class_number)
        or ""
    )
    campus = source.campus or source.location or "Unknown"
    units = source.units if source.units is not None else source.units_min
    meetings = tuple(
        Meeting(
            days=meeting.days,
            start_time=meeting.start_time,
            end_time=meeting.end_time,
            location=meeting.room or source.location,
            meeting_dates=meeting.meeting_dates,
            instructor=meeting.instructor,
        )
        for meeting in source.meetings
    )
    has_timed_meeting = any(
        meeting.start_time is not None or meeting.end_time is not None
        for meeting in meetings
    )

    instruction_mode = classify_sdsu_instruction_mode(
        source.instruction_mode_text,
        has_timed_meeting=has_timed_meeting,
        fallback=source.instruction_mode,
    ) or source.instruction_mode
    prerequisite_text = source.prerequisite_text
    manual_review = bool(prerequisite_text and prerequisite_text.strip())
    return CourseSection(
        id=_section_id(
            term_code=source.term_code or detail.course.term_code,
            course_key=detail.course_key,
            class_number=source.class_number,
        ),
        term=source.term,
        term_code=source.term_code or detail.course.term_code,
        course_code=source.course_code,
        subject=source.subject,
        catalog_number=source.catalog_number,
        section_number=section_number,
        schedule_number=source.class_number,
        option_number=(
            source.option_number
            or (recovered_option_data.option_numbers if recovered_option_data else {}).get(source.class_number)
            or _option_number(course_info, source.class_number)
        ),
        source_course_key=detail.course_key,
        option_group_indices=tuple(sorted(set(
            source.option_group_indices
            or (recovered_option_data.group_indices if recovered_option_data else {}).get(source.class_number, ())
            or _option_group_indices(course_info, source.class_number)
        ))),
        option_primary_group_indices=tuple(sorted(set(
            source.option_primary_group_indices
            or (recovered_option_data.primary_group_indices if recovered_option_data else {}).get(source.class_number, ())
            or _option_primary_group_indices(course_info, source.class_number)
        ))),
        option_group_ids=tuple(sorted(set(
            (recovered_option_data.group_ids if recovered_option_data else {}).get(source.class_number, ())
        ))),
        option_primary_group_ids=tuple(sorted(set(
            (recovered_option_data.primary_group_ids if recovered_option_data else {}).get(source.class_number, ())
        ))),
        crse_id=detail.course.crse_id,
        crse_offer_nbr=detail.course.crse_offer_nbr,
        acad_career=detail.course.acad_career,
        component=source.component,
        title=source.title,
        description=source.description,
        units=units,
        units_min=source.units_min,
        units_max=source.units_max,
        units_text=source.units_text,
        class_difficulty=None,
        grading=source.grading,
        grading_text=source.grading_text,
        requirement_tags=(),
        program_tags=(),
        prerequisite_text=prerequisite_text,
        prerequisite_groups=(),
        prerequisite_manual_review=manual_review,
        enrollment_requirements=source.enrollment_requirements,
        class_notes=source.class_notes,
        instruction_mode=instruction_mode,
        instruction_mode_text=source.instruction_mode_text,
        seat_status=source.seat_status,
        seats_available=source.seats_available,
        seat_capacity=source.seat_capacity,
        seats_enrolled=source.seats_enrolled,
        waitlist_capacity=source.waitlist_capacity,
        waitlist_total=source.waitlist_total,
        waitlist_available=source.waitlist_available,
        campus=campus,
        location=source.location,
        instructor=source.instructor,
        meetings=meetings,
        professor=None,
        bookstore_url=source.bookstore_url,
        materials_description=source.materials_description,
        textbook_required=source.textbook_required,
        source_url=source.course_source_url,
        source_updated_at=detail.completed_at or detail.updated_at,
    )


def _normalise_course(
    *,
    detail: CourseDetailOutput,
    sections: Sequence[CourseSection],
) -> ProductionCourseRecord:
    assert detail.course_info is not None
    info = detail.course_info
    prerequisite_texts = tuple(
        sorted(
            {
                section.prerequisite_text.strip()
                for section in sections
                if section.prerequisite_text and section.prerequisite_text.strip()
            }
        )
    )
    physical = {section.schedule_number for section in sections}
    return ProductionCourseRecord(
        id=detail.course_key,
        term=info.term,
        term_code=info.term_code or detail.course.term_code,
        subject=info.subject,
        catalog_number=info.catalog_number,
        course_code=info.course_code,
        crse_id=detail.course.crse_id,
        crse_offer_nbr=detail.course.crse_offer_nbr,
        acad_career=detail.course.acad_career,
        title=info.title,
        description=info.description,
        units=info.units,
        units_min=info.units_min,
        units_max=info.units_max,
        units_text=info.units_text,
        grading=info.grading,
        grading_text=info.grading_text,
        components=info.components,
        course_career=info.course_career,
        prerequisite_texts=prerequisite_texts,
        section_ids=tuple(section.id for section in sections),
        course_section_listings=len(sections),
        unique_physical_sections=len(physical),
        source_url=info.source_url,
        source_updated_at=detail.completed_at or detail.updated_at,
    )


def _coverage(
    counters: Mapping[str, _CoverageCounter],
) -> tuple[FieldCoverage, ...]:
    rows: list[FieldCoverage] = []
    for field in sorted(counters):
        counter = counters[field]
        missing = counter.total - counter.present
        percent = 100.0 if counter.total == 0 else counter.present / counter.total * 100
        rows.append(
            FieldCoverage(
                field=field,
                total=counter.total,
                present=counter.present,
                missing=missing,
                percent_present=round(percent, 2),
                examples=tuple(counter.examples or ()),
            )
        )
    return tuple(rows)


def _observe_section_coverage(
    counters: dict[str, _CoverageCounter],
    section: CourseSection,
) -> None:
    example = f"{section.course_code} / {section.schedule_number}"
    observations: dict[str, bool] = {
        "section_number": bool(section.section_number),
        "description": bool(section.description),
        "component": bool(section.component),
        "prerequisite_text": bool(section.prerequisite_text),
        "campus": bool(section.campus and section.campus != "Unknown"),
        "location": bool(section.location),
        "instructor": bool(section.instructor),
        "meetings": bool(section.meetings),
        "timed_meetings": any(
            meeting.start_time is not None and meeting.end_time is not None
            for meeting in section.meetings
        ),
        "seat_capacity": section.seat_capacity is not None,
        "seats_enrolled": section.seats_enrolled is not None,
        "seats_available": section.seats_available is not None,
        "waitlist_available": section.waitlist_available is not None,
        "bookstore_url": bool(section.bookstore_url),
        "textbook_required": section.textbook_required is not None,
        "source_url": bool(section.source_url),
    }
    for field, present in observations.items():
        counters.setdefault(field, _CoverageCounter()).observe(
            present=present,
            example=example,
        )


def _write_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False),
    )


def _write_model_array(path: Path, values: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("[\n")
        first = True
        for value in values:
            if not first:
                handle.write(",\n")
            first = False
            if hasattr(value, "model_dump"):
                payload = value.model_dump(mode="json")
            else:
                payload = value
            rendered = json.dumps(payload, indent=2, ensure_ascii=False)
            handle.write("  ")
            handle.write(rendered.replace("\n", "\n  "))
        handle.write("\n]\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_file(path: Path, *, base: Path) -> ManifestFile:
    return ManifestFile(
        path=str(path.relative_to(base)),
        bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def _render_report_markdown(report: DatasetBuildReport) -> str:
    lines = [
        "# ClassCatalog dataset validation report",
        "",
        f"- Generated: `{report.generated_at}`",
        f"- Status: **{report.status.upper()}**",
        f"- Term: `{report.term or 'unknown'}` (`{report.term_code or 'unknown'}`)",
        f"- Errors: **{report.errors}**",
        f"- Warnings: **{report.warnings}**",
        "",
        "## Counts",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Expected subjects | {report.counts.expected_subjects} |",
        f"| Subject files | {report.counts.subject_files} |",
        f"| Complete subjects | {report.counts.complete_subjects} |",
        f"| Discovered courses | {report.counts.discovered_courses} |",
        f"| Complete course details | {report.counts.complete_course_details} |",
        f"| Course-section listings | {report.counts.course_section_listings} |",
        f"| Unique physical sections | {report.counts.unique_physical_sections} |",
        f"| Cross-listed physical sections | {report.counts.cross_listed_physical_sections} |",
        "",
        "## Class coverage audit",
        "",
        f"- Status: `{report.section_coverage_audit.status if report.section_coverage_audit else 'not_run'}`",
        f"- Displayed enrollment options: `{report.section_coverage_audit.displayed_options if report.section_coverage_audit else 0}`",
        f"- Accounted physical sections: `{report.section_coverage_audit.accounted_physical_sections if report.section_coverage_audit else 0}`",
        f"- Unaccounted physical sections: `{report.section_coverage_audit.unaccounted_physical_sections if report.section_coverage_audit else 0}`",
        f"- Standalone physical sections: `{report.section_coverage_audit.standalone_physical_sections if report.section_coverage_audit else 0}`",
        f"- Physical sections used in grouped options: `{report.section_coverage_audit.grouped_component_physical_sections if report.section_coverage_audit else 0}`",
        f"- Duplicate option memberships: `{report.section_coverage_audit.duplicate_option_memberships if report.section_coverage_audit else 0}`",
        "",
        "## Inventory reconciliation",
        "",
        f"- Status: `{report.inventory_diff.status}`",
        f"- Discovery courses: `{report.inventory_diff.discovery_courses}`",
        f"- Deep courses: `{report.inventory_diff.deep_courses}`",
        f"- Comparison identity: `{report.inventory_diff.identity_strategy}`",
        f"- Only in discovery: `{len(report.inventory_diff.only_in_discovery)}`",
        f"- Only in deep: `{len(report.inventory_diff.only_in_deep)}`",
        f"- PeopleSoft course rekeys: `{len(report.inventory_diff.rekeyed_courses)}`",
        f"- Offering-number changes: `{len(report.inventory_diff.offering_number_changes)}`",
        f"- Other metadata changes: `{len(report.inventory_diff.changed_courses)}`",
        "",
    ]
    if report.inventory_diff.only_in_discovery:
        lines.extend(["### Only in discovery", ""])
        for item in report.inventory_diff.only_in_discovery:
            lines.append(f"- `{item.course_code}` — {item.title} (`{item.course_key}`)")
        lines.append("")
    if report.inventory_diff.only_in_deep:
        lines.extend(["### Only in deep", ""])
        for item in report.inventory_diff.only_in_deep:
            lines.append(f"- `{item.course_code}` — {item.title} (`{item.course_key}`)")
        lines.append("")
    if report.inventory_diff.rekeyed_courses:
        lines.extend(["### PeopleSoft course rekeys", ""])
        for item in report.inventory_diff.rekeyed_courses:
            lines.append(
                f"- `{item.discovery.course_code}` — {item.discovery.title}: "
                f"`{item.discovery.crse_id}` → `{item.deep.crse_id}`"
            )
        lines.append("")
    if report.inventory_diff.offering_number_changes:
        lines.extend(["### Offering-number changes", ""])
        for item in report.inventory_diff.offering_number_changes:
            discovery_offers = ", ".join(item.discovery_offer_numbers) or "—"
            deep_offers = ", ".join(item.deep_offer_numbers) or "—"
            lines.append(
                f"- `{item.discovery.course_code}` — `{discovery_offers}` → "
                f"`{deep_offers}`"
            )
        lines.append("")
    if report.inventory_diff.changed_courses:
        lines.extend(["### Other metadata changes", ""])
        for item in report.inventory_diff.changed_courses:
            lines.append(
                f"- `{item.discovery.course_code}` — "
                f"{', '.join(item.changed_fields)}"
            )
        lines.append("")

    lines.extend(
        [
            "## Field coverage",
            "",
            "| Field | Present | Missing | Coverage |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in report.field_coverage:
        lines.append(
            f"| {row.field} | {row.present} | {row.missing} | {row.percent_present:.2f}% |"
        )

    lines.extend(["", "## Validation issues", ""])
    if not report.issues:
        lines.append("No validation issues.")
    else:
        for issue in report.issues:
            context = " / ".join(
                part
                for part in (issue.subject, issue.course_key, issue.class_number)
                if part
            )
            suffix = f" — {context}" if context else ""
            lines.append(
                f"- **{issue.severity.value.upper()}** `{issue.code}`: {issue.message}{suffix}"
            )
    lines.append("")
    return "\n".join(lines)


def _validate_deep_aggregate(
    *,
    deep_run_path: Path | None,
    deep_inventory: Mapping[str, InventoryCourseSnapshot],
    issues: list[ValidationIssue],
) -> None:
    if deep_run_path is None or not deep_run_path.is_file():
        return
    try:
        run = read_model(deep_run_path, ScrapeRunOutput)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="invalid_deep_aggregate",
                message=f"The aggregate deep-run file could not be validated: {exc}",
                path=str(deep_run_path),
            )
        )
        return
    aggregate_inventory = _inventory_from_run(run, issues)
    if set(aggregate_inventory) != set(deep_inventory):
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="deep_aggregate_inventory_stale",
                message=(
                    "The aggregate deep-run course inventory differs from the per-subject "
                    "outputs. The builder used the per-subject outputs as the source of truth."
                ),
                path=str(deep_run_path),
            )
        )
    expected_courses = len(deep_inventory)
    if (
        run.detail_courses_complete != expected_courses
        or run.detail_courses_partial != 0
        or run.errors
        or len(run.completed_subjects) != len(run.requested_subjects)
    ):
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="deep_aggregate_completion_stale",
                message=(
                    "The aggregate deep-run completion totals are older or incomplete compared "
                    "with the per-subject outputs. Production counts were recalculated from the "
                    "subject files."
                ),
                path=str(deep_run_path),
            )
        )


def build_production_dataset(config: DatasetBuildConfig) -> DatasetBuildResult:
    """Validate a completed deep scrape and build API-ready production JSON files."""

    generated_at = _utc_now()
    inputs = _resolve_inputs(config)
    issues: list[ValidationIssue] = []
    outputs: list[SubjectDetailOutput] = []
    paths = _subject_paths(inputs)
    enforce_subject_names = bool(inputs.expected_subjects)
    expected_subjects = inputs.expected_subjects or tuple(subject for subject, _ in paths)

    for expected_subject, path in paths:
        if not path.is_file():
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="subject_output_missing",
                    message="The expected per-subject output file does not exist.",
                    subject=expected_subject,
                    path=str(path),
                )
            )
            continue
        try:
            output = read_model(path, SubjectDetailOutput)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="subject_output_invalid",
                    message=f"The per-subject output failed schema validation: {exc}",
                    subject=expected_subject,
                    path=str(path),
                )
            )
            continue
        if enforce_subject_names and output.subject != expected_subject:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="subject_output_name_mismatch",
                    message=(
                        f"Expected subject {expected_subject!r}, but the file contains "
                        f"{output.subject!r}."
                    ),
                    subject=expected_subject,
                    path=str(path),
                )
            )
        outputs.append(output)

    term_values = {output.term for output in outputs}
    term_code_values = {output.term_code for output in outputs}
    run_ids = {output.run_id for output in outputs}
    term = next(iter(term_values)) if len(term_values) == 1 else None
    term_code = next(iter(term_code_values)) if len(term_code_values) == 1 else None
    source_run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
    if len(term_values) > 1:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="mixed_terms",
                message=f"Per-subject outputs contain multiple terms: {sorted(term_values)}",
            )
        )
    if len(term_code_values) > 1:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="mixed_term_codes",
                message=(
                    "Per-subject outputs contain multiple term codes: "
                    f"{sorted(term_code_values)}"
                ),
            )
        )
    if len(run_ids) > 1:
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="mixed_run_ids",
                message=(
                    "Per-subject outputs contain multiple run IDs. This is acceptable only if "
                    "the files were intentionally merged from compatible completed runs."
                ),
            )
        )

    if inputs.checkpoint is not None:
        if term is not None and inputs.checkpoint.term != term:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="checkpoint_term_mismatch",
                    message=(
                        f"Checkpoint term {inputs.checkpoint.term!r} does not match subject "
                        f"outputs {term!r}."
                    ),
                    path=str(inputs.checkpoint_path),
                )
            )
        if term_code is not None and inputs.checkpoint.term_code != term_code:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="checkpoint_term_code_mismatch",
                    message=(
                        f"Checkpoint term code {inputs.checkpoint.term_code!r} does not match "
                        f"subject outputs {term_code!r}."
                    ),
                    path=str(inputs.checkpoint_path),
                )
            )
        if inputs.checkpoint.completed_at is None:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="checkpoint_not_marked_complete",
                    message=(
                        "The checkpoint is not marked complete. Subject files are still fully "
                        "revalidated before publication."
                    ),
                    path=str(inputs.checkpoint_path),
                )
            )
        incomplete_states = tuple(
            state.subject
            for state in inputs.checkpoint.subjects
            if state.status.value != "complete"
        )
        if incomplete_states:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="checkpoint_subject_states_stale",
                    message=(
                        "The checkpoint contains non-complete subject states even though the "
                        "builder will independently validate each subject output: "
                        f"{', '.join(incomplete_states)}."
                    ),
                    path=str(inputs.checkpoint_path),
                )
            )

    if len(outputs) != len(expected_subjects):
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="subject_output_count_mismatch",
                message=(
                    f"Expected {len(expected_subjects)} subject files but validated "
                    f"{len(outputs)}."
                ),
                path=str(inputs.subject_output_dir),
            )
        )

    deep_inventory = _inventory_from_subjects(outputs, issues)
    inventory_diff = _build_inventory_diff(
        deep_inventory=deep_inventory,
        discovery_path=config.discovery_run_path,
        issues=issues,
    )
    _validate_deep_aggregate(
        deep_run_path=inputs.deep_run_path,
        deep_inventory=deep_inventory,
        issues=issues,
    )

    course_records: list[ProductionCourseRecord] = []
    section_records: list[CourseSection] = []
    section_by_id: dict[str, CourseSection] = {}
    duplicate_listings_removed = 0
    physical_to_listings: dict[tuple[str, str], list[CourseSection]] = defaultdict(list)
    coverage_counters: dict[str, _CoverageCounter] = {}
    grading_counts: Counter[str] = Counter()
    instruction_mode_counts: Counter[str] = Counter()
    seat_status_counts: Counter[str] = Counter()
    complete_subjects = 0
    complete_details = 0

    for output in outputs:
        path = inputs.subject_output_dir / f"{_slug(output.subject)}.json"
        if not output.search_result.complete or output.search_result.unresolved_partition_count:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="subject_search_incomplete",
                    message=(
                        "The saved subject search is incomplete or contains unresolved capped "
                        "partitions."
                    ),
                    subject=output.subject,
                    path=str(path),
                )
            )
        if not output.complete:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="subject_detail_incomplete",
                    message="The per-subject deep output is not complete.",
                    subject=output.subject,
                    path=str(path),
                )
            )
        else:
            complete_subjects += 1

        expected_keys = tuple(
            course_detail_key(hit)
            for hit in detail_target_hits(
                output.search_result,
                limit=output.detail_limit,
            )
        )
        if output.detail_target_course_keys != expected_keys:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="detail_target_mismatch",
                    message=(
                        "The persisted detail target list does not exactly match the complete "
                        "search inventory for this run."
                    ),
                    subject=output.subject,
                    path=str(path),
                )
            )

        detail_by_key = {detail.course_key: detail for detail in output.course_details}
        for key in output.detail_target_course_keys:
            detail = detail_by_key.get(key)
            if detail is None:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_detail_missing",
                        message="A targeted course has no persisted detail record.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
                continue
            if detail.status is not DetailCourseStatus.COMPLETE:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_detail_not_complete",
                        message=f"The course detail status is {detail.status.value!r}.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
                continue
            if detail.course_info is None:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_info_missing",
                        message="A complete course detail has no parsed Course Information record.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
                continue
            if not detail.course_info.options_complete:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_option_grid_incomplete",
                        message="A complete course retains an incomplete option grid.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
            if detail.course_info.units_min > detail.course_info.units_max:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_units_range_invalid",
                        message="Course units_min is greater than units_max.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
            if not detail.sections:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="course_sections_missing",
                        message="A complete scheduled course has no assembled section records.",
                        subject=output.subject,
                        course_key=key,
                    )
                )
                continue

            course_sections: list[CourseSection] = []
            recovered_option_data = _recovered_option_data(detail)
            for source in detail.sections:
                if (
                    source.subject != detail.course.subject
                    or source.course_code != detail.course.course_code
                ):
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="section_course_mismatch",
                            message=(
                                "The assembled section does not match its parent course "
                                "subject/code."
                            ),
                            subject=output.subject,
                            course_key=key,
                            class_number=source.class_number,
                        )
                    )
                    continue
                if source.units_min > source.units_max:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="section_units_range_invalid",
                            message="Section units_min is greater than units_max.",
                            subject=output.subject,
                            course_key=key,
                            class_number=source.class_number,
                        )
                    )
                    continue
                normalized = _normalise_section(
                    detail=detail,
                    source=source,
                    course_info=detail.course_info,
                    recovered_option_data=recovered_option_data,
                )
                existing = section_by_id.get(normalized.id)
                if existing is not None:
                    if existing != normalized:
                        issues.append(
                            ValidationIssue(
                                severity=ValidationSeverity.ERROR,
                                code="conflicting_course_section_listing",
                                message=(
                                    "The same normalized section ID has conflicting records."
                                ),
                                subject=output.subject,
                                course_key=key,
                                class_number=source.class_number,
                            )
                        )
                    else:
                        duplicate_listings_removed += 1
                    continue
                section_by_id[normalized.id] = normalized
                section_records.append(normalized)
                course_sections.append(normalized)
                physical_key = (
                    normalized.term_code or "",
                    normalized.schedule_number,
                )
                physical_to_listings[physical_key].append(normalized)
                _observe_section_coverage(coverage_counters, normalized)
                grading_counts[normalized.grading.value] += 1
                instruction_mode_counts[normalized.instruction_mode.value] += 1
                seat_status_counts[normalized.seat_status.value] += 1

            if not course_sections:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="normalized_course_sections_missing",
                        message=(
                            "No valid production section listings remained after normalization."
                        ),
                        subject=output.subject,
                        course_key=key,
                    )
                )
                continue
            course_records.append(
                _normalise_course(detail=detail, sections=course_sections)
            )
            complete_details += 1

    cross_listed = sum(1 for listings in physical_to_listings.values() if len(listings) > 1)
    for (physical_term, class_number), listings in physical_to_listings.items():
        course_codes = {listing.course_code for listing in listings}
        if len(course_codes) > 1:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="cross_listed_physical_section",
                    message=(
                        "One physical class number appears under multiple course listings: "
                        f"{', '.join(sorted(course_codes))}."
                    ),
                    class_number=class_number,
                    path=physical_term,
                )
            )

    counts = DatasetCounts(
        expected_subjects=len(expected_subjects),
        subject_files=len(outputs),
        complete_subjects=complete_subjects,
        discovered_courses=len(deep_inventory),
        complete_course_details=complete_details,
        course_section_listings=len(section_records),
        unique_physical_sections=len(physical_to_listings),
        cross_listed_physical_sections=cross_listed,
        duplicate_course_section_listings_removed=duplicate_listings_removed,
    )

    section_coverage_audit = CourseRepository(tuple(section_records)).coverage_audit()
    if section_coverage_audit.unaccounted_physical_sections:
        examples = "; ".join(section_coverage_audit.unaccounted_examples[:5])
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="physical_sections_unaccounted",
                message=(
                    f"{section_coverage_audit.unaccounted_physical_sections} physical SDSU "
                    "sections are not represented by any standalone or grouped ClassCatalog "
                    f"option. Examples: {examples or 'none available'}."
                ),
            )
        )

    errors = sum(issue.severity is ValidationSeverity.ERROR for issue in issues)
    warnings = sum(issue.severity is ValidationSeverity.WARNING for issue in issues)
    status = "passed" if errors == 0 else "failed"

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = output_dir / "validation-report.json"
    report_markdown_path = output_dir / "validation-report.md"
    inventory_path = output_dir / "inventory-diff.json"
    courses_path = output_dir / "courses.json"
    sections_path = output_dir / "sections.json"
    manifest_path = output_dir / "manifest.json"

    output_files: dict[str, str] = {
        "validation_report_json": str(report_json_path),
        "validation_report_markdown": str(report_markdown_path),
        "inventory_diff": str(inventory_path),
    }
    if errors == 0 or not config.strict:
        output_files.update(
            {
                "courses": str(courses_path),
                "sections": str(sections_path),
                "manifest": str(manifest_path),
            }
        )

    can_publish = errors == 0 or not config.strict
    installed_to: str | None = None

    if can_publish:
        ordered_courses = sorted(
            course_records,
            key=lambda item: (
                item.subject.casefold(),
                item.catalog_number.casefold(),
                item.id,
            ),
        )
        ordered_sections = sorted(
            section_records,
            key=lambda item: (
                item.subject.casefold(),
                item.catalog_number.casefold(),
                item.section_number.casefold(),
                item.schedule_number,
            ),
        )
        _write_model_array(courses_path, ordered_courses)
        _write_model_array(sections_path, ordered_sections)

        if errors == 0 and config.api_data_path is not None:
            config.api_data_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = config.api_data_path.with_suffix(
                f"{config.api_data_path.suffix}.tmp"
            )
            shutil.copyfile(sections_path, temporary)
            temporary.replace(config.api_data_path)
            installed_to = str(config.api_data_path)

    report = DatasetBuildReport(
        generated_at=generated_at,
        status=status,
        term=term,
        term_code=term_code,
        source_run_id=source_run_id,
        checkpoint_path=str(inputs.checkpoint_path) if inputs.checkpoint_path else None,
        subject_output_dir=str(inputs.subject_output_dir),
        deep_run_path=str(inputs.deep_run_path) if inputs.deep_run_path else None,
        discovery_run_path=(
            str(config.discovery_run_path) if config.discovery_run_path else None
        ),
        counts=counts,
        inventory_diff=inventory_diff,
        section_coverage_audit=section_coverage_audit,
        field_coverage=_coverage(coverage_counters),
        grading_counts=dict(grading_counts),
        instruction_mode_counts=dict(instruction_mode_counts),
        seat_status_counts=dict(seat_status_counts),
        issues=tuple(issues),
        errors=errors,
        warnings=warnings,
        output_files=output_files,
        api_data_installed_to=installed_to,
    )

    _write_json(inventory_path, inventory_diff.model_dump(mode="json"))
    _write_json(report_json_path, report.model_dump(mode="json"))
    atomic_write(report_markdown_path, _render_report_markdown(report))

    if can_publish:
        manifest_files = (
            _manifest_file(courses_path, base=output_dir),
            _manifest_file(sections_path, base=output_dir),
            _manifest_file(inventory_path, base=output_dir),
            _manifest_file(report_json_path, base=output_dir),
            _manifest_file(report_markdown_path, base=output_dir),
        )
        manifest = DatasetManifest(
            generated_at=generated_at,
            status=status,
            term=term,
            term_code=term_code,
            source_run_id=source_run_id,
            counts=counts,
            inventory_status=inventory_diff.status,
            files=manifest_files,
        )
        _write_json(manifest_path, manifest.model_dump(mode="json"))

    exit_code = 0 if errors == 0 else 1
    return DatasetBuildResult(report=report, exit_code=exit_code)
