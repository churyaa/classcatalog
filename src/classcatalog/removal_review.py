"""Manual, fail-closed review of possibly removed SDSU physical class sections.

This is intentionally NOT a scheduled deletion service.  Seat age and worker
failures only nominate sections.  Only fresh, complete SDSU subject/course
checks can authorize a manual administrator deletion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
import json
import logging
import os
from pathlib import Path
import shutil
from threading import RLock, Thread
from typing import Callable
from uuid import uuid4

from classcatalog.models import CourseSection
from classcatalog.repository import CourseRepository
from classcatalog.deleted_history import DeletedClassHistory  # CLASSCATALOG_DELETED_HISTORY_V1
from classcatalog.seats import is_course_info_url  # CLASSCATALOG_REMOVAL_SOURCE_LINK_V1
from classcatalog.scraping.parser import parse_course_info_page
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession

LOG = logging.getLogger(__name__)
STALE_HOURS = 24
CONFIRMATION_HOURS = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _datetime(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _key(section: CourseSection) -> str:
    return f"{section.term_code or section.term}::{section.schedule_number}"


def _physical(section: CourseSection) -> tuple[str, str]:
    return section.term_code or section.term, section.schedule_number


class RemovalReviewService:
    def __init__(
        self,
        repository: CourseRepository,
        data_path: Path,
        *,
        seat_service=None,
        probe: Callable[[CourseSection], bool] | None = None,
        stale_hours: int = STALE_HOURS,
    ) -> None:
        self.repository = repository
        self.data_path = data_path
        self.seat_service = seat_service
        self.deleted_history = DeletedClassHistory(data_path)
        self.probe = probe or self._probe_live
        self.stale_hours = stale_hours
        self._lock = RLock()
        self._states: dict[str, dict[str, object]] = {}
        self._ignored: set[str] = set()

    def _physical_sections(self) -> dict[str, list[CourseSection]]:
        records: dict[str, list[CourseSection]] = {}
        for section in self.repository.sections:
            records.setdefault(_key(section), []).append(section)
        return records

    def candidates(self) -> dict[str, object]:
        now = _now()
        failures = (
            self.seat_service.admin_status().get("seat_failures", [])
            if self.seat_service is not None else []
        )
        missing_number_urls = {
            str(failure.get("source_url"))
            for failure in failures
            if not failure.get("resolved")
            and "contains none of the expected class numbers" in str(failure.get("detail", ""))
        }
        with self._lock:
            groups = self._physical_sections()
            items: list[dict[str, object]] = []
            for key, sections in groups.items():
                if key in self._ignored:
                    continue
                oldest = max(
                    (_datetime(s.seat_updated_at) for s in sections if _datetime(s.seat_updated_at)),
                    default=None,
                )
                age = max(0, (now - oldest).total_seconds() / 3600) if oldest else None
                failed = any(str(s.source_url) in missing_number_urls for s in sections)
                if (age is None or age < self.stale_hours) and not failed:
                    continue
                state = self._states.get(key, {})
                status = str(state.get("status") or ("suspected" if failed else "stale"))
                # No successful live seat update since verification? Keep explicit
                # result until a new manual review or cache refresh supersedes it.
                items.append({
                    "key": key,
                    "term": sections[0].term,
                    "course_code": sections[0].course_code,
                    "course_codes": sorted({s.course_code for s in sections}),
                    "class_number": sections[0].schedule_number,
                    "source_url": next(
                        (str(s.source_url) for s in sections if is_course_info_url(s.source_url)),
                        None,
                    ),
                    "seat_age_hours": round(age, 1) if age is not None else None,
                    "last_seat_update": oldest.isoformat() if oldest else None,
                    "missing_class_number_failure": failed,
                    "status": status,
                    "verified_at": state.get("verified_at"),
                    "error": state.get("error"),
                    "can_delete": status == "confirmed_missing"
                    and self._confirmation_valid(key, sections, now),
                    "crosslisted": len({(s.course_code, s.crse_id or "") for s in sections}) > 1,
                })
            items.sort(key=lambda row: (
                0 if row["status"] == "confirmed_missing" else 1,
                0 if row["missing_class_number_failure"] else 1,
                -(row["seat_age_hours"] or 0),
                str(row["course_code"]),
            ))
            return {"stale_threshold_hours": self.stale_hours,
                    "total_flagged": len(items), "items": items[:250]}

    def _confirmation_valid(self, key: str, sections: list[CourseSection], now: datetime) -> bool:
        state = self._states.get(key, {})
        checked = _datetime(state.get("verified_at"))
        return bool(
            state.get("status") == "confirmed_missing" and checked is not None
            and 0 <= (now - checked).total_seconds() <= CONFIRMATION_HOURS * 3600
            and tuple(sorted(s.id for s in sections)) == state.get("section_ids")
            and tuple(sorted(str(s.seat_updated_at or "") for s in sections)) == state.get("seat_updates")
            and len({(s.course_code, s.crse_id or "") for s in sections}) == 1
        )

    def request_verification(self, key: str) -> None:
        with self._lock:
            sections = self._physical_sections().get(key)
            if not sections:
                raise ValueError("This class number is not in the active dataset.")
            if len({(s.course_code, s.crse_id or "") for s in sections}) > 1:
                raise ValueError("Cross-listed class numbers require separate manual dataset review.")
            if key not in {item["key"] for item in self.candidates()["items"]}:
                raise ValueError("This class has no stale-seat or missing-number signal.")
            if any(st.get("status") == "checking" for st in self._states.values()):
                raise ValueError("This class is already being verified.")
            self._states[key] = {"status": "checking"}
            section = sections[0]
        Thread(target=self._verify, args=(key, section), daemon=True,
               name="classcatalog-removal-verification").start()

    def _verify(self, key: str, section: CourseSection) -> None:
        try:
            # Two independent complete searches, not two reads of an old URL.
            missing_first = self.probe(section)
            missing_second = self.probe(section) if missing_first else False
            status = "confirmed_missing" if missing_first and missing_second else "still_present"
            error = None
        except Exception as exc:  # fail closed, never authorize deletion on a network/parse error
            status = "inconclusive"
            error = f"{type(exc).__name__}: {exc}"[:400]
            LOG.warning("removal_verification_inconclusive class=%s error=%s", key, error)
        with self._lock:
            current = self._physical_sections().get(key, [])
            if not current or _physical(current[0]) != _physical(section):
                self._states.pop(key, None)
                return
            self._states[key] = {
                "status": status,
                "error": error,
                "verified_at": _now().isoformat(),
                "section_ids": tuple(sorted(s.id for s in current)),
                "seat_updates": tuple(sorted(str(s.seat_updated_at or "") for s in current)),
            }

    @staticmethod
    def _probe_live(section: CourseSection) -> bool:
        """Return True only when a complete live search proves the old class absent."""
        if not section.term_code:
            raise ValueError("Term STRM is missing; live deletion verification is unavailable.")
        with SdsuPeopleSoftSession(SdsuHttpConfig()) as client:
            client.bootstrap()
            _pages, result = client.scrape_subject(
                term=section.term, term_code=section.term_code,
                subject=section.subject, require_detail_urls=True,
            )
            if not result.complete or not result.exact_facet_applied:
                raise ValueError("SDSU subject inventory is incomplete or its exact facet was not applied.")
            hits = [hit for hit in result.courses if hit.course_code == section.course_code]
            if section.crse_id:
                hits.extend(hit for hit in result.courses if hit.crse_id == section.crse_id)
            unique = list({(h.crse_id or h.course_code, h.acad_career): h for h in hits}.values())
            if not unique:
                return True
            if result.partitioned:
                result = client.prepare_partitioned_detail_result(result)
                detail_urls = {(h.crse_id or h.course_code, h.acad_career): h.detail_url for h in result.courses}
            else:
                detail_urls = {(h.crse_id or h.course_code, h.acad_career): h.detail_url for h in unique}
            for hit in unique:
                url = detail_urls.get((hit.crse_id or hit.course_code, hit.acad_career))
                if not url:
                    raise ValueError("SDSU did not supply a usable course-detail URL.")
                pages = client.fetch_detail_pages(url)
                parsed = parse_course_info_page(pages.course_info_html, source_url=pages.course_info_url)
                if parsed.course_code != hit.course_code or not parsed.options_complete:
                    raise ValueError("SDSU course grid is mismatched or incomplete.")
                if not parsed.options:
                    raise ValueError("SDSU returned no verifiable course-option rows.")
                numbers = {option.class_number for option in parsed.options}
                if section.schedule_number in numbers:
                    return False
            return True

    def _prune_seat_refresh_after_deletion(self) -> None:
        """Drop deleted sources/cache from the running seat worker, when compatible.

        This lives in the review module so older local versions of seats.py do not
        have to match a fragile patch anchor. A worker that was already fetching a
        deleted class may finish later; a backend restart fully resets it.
        """
        worker = self.seat_service
        if worker is None:
            return
        prune = getattr(worker, "prune_removed_sections", None)
        if callable(prune):
            try:
                prune()
            except Exception:
                LOG.exception("removal_review_seat_prune_failed")
            return
        required = (
            "_lock", "_build_sources", "_sources", "_source_by_schedule",
            "_source_by_course_code", "_manual_queue", "_interests",
            "_records", "_source_last_success", "_source_last_attempt",
        )
        if not all(hasattr(worker, name) for name in required):
            LOG.warning("removal_review_seat_prune_unavailable: restart service to clear old sources")
            return
        try:
            with worker._lock:
                valid_keys = set(self.repository.seat_snapshot(()))
                worker._sources = worker._build_sources()
                worker._source_by_schedule = {}
                worker._source_by_course_code = {}
                for url, source in worker._sources.items():
                    for class_number in source.schedule_to_term:
                        worker._source_by_schedule.setdefault(class_number, set()).add(url)
                    for course_code in source.course_codes:
                        normalized = " ".join(course_code.strip().upper().split())
                        worker._source_by_course_code.setdefault(normalized, set()).add(url)
                worker._manual_queue = deque(
                    url for url in worker._manual_queue if url in worker._sources
                )
                worker._interests = {
                    url: deadline for url, deadline in worker._interests.items()
                    if url in worker._sources
                }
                worker._records = {
                    key: record for key, record in worker._records.items()
                    if key in valid_keys
                }
                worker._source_last_success = {
                    url: ts for url, ts in worker._source_last_success.items()
                    if url in worker._sources
                }
                worker._source_last_attempt = {
                    url: ts for url, ts in worker._source_last_attempt.items()
                    if url in worker._sources
                }
                if hasattr(worker, "_resolved_url_by_source"):
                    worker._resolved_url_by_source = {
                        url: value for url, value in worker._resolved_url_by_source.items()
                        if url in worker._sources
                    }
                if hasattr(worker, "_dirty"):
                    worker._dirty = True
            wake = getattr(worker, "_wake", None)
            if wake is not None:
                wake.set()
        except Exception:
            LOG.exception("removal_review_seat_prune_failed: restart backend to clear stale seat worker state")

    def dismiss(self, key: str) -> None:
        with self._lock:
            if key not in self._physical_sections():
                raise ValueError("Class number not found.")
            self._ignored.add(key)
            self._states.pop(key, None)

    def delete_confirmed(self, key: str) -> dict[str, object]:
        with self._lock:
            sections = self._physical_sections().get(key, [])
            if not sections or not self._confirmation_valid(key, sections, _now()):
                raise ValueError("A fresh, complete live confirmation is required before deletion.")
            current_updates = [_datetime(s.seat_updated_at) for s in sections]
            if any(updated and (_now() - updated).total_seconds() < self.stale_hours * 3600
                   for updated in current_updates):
                raise ValueError("A recent seat update was found; deletion must be verified again.")
            if not self.data_path.is_file():
                raise ValueError("Active on-disk dataset is missing. Deletion cancelled.")
            original_bytes = self.data_path.read_bytes()
            raw = json.loads(original_bytes)
            if not isinstance(raw, list):
                raise ValueError("Active dataset is not a JSON array.")
            parsed = [CourseSection.model_validate(item) for item in raw]
            group = [section for section in parsed if _key(section) == key]
            if tuple(sorted(s.id for s in group)) != tuple(sorted(s.id for s in sections)):
                raise ValueError("On-disk dataset differs from verified live records; verify again.")
            remaining = [item for item, section in zip(raw, parsed) if _key(section) != key]
            if not remaining:
                raise ValueError("Refusing to empty the entire active catalog.")
            updated_sections = tuple(s for s in self.repository.sections if _key(s) != key)
            before_audit = self.repository.coverage_audit()
            after_audit = CourseRepository(updated_sections, catalog=self.repository.catalog).coverage_audit()
            if (after_audit.unaccounted_physical_sections > before_audit.unaccounted_physical_sections
                    or after_audit.unaccounted_course_section_listings > before_audit.unaccounted_course_section_listings):
                raise ValueError("Removal would create new orphaned class components. Use a full dataset review.")
            # Write a restorable backup and atomically replace the data file.
            if self.data_path.read_bytes() != original_bytes:
                raise ValueError("Active dataset changed during verification; deletion cancelled.")
            backup_dir = self.data_path.parent / "removal-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{self.data_path.stem}-before-removal-{uuid4().hex}.json"
            shutil.copy2(self.data_path, backup)
            # CLASSCATALOG_DELETED_HISTORY_V1: write a durable tombstone BEFORE
            # removing the only record capable of identifying a future replacement.
            # Fail closed if history storage is inaccessible or invalid. A failed
            # dataset write compensates by removing the pending history entry.
            # Archive exact, validated on-disk class records, not enriched runtime rows.
            # Runtime catalog enrichment may alter requirement and program tags.
            history_id = self.deleted_history.append_confirmed(group, backup)
            temporary = self.data_path.with_name(f"{self.data_path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(remaining, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                os.replace(temporary, self.data_path)
            except BaseException:
                self.deleted_history.undo_pending(history_id)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            self.repository.replace_inventory(updated_sections)
            self._prune_seat_refresh_after_deletion()
            self._states.pop(key, None)
            self._ignored.discard(key)
            return {"deleted": True, "class_number": sections[0].schedule_number,
                    "course_code": sections[0].course_code, "term": sections[0].term,
                    "removed_listings": len(group), "backup_path": str(backup)}
