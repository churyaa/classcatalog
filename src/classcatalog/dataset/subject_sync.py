"""CS-style safe Spring 2027 subject-inventory sync.

Adapted from classcatalog.dataset.targeted_sync: preview is read-only, apply has
backup+rollback, and all terms/subjects outside the selected scope are retained.
Unlike course-key sync, this handles added/deleted courses and offer-number rekeys.

Local usage:
    python -m classcatalog.dataset.subject_sync --active ... --incoming ... \
      --checkpoint ... --term 'Spring 2027' --expected-subjects 48

IMPORTANT: Do not apply to a file being read by a running backend. Preview and
review the removals, provenance, and API validation first.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

from classcatalog.class_visibility import read_hidden, write_hidden
from classcatalog.dataset.install import (
    _load_sections, _section_sort_key, _validate_unique_sections, _write_sections,
)
from classcatalog.dataset.lifecycle import _prune_seat_cache, _prune_instructor_cache
from classcatalog.dataset.targeted_sync import _backup, _digest, _physical, _preserve_metadata
from classcatalog.models import CourseSection
from classcatalog.repository import CourseRepository


SEAT_FIELDS = (
    "seat_status", "seats_available", "seat_capacity", "seats_enrolled",
    "waitlist_available", "waitlist_capacity", "waitlist_total", "seat_updated_at",
)


def load_subjects(checkpoint_path: Path, expected_count: int) -> frozenset[str]:
    raw = json.loads(checkpoint_path.read_text(encoding="utf-8-sig"))
    entries = raw.get("subjects")
    if not isinstance(entries, list):
        raise ValueError("Checkpoint has no subjects list")
    subjects: list[str] = []
    for entry in entries:
        if entry.get("status") != "complete":
            raise ValueError(f"Incomplete subject in checkpoint: {entry.get('subject')}")
        subject = str(entry.get("subject") or "").strip().upper()
        if not subject:
            raise ValueError("Blank checkpoint subject")
        subjects.append(subject)
    if len(subjects) != expected_count or len(set(subjects)) != expected_count:
        raise ValueError(
            f"Expected exactly {expected_count} distinct complete subjects, "
            f"got {len(subjects)} entries/{len(set(subjects))} unique"
        )
    return frozenset(subjects)


def in_scope(section: CourseSection, *, term: str, subjects: frozenset[str]) -> bool:
    return section.term.strip() == term and section.subject.strip().upper() in subjects


def _identity(section: CourseSection) -> tuple[str, str, str, str]:
    return (
        section.subject.strip().upper(),
        " ".join(section.course_code.strip().upper().split()),
        (section.crse_id or "").strip(),
        (section.acad_career or "").strip().upper(),
    )


def _identity_details(section: CourseSection) -> dict[str, str]:
    """Show ALL meaningful fields when a physical class has changed identity."""
    return {
        "id": section.id,
        "subject": section.subject,
        "course_code": section.course_code,
        "crse_id": section.crse_id or "",
        "crse_offer_nbr": section.crse_offer_nbr or "",
        "acad_career": section.acad_career or "",
        "source_course_key": section.source_course_key or "",
        "section_number": section.section_number or "",
    }




def load_approved_rekeys(path: Path | None) -> dict[tuple[str, str], dict[str, object]]:
    """Read explicit, fully specified identity rekey approvals. Never auto-approve.

    Every approval must match BOTH complete old/new identity snapshots exactly.
    An approval authorizes a stable ID for the same actual class/course with a
    changed career, but never authorizes copying old instructor/meeting data.
    """
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError("Approved rekeys must be a JSON array")
    approvals: dict[tuple[str, str], dict[str, object]] = {}
    expected = {"term_code", "schedule_number", "before", "after", "review_note", "restore_historical_section_number"}
    for record in raw:
        if not isinstance(record, dict) or set(record) != expected:
            raise ValueError("Each approved rekey must have term_code, schedule_number, before, after, review_note, restore_historical_section_number")
        if type(record["restore_historical_section_number"]) is not bool:
            raise ValueError("restore_historical_section_number must be a boolean")
        note = record.get("review_note")
        if not isinstance(note, str) or len(note.strip()) < 15 or "UNREVIEWED" in note.upper():
            raise ValueError("Each approved rekey requires a meaningful review_note; UNREVIEWED is rejected")
        if not isinstance(record["before"], dict) or not isinstance(record["after"], dict):
            raise ValueError("Approved rekey old/new snapshots must be objects")
        key = (str(record["term_code"]), str(record["schedule_number"]))
        if not all(key) or key in approvals:
            raise ValueError(f"Duplicate or empty approved physical class: {key}")
        approvals[key] = record
    return approvals


def _approved_career_rekey(
    before: CourseSection, after: CourseSection,
    conflict: dict[str, object], approvals: dict[tuple[str, str], dict[str, object]],
) -> bool:
    """Only explicit UGRD/GLBL-style reclassification, never another course."""
    key = _physical(after)
    approval = approvals.get(key)
    if approval is None:
        return False
    actual = {field: value for field, value in approval.items() if field not in ("review_note", "restore_historical_section_number")}
    if actual != conflict:
        raise ValueError(f"Approval does not match current source/baseline identity for {key}")
    if approval["restore_historical_section_number"] and (
        not before.section_number or after.section_number
    ):
        raise ValueError(
            f"Historical section number can only be restored when old is present and new is blank: {key}"
        )
    if (
        before.subject.strip().upper() != after.subject.strip().upper()
        or before.course_code.strip().upper() != after.course_code.strip().upper()
        or not before.crse_id
        or before.crse_id != after.crse_id
        or before.acad_career == after.acad_career
    ):
        raise ValueError(f"Approval cannot waive a course ID/code/subject mismatch: {key}")
    return True


def _index_physical(rows: tuple[CourseSection, ...], label: str) -> dict[tuple[str, str], CourseSection]:
    index: dict[tuple[str, str], CourseSection] = {}
    for section in rows:
        key = _physical(section)
        if not all(key):
            raise ValueError(f"{label}: blank physical identity: {section.id}")
        if key in index:
            raise ValueError(f"{label}: duplicate physical class: {key}")
        index[key] = section
    return index


def _merge_retained(
    before: CourseSection, after: CourseSection,
    *, counts: Counter[str], id_remap: dict[str, str],
) -> CourseSection:
    if _identity(before) != _identity(after):
        raise ValueError(
            "Physical class changed subject/course/career: "
            f"{_physical(before)} {before.course_code!r} -> {after.course_code!r}; "
            "must review this reuse manually"
        )
    updates: dict[str, object] = {}
    if before.id != after.id:
        updates["id"] = before.id
        id_remap[after.id] = before.id
        counts["stable_ids_preserved"] += 1
        if (before.crse_id or "") != (after.crse_id or ""):
            counts["stable_ids_with_crse_id_rekey"] += 1
    same_crse_id = (before.crse_id or "") == (after.crse_id or "")
    if same_crse_id:
        # A missing value in the new scrape is not evidence that an existing
        # meeting was cancelled. Preserve it for review, not as verified current.
        for field in ("instructor", "meetings", "section_number"):
            old_value = getattr(before, field)
            new_value = getattr(after, field)
            if old_value and not new_value:
                updates[field] = old_value
                counts[f"carried_forward_{field}"] += 1
    # Preserve newer seat snapshot only if both timestamps exist and old is newer.
    old_seat_time = None
    new_seat_time = None
    try:
        if before.seat_updated_at and after.seat_updated_at:
            old_seat_time = datetime.fromisoformat(before.seat_updated_at.replace("Z", "+00:00")).astimezone(UTC)
            new_seat_time = datetime.fromisoformat(after.seat_updated_at.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        pass
    if old_seat_time is not None and new_seat_time is not None and old_seat_time > new_seat_time:
        for field in SEAT_FIELDS:
            updates[field] = getattr(before, field)
        counts["newer_seat_snapshots_preserved"] += 1
    return after.model_copy(update=updates) if updates else after


def _rewire_linked_components(
    section: CourseSection, id_remap: dict[str, str], counts: Counter[str]
) -> CourseSection:
    if not section.linked_components or not id_remap:
        return section
    linked = []
    changed = False
    for component in section.linked_components:
        replacement = id_remap.get(component.id)
        if replacement and replacement != component.id:
            linked.append(component.model_copy(update={"id": replacement}))
            changed = True
            counts["linked_component_ids_rewritten"] += 1
        else:
            linked.append(component)
    return section.model_copy(update={"linked_components": tuple(linked)}) if changed else section


def plan(
    active: tuple[CourseSection, ...],
    incoming: tuple[CourseSection, ...],
    *, term: str, subjects: frozenset[str],
    approved_rekeys: dict[tuple[str, str], dict[str, object]] | None = None,
) -> tuple[tuple[CourseSection, ...], dict[str, object], set[tuple[str, str]]]:
    if not term.strip() or not subjects or not incoming:
        raise ValueError("Term, subjects and incoming data are required")
    if any(not in_scope(s, term=term, subjects=subjects) for s in incoming):
        raise ValueError("Incoming contains a term or subject outside the declared scope")
    incoming_subjects = {s.subject.strip().upper() for s in incoming}
    if incoming_subjects != subjects:
        raise ValueError(
            "Incoming does not cover every checkpoint subject: "
            f"missing={sorted(subjects-incoming_subjects)} extra={sorted(incoming_subjects-subjects)}"
        )
    term_codes = {s.term_code for s in incoming if s.term_code}
    if len(term_codes) != 1 or term_codes != {"2273"}:
        raise ValueError(f"Expected Spring 2027 term code 2273; got {sorted(term_codes)}")
    _validate_unique_sections(active, context="active baseline")
    _validate_unique_sections(incoming, context="incoming 48-subject inventory")
    active_index = _index_physical(active, "active baseline")
    incoming_index = _index_physical(incoming, "incoming")
    current = tuple(s for s in active if in_scope(s, term=term, subjects=subjects))
    preserved = tuple(s for s in active if not in_scope(s, term=term, subjects=subjects))
    old_index = _index_physical(current, "old update scope")
    preserved_index = _index_physical(preserved, "preserved outside scope")
    collisions = set(incoming_index) & set(preserved_index)
    if collisions:
        raise ValueError(f"Incoming conflicts with preserved classes: {sorted(collisions)[:10]}")
    if not current:
        raise ValueError("No existing classes in update scope; refusing surprising replacement")
    approvals = approved_rekeys or {}
    id_remap: dict[str, str] = {}
    counts: Counter[str] = Counter()
    identity_conflicts: list[dict[str, object]] = []
    approved_identity_conflicts: list[dict[str, object]] = []
    approved_keys: set[tuple[str, str]] = set()
    replacement: list[CourseSection] = []
    for row in incoming:
        old = old_index.get(_physical(row))
        if old is None:
            replacement.append(row)
            continue
        if _identity(old) != _identity(row):
            conflict = {
                "term_code": row.term_code or row.term,
                "schedule_number": row.schedule_number,
                "before": _identity_details(old),
                "after": _identity_details(row),
            }
            identity_conflicts.append(conflict)
            if _approved_career_rekey(old, row, conflict, approvals):
                # Preserve an existing opaque ID to protect saved schedules.
                # Fresh career, offer number and source key stay from the new scrape.
                # Do NOT carry historical meetings/instructor across career changes.
                approved_keys.add(_physical(row))
                approved_identity_conflicts.append(conflict)
                if old.id != row.id:
                    id_remap[row.id] = old.id
                    counts["stable_ids_preserved"] += 1
                    counts["approved_career_rekey_ids_preserved"] += 1
                updates = {"id": old.id}
                if approvals[_physical(row)]["restore_historical_section_number"]:
                    # Explicit, class-specific review only. This is historical metadata,
                    # not a statement that SDSU's current section number was verified.
                    updates["section_number"] = old.section_number
                    counts["reviewed_career_rekey_section_numbers_carried"] += 1
                replacement.append(row.model_copy(update=updates))
            else:
                # Retain the new row unchanged for read-only preview only.
                replacement.append(row)
            continue
        replacement.append(_merge_retained(old, row, counts=counts, id_remap=id_remap))
    unused_approvals = set(approvals) - approved_keys
    if unused_approvals:
        raise ValueError(f"Approvals were not used by exact career rekeys: {sorted(unused_approvals)}")
    replacement = [_rewire_linked_components(s, id_remap, counts) for s in replacement]
    replacement_index = _index_physical(tuple(replacement), "reconciled incoming")
    candidate = tuple(sorted((*preserved, *replacement), key=_section_sort_key))
    # Reconstruct with schema validation rather than trusting Pydantic model_copy.
    candidate = tuple(CourseSection.model_validate(s.model_dump(mode="json")) for s in candidate)
    _validate_unique_sections(candidate, context="subject-sync merged candidate")
    _index_physical(candidate, "subject-sync merged candidate")
    if not candidate:
        raise ValueError("Refusing empty inventory")
    before_audit = CourseRepository(active).coverage_audit()
    after_audit = CourseRepository(candidate).coverage_audit()
    if (
        after_audit.unaccounted_physical_sections > before_audit.unaccounted_physical_sections
        or after_audit.unaccounted_course_section_listings > before_audit.unaccounted_course_section_listings
    ):
        raise ValueError(
            "Subject sync would worsen section/linked-component coverage: "
            f"before={before_audit.unaccounted_physical_sections}, "
            f"after={after_audit.unaccounted_physical_sections}"
        )
    before_keys, after_keys = set(old_index), set(incoming_index)
    common = before_keys & after_keys
    regressions = Counter()
    regression_details: list[dict[str, str]] = []
    for key in common:
        before = old_index[key]
        after = replacement_index[key]
        for field in ("instructor", "meetings", "section_number"):
            if getattr(before, field) and not getattr(after, field):
                regressions[field] += 1
                regression_details.append({
                    "term_code": key[0],
                    "schedule_number": key[1],
                    "field": field,
                    "old_course_key": before.source_course_key or "",
                    "new_course_key": after.source_course_key or "",
                    "old_id": before.id,
                    "new_id": after.id,
                    "crse_id_changed": str((before.crse_id or "") != (after.crse_id or "")),
                    "old_section_number": before.section_number if field == "section_number" else "",
                    "new_section_number": after.section_number if field == "section_number" else "",
                })
    report: dict[str, object] = {
        "term": term,
        "term_code": "2273",
        "subjects": sorted(subjects),
        "original_sections": len(active),
        "old_target_sections": len(current),
        "retained_physical_classes": len(common),
        "retained_same_course_identity": len(common) - len(identity_conflicts),
        "identity_conflict_count": len(identity_conflicts),
        "approved_identity_rekey_count": len(approved_keys),
        "unapproved_identity_conflict_count": len(identity_conflicts) - len(approved_keys),
        "approved_identity_rekeys": approved_identity_conflicts,
        "identity_conflicts": sorted(
            identity_conflicts,
            key=lambda item: (str(item["term_code"]), str(item["schedule_number"])),
        ),
        "added_physical_classes": len(after_keys-before_keys),
        "removed_physical_classes": len(before_keys-after_keys),
        "removed_list": [f"{a}::{b}" for a,b in sorted(before_keys-after_keys)],
        "preserved_outside_scope": len(preserved),
        "candidate_sections": len(candidate),
        "stable_ids_preserved": counts["stable_ids_preserved"],
        "reviewed_career_rekey_section_numbers_carried": counts["reviewed_career_rekey_section_numbers_carried"],
        "stable_ids_with_crse_id_rekey": counts["stable_ids_with_crse_id_rekey"],
        "metadata_carried_forward": {
            f: counts[f"carried_forward_{f}"]
            for f in ("instructor", "meetings", "section_number")
        },
        "metadata_regressions_remaining": dict(regressions),
        "metadata_regression_details": sorted(regression_details, key=lambda x: (x["term_code"], x["schedule_number"], x["field"])),
        "newer_seat_snapshots_preserved": counts["newer_seat_snapshots_preserved"],
        "linked_component_ids_rewritten": counts["linked_component_ids_rewritten"],
        "coverage_unaccounted_before": before_audit.unaccounted_physical_sections,
        "coverage_unaccounted_after": after_audit.unaccounted_physical_sections,
        "note": "Carried-forward metadata is historical, not independently verified current SDSU data.",
        "applied": False,
    }
    # Structural guarantee: all records outside scope survive unchanged.
    before_preserved = { _physical(s): s.model_dump(mode="json") for s in preserved }
    after_preserved = { _physical(s): s.model_dump(mode="json") for s in candidate if not in_scope(s, term=term, subjects=subjects) }
    if before_preserved != after_preserved:
        raise ValueError("Out-of-scope records changed unexpectedly")
    return candidate, report, before_keys


def merge(
    *, active_path: Path, incoming_path: Path, checkpoint_path: Path,
    term: str, expected_subjects: int = 48, apply: bool = False,
    expected_active_sha256: str | None = None,
    max_removals: int = 0, max_metadata_regressions: int = 0,
    seat_cache_path: Path | None = None, instructor_cache_path: Path | None = None,
    hidden_path: Path | None = None,
    approved_rekeys_path: Path | None = None,
) -> dict[str, object]:
    if active_path.resolve() == incoming_path.resolve():
        raise ValueError("Active and incoming must be different paths")
    report_path = incoming_path.parent / "validation-report.json"
    build_report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if build_report.get("status") != "passed" or build_report.get("term") != term:
        raise ValueError("Incoming build must have a passing validation report for term")
    subjects = load_subjects(checkpoint_path, expected_subjects)
    before_hash = _digest(active_path)
    incoming_hash = _digest(incoming_path)
    active = _load_sections(active_path)
    incoming = _load_sections(incoming_path)
    approvals = load_approved_rekeys(approved_rekeys_path)
    candidate, report, affected_keys = plan(
        active, incoming, term=term, subjects=subjects, approved_rekeys=approvals,
    )
    report["active_sha256_before"] = before_hash
    report["incoming_sha256"] = incoming_hash
    report["approved_rekeys_sha256"] = _digest(approved_rekeys_path) if approved_rekeys_path else None
    report["validation_report"] = str(report_path)
    hidden_path = hidden_path or active_path.with_name("hidden_classes.json")
    hidden = read_hidden(hidden_path)
    removed = set(report["removed_list"])
    to_clear = sorted(k for k, v in hidden.items() if k in removed and str(v.get("term")) == term)
    report["removed_hidden_overlays_to_clear"] = to_clear
    if not apply:
        return report
    if report["unapproved_identity_conflict_count"]:
        raise ValueError(
            "Apply blocked: physical class identity conflicts need manual review; "
            f"count={report['unapproved_identity_conflict_count']}; "
            "see preview identity_conflicts. No file was modified."
        )
    if not expected_active_sha256 or before_hash.lower() != expected_active_sha256.lower():
        raise ValueError("Apply requires matching --expected-active-sha256 from preview")
    if report["removed_physical_classes"] > max_removals:
        raise ValueError("Removal count exceeds --max-removals; review first")
    if sum(report["metadata_regressions_remaining"].values()) > max_metadata_regressions:
        raise ValueError("Unresolved missing metadata exceeds --max-metadata-regressions")
    if (approved_rekeys_path and _digest(approved_rekeys_path) != report["approved_rekeys_sha256"]):
        raise ValueError("Approval file changed during validation")
    if _digest(active_path) != before_hash or _digest(incoming_path) != incoming_hash:
        raise ValueError("Active or incoming file changed since validation; retry")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backups_dir = active_path.parent / "backups" / f"subject-sync-{stamp}-{os.getpid()}"
    touched = [active_path, hidden_path]
    for path in (seat_cache_path, instructor_cache_path):
        if path is not None:
            touched.append(path)
    if len({p.resolve() for p in touched}) != len(touched) or len({p.name for p in touched}) != len(touched):
        raise ValueError("Touched backup paths collide")
    saved = {p: _backup(p, backups_dir) for p in touched}
    original_stats = {p: p.stat() for p in touched if p.is_file()}
    try:
        _write_sections(active_path, candidate)
        _preserve_metadata(active_path, original_stats[active_path])
        if to_clear:
            write_hidden(hidden_path, {k: v for k,v in hidden.items() if k not in to_clear})
            if hidden_path in original_stats:
                _preserve_metadata(hidden_path, original_stats[hidden_path])
        if seat_cache_path is not None:
            summary = _prune_seat_cache(seat_cache_path, candidate)
            if summary.error:
                raise ValueError(f"Seat-cache pruning failed: {summary.error}")
            report["seat_cache_pruned_records"] = summary.removed_records
            if seat_cache_path in original_stats:
                _preserve_metadata(seat_cache_path, original_stats[seat_cache_path])
        if instructor_cache_path is not None:
            summary = _prune_instructor_cache(instructor_cache_path, candidate)
            if summary.error:
                raise ValueError(f"Instructor-cache pruning failed: {summary.error}")
            report["instructor_cache_pruned_records"] = summary.removed_records
            if instructor_cache_path in original_stats:
                _preserve_metadata(instructor_cache_path, original_stats[instructor_cache_path])
        reloaded = _load_sections(active_path)
        if len(reloaded) != len(candidate) or any(
            a.model_dump(mode="json") != b.model_dump(mode="json")
            for a, b in zip(reloaded, candidate)
        ):
            raise ValueError("Post-install full-content verification failed")
    except Exception:
        import shutil
        for p, backup in saved.items():
            if backup is None:
                p.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, p)
        raise
    report["applied"] = True
    report["backup_dir"] = str(backups_dir)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--term", default="Spring 2027")
    parser.add_argument("--expected-subjects", type=int, default=48)
    parser.add_argument("--seat-cache-path", type=Path)
    parser.add_argument("--instructor-cache-path", type=Path)
    parser.add_argument("--hidden-path", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-active-sha256")
    parser.add_argument("--max-removals", type=int, default=0)
    parser.add_argument("--max-metadata-regressions", type=int, default=0)
    parser.add_argument("--approved-rekeys", type=Path, help="Explicit reviewed identity-rekey JSON file")
    args = parser.parse_args()
    try:
        result = merge(
            active_path=args.active, incoming_path=args.incoming,
            checkpoint_path=args.checkpoint, term=args.term,
            expected_subjects=args.expected_subjects, apply=args.apply,
            expected_active_sha256=args.expected_active_sha256,
            max_removals=args.max_removals,
            max_metadata_regressions=args.max_metadata_regressions,
            seat_cache_path=args.seat_cache_path,
            instructor_cache_path=args.instructor_cache_path,
            hidden_path=args.hidden_path,
            approved_rekeys_path=args.approved_rekeys,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"subject_sync_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
