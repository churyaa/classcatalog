from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import Mapping, Sequence
from urllib.parse import urlencode, urlparse

from classcatalog.instructors import (
    clean_live_instructor_name,
    load_instructor_cache,
    resolve_instructor_cache_path,
    write_instructor_cache,
)
from classcatalog.models import SeatStatus
from classcatalog.ratings import is_placeholder_instructor
from classcatalog.repository import CourseRepository
from classcatalog.scraping.class_detail import parse_class_information_page
from classcatalog.scraping.class_post import find_class_number_actions
from classcatalog.scraping.constants import DETAIL_URL, INSTITUTION_CODE
from classcatalog.scraping.course_option_pagination import CourseOptionPaginationError
from classcatalog.scraping.parser import ResultParseError, parse_course_info_page
from classcatalog.scraping.session import (
    PeopleSoftSessionError,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
)

SEAT_CACHE_SCHEMA_VERSION = 1
SEAT_REFRESH_ENABLED_ENV = "CLASSCATALOG_SEAT_REFRESH_ENABLED"
SEAT_CACHE_PATH_ENV = "CLASSCATALOG_SEAT_CACHE_PATH"
SEAT_REQUEST_DELAY_ENV = "CLASSCATALOG_SEAT_REQUEST_DELAY_SECONDS"
SEAT_PRIORITY_INTERVAL_ENV = "CLASSCATALOG_SEAT_PRIORITY_INTERVAL_SECONDS"
SEAT_PREFERRED_TERM_ENV = "CLASSCATALOG_SEAT_PREFERRED_TERM"  # SPRING_SEAT_REFRESH_PRIORITY_V1
SEAT_INTEREST_TTL_ENV = "CLASSCATALOG_SEAT_INTEREST_TTL_SECONDS"
SEAT_BROWSER_POLL_ENV = "CLASSCATALOG_SEAT_BROWSER_POLL_SECONDS"
SEAT_STALE_AFTER_ENV = "CLASSCATALOG_SEAT_STALE_AFTER_SECONDS"

DEFAULT_REQUEST_DELAY_SECONDS = 0.75
DEFAULT_PRIORITY_INTERVAL_SECONDS = 180.0
DEFAULT_INTEREST_TTL_SECONDS = 900.0
DEFAULT_BROWSER_POLL_SECONDS = 30
DEFAULT_STALE_AFTER_SECONDS = 1800
DEFAULT_CACHE_FLUSH_SECONDS = 30.0
DEFAULT_STATUS_COUNTS_CACHE_SECONDS = 10.0  # BACKEND_PERFORMANCE_PATCH_1
MAX_RETAINED_SEAT_FAILURES = 100
# SEAT_REFRESH_PATCH_3: bounded source retries and admin-only diagnostics.
SLOW_SEAT_REFRESH_SECONDS = 20.0
SEAT_FAILURE_COOLDOWN_SECONDS = 60.0
SEAT_FAILURE_COOLDOWN_MAX_SECONDS = 1800.0
# Confirmed source-inventory drift is retried periodically, but not every minute.
SEAT_INVENTORY_MISMATCH_COOLDOWN_SECONDS = 1800.0
SEAT_INVENTORY_MISMATCH_COOLDOWN_MAX_SECONDS = 21600.0
# Bound expensive per-section PeopleSoft modal requests on linked options.
MAX_LINKED_SEAT_MODAL_LOOKUPS_PER_SOURCE = 4

class SeatInventoryMismatchError(ResultParseError):
    """A complete, correct-course-and-term grid lists other class numbers.

    This is inventory-reconciliation evidence, NOT permission to change a
    stored class number, mark it canceled, or reuse a different class's seats.
    """

    def __init__(
        self, *, course_code: str, term: str,
        expected_class_numbers: set[str], observed_class_numbers: set[str],
    ) -> None:
        self.course_code = course_code
        self.term = term
        self.expected_class_numbers = tuple(sorted(expected_class_numbers))
        self.observed_class_numbers = tuple(sorted(observed_class_numbers))
        super().__init__(
            "The course-information page contains none of the expected class numbers. "
            f"Course={course_code}, term={term}, "
            f"expected={self.expected_class_numbers}, observed={self.observed_class_numbers}."
        )


LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(float(raw), minimum)
    except ValueError:
        return default


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(int(raw), minimum)
    except ValueError:
        return default


