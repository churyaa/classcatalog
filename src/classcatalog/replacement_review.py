"""Conservative automatic insertion of verified replacements during Admin modified-class scans.

Never automatically delete a class. One missing section and one new section in an
otherwise complete, matching SDSU course must be independently confirmed twice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4
import json
import logging
import os
import shutil

from classcatalog.models import CourseSection, InstructionMode, Meeting, SeatStatus
from classcatalog.instruction_modes import classify_sdsu_instruction_mode
from classcatalog.repository import CourseRepository
from classcatalog.deleted_history import DeletedClassHistory  # CLASSCATALOG_DELETED_HISTORY_V1
from classcatalog.scraping.class_detail import (
    merge_class_information_records,
    parse_class_information_page,
)
from classcatalog.scraping.parser import parse_course_info_page
from classcatalog.scraping.class_post import find_class_number_action_occurrences
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession
from classcatalog.seats import is_course_info_url

LOG = logging.getLogger(__name__)


def _identity(section: CourseSection) -> tuple[str, str]:
    return (section.term_code or section.term, section.schedule_number)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplacementReviewService:
    """Discovers replacements opportunistically; the Admin scan is the trigger."""

    def __init__(self, repository: CourseRepository, data_path: Path, *,
                 verify_replacement: Callable[[CourseSection, str, set[str]], CourseSection] | None = None,
                 on_installed: Callable[[], None] | None = None) -> None:
        self.repository = repository
        self.data_path = data_path
        self.deleted_history = DeletedClassHistory(data_path)
        self.verify_replacement = verify_replacement or self._verify_replacement_live
        self.on_installed = on_installed
        self._lock = RLock()
        self._history: list[dict[str, object]] = []

    def history(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._history[-100:]][::-1]

    def _record(self, old: CourseSection, new_number: str | None, status: str, detail: str) -> None:
        with self._lock:
            self._history.append({
                "term": old.term, "course_code": old.course_code,
                "old_class_number": old.schedule_number,
                "new_class_number": new_number,
                "status": status, "detail": detail[:350], "at": _now(),
                "source_url": old.source_url if is_course_info_url(old.source_url) else None,
            })
            self._history = self._history[-100:]

    # CLASSCATALOG_DELETED_HISTORY_V1: historical sources are considered
    # only if a confirmed manual deletion recorded them, and only while absent
    # from the active repository. Never trust the old course URL as proof.
    def historical_sources(self, term: str, course_code: str = "") -> tuple[CourseSection, ...]:
        active = {_identity(s) for s in self.repository.sections}
        return tuple(source for source in self.deleted_history.sources(term, course_code)
                     if _identity(source) not in active and is_course_info_url(source.source_url))

    def is_deleted_source(self, source: CourseSection) -> bool:
        return (not any(_identity(s) == _identity(source) for s in self.repository.sections)
                and self.deleted_history.contains(source))

    def consider_deleted(self, source: CourseSection) -> None:
        """Try a confirmed removed class without using an obsolete saved URL."""
        if not self.is_deleted_source(source) or not source.term_code:
            return
        try:
            result = self._find_verified_replacement(source)
            if result is None:
                return
            number, candidate = result
            self._install(source, candidate)
            self._record(source, number, "added",
                         "Verified replacement added from deleted-class history. Old class remains deleted.")
        except ValueError as exc:
            self._record(source, None, "skipped", str(exc))
            LOG.info("deleted_history_replacement_skipped course=%s class=%s error=%s",
                     source.course_code, source.schedule_number, exc)
        except Exception as exc:
            self._record(source, None, "error", f"{type(exc).__name__}: {exc}")
            LOG.warning("deleted_history_replacement_failed course=%s class=%s",
                        source.course_code, source.schedule_number, exc_info=True)

    def consider_grid(self, source: CourseSection, parsed: object | None) -> None:
        """Called for completed source grids, or stale-source failures. Never trusts
        the cached course URL alone to authorize an import.
        """
        if not source.term_code or not is_course_info_url(source.source_url):
            return
        if parsed is not None:
            if (parsed.course_code != source.course_code or not parsed.options_complete
                    or (getattr(parsed, "term_code", None) and parsed.term_code != source.term_code)):
                return
            seen = {str(opt.class_number).strip() for opt in parsed.options}
            # No evidence of a disappearing section means no expensive verification.
            if source.schedule_number in seen:
                return
        else:
            # Avoid expensive retry for ordinary temporary page errors unless the
            # section has failed to refresh for at least one day.
            try:
                timestamp = datetime.fromisoformat(str(source.seat_updated_at).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600
            except (TypeError, ValueError):
                return
            if age_hours < 24:
                return
        try:
            # Candidate discovery must always come from a fresh SDSU subject search,
            # never just from a previously saved course information URL.
            result = self._find_verified_replacement(source)
            if result is None:
                return
            new_number, candidate = result
            self._install(source, candidate)
            self._record(source, new_number, "added", "Verified replacement added automatically. Old class remains for manual removal review.")
        except ValueError as exc:
            self._record(source, None, "skipped", str(exc))
            LOG.info("replacement_review_skipped course=%s class=%s error=%s", source.course_code, source.schedule_number, exc)
        except Exception as exc:
            self._record(source, None, "error", f"{type(exc).__name__}: {exc}")
            LOG.warning("replacement_review_failed course=%s class=%s", source.course_code, source.schedule_number, exc_info=True)

    def _find_verified_replacement(self, source: CourseSection) -> tuple[str, CourseSection] | None:
        # Block automatically replacing linked or cross-listed class numbers; these
        # require topology-aware dataset reconciliation, not single-section cloning.
        all_sections = tuple(self.repository.sections)
        exact_old = [s for s in all_sections if _identity(s) == _identity(source)]
        was_deleted = not exact_old and self.deleted_history.contains(source)
        if not was_deleted and (len(exact_old) != 1 or exact_old[0].id != source.id):
            raise ValueError("Class is neither active nor a confirmed, archived manual deletion.")
        if was_deleted and any(s.id == source.id for s in all_sections):
            raise ValueError("Archived class ID was reused; manual reconciliation required.")
        if (source.linked_components or
                any(group != source.schedule_number for group in source.option_group_ids) or
                (source.option_group_ids and set(source.option_primary_group_ids) != set(source.option_group_ids))):
            raise ValueError("Linked enrollment groups require manual inventory reconciliation.")
        course = [s for s in all_sections if s.term == source.term
                  and s.course_code == source.course_code
                  and s.crse_id == source.crse_id
                  and (not source.acad_career or s.acad_career == source.acad_career)]
        # Include confirmed deletions from the SAME stable SDSU offering.
        # Multiple old missing classes make the mapping ambiguous; do not guess.
        deleted = [s for s in self.deleted_history.sources(source.term, source.course_code)
                   if s.crse_id == source.crse_id
                   and (not source.acad_career or s.acad_career == source.acad_career)
                   and _identity(s) not in {_identity(a) for a in all_sections}]
        course.extend(deleted)
        if not course:
            return None
        # If an individual course contains multiple disappearing class numbers,
        # do not guess which old section corresponds to which new section.
        first = self._fresh_course_snapshot(source)
        if first is None:
            return None
        numbers1, candidate_numbers = first
        saved = {s.schedule_number for s in course}
        missing = saved - numbers1
        new = numbers1 - saved
        if not new:
            return None  # Replacement already imported or no new section present.
        if len(missing) != 1 or len(new) != 1 or source.schedule_number not in missing:
            if missing or new:
                raise ValueError("Ambiguous section changes: expected exactly one missing and one added class number.")
            return None
        number = next(iter(new))
        if any(_identity(s) == (source.term_code, number) for s in all_sections):
            raise ValueError("New class number already exists under another course in the active dataset.")
        # Second independent complete subject and course snapshot, plus class tabs.
        candidate = self.verify_replacement(source, number, numbers1)
        if candidate.schedule_number != number or candidate.term_code != source.term_code:
            raise ValueError("Verification returned a different class number or semester.")
        if candidate.course_code != source.course_code:
            raise ValueError("Verification returned a different course.")
        return number, candidate

    @staticmethod
    def _fresh_course_snapshot(source: CourseSection) -> tuple[set[str], dict[str, object]] | None:
        if not source.crse_id:
            # Course code alone can identify several academic offerings/careers.
            # Restrict automation to SDSU course records with stable identity.
            raise ValueError("Missing stable SDSU CRSE_ID; manual add required.")
        with SdsuPeopleSoftSession(SdsuHttpConfig()) as client:
            client.bootstrap()
            _pages, result = client.scrape_subject(term=source.term, term_code=source.term_code,
                                                  subject=source.subject, require_detail_urls=True)
            if not result.complete or not result.exact_facet_applied or result.term_code != source.term_code:
                raise ValueError("SDSU live subject inventory incomplete; nothing imported.")
            if result.partitioned:
                result = client.prepare_partitioned_detail_result(result)
            matching = [hit for hit in result.courses if hit.crse_id == source.crse_id
                        and hit.course_code == source.course_code
                        and (not source.acad_career or hit.acad_career == source.acad_career)]
            if len(matching) != 1 or not matching[0].detail_url:
                raise ValueError("Stable course identity was absent or ambiguous in live SDSU search.")
            pages = client.fetch_detail_pages(matching[0].detail_url)
            grid = parse_course_info_page(pages.course_info_html, source_url=pages.course_info_url)
            if (not grid.options_complete or grid.course_code != source.course_code or
                    (grid.term_code and grid.term_code != source.term_code) or not grid.options):
                raise ValueError("Incomplete or mismatched live course options.")
            numbers = {str(opt.class_number).strip() for opt in grid.options if opt.class_number}
            if len(numbers) != len(grid.options):
                raise ValueError("Duplicate or missing SDSU class numbers; cannot auto-import.")
            return numbers, {"grid": grid, "url": pages.course_info_url}

    @classmethod
    def _verify_replacement_live(cls, source: CourseSection, new_number: str, expected_numbers: set[str]) -> CourseSection:
        # A second complete and independent check; later the class modal supplies
        # current instructor, room, meeting rows and enrollment seats.
        if not source.crse_id:
            raise ValueError("A verified stable course ID is required.")
        with SdsuPeopleSoftSession(SdsuHttpConfig()) as client:
            client.bootstrap()
            _pages, result = client.scrape_subject(term=source.term, term_code=source.term_code,
                                                  subject=source.subject, require_detail_urls=True)
            if not result.complete or not result.exact_facet_applied or result.term_code != source.term_code:
                raise ValueError("Second SDSU inventory incomplete.")
            if result.partitioned:
                result = client.prepare_partitioned_detail_result(result)
            matches = [h for h in result.courses if h.crse_id == source.crse_id
                       and h.course_code == source.course_code
                       and (not source.acad_career or h.acad_career == source.acad_career)]
            if len(matches) != 1 or not matches[0].detail_url:
                raise ValueError("Second subject search did not uniquely identify the same SDSU course.")
            pages = client.fetch_detail_pages(matches[0].detail_url)
            parsed = parse_course_info_page(pages.course_info_html, source_url=pages.course_info_url)
            if (not parsed.options_complete or parsed.course_code != source.course_code or
                    (parsed.term_code and parsed.term_code != source.term_code) or not parsed.options):
                raise ValueError("Second SDSU course grid incomplete or mismatched.")
            numbers = {o.class_number.strip() for o in parsed.options if o.class_number}
            if numbers != expected_numbers or source.schedule_number in numbers or new_number not in numbers:
                raise ValueError("Independent SDSU course snapshots disagree on class numbers.")
            option = next(o for o in parsed.options if o.class_number == new_number)
            row_actions = find_class_number_action_occurrences(pages.course_info_html)
            if any(a.source_row_index == option.source_row_index and a.class_number != new_number
                   for a in row_actions):
                raise ValueError("New SDSU section is linked to other class numbers; manual import required.")
            class_page = client.fetch_class_number_page(pages.course_info_url, new_number)
            first = parse_class_information_page(class_page.html)
            if first.class_number != new_number:
                raise ValueError("SDSU class modal number mismatch.")
            tabs = client.fetch_class_detail_tab_pages_from_class_page(class_page, ["CD", "MI", "CA"])
            parts = [first]
            for tab in tabs:
                item = parse_class_information_page(tab.html)
                if item.class_number != new_number:
                    raise ValueError("SDSU class tab switched to a different class.")
                parts.append(item)
            details = merge_class_information_records(*parts)
            return cls._build_candidate(source, new_number, parsed, option, details, pages.course_info_url)

    @staticmethod
    def _build_candidate(source: CourseSection, number: str, course, option, details,
                         source_url: str) -> CourseSection:
        if not is_course_info_url(source_url):
            raise ValueError("Invalid official SDSU source URL.")
        if not details.meetings and not (option.days and option.start_time and option.end_time):
            raise ValueError("New class has no verifiable meeting data; manual import required.")
        if details.meetings:
            meetings = tuple(Meeting(
                days=tuple(m.days), start_time=m.start_time, end_time=m.end_time,
                location=m.room or option.location, meeting_dates=m.meeting_dates or option.meeting_dates,
                instructor=m.instructor or option.instructor,
            ) for m in details.meetings)
        else:
            meetings = (Meeting(days=tuple(option.days), start_time=option.start_time,
                                end_time=option.end_time, location=option.location,
                                meeting_dates=option.meeting_dates, instructor=option.instructor),)
        if not meetings or any(not m.days or not m.start_time or not m.end_time for m in meetings):
            raise ValueError("Incomplete new meeting schedule; manual import required.")
        instructor = next((m.instructor for m in meetings if m.instructor), option.instructor)
        location = details.location or next((m.location for m in meetings if m.location), option.location)
        mode = classify_sdsu_instruction_mode(
            details.instruction_mode_text,
            has_timed_meeting=any(m.start_time for m in meetings),
            fallback=details.instruction_mode or InstructionMode.OTHER,
        ) or InstructionMode.OTHER
        if option.status is SeatStatus.UNKNOWN and details.status is SeatStatus.UNKNOWN:
            raise ValueError("New SDSU section has unknown enrollment state.")
        seats_available = details.seats_available if details.seats_available is not None else option.open_seats
        seat_capacity = details.seat_capacity if details.seat_capacity is not None else option.seat_capacity
        seats_enrolled = details.seats_enrolled if details.seats_enrolled is not None else option.seats_enrolled
        status = SeatStatus.OPEN if seats_available is not None and seats_available > 0 else (
            details.status if details.status is not SeatStatus.UNKNOWN else option.status)
        data = source.model_dump(mode="json")
        data.update({
            "id": f"{source.id}-replacement-{number}",
            "schedule_number": number,
            "section_number": details.section_number or option.section_number or "",
            "option_number": option.option_number,
            "option_group_indices": [option.option_group_index] if option.option_group_index else [],
            "option_primary_group_indices": [option.option_group_index] if option.option_group_index else [],
            "option_group_ids": [number] if source.option_group_ids else [],
            "option_primary_group_ids": [number] if source.option_primary_group_ids else [],
            "linked_components": [],
            "instructor": instructor, "professor": None,
            "location": location,
            "instruction_mode": mode.value,
            "instruction_mode_text": details.instruction_mode_text,
            "meetings": [m.model_dump(mode="json") for m in meetings],
            "seat_status": status.value,
            "seats_available": seats_available, "seat_capacity": seat_capacity,
            "seats_enrolled": seats_enrolled,
            "waitlist_available": details.waitlist_available,
            "waitlist_capacity": details.waitlist_capacity,
            "waitlist_total": details.waitlist_total,
            "seat_updated_at": _now(),
            "source_url": source_url, "source_updated_at": _now(),
            "bookstore_url": details.bookstore_url,
            "materials_description": details.materials_description,
            "textbook_required": details.textbook_required,
            "class_notes": list(details.class_notes),
            "enrollment_requirements": list(details.enrollment_requirements),
        })
        return CourseSection.model_validate(data)

    def _install(self, source: CourseSection, added: CourseSection) -> None:
        with self._lock:
            # Recheck live and disk identities at commit time. Never overwrite an
            # existing listing or apply against unrelated changed source inventory.
            current = tuple(self.repository.sections)
            old = next((s for s in current if s.id == source.id), None)
            was_deleted = old is None and self.deleted_history.contains(source)
            if not was_deleted and (old is None or old.schedule_number != source.schedule_number):
                raise ValueError("Original class changed and is not in confirmed deletion history.")
            if was_deleted and any(_identity(s) == _identity(source) for s in current):
                raise ValueError("Deleted class number reappeared in the active dataset.")
            if any(_identity(s) == _identity(added) or s.id == added.id for s in current):
                raise ValueError("New class is already installed.")
            if not self.data_path.is_file():
                raise ValueError("Active dataset file missing.")
            before = self.data_path.read_bytes()
            raw = json.loads(before)
            if not isinstance(raw, list):
                raise ValueError("Active sections dataset must be a JSON array.")
            parsed = [CourseSection.model_validate(item) for item in raw]
            disk_old = next((s for s in parsed if s.id == source.id), None)
            if was_deleted:
                if disk_old is not None or any(_identity(s) == _identity(source) for s in parsed):
                    raise ValueError("Previously deleted class reappeared on disk; cannot use history.")
                if not self.deleted_history.contains(source):
                    raise ValueError("Confirmed deletion history changed; rescan.")
            elif disk_old is None or disk_old.schedule_number != source.schedule_number:
                raise ValueError("Disk inventory changed during verification.")
            if any(_identity(s) == _identity(added) or s.id == added.id for s in parsed):
                raise ValueError("Already present on disk.")
            candidate = CourseRepository((*current, added), catalog=self.repository.catalog)
            before_audit = self.repository.coverage_audit()
            after_audit = candidate.coverage_audit()
            if (after_audit.unaccounted_physical_sections > before_audit.unaccounted_physical_sections or
                    after_audit.unaccounted_course_section_listings > before_audit.unaccounted_course_section_listings):
                raise ValueError("New section would not be represented by ClassCatalog grouping.")
            if self.data_path.read_bytes() != before:
                raise ValueError("Dataset changed during import; rescan.")
            backups = self.data_path.parent / "replacement-backups"
            backups.mkdir(parents=True, exist_ok=True)
            backup = backups / f"{self.data_path.stem}-before-replacement-{uuid4().hex}.json"
            shutil.copy2(self.data_path, backup)
            temporary = self.data_path.with_name(f"{self.data_path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps([*raw, added.model_dump(mode="json")], indent=2,
                                                ensure_ascii=False) + "\n", encoding="utf-8")
                os.replace(temporary, self.data_path)
            finally:
                temporary.unlink(missing_ok=True)
            self.repository.replace_inventory((*current, added))
            if self.on_installed is not None:
                try:
                    self.on_installed()
                except Exception:
                    LOG.exception("replacement_review_post_install_refresh_failed; restart backend")
