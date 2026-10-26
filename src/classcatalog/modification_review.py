"""Admin-only, manual review for SDSU schedule/instructor/location changes.

A lightweight course-grid scan nominates changes; a new live SDSU subject +
class-modal read verifies them before Apply is allowed. No automatic edits.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import ExitStack
from pathlib import Path
from threading import RLock, Thread
from typing import Callable
from uuid import uuid4
import json
import logging
import os
import shutil

from classcatalog.models import CourseSection, InstructionMode, Meeting
from classcatalog.repository import CourseRepository
from classcatalog.replacement_review import ReplacementReviewService  # CLASSCATALOG_REPLACEMENT_IMPORT_V1
from classcatalog.scraping.class_detail import (
    merge_class_information_records, parse_class_information_page,
)
from classcatalog.scraping.parser import parse_course_info_page
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession
from classcatalog.seats import is_course_info_url

LOG = logging.getLogger(__name__)
VERIFY_TTL_SECONDS = 3600
FIELDS = ("instructor", "meetings", "location", "instruction_mode")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _key(section: CourseSection) -> str:
    return f"{section.term_code or section.term}::{section.schedule_number}"


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _signature(section: CourseSection) -> str:
    return json.dumps({field: getattr(section, field) if field != "meetings" else
                       [meeting.model_dump(mode="json") for meeting in section.meetings]
                       for field in FIELDS}, default=str, sort_keys=True)


def _display(value: object) -> str:
    if isinstance(value, (list, tuple)):
        if not value:
            return "No scheduled meetings"
        if all(isinstance(v, Meeting) for v in value):
            return "; ".join(
                f"{'/'.join(day.value.title() for day in m.days) or 'TBA'} "
                f"{m.start_time.strftime('%I:%M %p').lstrip('0') if m.start_time else 'TBA'} - "
                f"{m.end_time.strftime('%I:%M %p').lstrip('0') if m.end_time else 'TBA'}"
                f"{(' @ ' + m.location) if m.location else ''}"
                for m in value
            )
    if isinstance(value, InstructionMode):
        return value.value.replace("_", " ").title()
    return str(value or "Not assigned")


def _changed(section: CourseSection, proposal: dict[str, object]) -> dict[str, dict[str, str]]:
    changes = {}
    for field, next_value in proposal.items():
        previous = getattr(section, field)
        if field == "instructor":
            if _norm(str(previous or "")).casefold() == _norm(str(next_value)).casefold():
                continue
        elif field == "location":
            if _norm(str(previous or "")).casefold() == _norm(str(next_value)).casefold():
                continue
        elif previous == next_value:
            continue
        changes[field] = {"before": _display(previous), "after": _display(next_value)}
    return changes


def _from_option(section: CourseSection, option) -> dict[str, object]:
    """Only trust explicitly populated values; never erase unknown data."""
    proposal: dict[str, object] = {}
    if _norm(option.instructor) and _norm(option.instructor).casefold() not in {"staff", "tba", "to be announced"}:
        proposal["instructor"] = _norm(option.instructor)
    if _norm(option.location):
        proposal["location"] = _norm(option.location)
    # Course grid can summarize rather than enumerate multi-meeting classes.
    # Never replace a complex meeting schedule with one summary row.
    if len(section.meetings) == 1 and option.days and option.start_time and option.end_time:
        old = section.meetings[0]
        proposal["meetings"] = (old.model_copy(update={
            "days": tuple(option.days), "start_time": option.start_time,
            "end_time": option.end_time,
            "location": _norm(option.location) or old.location,
            "meeting_dates": _norm(option.meeting_dates) or old.meeting_dates,
            "instructor": _norm(option.instructor) or old.instructor,
        }),)
    return proposal


def _verified_fields(section: CourseSection, option, details) -> dict[str, object]:
    proposal = _from_option(section, option)
    if details is None:
        return proposal
    if details.meetings:
        meetings = tuple(Meeting(
            days=tuple(row.days), start_time=row.start_time, end_time=row.end_time,
            location=_norm(row.room) or (_norm(option.location) or None),
            meeting_dates=_norm(row.meeting_dates) or (_norm(option.meeting_dates) or None),
            instructor=_norm(row.instructor) or (_norm(option.instructor) or None),
        ) for row in details.meetings)
        if meetings and all(m.days and m.start_time and m.end_time for m in meetings):
            proposal["meetings"] = meetings
        candidate_instructor = next((_norm(m.instructor) for m in meetings if _norm(m.instructor)), "")
        if candidate_instructor and candidate_instructor.casefold() not in {"staff", "tba", "to be announced"}:
            proposal["instructor"] = candidate_instructor
        rooms = {_norm(m.location) for m in meetings if _norm(m.location)}
        if len(rooms) == 1:
            proposal["location"] = rooms.pop()
    if _norm(details.location):
        proposal["location"] = _norm(details.location)
    if details.instruction_mode and details.instruction_mode != InstructionMode.OTHER:
        proposal["instruction_mode"] = details.instruction_mode
    return proposal


class ModificationReviewService:
    def __init__(
        self, repository: CourseRepository, data_path: Path, *,
        fetch_grid: Callable[[CourseSection], object] | None = None,
        verify_live: Callable[[CourseSection], tuple[object, object]] | None = None,
    ) -> None:
        self.repository = repository
        self.data_path = data_path
        self.replacements = ReplacementReviewService(repository, data_path)
        self.fetch_grid = fetch_grid or self._fetch_grid_live
        self._custom_grid_fetch = fetch_grid is not None
        self.verify_live = verify_live or self._verify_live
        self._lock = RLock()
        self._candidates: dict[str, dict[str, object]] = {}
        self._ignored: set[str] = set()
        self._scanning = False
        self._scan: dict[str, object] = {"running": False, "scanned": 0, "total": 0, "errors": 0, "term": None, "course_code": None}

    def _groups(self) -> dict[str, list[CourseSection]]:
        groups: dict[str, list[CourseSection]] = {}
        for section in self.repository.sections:
            groups.setdefault(_key(section), []).append(section)
        return groups

    def overview(self) -> dict[str, object]:
        with self._lock:
            groups = self._groups()
            items = []
            for key, row in self._candidates.items():
                matches = groups.get(key)
                if not matches or key in self._ignored:
                    continue
                current = matches[0]
                item = dict(row)
                item["can_apply"] = bool(
                    row.get("status") == "verified"
                    and not row.get("crosslisted")
                    and self._current_confirmation(key, current)
                )
                if row.get("status") == "verified" and not item["can_apply"]:
                    item["status"] = "needs_reverification"
                items.append(item)
            items.sort(key=lambda x: (x.get("term", ""), x.get("course_code", ""), x.get("class_number", "")))
            # CLASSCATALOG_DELETED_HISTORY_V1: keep semesters with only
            # archived deletions selectable in the Admin semester picker.
            terms = {s.term for s in self.repository.sections}
            terms.update(s.term for s in self.replacements.deleted_history.sources())
            return {"scan": dict(self._scan), "items": items[:500], "total_flagged": len(items),
                    "available_terms": sorted(terms),
                    "replacements": self.replacements.history()}

    def _current_confirmation(self, key: str, section: CourseSection) -> bool:
        row = self._candidates.get(key, {})
        at = _date(str(row.get("verified_at") or ""))
        return bool(at and 0 <= (_now() - at).total_seconds() <= VERIFY_TTL_SECONDS
                    and row.get("fingerprint") == _signature(section))

    def request_scan(self, term: str, course_code: str = "") -> dict[str, object]:
        normalized = _norm(course_code).upper()
        with self._lock:
            if self._scanning:
                raise ValueError("A modified-class scan is already running.")
            groupings = self._groups()
            sources: dict[str, CourseSection] = {}
            for groups in groupings.values():
                section = groups[0]
                if section.term != term or (normalized and _norm(section.course_code).upper() != normalized):
                    continue
                if is_course_info_url(section.source_url):
                    sources[f"{section.term}::{section.source_url}"] = section
            # Include archived manually removed classes even when their
            # course has no remaining active listings. Use independent keying so
            # archived and active sources of one course both get inspected.
            for deleted in self.replacements.historical_sources(term, normalized):
                sources[f"deleted::{deleted.term}::{deleted.schedule_number}"] = deleted
            if not sources:
                raise ValueError("No current or archived SDSU course sources found for that term/course.")
            self._scanning = True
            self._scan = {"running": True, "scanned": 0, "total": len(sources), "errors": 0,
                          "term": term, "course_code": normalized or None, "last_error": None}
        Thread(target=self._scan_worker, args=(tuple(sources.values()),), daemon=True,
               name="classcatalog-modification-scan").start()
        return {"accepted": True, "course_pages": len(sources)}

    def _scan_worker(self, sources: tuple[CourseSection, ...]) -> None:
        try:
            # Reuse one paced PeopleSoft session across the scan, rather than
            # bootstrapping the public landing page for every single course.
            with ExitStack() as stack:
                client = None
                if not self._custom_grid_fetch and any(
                    not self.replacements.is_deleted_source(src) for src in sources
                ):
                    client = stack.enter_context(SdsuPeopleSoftSession(SdsuHttpConfig()))
                    client.bootstrap()
                for source in sources:
                    try:
                        if self.replacements.is_deleted_source(source):
                            # Fresh subject searches are performed by the replacement
                            # verifier, never by the obsolete saved course URL.
                            self.replacements.consider_deleted(source)
                            continue
                        parsed = self.fetch_grid(source) if client is None else self._fetch_grid_from_session(client, source)
                        if parsed.course_code != source.course_code or not parsed.options_complete:
                            raise ValueError("Course grid mismatch or incomplete options; no modifications accepted.")
                        self.replacements.consider_grid(source, parsed)
                        options = {option.class_number: option for option in parsed.options}
                        for key, group in self._groups().items():
                            section = group[0]
                            if section.term != source.term or section.course_code != source.course_code or section.source_url != source.source_url:
                                continue
                            option = options.get(section.schedule_number)
                            if option is None:
                                continue  # Missing sections belong in the removal-review queue.
                            proposed = _from_option(section, option)
                            changes = _changed(section, proposed)
                            with self._lock:
                                if key in self._ignored:
                                    continue
                                old = self._candidates.get(key)
                                if changes:
                                    self._candidates[key] = {
                                        "key": key, "term": section.term,
                                        "course_code": section.course_code,
                                        "class_number": section.schedule_number,
                                        "source_url": section.source_url,
                                        "status": "needs_verification", "changes": changes,
                                        "crosslisted": len({s.course_code for s in group}) > 1,
                                    }
                                elif old and old.get("status") == "needs_verification":
                                    self._candidates.pop(key, None)
                    except Exception as exc:
                        LOG.warning("modified_scan_source_failed course=%s error=%s", source.course_code, exc)
                        self.replacements.consider_grid(source, None)
                        with self._lock:
                            self._scan["errors"] = int(self._scan["errors"]) + 1
                            self._scan["last_error"] = f"{source.course_code}: {type(exc).__name__}: {exc}"[:300]
                    finally:
                        with self._lock:
                            self._scan["scanned"] = int(self._scan["scanned"]) + 1
        except Exception as exc:
            LOG.warning("modified_scan_aborted error=%s", exc)
            with self._lock:
                self._scan["errors"] = int(self._scan["errors"]) + 1
                self._scan["last_error"] = f"Scan aborted: {type(exc).__name__}: {exc}"[:300]
        finally:
            with self._lock:
                self._scanning = False
                self._scan["running"] = False

    @staticmethod
    def _fetch_grid_from_session(client, section: CourseSection):
        # Reset stale PeopleSoft component state once, but do not loosen the
        # complete-grid / exact-course matching requirements below.
        try:
            loaded = client.fetch_course_info_page(str(section.source_url))
            parsed = parse_course_info_page(loaded.html, source_url=loaded.canonical_url)
        except Exception:
            client.reset_public_session()
            client.bootstrap()
            loaded = client.fetch_course_info_page(str(section.source_url))
            parsed = parse_course_info_page(loaded.html, source_url=loaded.canonical_url)
        return parsed

    @staticmethod
    def _fetch_grid_live(section: CourseSection):
        with SdsuPeopleSoftSession(SdsuHttpConfig()) as client:
            client.bootstrap()
            loaded = client.fetch_course_info_page(str(section.source_url))
            return parse_course_info_page(loaded.html, source_url=loaded.canonical_url)

    @staticmethod
    def _verify_live(section: CourseSection):
        if not section.term_code:
            raise ValueError("Cannot verify section without an SDSU term code.")
        with SdsuPeopleSoftSession(SdsuHttpConfig()) as client:
            client.bootstrap()
            _pages, result = client.scrape_subject(
                term=section.term, term_code=section.term_code,
                subject=section.subject, require_detail_urls=True,
            )
            if not result.complete or not result.exact_facet_applied:
                raise ValueError("SDSU subject search was incomplete; verification cancelled.")
            if result.partitioned:
                result = client.prepare_partitioned_detail_result(result)
            hits = [h for h in result.courses if h.course_code == section.course_code
                    or (section.crse_id and h.crse_id == section.crse_id)]
            if not hits:
                raise ValueError("Course not present in live SDSU search; check Possible Removed Classes.")
            for hit in hits:
                if not hit.detail_url:
                    raise ValueError("SDSU result did not include a usable detail URL.")
                pages = client.fetch_detail_pages(hit.detail_url)
                parsed = parse_course_info_page(pages.course_info_html, source_url=pages.course_info_url)
                if (parsed.course_code != section.course_code or not parsed.options_complete
                        or (parsed.term_code and section.term_code and parsed.term_code != section.term_code)):
                    raise ValueError("Course information grid or semester mismatch or incomplete.")
                option = next((o for o in parsed.options if o.class_number == section.schedule_number), None)
                if option is None:
                    continue
                # The official class modal contains the full Meeting Information tab.
                class_page = client.fetch_class_number_page(pages.course_info_url, section.schedule_number)
                first = parse_class_information_page(class_page.html)
                if first.class_number != section.schedule_number:
                    raise ValueError("Class modal did not match the requested class number.")
                records = [first]
                tab_pages = client.fetch_class_detail_tab_pages_from_class_page(class_page, ["CD", "MI", "CA"])
                for tab in tab_pages:
                    part = parse_class_information_page(tab.html)
                    if part.class_number != section.schedule_number:
                        raise ValueError("Class information tab changed class number.")
                    records.append(part)
                return option, merge_class_information_records(*records)
            raise ValueError("Class number is missing in live SDSU course options; use removal review.")

    def request_verification(self, key: str) -> None:
        with self._lock:
            groups = self._groups()
            rows = groups.get(key)
            if not rows or key not in self._candidates:
                raise ValueError("Modification candidate no longer exists.")
            if len({(s.course_code, s.crse_id or "") for s in rows}) > 1:
                raise ValueError("Cross-listed sections need separate manual reconciliation.")
            if self._candidates[key]["status"] == "checking":
                raise ValueError("Verification is already running for this class.")
            self._candidates[key] = {**self._candidates[key], "status": "checking", "verified_at": None}
            section = rows[0]
        Thread(target=self._verify_worker, args=(key, section), daemon=True,
               name="classcatalog-modification-verify").start()

    def _verify_worker(self, key: str, section: CourseSection) -> None:
        try:
            option, details = self.verify_live(section)
            if option.class_number != section.schedule_number:
                raise ValueError("Verified SDSU class number mismatch.")
            proposal = _verified_fields(section, option, details)
            changes = _changed(section, proposal)
            status = "verified" if changes else "unchanged"
            err = None
        except Exception as exc:
            status, err, proposal, changes = "inconclusive", f"{type(exc).__name__}: {exc}"[:300], {}, {}
            LOG.warning("modified_verify_failed class=%s error=%s", key, err)
        with self._lock:
            current = self._groups().get(key)
            if not current or _signature(current[0]) != _signature(section):
                self._candidates.pop(key, None)
                return
            previous = self._candidates.get(key, {})
            if key in self._ignored:
                return
            if status == "unchanged":
                self._candidates.pop(key, None)
            else:
                self._candidates[key] = {
                    **previous, "status": status, "changes": changes or previous.get("changes", {}),
                    "error": err, "verified_at": _now().isoformat() if status == "verified" else None,
                    "fingerprint": _signature(section) if status == "verified" else None,
                    "proposal": proposal if status == "verified" else {},
                }

    def dismiss(self, key: str) -> None:
        with self._lock:
            if key not in self._candidates:
                raise ValueError("Modification candidate not found.")
            self._ignored.add(key)
            self._candidates.pop(key, None)

    def apply_verified(self, key: str) -> dict[str, object]:
        with self._lock:
            sections_by_key = self._groups()
            section_group = sections_by_key.get(key, [])
            if not section_group or len({(s.course_code, s.crse_id or "") for s in section_group}) > 1:
                raise ValueError("Section is missing or cross-listed; cannot apply automatically.")
            current = section_group[0]
            row = self._candidates.get(key, {})
            if not self._current_confirmation(key, current) or row.get("status") != "verified":
                raise ValueError("A fresh live SDSU verification is required before applying.")
            proposal = row.get("proposal", {})
            if not isinstance(proposal, dict) or not _changed(current, proposal):
                raise ValueError("No verified changes remain to apply.")
            if not self.data_path.is_file():
                raise ValueError("The active dataset file is missing.")
            original = self.data_path.read_bytes()
            raw = json.loads(original)
            if not isinstance(raw, list):
                raise ValueError("The active dataset must be a JSON array.")
            parsed = [CourseSection.model_validate(item) for item in raw]
            stored = [s for s in parsed if _key(s) == key]
            if len(stored) != len(section_group) or sorted(s.id for s in stored) != sorted(s.id for s in section_group):
                raise ValueError("Disk inventory differs from verified dataset; rescan and verify.")
            if any(_signature(s) != _signature(next(x for x in section_group if x.id == s.id)) for s in stored):
                raise ValueError("Disk schedule details differ from verification; reload and verify.")
            patch = dict(proposal)
            if "instructor" in patch:
                patch["professor"] = None  # Do not retain a rating for the former instructor.
            updated_raw = []
            for item, section in zip(raw, parsed):
                if _key(section) == key:
                    candidate = CourseSection.model_validate({**item, **{
                        name: [m.model_dump(mode="json") for m in value] if name == "meetings" else
                        value.value if isinstance(value, InstructionMode) else value
                        for name, value in patch.items()
                    }})
                    updated_raw.append(candidate.model_dump(mode="json"))
                else:
                    updated_raw.append(item)
            if self.data_path.read_bytes() != original:
                raise ValueError("Active dataset changed during review. Nothing applied.")
            backups = self.data_path.parent / "modification-backups"
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups / f"{self.data_path.stem}-before-modification-{uuid4().hex}.json"
            shutil.copy2(self.data_path, backup)
            temp = self.data_path.with_name(f"{self.data_path.name}.{uuid4().hex}.tmp")
            try:
                temp.write_text(json.dumps(updated_raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                os.replace(temp, self.data_path)
            finally:
                temp.unlink(missing_ok=True)
            replacement = tuple(
                s.model_copy(update=patch) if _key(s) == key else s for s in self.repository.sections
            )
            self.repository.replace_inventory(replacement)
            self._candidates.pop(key, None)
            self._ignored.discard(key)
            return {"applied": True, "term": current.term, "course_code": current.course_code,
                    "class_number": current.schedule_number, "updated_fields": sorted(row["changes"]),
                    "backup_path": str(backup)}
