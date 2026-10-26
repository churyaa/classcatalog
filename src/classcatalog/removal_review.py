"""Verify missing SDSU physical classes and hide them in a durable overlay.

sections.json is immutable here; public search and SEO use the visible view.
"""
from __future__ import annotations

from datetime import datetime, timezone
from collections import deque
from contextlib import contextmanager
import hashlib
import time
from threading import Event, Lock
import json
import logging
import os
from pathlib import Path
import shutil
from threading import RLock, Thread
from typing import Callable
from uuid import uuid4

from classcatalog.models import CourseSection
from classcatalog.class_visibility import read_hidden, write_hidden  # CLASSCATALOG_VISIBILITY_OVERLAY_V1
from classcatalog.repository import CourseRepository
from classcatalog.seats import is_course_info_url  # CLASSCATALOG_REMOVAL_SOURCE_LINK_V1
from classcatalog.scraping.parser import parse_course_info_page
from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession

LOG = logging.getLogger(__name__)
STALE_HOURS = 48
CONFIRMATION_HOURS = 1
# CLASSCATALOG_AUTOMATIC_REMOVAL_V1
# CLASSCATALOG_REMOVAL_QUEUE_SPEED_V1: one SDSU-verified candidate per two-minute cycle.
# A verification may itself take longer than two minutes, so the effective rate
# is bounded by SDSU response time. This avoids bursts of live subject scrapes.
AUTO_CYCLE_SECONDS = 120
AUTO_INITIAL_SECONDS = 30
AUTO_CHECKS_PER_CYCLE = 1
AUTO_RETRY_SECONDS = 24 * 3600  # Avoid repeatedly scraping classes that still exist.


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
        self.probe = probe or self._probe_live
        self.stale_hours = stale_hours
        self._lock = RLock()
        self._states: dict[str, dict[str, object]] = {}
        self._ignored: set[str] = set()
        # CLASSCATALOG_AUTOMATIC_REMOVAL_V1: automatic checks never infer absence from stale seats alone.
        self._auto_lock = Lock()
        self._auto_stop = Event()
        self._auto_thread: Thread | None = None
        self._auto_attempts: dict[str, float] = {}
        self._auto_deletions: deque[dict[str, object]] = deque(maxlen=25)
        self._auto_last_cycle: str | None = None
        self._auto_last_error: str | None = None
        self.on_deleted: Callable[[], None] | None = None
        # CLASSCATALOG_VISIBILITY_OVERLAY_V1: persistent visibility (never edit sections.json).
        self._visibility_path = data_path.with_name('hidden_classes.json')
        self._dataset_digest = hashlib.sha256(data_path.read_bytes()).digest() if data_path.is_file() else None
        self._full_sections = tuple(repository.sections)
        self._hidden_records = read_hidden(self._visibility_path)
        if self._hidden_records:
            self._apply_visibility()

    def _physical_sections(self) -> dict[str, list[CourseSection]]:
        records: dict[str, list[CourseSection]] = {}
        for section in self.repository.sections:
            records.setdefault(_key(section), []).append(section)
        return records

    def candidates(self, *, limit: int | None = 250) -> dict[str, object]:
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
                    "total_flagged": len(items), "items": (items if limit is None else items[:max(0, limit)]),
                    "auto_enabled": bool(self._auto_thread and self._auto_thread.is_alive()),
                    "auto_interval_minutes": AUTO_CYCLE_SECONDS // 60,
                    "auto_last_cycle": self._auto_last_cycle,
                    "auto_last_error": self._auto_last_error,
                    "hidden_items": self.hidden_items(),
                    "recent_auto_hides": list(self._auto_deletions)}

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
            if key not in {item["key"] for item in self.candidates(limit=None)["items"]}:
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
            unique = list({(h.course_code, h.crse_id or "", h.crse_offer_nbr or "", h.acad_career or ""): h for h in hits}.values())
            if not unique:
                return True
            if result.partitioned:
                result = client.prepare_partitioned_detail_result(result)
                detail_urls = {(h.course_code, h.crse_id or "", h.crse_offer_nbr or "", h.acad_career or ""): h.detail_url for h in result.courses}
            else:
                detail_urls = {(h.course_code, h.crse_id or "", h.crse_offer_nbr or "", h.acad_career or ""): h.detail_url for h in unique}
            for hit in unique:
                url = detail_urls.get((hit.course_code, hit.crse_id or "", hit.crse_offer_nbr or "", hit.acad_career or ""))
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

    # CLASSCATALOG_VISIBILITY_OVERLAY_V1: preserve all original records in memory.
    # Only the public repository view is filtered; sections.json is immutable here.
    def _hidden_match(self, section: CourseSection, records=None) -> bool:
        records = self._hidden_records if records is None else records
        item = records.get(_key(section))
        if item is None:
            return False
        group = [s for s in self._full_sections if _key(s) == _key(section)]
        return (tuple(sorted(item["section_ids"])) == tuple(sorted(s.id for s in group))
                and item["course_code"] == section.course_code
                and item.get("crse_id", "") == str(section.crse_id or ""))

    def _apply_visibility(self) -> None:
        # Preserve live seat/instructor changes for sections that remain visible.
        visible_by_id = {s.id: s for s in self.repository.sections}
        self._full_sections = tuple(visible_by_id.get(s.id, s) for s in self._full_sections)
        filtered = tuple(s for s in self._full_sections if not self._hidden_match(s))
        if filtered != self.repository.sections:
            self.repository.replace_inventory(filtered)
        self._prune_seat_refresh_after_deletion()
        if self.on_deleted is not None:
            self.on_deleted()  # Rebuild SEO from the visible repository only.

    def _assert_same_dataset(self) -> list[CourseSection]:
        """Never update visibility against a newer on-disk semester without restart."""
        current = self.data_path.read_bytes()
        if hashlib.sha256(current).digest() != self._dataset_digest:
            raise ValueError("sections.json changed since startup; restart the backend before changing visibility.")
        raw = json.loads(current)
        if not isinstance(raw, list):
            raise ValueError("Active dataset is not a JSON array.")
        return [CourseSection.model_validate(entry) for entry in raw]

    def hidden_items(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "key": item["key"], "term": item["term"],
                    "course_code": item["course_code"],
                    "class_number": item["class_number"],
                    "hidden_at": item["hidden_at"],
                    "verified_at": item["verified_at"],
                    "reason": item.get("reason", "SDSU confirmed absent"),
                    "source_url": item.get("source_url"),
                    "applied": any(self._hidden_match(s) for s in self._full_sections
                                   if _key(s) == item["key"]),
                }
                for item in self._hidden_records.values()
            ]

    def hide_confirmed(self, key: str) -> dict[str, object]:
        """Hide a verified physical class; never rewrite the source dataset."""
        with self._lock:
            sections = self._physical_sections().get(key, [])
            if not sections or not self._confirmation_valid(key, sections, _now()):
                raise ValueError("Fresh, complete SDSU confirmation is required before hiding.")
            if any((stamp := _datetime(s.seat_updated_at)) and
                   (_now() - stamp).total_seconds() < self.stale_hours * 3600
                   for s in sections):
                raise ValueError("Recent seat data invalidated the removal confirmation.")
            on_disk = self._assert_same_dataset()
            original_group = [s for s in on_disk if _key(s) == key]
            if sorted(s.id for s in original_group) != sorted(s.id for s in sections):
                raise ValueError("Verified section IDs differ from the source dataset.")
            if len({(s.course_code, s.crse_id or "") for s in original_group}) != 1:
                raise ValueError("Cross-listed sections cannot be hidden automatically.")
            # Reject orphaned mandatory linked components; a full rescrape is safer.
            candidate = tuple(s for s in self.repository.sections if _key(s) != key)
            if not candidate:
                raise ValueError("Refusing to hide the entire active catalog.")
            previous = self.repository.coverage_audit()
            after = CourseRepository(candidate, catalog=self.repository.catalog).coverage_audit()
            if (after.unaccounted_physical_sections > previous.unaccounted_physical_sections
                    or after.unaccounted_course_section_listings > previous.unaccounted_course_section_listings):
                raise ValueError("Hiding this section would orphan a linked class. Rescrape the term.")
            if key in self._hidden_records:
                raise ValueError("This class is already hidden.")
            record = {
                "key": key, "section_ids": sorted(s.id for s in original_group),
                "class_number": sections[0].schedule_number,
                "course_code": sections[0].course_code, "term": sections[0].term,
                "crse_id": str(sections[0].crse_id or ""),
                "hidden_at": _now().isoformat(),
                "verified_at": str(self._states[key]["verified_at"]),
                "reason": "confirmed_missing_from_sdsu",
                "source_url": next((str(s.source_url) for s in sections
                                    if is_course_info_url(s.source_url)), None),
            }
            updated = dict(self._hidden_records)
            updated[key] = record
            self._assert_same_dataset()
            write_hidden(self._visibility_path, updated)
            self._hidden_records = updated
            self._apply_visibility()
            self._states.pop(key, None)
            self._ignored.discard(key)
            return {"hidden": True, "term": record["term"],
                    "course_code": record["course_code"],
                    "class_number": record["class_number"],
                    "visibility_path": str(self._visibility_path)}

    def unhide(self, key: str) -> dict[str, object]:
        """Admin-only visibility restore: the class remains in sections.json."""
        with self._lock:
            record = self._hidden_records.get(key)
            if record is None:
                raise ValueError("This class is not marked hidden.")
            on_disk = self._assert_same_dataset()
            group = [s for s in on_disk if _key(s) == key]
            if (sorted(s.id for s in group) != sorted(record["section_ids"])
                    or any(s.course_code != record["course_code"]
                           or str(s.crse_id or "") != record.get("crse_id", "") for s in group)):
                raise ValueError("The class changed during rescraping; restart or reconcile visibility.")
            updated = dict(self._hidden_records)
            del updated[key]
            write_hidden(self._visibility_path, updated)
            self._hidden_records = updated
            self._apply_visibility()
            self._auto_attempts.pop(key, None)
            self._states.pop(key, None)
            return {"unhidden": True, "term": record["term"],
                    "course_code": record["course_code"],
                    "class_number": record["class_number"]}

    # CLASSCATALOG_AUTOMATIC_REMOVAL_V1: safe background verifier/hider.
    @contextmanager
    def _process_auto_lock(self):
        """Single writer across Uvicorn workers on one host (OS-released lock)."""
        lock_path = self.data_path.with_name(self.data_path.name + '.removal-auto.lock')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b'0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                yield False
                return
            yield True
        finally:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def auto_cycle(self, *, max_checks: int = AUTO_CHECKS_PER_CYCLE) -> dict[str, int]:
        """Check bounded stale candidates; hide ONLY after fresh double verification."""
        if not self._auto_lock.acquire(blocking=False):
            return {"checked": 0, "hidden": 0}
        checked = hidden = 0
        try:
            with self._process_auto_lock() as acquired:
                if not acquired:
                    return {"checked": 0, "hidden": 0}
                with self._lock:
                    self._auto_last_cycle = _now().isoformat()
                    self._auto_last_error = None
                for item in self.candidates(limit=None)["items"]:
                    if checked >= max(0, max_checks) or self._auto_stop.is_set():
                        break
                    key = str(item["key"])
                    age = item.get("seat_age_hours")
                    # Never auto-delete classes with no proven historical seat update.
                    # Missing-number errors alone can flag a class for MANUAL review,
                    # but cannot bypass the 48h automatic deletion threshold.
                    if age is None or age < self.stale_hours or item.get("crosslisted"):
                        continue
                    with self._lock:
                        last = self._auto_attempts.get(key)
                        if last is not None and time.monotonic() - last < AUTO_RETRY_SECONDS:
                            continue
                        sections = self._physical_sections().get(key, [])
                        if not sections or key in self._ignored or self._states.get(key, {}).get('status') == 'checking':
                            continue
                        # Do not interrupt an administrator's existing verification.
                        if any(st.get('status') == 'checking' for st in self._states.values()):
                            break
                        self._auto_attempts[key] = time.monotonic()
                        self._states[key] = {"status": "checking"}
                        section = sections[0]
                    checked += 1
                    try:
                        if not self.data_path.is_file():
                            raise ValueError('Active dataset missing; automatic deletion disabled.')
                        original_digest = hashlib.sha256(self.data_path.read_bytes()).digest()
                        self._verify(key, section)  # two independent complete live checks
                        with self._lock:
                            status = self._states.get(key, {}).get('status')
                        if status != 'confirmed_missing':
                            continue
                        # A rescrape or other admin mutation while checking invalidates
                        # this verification, even if the class IDs happen to be the same.
                        if hashlib.sha256(self.data_path.read_bytes()).digest() != original_digest:
                            raise ValueError('Dataset changed during live verification; no class hidden.')
                        result = self.hide_confirmed(key)
                        hidden += 1
                        with self._lock:
                            self._auto_deletions.appendleft({
                                "term": result["term"], "course_code": result["course_code"],
                                "class_number": result["class_number"], "hidden_at": _now().isoformat(),
                            })
                        LOG.warning('automatic_class_hidden term=%s course=%s class=%s',
                                    result['term'], result['course_code'], result['class_number'])
                    except Exception as exc:
                        detail = f'{type(exc).__name__}: {exc}'[:350]
                        with self._lock:
                            self._auto_last_error = detail
                            self._states[key] = {"status": "inconclusive", "error": detail}
                        LOG.exception('automatic_class_hide_skipped class=%s', key)
        finally:
            self._auto_lock.release()
        return {"checked": checked, "hidden": hidden}

    def _auto_run(self) -> None:
        if self._auto_stop.wait(AUTO_INITIAL_SECONDS):
            return
        while not self._auto_stop.is_set():
            try:
                self.auto_cycle()
            except Exception as exc:
                LOG.exception('automatic_class_removal_cycle_failed')
                with self._lock:
                    self._auto_last_error = f'{type(exc).__name__}: {exc}'[:350]
            if self._auto_stop.wait(AUTO_CYCLE_SECONDS):
                break

    def start_auto(self) -> None:
        with self._lock:
            if self._auto_thread and self._auto_thread.is_alive():
                return
            self._auto_stop.clear()
            self._auto_thread = Thread(target=self._auto_run, daemon=True,
                                       name='classcatalog-automatic-removal')
            self._auto_thread.start()

    def stop_auto(self) -> None:
        self._auto_stop.set()
        thread = self._auto_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def dismiss(self, key: str) -> None:
        with self._lock:
            if key not in self._physical_sections():
                raise ValueError("Class number not found.")
            self._ignored.add(key)
            self._states.pop(key, None)