def seat_refresh_env_enabled(default: bool = True) -> bool:
    raw = os.getenv(SEAT_REFRESH_ENABLED_ENV)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def resolve_seat_cache_path() -> Path:
    configured = os.getenv(SEAT_CACHE_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent / "data" / "seat_cache.json"


def is_course_info_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    is_sdsu_host = host == "sdsu.edu" or host.endswith(".sdsu.edu")
    return (
        parsed.scheme.casefold() == "https"
        and is_sdsu_host
        and "SSR_CRSE_INFO_FL.GBL" in parsed.path.upper()
    )


@dataclass(frozen=True, slots=True)
class SeatSource:
    url: str
    course_codes: tuple[str, ...]
    schedule_to_term: Mapping[str, str]
    recovery_detail_url: str | None = None
    recovery_subject: str | None = None
    recovery_term: str | None = None
    recovery_term_code: str | None = None


class SeatRefreshService:
    """Low-rate rolling seat refresh over persisted SDSU Course Information URLs."""

    def __init__(
        self,
        repository: CourseRepository,
        *,
        cache_path: Path | None = None,
        instructor_cache_path: Path | None = None,
        request_delay_seconds: float | None = None,
        priority_interval_seconds: float | None = None,
        interest_ttl_seconds: float | None = None,
        browser_poll_seconds: int | None = None,
        stale_after_seconds: int | None = None,
        preferred_term: str | None = None,
        session_factory=None,
    ) -> None:
        self.repository = repository
        self.cache_path = cache_path or resolve_seat_cache_path()
        self.instructor_cache_path = instructor_cache_path or resolve_instructor_cache_path()
        self.request_delay_seconds = (
            request_delay_seconds
            if request_delay_seconds is not None
            else _env_float(SEAT_REQUEST_DELAY_ENV, DEFAULT_REQUEST_DELAY_SECONDS)
        )
        self.priority_interval_seconds = (
            priority_interval_seconds
            if priority_interval_seconds is not None
            else _env_float(SEAT_PRIORITY_INTERVAL_ENV, DEFAULT_PRIORITY_INTERVAL_SECONDS)
        )
        self.interest_ttl_seconds = (
            interest_ttl_seconds
            if interest_ttl_seconds is not None
            else _env_float(SEAT_INTEREST_TTL_ENV, DEFAULT_INTEREST_TTL_SECONDS)
        )
        self.browser_poll_seconds = (
            browser_poll_seconds
            if browser_poll_seconds is not None
            else _env_int(SEAT_BROWSER_POLL_ENV, DEFAULT_BROWSER_POLL_SECONDS, minimum=15)
        )
        self.stale_after_seconds = (
            stale_after_seconds
            if stale_after_seconds is not None
            else _env_int(SEAT_STALE_AFTER_ENV, DEFAULT_STALE_AFTER_SECONDS, minimum=60)
        )
        self._session_factory = session_factory or self._default_session_factory
        self._instructor_records = load_instructor_cache(self.instructor_cache_path)
        self.repository.apply_instructor_updates({
            key: str(raw.get("instructor") or "")
            for key, raw in self._instructor_records.items()
        })
        self._lock = RLock()
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self._sources = self._build_sources()
        # Routine refresh scheduling: Spring first and four preferred pages for
        # each non-preferred page. Manual and actively viewed pages retain
        # their existing higher-priority queues. Other terms never starve.
        configured_term = (
            preferred_term if preferred_term is not None
            else os.getenv(SEAT_PREFERRED_TERM_ENV, "")
        ).strip()
        spring_terms = [
            term for term in self.repository.options().terms
            if term.startswith("Spring ")
        ]
        self.preferred_term = configured_term or (spring_terms[-1] if spring_terms else "")
        preferred_term_codes = {
            section.term_code or section.term
            for section in self.repository.sections
            if section.term == self.preferred_term
        }
        self._preferred_sources = tuple(
            url for url, source in self._sources.items()
            if any(code in preferred_term_codes for code in source.schedule_to_term.values())
        )
        preferred_set = set(self._preferred_sources)
        self._other_sources = tuple(url for url in self._sources if url not in preferred_set)
        self._routine_turn = 0
        self._preferred_cursor = 0
        self._other_cursor = 0
        # Unique refreshable physical seats and source URLs by semester.
        # This is built once; duplicate/cross-listed section rows count once.
        code_to_name = {
            section.term_code or section.term: section.term
            for section in self.repository.sections
        }
        keys_by_term: dict[str, set[tuple[str, str]]] = {}
        sources_by_term: dict[str, set[str]] = {}
        self._source_term_names: dict[str, tuple[str, ...]] = {}
        for url, source in self._sources.items():
            names: set[str] = set()
            for schedule, code in source.schedule_to_term.items():
                name = code_to_name.get(code, code)
                names.add(name)
                keys_by_term.setdefault(name, set()).add((code, schedule))
                sources_by_term.setdefault(name, set()).add(url)
            self._source_term_names[url] = tuple(sorted(names))
        self._term_physical_keys = {
            name: tuple(sorted(keys)) for name, keys in keys_by_term.items()
        }
        self._term_source_pages = {
            name: frozenset(urls) for name, urls in sources_by_term.items()
        }
        # Rotate non-preferred semesters separately. The old single _other_sources
        # queue placed thousands of Fall pages before Summer and Winter, causing
        # those terms to wait for a nearly complete Fall sweep.
        self._other_term_sources: dict[str, tuple[str, ...]] = {}
        assigned_other_urls: set[str] = set()
        for name in sorted(self._term_source_pages):
            if name == self.preferred_term:
                continue
            urls = tuple(
                url for url in self._other_sources
                if url in self._term_source_pages[name] and url not in assigned_other_urls
            )
            if urls:
                self._other_term_sources[name] = urls
                assigned_other_urls.update(urls)
        self._other_term_order = tuple(self._other_term_sources)
        self._other_term_turn = 0
        self._other_term_cursors = {name: 0 for name in self._other_term_order}
        self._source_by_schedule: dict[str, set[str]] = {}
        self._source_by_course_code: dict[str, set[str]] = {}
        for url, source in self._sources.items():
            for schedule_number in source.schedule_to_term:
                self._source_by_schedule.setdefault(schedule_number, set()).add(url)
            for course_code in source.course_codes:
                normalized_code = " ".join(course_code.strip().upper().split())
                self._source_by_course_code.setdefault(normalized_code, set()).add(url)

        self._records: dict[tuple[str, str], dict[str, object]] = {}
        self._source_last_success: dict[str, float] = {}
        self._source_last_attempt: dict[str, float] = {}
        self._source_retry_after: dict[str, float] = {}
        self._source_failure_streak: dict[str, int] = {}
        self._term_attempts: dict[str, int] = {}
        self._term_successes: dict[str, int] = {}
        self._term_failures: dict[str, int] = {}
        self._successful_since_start: set[str] = set()
        self._recent_durations: deque[float] = deque(maxlen=100)
        self._slow_refresh_attempts = 0
        self._term_snapshot: list[dict[str, object]] | None = None
        self._term_snapshot_built_at = 0.0
        self._term_snapshot_records = -1
        self._resolved_url_by_source: dict[str, str] = {}
        # Inventory signals are *not* evidence sufficient to hide a class.
        # Persist per-source only, bounded by the refreshable source count.
        self._missing_numbers_by_source: dict[str, set[str]] = {}
        self._unlisted_numbers_by_source: dict[str, set[str]] = {}
        self._interests: dict[str, float] = {}
        self._manual_queue: deque[str] = deque()
        self._cycle_seen: set[str] = set()
        self._last_full_cycle_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_success_at: float | None = None
        self._last_error: str | None = None
        self._last_source: str | None = None
        self._last_course: str | None = None
        self._last_refresh_duration_seconds: float | None = None
        self._last_sections_updated = 0
        self._last_instructor_updates = 0
        self._successful_sources = 0
        self._failed_sources = 0
        self._requests_completed = 0
        self._failure_history: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._dirty = False
        self._last_flush_monotonic = 0.0
        self._freshness_counts: tuple[int, int, int] | None = None
        self._freshness_built_at = 0.0
        self._freshness_record_total = -1
        self._freshness_cache_hits = 0
        self._freshness_cache_misses = 0
        self._freshness_last_recount_ms = 0.0
        self._load_cache()
        self._apply_cached_records()

    def _default_session_factory(self) -> SdsuPeopleSoftSession:
        config = SdsuHttpConfig(
            delay_seconds=self.request_delay_seconds,
            jitter_seconds=min(max(self.request_delay_seconds * 0.2, 0.0), 0.25),
            max_retries=2,
        )
        return SdsuPeopleSoftSession(config)

    def _build_sources(self) -> dict[str, SeatSource]:
        raw: dict[str, dict[str, object]] = {}
        for section in self.repository.sections:
            if not is_course_info_url(section.source_url):
                continue
            url = str(section.source_url)
            entry = raw.setdefault(
                url,
                {
                    "course_codes": set(),
                    "schedule_to_term": {},
                    "recovery_detail_url": None,
                    "recovery_subject": None,
                    "recovery_term": None,
                    "recovery_term_code": None,
                },
            )
            entry["course_codes"].add(section.course_code)  # type: ignore[union-attr]
            term_key = section.term_code or section.term
            entry["schedule_to_term"].setdefault(section.schedule_number, term_key)  # type: ignore[union-attr]
            if (
                entry.get("recovery_detail_url") is None
                and section.crse_id
                and section.crse_offer_nbr
                and section.term_code
                and section.acad_career
                and section.schedule_number
            ):
                query = urlencode(
                    {
                        "Page": "SSR_CS_WRAP_FL",
                        "Action": "U",
                        "CRSE_ID": section.crse_id,
                        "CRSE_OFFER_NBR": section.crse_offer_nbr,
                        "STRM": section.term_code,
                        "INSTITUTION": INSTITUTION_CODE,
                        "ACAD_CAREER": section.acad_career,
                        "CLASS_NBR": section.schedule_number,
                        "SEC": section.section_number,
                        "pts_Portal": "EMPLOYEE",
                        "pts_PortalHostNode": "SA",
                        "pts_Market": "GBL",
                    }
                )
                entry["recovery_detail_url"] = f"{DETAIL_URL}?{query}"
                entry["recovery_subject"] = section.subject
                entry["recovery_term"] = section.term
                entry["recovery_term_code"] = section.term_code
        return {
            url: SeatSource(
                url=url,
                course_codes=tuple(sorted(value["course_codes"])),  # type: ignore[arg-type]
                schedule_to_term=dict(value["schedule_to_term"]),  # type: ignore[arg-type]
                recovery_detail_url=(
                    str(value.get("recovery_detail_url"))
                    if value.get("recovery_detail_url")
                    else None
                ),
                recovery_subject=(
                    str(value.get("recovery_subject"))
                    if value.get("recovery_subject")
                    else None
                ),
                recovery_term=(
                    str(value.get("recovery_term"))
                    if value.get("recovery_term")
                    else None
                ),
                recovery_term_code=(
                    str(value.get("recovery_term_code"))
                    if value.get("recovery_term_code")
                    else None
                ),
            )
            for url, value in raw.items()
        }

    @property
    def refreshable_source_count(self) -> int:
        return len(self._sources)

    @property
    def enabled(self) -> bool:
        return bool(self._sources)

    def start(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = Thread(target=self._run, name="classcatalog-seat-refresh", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._flush_cache(force=True)

    def register_interest(self, schedule_numbers: Sequence[str]) -> int:
        now = time.time()
        expires = now + self.interest_ttl_seconds
        added_sources: set[str] = set()
        with self._lock:
            for raw in schedule_numbers:
                schedule = str(raw).strip()
                if not schedule:
                    continue
                for source_url in self._source_by_schedule.get(schedule, ()):
                    self._interests[source_url] = expires
                    added_sources.add(source_url)
        if added_sources:
            self._wake.set()
        return len(added_sources)

    def request_full_refresh(self) -> int:
        with self._lock:
            queued = set(self._manual_queue)
            candidates = sorted(
                self._sources,
                key=lambda url: self._source_last_success.get(url, 0.0),
            )
            for url in candidates:
                if url not in queued:
                    self._manual_queue.append(url)
            count = len(self._manual_queue)
        self._wake.set()
        return count

    def request_course_refresh(self, course_code: str) -> int:
        """Queue every refreshable page for one course ahead of routine work."""

        normalized_code = " ".join(str(course_code).strip().upper().split())
        with self._lock:
            candidates = sorted(
                self._source_by_course_code.get(normalized_code, ()),
                key=lambda url: self._source_last_success.get(url, 0.0),
            )
            if not candidates:
                return 0

            # Move matching pages to the front even when a full manual sweep has
            # already queued them. This keeps a single-course Admin refresh prompt.
            for url in candidates:
                try:
                    self._manual_queue.remove(url)
                except ValueError:
                    pass
            self._manual_queue.extendleft(reversed(candidates))

        self._wake.set()
        return len(candidates)

    def _tba_instructor_targets(
        self,
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[str]]:
        all_physical: set[tuple[str, str]] = set()
        refreshable_physical: set[tuple[str, str]] = set()
        source_urls: set[str] = set()
        for section in self.repository.sections:
            if not is_placeholder_instructor(section.instructor):
                continue
            key = (section.term_code or section.term, section.schedule_number)
            all_physical.add(key)
            source_url = str(section.source_url or "")
            if source_url in self._sources:
                refreshable_physical.add(key)
                source_urls.add(source_url)
        return all_physical, refreshable_physical, source_urls

    def request_tba_instructor_refresh(self) -> tuple[int, int]:
        """Queue only course pages that still contain placeholder instructors."""

        _all_physical, refreshable_physical, source_urls = self._tba_instructor_targets()
        with self._lock:
            candidates = sorted(
                source_urls,
                key=lambda url: self._source_last_success.get(url, 0.0),
            )
            for url in candidates:
                try:
                    self._manual_queue.remove(url)
                except ValueError:
                    pass
            self._manual_queue.extendleft(reversed(candidates))
        if candidates:
            self._wake.set()
        return len(candidates), len(refreshable_physical)

    def _next_ready_routine(
        self,
        urls: tuple[str, ...],
        cursor_name: str,
        now: float,
        *,
        term: str | None = None,
    ) -> str | None:
        """Select the least recently successful eligible page within a pool.

        Never-refreshed pages are preferred over pages with prior successes.
        Last-attempt time breaks ties to avoid repeatedly selecting one failed
        page as its cooldown expires. Cursors break remaining ties fairly.
        Admin requests and actively-viewed class priorities are unchanged.
        """
        count = len(urls)
        if not count:
            return None
        cursor = (
            self._other_term_cursors[term]
            if term is not None else getattr(self, cursor_name)
        )
        best_index: int | None = None
        best_age: tuple[float, float] | None = None
        for offset in range(count):
            index = (cursor + offset) % count
            url = urls[index]
            if self._source_retry_after.get(url, 0.0) > now:
                continue
            age = (
                self._source_last_success.get(url, 0.0),
                self._source_last_attempt.get(url, 0.0),
            )
            if best_age is None or age < best_age:
                best_age = age
                best_index = index
                if age == (0.0, 0.0):
                    break
        if best_index is None:
            return None
        if term is not None:
            self._other_term_cursors[term] = best_index + 1
        else:
            setattr(self, cursor_name, best_index + 1)
        return urls[best_index]

    def _next_other_term_source(self, now: float) -> str | None:
        """Give each other semester its own turn, skipping cooling pools.

        A course URL shared across terms is assigned to just one pool; a single
        successful request still refreshes all sections represented by that URL.
        """
        count = len(self._other_term_order)
        for _ in range(count):
            name = self._other_term_order[self._other_term_turn % count]
            self._other_term_turn += 1
            url = self._next_ready_routine(
                self._other_term_sources[name], "_other_cursor", now, term=name
            )
            if url is not None:
                return url
        return None

    def _next_source(self, iteration: int) -> str | None:
        now = time.time()
        with self._lock:
            # Explicit admin refreshes always win, including when on cooldown.
            while self._manual_queue:
                url = self._manual_queue.popleft()
                if url in self._sources:
                    return url

            expired = [url for url, expires in self._interests.items() if expires <= now]
            for url in expired:
                self._interests.pop(url, None)

            if iteration % 5 == 0:
                priority = [
                    url for url in self._interests
                    if self._source_retry_after.get(url, 0.0) <= now
                    and now - self._source_last_attempt.get(url, 0.0) >= self.priority_interval_seconds
                ]
                if priority:
                    return min(priority, key=lambda url: self._source_last_attempt.get(url, 0.0))

            if not self._sources:
                return None
            # Preserve the existing 4:1 Spring/other preference while
            # rotating *which* non-Spring semester receives each other turn.
            # Inside each semester, favor oldest/never refreshed pages.
            use_preferred = bool(self._preferred_sources) and (
                not self._other_sources or self._routine_turn % 5 != 4
            )
            self._routine_turn += 1
            if use_preferred:
                url = self._next_ready_routine(
                    self._preferred_sources, "_preferred_cursor", now
                )
                return url if url is not None else self._next_other_term_source(now)
            url = self._next_other_term_source(now)
            if url is not None:
                return url
            return self._next_ready_routine(
                self._preferred_sources, "_preferred_cursor", now
            )

    def _run(self) -> None:
        iteration = 0
        while not self._stop.is_set():
            try:
                with self._session_factory() as client:
                    client.bootstrap()
                    while not self._stop.is_set():
                        source_url = self._next_source(iteration)
                        iteration += 1
                        if source_url is None:
                            self._wake.wait(5.0)
                            self._wake.clear()
                            continue
                        self._refresh_source(client, source_url)
                        self._flush_cache()
            except Exception as exc:  # noqa: BLE001 - worker must stay alive after upstream/session faults
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("seat_refresh_worker_error error=%s: %s", type(exc).__name__, exc)
                if self._stop.wait(15.0):
                    break

    def _record_source_failure(
        self,
        source_url: str,
        source: SeatSource,
        exc: BaseException,
        *,
        duration_seconds: float,
    ) -> None:
        failed_at = time.time()
        failed_at_iso = datetime.fromtimestamp(failed_at, tz=timezone.utc).isoformat()
        course_codes = tuple(source.course_codes) or ("Unknown course",)
        with self._lock:
            existing = self._failure_history.pop(source_url, None)
            occurrences = int(existing.get("occurrences", 0)) + 1 if existing else 1
            first_failed_at = (
                str(existing.get("first_failed_at"))
                if existing and existing.get("first_failed_at")
                else failed_at_iso
            )
            # Different failure categories should not inherit each other's
            # retry streak. An admin manual request still bypasses the cooldown.
            previous_type = str(existing.get("error_type")) if existing else None
            streak = (self._source_failure_streak.get(source_url, 0) + 1
                      if previous_type == type(exc).__name__ else 1)
            self._source_failure_streak[source_url] = streak
            if isinstance(exc, SeatInventoryMismatchError):
                # Start at 30 minutes, then 1h / 2h / 4h / 6h max.
                delay = min(SEAT_INVENTORY_MISMATCH_COOLDOWN_MAX_SECONDS,
                            SEAT_INVENTORY_MISMATCH_COOLDOWN_SECONDS * (2 ** min(streak - 1, 4)))
                # Record observed changes for read-only inventory review.
                # Never alter live section seats or the source inventory.
                self._missing_numbers_by_source[source_url] = set(exc.expected_class_numbers)
                self._unlisted_numbers_by_source[source_url] = set(exc.observed_class_numbers)
                self._dirty = True
            else:
                delay = min(SEAT_FAILURE_COOLDOWN_MAX_SECONDS,
                            SEAT_FAILURE_COOLDOWN_SECONDS * (2 ** min(streak - 1, 6)))
            next_retry = failed_at + delay
            self._source_retry_after[source_url] = next_retry
            for name in self._source_term_names.get(source_url, ()):
                self._term_failures[name] = self._term_failures.get(name, 0) + 1
            self._record_duration_locked(duration_seconds)
            self._failure_history[source_url] = {
                "course_code": course_codes[0],
                "course_codes": course_codes,
                "source_url": source_url,
                "first_failed_at": first_failed_at,
                "last_failed_at": failed_at_iso,
                "last_failed_timestamp": failed_at,
                "error_type": type(exc).__name__,
                "detail": str(exc),
                "expected_class_numbers": (
                    list(exc.expected_class_numbers)
                    if isinstance(exc, SeatInventoryMismatchError) else []
                ),
                "observed_class_numbers": (
                    list(exc.observed_class_numbers)
                    if isinstance(exc, SeatInventoryMismatchError) else []
                ),
                "occurrences": occurrences,
                "retry_after_at": datetime.fromtimestamp(next_retry, tz=timezone.utc).isoformat(),
                "last_duration_seconds": round(duration_seconds, 3),
                "resolved": False,
            }
            while len(self._failure_history) > MAX_RETAINED_SEAT_FAILURES:
                self._failure_history.popitem(last=False)
            self._failed_sources += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._last_refresh_duration_seconds = duration_seconds

        if isinstance(exc, SeatInventoryMismatchError):
            # Known inventory drift should not flood journald with tracebacks.
            LOGGER.warning(
                "seat_inventory_mismatch course=%s term=%s source=%s "
                "occurrences=%s retry_seconds=%.0f expected=%s observed=%s",
                exc.course_code, exc.term, source_url, occurrences, delay,
                exc.expected_class_numbers, exc.observed_class_numbers,
            )
        else:
            LOGGER.error(
                "seat_refresh_failed course=%s source=%s occurrences=%s duration_seconds=%.2f error=%s: %s",
                ", ".join(course_codes), source_url, occurrences,
                duration_seconds, type(exc).__name__, exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _parse_expected_source_page(self, loaded, source: SeatSource):
        """Parse and validate that PeopleSoft returned the expected course grid.

        A stale Fluid component URL can return HTTP 200 with a perfectly renderable
        page that is *not* ``SSR_CRSE_INFO_FL``.  Treat those structural parse
        failures as stale component state so the refresh service can rediscover the
        course-information URL through the stable course wrapper.
        """

        parsed = parse_course_info_page(loaded.html, source_url=loaded.canonical_url)
        if source.course_codes and parsed.course_code not in source.course_codes:
            raise ResultParseError(
                "The course-information page belongs to an unexpected course "
                f"({parsed.course_code!r}; expected one of {source.course_codes!r})."
            )
        expected_schedules = set(source.schedule_to_term)
        returned_schedules = {
            option.class_number.strip()
            for option in parsed.options
            if option.class_number and option.class_number.strip()
        }
        # One PeopleSoft option row can include multiple physical class actions.
        # The ordinary grid parser only emits its first component.
        returned_schedules.update(
            action.class_number for action in find_class_number_actions(loaded.html)
        )
        if expected_schedules and returned_schedules.isdisjoint(expected_schedules):
            # The URL's STRM/term_code is not independent proof: PeopleSoft's
            # parsed term_code comes from the URL query. Require the displayed
            # term, exact course, complete grid, and actual visible option rows.
            term_names = self._source_term_names.get(source.url, ())
            expected_term_codes = set(source.schedule_to_term.values())
            parsed_term_code = getattr(parsed, "term_code", None)
            safe_inventory_evidence = (
                bool(returned_schedules)
                and bool(parsed.options)
                and getattr(parsed, "options_complete", False) is True
                and len(term_names) == 1
                and str(getattr(parsed, "term", "")).strip().casefold()
                    == term_names[0].casefold()
                and len(expected_term_codes) == 1
                and (not parsed_term_code or parsed_term_code in expected_term_codes)
            )
            if safe_inventory_evidence:
                raise SeatInventoryMismatchError(
                    course_code=parsed.course_code,
                    term=term_names[0],
                    expected_class_numbers=expected_schedules,
                    observed_class_numbers=returned_schedules,
                )
            # Incomplete/wrong-term/empty grids still need session recovery.
            raise ResultParseError(
                "The course-information page contains none of the expected class numbers."
            )
        return parsed

    def _refresh_linked_component_seats(
        self,
        client: SdsuPeopleSoftSession,
        loaded,
        source: SeatSource,
        *,
        parsed_term_code: str | None,
        cache_rows: dict[tuple[str, str], dict[str, object]],
        updates: dict[tuple[str, str], dict[str, object]],
        refreshed_at: str,
    ) -> None:
        """Read each missing linked class from its own Class Availability modal.

        SDSU renders a whole enrollment option in one HTML row (e.g. Discussion
        1002 + Laboratory 1003). Grid-level seat text can repeat the PRIMARY
        component's 64/70 for both links, so it must not be credited to the lab.
        Only an exact class-number modal with actual seat counts can update the
        secondary component's independent seat timestamp.
        """
        expected_terms = set(source.schedule_to_term.values())
        if len(expected_terms) != 1 or parsed_term_code not in expected_terms:
            return

        visible_numbers = {
            action.class_number for action in find_class_number_actions(loaded.html)
        }
        missing = (
            set(source.schedule_to_term)
            & visible_numbers
            - {schedule for _term, schedule in cache_rows}
        )
        if not missing:
            return

        # Prevent many class-modal POSTs from blocking the whole seat-refresh queue.
        # When the source is refreshed again, prioritize older/missing records.
        with self._lock:
            candidates = sorted(
                missing,
                key=lambda number: (
                    _parse_iso_timestamp(
                        self._records.get(
                            (source.schedule_to_term[number], number), {}
                        ).get("updated_at")
                    ) or 0.0,
                    number,
                ),
            )
        for number in candidates[:MAX_LINKED_SEAT_MODAL_LOOKUPS_PER_SOURCE]:
            term_key = source.schedule_to_term[number]
            try:
                tab = client.fetch_class_detail_tab_page(
                    loaded.canonical_url, number, "CA"
                )
                live = parse_class_information_page(tab.html)
                if live.class_number != number or live.selected_tab_value != "CA":
                    raise ValueError(
                        "Class Availability modal did not confirm the requested class"
                    )
                if live.seats_available is None or live.seat_capacity is None:
                    raise ValueError(
                        "Class Availability modal omitted individual seat counts"
                    )
            except Exception as exc:  # noqa: BLE001 - one lab cannot block other seats
                LOGGER.warning(
                    "seat_linked_component_modal_failed course=%s class=%s "
                    "error=%s: %s",
                    ", ".join(source.course_codes[:3]),
                    number,
                    type(exc).__name__,
                    exc,
                )
                continue

            # Open seats win over a delayed Waitlist/Closed status, just as they
            # do for ordinary option rows. Unknown status does not override data.
            if live.seats_available > 0:
                status = SeatStatus.OPEN
            elif live.status is not SeatStatus.UNKNOWN:
                status = live.status
            else:
                current = self.repository.seat_snapshot((number,)).get(
                    (term_key, number)
                )
                status = current.seat_status if current is not None else SeatStatus.UNKNOWN

            enrolled = live.seats_enrolled
            if enrolled is None:
                enrolled = max(live.seat_capacity - live.seats_available, 0)
            key = (term_key, number)
            updates[key] = {
                "seat_status": status,
                "seats_available": live.seats_available,
                "seat_capacity": live.seat_capacity,
                "seats_enrolled": enrolled,
                "seat_updated_at": refreshed_at,
            }
            cache_rows[key] = {
                "term": term_key,
                "schedule_number": number,
                "seat_status": status.value,
                "seats_available": live.seats_available,
                "seat_capacity": live.seat_capacity,
                "seats_enrolled": enrolled,
                "updated_at": refreshed_at,
                "source_url": source.url,
            }

    def _refresh_source(self, client: SdsuPeopleSoftSession, source_url: str) -> None:
        source = self._sources[source_url]
        started_monotonic = time.monotonic()
        attempted_at = time.time()
        with self._lock:
            self._last_attempt_at = attempted_at
            self._source_last_attempt[source_url] = attempted_at
            self._last_source = source_url
            self._last_course = ", ".join(source.course_codes[:3])
            for name in self._source_term_names.get(source_url, ()):
                self._term_attempts[name] = self._term_attempts.get(name, 0) + 1

        try:
            effective_url = self._resolved_url_by_source.get(source_url, source_url)
            direct_error: BaseException | None = None
            try:
                loaded = client.fetch_course_info_page(effective_url)
                parsed = self._parse_expected_source_page(loaded, source)
            except SeatInventoryMismatchError:
                # A verified complete grid cannot be repaired by repeating the
                # same stale URL with a fresh PeopleSoft session.
                raise
            except (PeopleSoftSessionError, ResultParseError, CourseOptionPaginationError) as exc:
                direct_error = exc
                client.reset_public_session()
                client.bootstrap()
                try:
                    loaded = client.fetch_course_info_page(effective_url)
                    parsed = self._parse_expected_source_page(loaded, source)
                except SeatInventoryMismatchError:
                    raise
                except (PeopleSoftSessionError, ResultParseError, CourseOptionPaginationError):
                    if source.recovery_detail_url is None:
                        raise direct_error
                    if (
                        source.recovery_subject
                        and source.recovery_term
                        and source.recovery_term_code
                    ):
                        # A canonical detail URL is not enough in a brand-new Fluid
                        # session.  Rebuild the same exact-subject search state that the
                        # production deep scraper uses when resuming persisted detail
                        # URLs, then follow the wrapper/grouplet chain only as far as
                        # Course Information.
                        client.rehydrate_subject_context(
                            term=source.recovery_term,
                            term_code=source.recovery_term_code,
                            subject=source.recovery_subject,
                        )
                    recovered = client.fetch_detail_pages(source.recovery_detail_url)
                    loaded = type(
                        "RecoveredCourseInfoPage",
                        (),
                        {
                            "html": recovered.course_info_html,
                            "canonical_url": recovered.course_info_url,
                            "expansion_count": recovered.course_info_expansion_count,
                        },
                    )()
                    parsed = self._parse_expected_source_page(loaded, source)
                    with self._lock:
                        self._resolved_url_by_source[source_url] = recovered.course_info_url
                        self._dirty = True
            updates: dict[tuple[str, str], dict[str, object]] = {}
            cache_rows: dict[tuple[str, str], dict[str, object]] = {}
            instructor_updates: dict[tuple[str, str], str] = {}
            instructor_cache_rows: dict[tuple[str, str], dict[str, object]] = {}
            current_sections = self.repository.seat_snapshot(tuple(source.schedule_to_term))
            observed_numbers = {
                str(option.class_number).strip()
                for option in parsed.options
                if getattr(option, "class_number", None)
            }
            expected_numbers = set(source.schedule_to_term)
            expected_terms = set(source.schedule_to_term.values())
            # A partially paginated or wrong-term response can NEVER provide
            # disappearance evidence. An intersection proves this is at least
            # the course grid we expected (not an unrelated PeopleSoft page).
            trustworthy_grid = (
                getattr(parsed, "options_complete", False) is True
                and len(expected_terms) == 1
                and getattr(parsed, "term_code", None) in expected_terms
                and bool(observed_numbers & expected_numbers)
            )
            refreshed_at = _utc_now()
            for option in parsed.options:
                class_number = option.class_number.strip()
                if not class_number:
                    continue
                term_key = source.schedule_to_term.get(class_number)
                if term_key is None:
                    continue
                key = (term_key, class_number)
                current = current_sections.get(key)
                refreshed_instructor = clean_live_instructor_name(option.instructor)
                if (
                    current is not None
                    and is_placeholder_instructor(current.instructor)
                    and not is_placeholder_instructor(refreshed_instructor)
                ):
                    instructor_updates[key] = refreshed_instructor
                    instructor_cache_rows[key] = {
                        "term": term_key,
                        "schedule_number": class_number,
                        "instructor": refreshed_instructor,
                        "updated_at": refreshed_at,
                        "source_url": source_url,
                    }
                if (
                    option.status is SeatStatus.UNKNOWN
                    and option.open_seats is None
                    and option.seat_capacity is None
                    and option.seats_enrolled is None
                ):
                    continue
                # Open seats are authoritative for the transition users care about
                # most. PeopleSoft can briefly leave an old Waitlist/Closed label in
                # the row while simultaneously reporting newly available seats.
                # Never leave ClassCatalog showing Waitlist when SDSU says seats are
                # available.
                refreshed_status = (
                    SeatStatus.OPEN
                    if option.open_seats is not None and option.open_seats > 0
                    else option.status
                )
                update = {
                    "seat_status": refreshed_status,
                    "seats_available": option.open_seats,
                    "seat_capacity": option.seat_capacity,
                    "seats_enrolled": option.seats_enrolled,
                    "seat_updated_at": refreshed_at,
                }
                updates[key] = update
                cache_rows[key] = {
                    "term": term_key,
                    "schedule_number": class_number,
                    "seat_status": refreshed_status.value,
                    "seats_available": option.open_seats,
                    "seat_capacity": option.seat_capacity,
                    "seats_enrolled": option.seats_enrolled,
                    "updated_at": refreshed_at,
                    "source_url": source_url,
                }

            # Recover physical secondary components omitted by the option-row parser.
            self._refresh_linked_component_seats(
                client, loaded, source,
                parsed_term_code=getattr(parsed, "term_code", None),
                cache_rows=cache_rows,
                updates=updates,
                refreshed_at=refreshed_at,
            )
            changed_instructors = self.repository.apply_instructor_updates(instructor_updates)
            if changed_instructors:
                with self._lock:
                    self._instructor_records.update(instructor_cache_rows)
                try:
                    write_instructor_cache(self._instructor_records, self.instructor_cache_path)
                except OSError as exc:
                    LOGGER.warning("instructor_cache_write_failed error=%s", exc)
            changed = self.repository.apply_seat_updates(updates)
            completed_at = time.time()
            with self._lock:
                if trustworthy_grid:
                    # Actions prove the class is still listed even when the
                    # one-component-per-row parser omits a linked lab.
                    listed_numbers = observed_numbers | {
                        action.class_number
                        for action in find_class_number_actions(loaded.html)
                    }
                    missing = expected_numbers - listed_numbers
                    unlisted = observed_numbers - expected_numbers
                    if missing:
                        self._missing_numbers_by_source[source_url] = missing
                    else:
                        self._missing_numbers_by_source.pop(source_url, None)
                    if unlisted:
                        self._unlisted_numbers_by_source[source_url] = unlisted
                    else:
                        self._unlisted_numbers_by_source.pop(source_url, None)
                    self._dirty = True
                self._records.update(cache_rows)
                self._source_last_success[source_url] = completed_at
                self._source_retry_after.pop(source_url, None)
                self._source_failure_streak.pop(source_url, None)
                self._successful_since_start.add(source_url)
                for name in self._source_term_names.get(source_url, ()):
                    self._term_successes[name] = self._term_successes.get(name, 0) + 1
                retained_failure = self._failure_history.get(source_url)
                if retained_failure is not None:
                    retained_failure["resolved"] = True
                self._last_success_at = completed_at
                self._last_error = None
                self._last_sections_updated = len(cache_rows)
                self._last_instructor_updates = changed_instructors
                self._successful_sources += 1
                self._requests_completed += 1 + loaded.expansion_count
                self._cycle_seen.add(source_url)
                if len(self._cycle_seen) >= len(self._sources):
                    self._last_full_cycle_at = completed_at
                    self._cycle_seen.clear()
                self._dirty = self._dirty or bool(cache_rows)
                self._last_refresh_duration_seconds = time.monotonic() - started_monotonic
                self._record_duration_locked(self._last_refresh_duration_seconds)
            if time.monotonic() - started_monotonic >= SLOW_SEAT_REFRESH_SECONDS:
                LOGGER.warning("slow_seat_refresh course=%s duration_seconds=%.2f",
                               ", ".join(source.course_codes[:3]),
                               time.monotonic() - started_monotonic)
            _ = changed
        except Exception as exc:  # noqa: BLE001 - one failed course must not stop the rolling queue
            self._record_source_failure(
                source_url,
                source,
                exc,
                duration_seconds=time.monotonic() - started_monotonic,
            )

    @staticmethod
    def _cache_key(term: str, schedule_number: str) -> str:
        return f"{term}::{schedule_number}"

    def _load_cache(self) -> None:
        if not self.cache_path.is_file():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != SEAT_CACHE_SCHEMA_VERSION:
            return
        records = payload.get("sections")
        if isinstance(records, dict):
            for raw in records.values():
                if not isinstance(raw, dict):
                    continue
                term = str(raw.get("term") or "").strip()
                schedule = str(raw.get("schedule_number") or "").strip()
                if term and schedule:
                    self._records[(term, schedule)] = dict(raw)
        sources = payload.get("sources")
        if isinstance(sources, dict):
            for url, raw in sources.items():
                if url not in self._sources or not isinstance(raw, dict):
                    continue
                value = _parse_iso_timestamp(raw.get("last_success_at"))
                if value is not None:
                    self._source_last_success[url] = value
                resolved_url = raw.get("resolved_url")
                if isinstance(resolved_url, str) and is_course_info_url(resolved_url):
                    self._resolved_url_by_source[url] = resolved_url
                source = self._sources[url]
                for field, destination in (
                    ("missing_class_numbers", self._missing_numbers_by_source),
                    ("unlisted_class_numbers", self._unlisted_numbers_by_source),
                ):
                    values = raw.get(field)
                    if not isinstance(values, list):
                        continue
                    cleaned = {x.strip() for x in values if isinstance(x, str) and x.strip()}
                    if field == "missing_class_numbers":
                        cleaned.intersection_update(source.schedule_to_term)
                    if cleaned:
                        destination[url] = cleaned
        # A cache write can now contain inventory-drift evidence without any
        # successful seat fetch. Never misreport that write as a live refresh.
        if self._source_last_success:
            self._last_success_at = max(self._source_last_success.values())

    def _apply_cached_records(self) -> None:
        updates: dict[tuple[str, str], dict[str, object]] = {}
        for key, raw in self._records.items():
            try:
                status = SeatStatus(str(raw.get("seat_status") or "unknown"))
            except ValueError:
                status = SeatStatus.UNKNOWN
            updates[key] = {
                "seat_status": status,
                "seats_available": raw.get("seats_available"),
                "seat_capacity": raw.get("seat_capacity"),
                "seats_enrolled": raw.get("seats_enrolled"),
                "seat_updated_at": raw.get("updated_at"),
            }
        self.repository.apply_seat_updates(updates)

    def _flush_cache(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._dirty:
                return
            if not force and now - self._last_flush_monotonic < DEFAULT_CACHE_FLUSH_SECONDS:
                return
            records = {
                self._cache_key(term, schedule): dict(raw)
                for (term, schedule), raw in self._records.items()
            }
            sources = {
                url: {
                    "last_success_at": (
                        datetime.fromtimestamp(self._source_last_success[url], tz=timezone.utc).isoformat()
                        if url in self._source_last_success else None
                    ),
                    "resolved_url": self._resolved_url_by_source.get(url),
                    "missing_class_numbers": sorted(self._missing_numbers_by_source.get(url, ())),
                    "unlisted_class_numbers": sorted(self._unlisted_numbers_by_source.get(url, ())),
                }
                for url in (
                    self._source_last_success.keys()
                    | self._missing_numbers_by_source.keys()
                    | self._unlisted_numbers_by_source.keys()
                )
            }
            payload = {
                "schema_version": SEAT_CACHE_SCHEMA_VERSION,
                "updated_at": _utc_now(),
                "sections": records,
                "sources": sources,
            }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.cache_path)
        except OSError as exc:
            with self._lock:
                self._last_error = f"Seat cache write failed: {exc}"
            return
        with self._lock:
            self._dirty = False
            self._last_flush_monotonic = now

    def public_status(self) -> dict[str, object]:
        """Publish live status with short-lived cached *summary* counts.

        Actual section seats and last-refresh timestamps remain immediately live.
        Count summaries are cached for at most 10 seconds, unless record count
        changes, which also keeps fresh + stale + pending equal to cached_sections.
        """
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            cache_valid = (
                self._freshness_counts is not None
                and monotonic_now - self._freshness_built_at < DEFAULT_STATUS_COUNTS_CACHE_SECONDS
                and len(self._records) == self._freshness_record_total
            )
            if cache_valid:
                self._freshness_cache_hits += 1
            else:
                recount_start = time.perf_counter()
                fresh_cached = 0
                stale_cached = 0
                pending_cached = 0
                for raw in self._records.values():
                    updated_at = _parse_iso_timestamp(raw.get("updated_at"))
                    if updated_at is None:
                        pending_cached += 1
                    elif max(0.0, now - updated_at) > self.stale_after_seconds:
                        stale_cached += 1
                    else:
                        fresh_cached += 1
                self._freshness_counts = (fresh_cached, stale_cached, pending_cached)
                self._freshness_record_total = len(self._records)
                self._freshness_built_at = monotonic_now
                self._freshness_cache_misses += 1
                self._freshness_last_recount_ms = (time.perf_counter() - recount_start) * 1000.0
                if self._freshness_last_recount_ms >= 250.0:
                    LOGGER.warning(
                        "slow_seat_status_recount duration_ms=%.1f records=%d",
                        self._freshness_last_recount_ms, len(self._records),
                    )

            fresh_cached, stale_cached, pending_cached = self._freshness_counts
            age = None if self._last_success_at is None else max(0.0, now - self._last_success_at)
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "refreshable_course_pages": len(self._sources),
                "cached_sections": len(self._records),
                "fresh_cached_sections": fresh_cached,
                "stale_cached_sections": stale_cached,
                "pending_cached_sections": pending_cached,
                "last_success_at": (
                    datetime.fromtimestamp(self._last_success_at, tz=timezone.utc).isoformat()
                    if self._last_success_at is not None
                    else None
                ),
                "age_seconds": round(age, 1) if age is not None else None,
                "browser_poll_seconds": self.browser_poll_seconds,
                "stale_after_seconds": self.stale_after_seconds,
                "priority_interval_seconds": self.priority_interval_seconds,
                "preferred_term": self.preferred_term,
                "preferred_source_pages": len(self._preferred_sources),
                "other_source_pages": len(self._other_sources),
            }

    def missing_section_keys(self) -> set[str]:
        """Physical keys absent from the last trustworthy complete course grid."""
        with self._lock:
            keys: set[str] = set()
            for url, numbers in self._missing_numbers_by_source.items():
                source = self._sources.get(url)
                if source is None:
                    continue
                for number in numbers:
                    term = source.schedule_to_term.get(number)
                    if term is not None:
                        keys.add(f"{term}::{number}")
            return keys

    def unlisted_source_urls(self) -> set[str]:
        """Sources with newly observed numbers needing a targeted detail rescrape."""
        with self._lock:
            return {url for url, values in self._unlisted_numbers_by_source.items()
                    if values and url in self._sources}

    def inventory_drift_status(self) -> dict[str, object]:
        """Admin-only read-only inventory-change telemetry."""
        with self._lock:
            missing = sorted(self.missing_section_keys())
            unlisted = sorted({
                f"{','.join(self._sources[url].course_codes)}::{number}"
                for url, numbers in self._unlisted_numbers_by_source.items()
                if url in self._sources
                for number in numbers
            })
            return {
                "missing_section_candidates": len(missing),
                "missing_section_examples": missing[:20],
                "unlisted_class_numbers": len(unlisted),
                "unlisted_class_examples": unlisted[:20],
            }

    def missing_number_source_urls(self) -> set[str]:
        """Minimal, lock-safe failure query for removal review.

        Avoid rebuilding the full admin health response and scanning TBA targets
        every time the Possible Removed Classes dashboard refreshes.
        """
        with self._lock:
            return {
                source_url
                for source_url, failure in self._failure_history.items()
                if "contains none of the expected class numbers" in str(failure.get("detail", ""))
                and not bool(failure.get("resolved", False))
                and not (
                    (success := self._source_last_success.get(source_url)) is not None
                    and success > float(failure.get("last_failed_timestamp") or 0.0)
                )
            }

    def _record_duration_locked(self, duration: float) -> None:
        """Called under self._lock; retain only the last 100 attempt durations."""
        self._recent_durations.append(duration)
        if duration >= SLOW_SEAT_REFRESH_SECONDS:
            self._slow_refresh_attempts += 1

    def _semester_freshness(self) -> list[dict[str, object]]:
        """Admin-only stats for unique refreshable physical classes by term."""
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            cached = (
                self._term_snapshot is not None
                and monotonic_now - self._term_snapshot_built_at < DEFAULT_STATUS_COUNTS_CACHE_SECONDS
                and self._term_snapshot_records == len(self._records)
            )
            if cached:
                return self._term_snapshot
            rows: list[dict[str, object]] = []
            for term, keys in sorted(self._term_physical_keys.items()):
                fresh = stale = pending = cached_count = 0
                for key in keys:
                    record = self._records.get(key)
                    if record is None:
                        continue
                    cached_count += 1
                    timestamp = _parse_iso_timestamp(record.get('updated_at'))
                    if timestamp is None:
                        pending += 1
                    elif max(0.0, now - timestamp) > self.stale_after_seconds:
                        stale += 1
                    else:
                        fresh += 1
                total = len(keys)
                rows.append({
                    'term': term,
                    'refreshable_sections': total,
                    'source_pages': len(self._term_source_pages.get(term, ())),
                    'cached_sections': cached_count,
                    'fresh_cached_sections': fresh,
                    'stale_cached_sections': stale,
                    'pending_cached_sections': pending,
                    'uncached_sections': total - cached_count,
                    'fresh_percent': round(100.0 * fresh / total, 1) if total else 0.0,
                })
            self._term_snapshot = rows
            self._term_snapshot_built_at = monotonic_now
            self._term_snapshot_records = len(self._records)
            return rows

    def admin_status(self) -> dict[str, object]:
        public = self.public_status()
        all_tba, refreshable_tba, tba_sources = self._tba_instructor_targets()
        freshness = self._semester_freshness()
        with self._lock:
            failures: list[dict[str, object]] = []
            for source_url, retained in reversed(self._failure_history.items()):
                failed_at = float(retained.get("last_failed_timestamp") or 0.0)
                last_success_at = self._source_last_success.get(source_url)
                failures.append(
                    {
                        key: value
                        for key, value in retained.items()
                        if key != "last_failed_timestamp"
                    }
                    | {
                        "resolved": bool(
                            retained.get("resolved", last_success_at and last_success_at > failed_at)
                        )
                    }
                )
            public.update(
                {
                    "last_attempt_at": (
                        datetime.fromtimestamp(self._last_attempt_at, tz=timezone.utc).isoformat()
                        if self._last_attempt_at is not None
                        else None
                    ),
                    "last_full_cycle_at": (
                        datetime.fromtimestamp(self._last_full_cycle_at, tz=timezone.utc).isoformat()
                        if self._last_full_cycle_at is not None
                        else None
                    ),
                    "last_course": self._last_course,
                    "last_error": self._last_error,
                    "last_refresh_duration_seconds": self._last_refresh_duration_seconds,
                    "last_sections_updated": self._last_sections_updated,
                    "last_instructor_updates": self._last_instructor_updates,
                    "tba_instructor_physical_sections": len(refreshable_tba),
                    "all_tba_instructor_physical_sections": len(all_tba),
                    "tba_instructor_course_pages": len(tba_sources),
                    "instructor_cache_records": len(self._instructor_records),
                    "successful_course_refreshes": self._successful_sources,
                    "failed_course_refreshes": self._failed_sources,
                    "requests_completed": self._requests_completed,
                    "priority_sources": len(self._interests),
                    "manual_refresh_pending": len(self._manual_queue),
                    "refreshable_course_codes": sorted(self._source_by_course_code),
                    "seat_failures": failures,
                    "seat_failure_retention_limit": MAX_RETAINED_SEAT_FAILURES,
                    "inventory_drift": self.inventory_drift_status(),
                    "inventory_mismatch_sources_since_start": sum(
                        1 for failure in self._failure_history.values()
                        if failure.get("error_type") == "SeatInventoryMismatchError"
                        and not failure.get("resolved", False)
                    ),
                    "request_delay_seconds": self.request_delay_seconds,
                    "semester_freshness": [
                        dict(row,
                             attempts_since_start=self._term_attempts.get(str(row["term"]), 0),
                             successes_since_start=self._term_successes.get(str(row["term"]), 0),
                             failures_since_start=self._term_failures.get(str(row["term"]), 0),
                             source_pages_successful_since_start=len(
                                 self._successful_since_start.intersection(
                                     self._term_source_pages.get(str(row["term"]), ())
                                 )
                             )) for row in freshness
                    ],
                    "cooldown_sources": sum(
                        until > time.time() for until in self._source_retry_after.values()
                    ),
                    "slow_refresh_attempts": self._slow_refresh_attempts,
                    "recent_duration_samples": len(self._recent_durations),
                    "recent_duration_avg_seconds": (
                        round(sum(self._recent_durations) / len(self._recent_durations), 3)
                        if self._recent_durations else None
                    ),
                    "recent_duration_p95_seconds": (
                        round(sorted(self._recent_durations)[
                            (95 * len(self._recent_durations) + 99) // 100 - 1
                        ], 3) if self._recent_durations else None
                    ),
                    "seat_status_cache_hits": self._freshness_cache_hits,
                    "seat_status_cache_misses": self._freshness_cache_misses,
                    "seat_status_last_recount_ms": round(self._freshness_last_recount_ms, 3),
                    "seat_update_performance": self.repository.seat_update_performance(),
                    "priority_interval_seconds": self.priority_interval_seconds,
                    "stale_after_seconds": self.stale_after_seconds,
                }
            )
        return public

    def seat_records(self, schedule_numbers: Sequence[str]) -> dict[str, dict[str, object]]:
        wanted = {str(value).strip() for value in schedule_numbers if str(value).strip()}
        current = self.repository.seat_snapshot(tuple(wanted))
        records: dict[str, dict[str, object]] = {}
        for (term, schedule), section in current.items():
            cached = self._records.get((term, schedule), {})
            records[f"{section.term}::{schedule}"] = {
                "schedule_number": schedule,
                "term": section.term,
                "seat_status": section.seat_status.value,
                "seats_available": section.seats_available,
                "seat_capacity": section.seat_capacity,
                "seats_enrolled": section.seats_enrolled,
                "instructor": section.instructor,
                "updated_at": section.seat_updated_at or cached.get("updated_at"),
            }
        return records
