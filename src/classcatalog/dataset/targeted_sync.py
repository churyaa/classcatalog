"""Safely replace *only selected* term/course offerings from a validated scrape.

This command is the safe companion to --course-keys-file. In contrast, using
classcatalog.dataset.main --install-api-data on a partial scrape replaces the
entire semester and MUST NOT be done.

Preview by default; --apply requires a stopped API worker for coherent runtime
caches. Backups are created before atomic updates, with rollback on failure.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from classcatalog.class_visibility import read_hidden, write_hidden
from classcatalog.dataset.install import (
    _load_sections, _section_sort_key, _validate_unique_sections, _write_sections,
)
from classcatalog.dataset.lifecycle import _prune_seat_cache, _prune_instructor_cache
from classcatalog.models import CourseSection
from classcatalog.repository import CourseRepository


def offering_key(section: CourseSection) -> str:
    """Same four-part identity as scraping.progress.course_detail_key."""
    return "|".join((
        section.subject, section.crse_id or section.course_code,
        section.crse_offer_nbr or "", section.acad_career or "",
    ))


def load_keys(path: Path) -> frozenset[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Keys file must be a nonempty JSON array")
    if not all(isinstance(s, str) and s.strip() and s.count("|") == 3 for s in raw):
        raise ValueError("Invalid course keys file; expected subject|course|offer|career strings")
    return frozenset(s.strip() for s in raw)


def _physical(section: CourseSection) -> tuple[str, str]:
    return ((section.term_code or section.term).strip(), section.schedule_number.strip())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup(path: Path, directory: Path) -> Path | None:
    if not path.is_file():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    if target.exists():
        raise ValueError(f"Backup collision: {target}")
    shutil.copy2(path, target)
    return target


def _preserve_metadata(path: Path, metadata: os.stat_result) -> None:
    os.chmod(path, stat.S_IMODE(metadata.st_mode))
    if hasattr(os, "chown"):
        try:
            os.chown(path, metadata.st_uid, metadata.st_gid)
        except PermissionError:
            # A non-root operator can preserve their own file's mode; they
            # cannot necessarily restore a root-owned group's ownership.
            if os.stat(path).st_uid != metadata.st_uid:
                raise


def plan(
    active: tuple[CourseSection, ...],
    incoming: tuple[CourseSection, ...],
    keys: frozenset[str],
    term: str,
) -> tuple[tuple[CourseSection, ...], dict[str, object], set[tuple[str, str]]]:
    if not term.strip() or not incoming:
        raise ValueError("Expected nonempty term and incoming course records")
    if any(section.term != term for section in incoming):
        raise ValueError("Incoming sections must belong ONLY to --term")
    incoming_keys = {offering_key(section) for section in incoming}
    if incoming_keys != keys:
        raise ValueError(
            "Incoming courses do not exactly match requested keys; "
            f"missing={sorted(keys - incoming_keys)}, unexpected={sorted(incoming_keys - keys)}"
        )
    current = tuple(
        section for section in active
        if section.term == term and offering_key(section) in keys
    )
    current_keys = {offering_key(section) for section in current}
    if current_keys != keys:
        raise ValueError(
            "The requested course identity is not in the active dataset; "
            f"missing={sorted(keys-current_keys)}. Handle rekeys separately."
        )
    term_codes = {section.term_code for section in current if section.term_code}
    incoming_codes = {section.term_code for section in incoming if section.term_code}
    if len(term_codes | incoming_codes) > 1:
        raise ValueError("STRM mismatch between old and incoming course sections")
    unaffected = tuple(
        section for section in active
        if not (section.term == term and offering_key(section) in keys)
    )
    candidate = tuple(sorted((*unaffected, *incoming), key=_section_sort_key))
    _validate_unique_sections(candidate, context="targeted replacement candidate")
    if not candidate:
        raise ValueError("Refusing to produce empty inventory")

    before_audit = CourseRepository(active).coverage_audit()
    after_audit = CourseRepository(candidate).coverage_audit()
    if (
        after_audit.unaccounted_physical_sections > before_audit.unaccounted_physical_sections
        or after_audit.unaccounted_course_section_listings > before_audit.unaccounted_course_section_listings
    ):
        raise ValueError(
            "Targeted replacement would worsen linked-component/section coverage; "
            "rescrape the full term instead."
        )
    before_ids = {_physical(section) for section in current}
    after_ids = {_physical(section) for section in incoming}
    report: dict[str, object] = {
        "term": term,
        "course_keys": sorted(keys),
        "current_listings": len(current),
        "incoming_listings": len(incoming),
        "other_listings_preserved": len(unaffected),
        "active_listings_after": len(candidate),
        "removed_physical_classes": sorted(f"{code}::{number}" for code, number in before_ids-after_ids),
        "added_physical_classes": sorted(f"{code}::{number}" for code, number in after_ids-before_ids),
        "coverage_unaccounted_before": before_audit.unaccounted_physical_sections,
        "coverage_unaccounted_after": after_audit.unaccounted_physical_sections,
    }
    return candidate, report, before_ids


def merge(
    *, active_path: Path, incoming_path: Path, keys_path: Path,
    term: str, apply: bool = False,
    seat_cache_path: Path | None = None,
    instructor_cache_path: Path | None = None,
    hidden_path: Path | None = None,
) -> dict[str, object]:
    if active_path.resolve() == incoming_path.resolve():
        raise ValueError("Incoming and active paths must be different files")
    # Insist on a passing build, not arbitrary hand-edited JSON.
    build_report_path = incoming_path.parent / "validation-report.json"
    build_report = json.loads(build_report_path.read_text(encoding="utf-8"))
    if build_report.get("status") != "passed" or build_report.get("term") != term:
        raise ValueError("Incoming production build must have a passing validation report for this term")
    active_before_hash = _digest(active_path)
    active = _load_sections(active_path)
    incoming = _load_sections(incoming_path)
    keys = load_keys(keys_path)
    candidate, report, affected_old_keys = plan(active, incoming, keys, term)
    report["active_sha256_before"] = active_before_hash
    report["incoming_report"] = str(build_report_path)
    hidden_path = hidden_path or active_path.with_name("hidden_classes.json")
    hidden = read_hidden(hidden_path)
    to_clear = {key for key, item in hidden.items()
                if (str(item.get("term")) == term and
                    (tuple(key.split("::", 1)) in affected_old_keys))}
    report["existing_hidden_overlays_to_clear"] = sorted(to_clear)
    report["applied"] = False
    if not apply:
        return report
    if _digest(active_path) != active_before_hash:
        raise ValueError("Active dataset changed during preview; retry before applying")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backups_dir = active_path.parent / "backups" / f"targeted-sync-{stamp}-{os.getpid()}"
    touched = [active_path, hidden_path]
    if seat_cache_path is not None:
        touched.append(seat_cache_path)
    if instructor_cache_path is not None:
        touched.append(instructor_cache_path)
    # Check duplicate filenames before copying into the same backup folder.
    if len({p.name for p in touched}) != len(touched):
        raise ValueError("Backup paths would collide: use distinct files")
    saved = {path: _backup(path, backups_dir) for path in touched}
    original_stats = {path: path.stat() for path in touched if path.is_file()}
    try:
        _write_sections(active_path, candidate)
        _preserve_metadata(active_path, original_stats[active_path])
        if to_clear:
            write_hidden(hidden_path, {key: value for key, value in hidden.items() if key not in to_clear})
            if hidden_path in original_stats:
                _preserve_metadata(hidden_path, original_stats[hidden_path])
        if seat_cache_path is not None:
            summary = _prune_seat_cache(seat_cache_path, candidate)
            if summary.error:
                raise ValueError(f"Seat cache pruning failed: {summary.error}")
            if seat_cache_path in original_stats:
                _preserve_metadata(seat_cache_path, original_stats[seat_cache_path])
            report["seat_cache_pruned_records"] = summary.removed_records
        if instructor_cache_path is not None:
            summary = _prune_instructor_cache(instructor_cache_path, candidate)
            if summary.error:
                raise ValueError(f"Instructor cache pruning failed: {summary.error}")
            if instructor_cache_path in original_stats:
                _preserve_metadata(instructor_cache_path, original_stats[instructor_cache_path])
            report["instructor_cache_pruned_records"] = summary.removed_records
        reloaded = _load_sections(active_path)
        if len(reloaded) != len(candidate) or {section.id for section in reloaded} != {section.id for section in candidate}:
            raise ValueError("Post-install verification failed")
    except Exception:
        # Restore *all* touched files on any failed intermediate write.
        for path, backup in saved.items():
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                shutil.copy2(backup, path)
        raise
    report["applied"] = True
    report["backup_dir"] = str(backups_dir)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--incoming", type=Path, required=True)
    parser.add_argument("--keys-file", type=Path, required=True)
    parser.add_argument("--term", required=True)
    parser.add_argument("--seat-cache-path", type=Path)
    parser.add_argument("--instructor-cache-path", type=Path)
    parser.add_argument("--hidden-path", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply after preview with API stopped")
    args = parser.parse_args()
    try:
        report = merge(
            active_path=args.active, incoming_path=args.incoming,
            keys_path=args.keys_file, term=args.term, apply=args.apply,
            seat_cache_path=args.seat_cache_path,
            instructor_cache_path=args.instructor_cache_path,
            hidden_path=args.hidden_path,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"targeted_sync_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
