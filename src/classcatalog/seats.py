from __future__ import annotations

from bisect import bisect_left, insort
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import Event, RLock, Thread
import time
from typing import Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup

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
    PeopleSoftCircuitBreakerOpen,
    PeopleSoftSessionError,
    PeopleSoftTransientError,
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
# CLASSCATALOG_SEAT_OPTIONAL_RECOVERY_GUARD_V1: only defer EXTRA
# fallback calls; never interrupt an in-flight PeopleSoft request.
SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS = 50.0
SEAT_OPTIONAL_RECOVERY_COOLDOWN_SECONDS = 300.0
SEAT_FAILURE_COOLDOWN_SECONDS = 60.0
SEAT_FAILURE_COOLDOWN_MAX_SECONDS = 1800.0
# Confirmed source-inventory drift is retried periodically, but not every minute.
SEAT_INVENTORY_MISMATCH_COOLDOWN_SECONDS = 1800.0
SEAT_INVENTORY_MISMATCH_COOLDOWN_MAX_SECONDS = 21600.0
# Allow at most one expensive empty-page fresh-subject recovery every two minutes.
SEAT_EMPTY_GRID_RECOVERY_GLOBAL_INTERVAL_SECONDS = 120.0
# Bound expensive per-section PeopleSoft modal requests on linked options.
MAX_LINKED_SEAT_MODAL_LOOKUPS_PER_SOURCE = 4

class SeatRefreshRecoveryDeferredError(RuntimeError):
    """A slow failed seat fetch is deferred instead of starting more fallback calls."""

    def __init__(self, *, stage: str, elapsed_seconds: float) -> None:
        self.stage = stage
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"Skipped optional {stage} after {elapsed_seconds:.1f}s spent on this source"
        )


class SeatInventoryMismatchError(ResultParseError):
    """Correct-course-and-term evidence disagrees with stored class inventory.

    Either a complete grid displays other class numbers, or SDSU explicitly
    says "No scheduled classes" in the course option message field. This is a
    review signal, NOT permission to hide/remove classes or change their seats.
    """

    def __init__(
        self, *, course_code: str, term: str,
        expected_class_numbers: set[str], observed_class_numbers: set[str],
        reason: str = "different_class_numbers",
    ) -> None:
        self.course_code = course_code
        self.term = term
        self.reason = reason
        self.expected_class_numbers = tuple(sorted(expected_class_numbers))
        self.observed_class_numbers = tuple(sorted(observed_class_numbers))
        super().__init__(
            "The course-information page contains none of the expected class numbers. "
            f"Course={course_code}, term={term}, reason={reason}, "
            f"expected={self.expected_class_numbers}, observed={self.observed_class_numbers}."
        )


class SeatUnverifiedEmptyGridError(ResultParseError):
    """Empty course page was not independently verified as missing inventory.

    Preserve historical seats and do NOT create disappearance evidence. This
    happens when fresh discovery is unavailable, throttled, or inconclusive.
    """

    def __init__(
        self, *, course_code: str, term: str,
        expected_class_numbers: set[str], recovery_status: str,
    ) -> None:
        self.course_code = course_code
        self.term = term
        self.reason = "no_scheduled_classes"
        self.recovery_status = recovery_status
        self.expected_class_numbers = tuple(sorted(expected_class_numbers))
        self.observed_class_numbers: tuple[str, ...] = ()
        super().__init__(
            f"Unverified empty course listing for {course_code}, term={term}, "
            f"recovery_status={recovery_status}. No missing-section conclusion."
        )


LOGGER = logging.getLogger(__name__)

# Max routine pages in a row from one semester + subject before affinity pauses.
SEAT_ROUTINE_SUBJECT_BATCH_MAX = 8
# SEAT_BOUNDED_SUBJECT_BATCH_V2: limit the age gap and overtaking cost per batch.
SEAT_SUBJECT_BATCH_MAX_AGE_GAP_SECONDS = 900.0
SEAT_SUBJECT_BATCH_MAX_OVERTAKEN_PAGES = 12
# CLASSCATALOG_BOUNDED_VIEWED_REFRESH_V1: guarantee periodic catalog-wide progress.
SEAT_MAX_VIEWED_BURST = 4
# CLASSCATALOG_VERIFIED_SUBJECT_SESSION_REUSE_V1: experimental, OFF by default.
SEAT_SUBJECT_SESSION_REUSE_ENV = "CLASSCATALOG_SEAT_REUSE_SUBJECT_SESSION"


def _has_explicit_no_scheduled_classes_message(html: str) -> bool:
    """Recognize SDSU's *course-option* empty state, not generic page text.

    The page also contains hidden widgets labeled 'No results to display'.
    Only SSR_CLSRCH_F_WK_SSR_MSG_TEXT is suitable evidence that the current
    course view explicitly lists no scheduled classes. Even this is only a
    review signal: PeopleSoft filters may be responsible for an empty view.
    """
    soup = BeautifulSoup(html, 'html.parser')
    status = soup.find(id='SSR_CLSRCH_F_WK_SSR_MSG_TEXT')
    if status is None:
        return False
    message = ' '.join(status.get_text(' ', strip=True).split()).casefold()
    return message == 'no scheduled classes'


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
        self.subject_session_reuse_enabled = (
            os.getenv(SEAT_SUBJECT_SESSION_REUSE_ENV, "0").strip().lower()
            in {"1", "yes", "true", "on"}
        )
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
        self._consecutive_viewed_selections = 0
        # CLASSCATALOG_CONTEXT_FOLLOWUP_V1: at most one same-subject follow-up per viewed success.
        self._context_followup_after: tuple[str, float] | None = None
        # CLASSCATALOG_ROUTINE_SPRING_CONTEXT_FOLLOWUP_V1: one bounded Spring routine sibling on a verified success.
        self._routine_followup_after: tuple[str, float] | None = None
        # CLASSCATALOG_VIEWED_CHAIN_CONTEXT_V1: one extra verified Spring sibling after viewed follow-up.
        self._routine_followup_from_viewed = False
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
        self._source_by_term_schedule: dict[tuple[str, str], set[str]] = {}
        self._source_by_course_code: dict[str, set[str]] = {}
        for url, source in self._sources.items():
            for schedule_number in source.schedule_to_term:
                self._source_by_schedule.setdefault(schedule_number, set()).add(url)
                name = code_to_name.get(source.schedule_to_term[schedule_number], "")
                self._source_by_term_schedule.setdefault((name, schedule_number), set()).add(url)
            for course_code in source.course_codes:
                normalized_code = " ".join(course_code.strip().upper().split())
                self._source_by_course_code.setdefault(normalized_code, set()).add(url)
        # Build once; do not rescan all 6,000+ source pages on each selection.
        sources_by_context: dict[tuple[str, str, str], list[str]] = {}
        for url, source in self._sources.items():
            context = self._seat_source_context(source)
            if context is not None:
                sources_by_context.setdefault(context, []).append(url)
        self._sources_by_context = {
            context: tuple(urls) for context, urls in sources_by_context.items()
        }

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
        self._last_empty_recovery_attempt_monotonic = float("-inf")
        self._empty_grid_recovery_successes = 0
        # Inventory signals are *not* evidence sufficient to hide a class.
        # Persist per-source only, bounded by the refreshable source count.
        self._missing_numbers_by_source: dict[str, set[str]] = {}
        self._unlisted_numbers_by_source: dict[str, set[str]] = {}
        self._interests: dict[str, float] = {}
        # Remember which exact physical classes were viewed, not just their shared
        # course source. A single course page can contain dozens of linked sections.
        self._viewed_class_expirations: dict[tuple[str, str], float] = {}
        # Failed exact-class modal lookups should not monopolize future refreshes.
        self._linked_modal_retry_after: dict[tuple[str, str], float] = {}
        # CLASSCATALOG_SEAT_FRESHNESS_ARCH_V1: persistent browser-saved demand is
        # lower priority than visible classes and bounded by routine turns.
        self._saved_interests: dict[str, float] = {}
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
        # SEAT_SUBJECT_BATCH_TELEMETRY_V1: bounded in-memory telemetry and routine subject affinity.
        self._seat_throughput_events: deque[tuple[float, str, int]] = deque(maxlen=10000)
        # Bounded, nonpersistent timing samples: one per context attempt and page call.
        self._seat_stage_durations: deque[tuple[float, str, float]] = deque(maxlen=2000)
        self._worker_context_affinity: tuple[str, str, str] | None = None
        self._routine_batch_context: tuple[str, str, str] | None = None
        self._routine_batch_count = 0
        self._requests_completed = 0
        self._failure_history: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._dirty = False
        self._last_flush_monotonic = 0.0
        # CLASSCATALOG_SEAT_STATUS_INDEX_V1: sorted verified timestamps allow O(log n) recounts.
        self._freshness_epochs_by_key: dict[tuple[str, str], float | None] | None = None
        self._freshness_sorted_epochs: list[float] = []
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
                    self._viewed_class_expirations[(source_url, schedule)] = expires
                    added_sources.add(source_url)
        if added_sources:
            self._wake.set()
        return len(added_sources)

    def register_saved_interest(
        self, schedule_numbers: Sequence[str], *, section_keys: Sequence[str] = (),
    ) -> int:
        """Prioritize browser-saved favorites/schedule without starving routine work.

        This contains no account identifiers and stores no browser state on disk.
        Deduplicate physical class -> source mappings; only source URLs are kept.
        The browser renews this short-lived demand while its tab is active.
        """
        now = time.time()
        expires = now + self.interest_ttl_seconds
        added_sources: set[str] = set()
        with self._lock:
            for raw in schedule_numbers:
                schedule = str(raw).strip()
                if not schedule:
                    continue
                for url in self._source_by_schedule.get(schedule, ()):
                    self._saved_interests[url] = expires
                    added_sources.add(url)
            # Term-qualified keys prevent recycled class numbers in older
            # semesters from becoming priority targets for a saved Spring class.
            for raw in section_keys:
                value = str(raw).strip()
                term, separator, number = value.rpartition("::")
                if not separator or not term or not number:
                    continue
                for url in self._source_by_term_schedule.get((term, number), ()):
                    self._saved_interests[url] = expires
                    added_sources.add(url)
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
        """Choose oldest eligible work, with bounded same-subject affinity.

        Spring/other allocation is done by _next_source and is unchanged.
        Manual and viewed requests preempt this routine selection entirely.
        Never advance an already-successful page ahead of a never-successful
        page. Within a single freshness tier, permit at most a short, bounded
        bypass of similarly aged pages to avoid repeated PeopleSoft rehydrates.
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
        matching_index: int | None = None
        matching_age: tuple[float, float] | None = None
        eligible_ages: list[tuple[float, float]] = []
        active_context = self._worker_context_affinity
        if (
            active_context is not None
            and self._routine_batch_context == active_context
            and self._routine_batch_count >= SEAT_ROUTINE_SUBJECT_BATCH_MAX
        ):
            active_context = None

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
            if active_context is not None:
                eligible_ages.append(age)
                if self._seat_source_context(self._sources[url]) == active_context:
                    if matching_age is None or age < matching_age:
                        matching_index = index
                        matching_age = age
            elif age == (0.0, 0.0):
                # Original fast path when there is no verified session affinity.
                break

        if best_index is None or best_age is None:
            return None
        original_best_index = best_index
        if matching_index is not None and matching_age is not None:
            # A source with no prior successful seat fetch must never be
            # bypassed by one that has already succeeded. Those two groups
            # correspond to distinct freshness tiers.
            if best_age[0] == 0.0:
                same_tier = matching_age[0] == 0.0
                age_gap = matching_age[1] - best_age[1]
            else:
                same_tier = matching_age[0] > 0.0
                age_gap = matching_age[0] - best_age[0]
            if (
                same_tier
                and 0.0 <= age_gap <= SEAT_SUBJECT_BATCH_MAX_AGE_GAP_SECONDS
                and sum(age < matching_age for age in eligible_ages)
                    <= SEAT_SUBJECT_BATCH_MAX_OVERTAKEN_PAGES
            ):
                best_index = matching_index

        if term is not None:
            self._other_term_cursors[term] = best_index + 1
        else:
            setattr(self, cursor_name, best_index + 1)
        chosen_url = urls[best_index]
        if best_index != original_best_index:
            # Selection events are not successful refresh events: report them
            # separately in the admin-only throughput telemetry.
            self._seat_throughput_events.append((time.time(), "batch", 0))
        chosen_context = self._seat_source_context(self._sources[chosen_url])
        if chosen_context is None:
            self._routine_batch_context = None
            self._routine_batch_count = 0
        elif chosen_context == self._routine_batch_context:
            self._routine_batch_count += 1
        else:
            self._routine_batch_context = chosen_context
            self._routine_batch_count = 1
        return chosen_url

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

    def _choose_viewed_source_locked(self, urls: list[str], now: float) -> str:
        """Prefer eligible viewed pages, optionally reusing verified subject context.

        The oldest eligible page wins unless a matching-context page is close in
        age and bypasses at most the same bounded number as routine batching.
        Caller holds self._lock; no PeopleSoft request takes place here.
        """
        attempts = self._source_last_attempt
        chosen = min(urls, key=lambda url: attempts.get(url, 0.0))
        active_context = self._worker_context_affinity
        if active_context is not None:
            matching = [
                url for url in urls
                if self._seat_source_context(self._sources[url]) == active_context
            ]
            if matching:
                candidate = min(matching, key=lambda url: attempts.get(url, 0.0))
                oldest_attempt = attempts.get(chosen, 0.0)
                candidate_attempt = attempts.get(candidate, 0.0)
                older_pages = sum(
                    attempts.get(url, 0.0) < candidate_attempt for url in urls
                )
                if (
                    candidate_attempt >= oldest_attempt
                    and candidate_attempt - oldest_attempt
                        <= SEAT_SUBJECT_BATCH_MAX_AGE_GAP_SECONDS
                    and older_pages <= SEAT_SUBJECT_BATCH_MAX_OVERTAKEN_PAGES
                ):
                    chosen = candidate
        # Keep a bounded burst of viewed work so routine pages make progress.
        self._consecutive_viewed_selections += 1
        # The next scheduler call may reuse this context only after this
        # exact viewed source has actually completed successfully.
        self._context_followup_after = (chosen, now)
        self._routine_followup_after = None
        self._routine_followup_from_viewed = False
        self._routine_batch_context = None
        self._routine_batch_count = 0
        self._seat_throughput_events.append((now, "priority", 0))
        return chosen

    def _choose_context_followup_locked(
        self, context: tuple[str, str, str], now: float,
    ) -> str | None:
        """Choose one stale, non-viewed page while verified context is still live.

        Only the existing worker calls this, after a *successful* viewed refresh.
        No request is issued here. Cooldowns, interest and recent attempts are
        respected; at most one extra page can follow each viewed selection.
        """
        oldest: tuple[float, float] | None = None
        selected: str | None = None
        stale_before = now - self.stale_after_seconds
        for url in self._sources_by_context.get(context, ()):
            if url in self._interests:
                continue
            if self._source_retry_after.get(url, 0.0) > now:
                continue
            last_attempt = self._source_last_attempt.get(url, 0.0)
            if now - last_attempt < self.priority_interval_seconds:
                continue
            last_success = self._source_last_success.get(url, 0.0)
            if last_success > stale_before:
                continue
            age = (last_success, last_attempt)
            if oldest is None or age < oldest:
                selected, oldest = url, age
        return selected

    def _take_routine_context_followup_locked(self, now: float) -> str | None:
        """Consume a single pending verified Spring sibling; never chain implicitly.

        The viewed-origin exception may run before another viewed request, but
        only for ONE sibling. Admin manual work always runs before this helper.
        """
        routine_followup = self._routine_followup_after
        viewed_origin = self._routine_followup_from_viewed
        self._routine_followup_after = None
        self._routine_followup_from_viewed = False
        if (
            routine_followup is None
            or not self._preferred_sources
            or (self._other_sources and self._routine_turn % 5 == 4)
        ):
            return None
        root_url, selected_at = routine_followup
        context = self._seat_source_context(self._sources[root_url])
        if (
            context is None
            or context[0] != self.preferred_term
            or context != self._worker_context_affinity
            or self._source_last_success.get(root_url, 0.0) < selected_at
            or (
                self._routine_batch_context == context
                and self._routine_batch_count >= SEAT_ROUTINE_SUBJECT_BATCH_MAX
            )
        ):
            return None
        sibling = self._choose_context_followup_locked(context, now)
        if sibling is None:
            return None
        self._consecutive_viewed_selections = 0
        if self._routine_batch_context == context:
            self._routine_batch_count += 1
        else:
            self._routine_batch_context = context
            self._routine_batch_count = 1
        self._routine_turn += 1
        self._seat_throughput_events.append((now, "routine", 0))
        self._seat_throughput_events.append((now, "routine_context_followup", 0))
        if viewed_origin:
            self._seat_throughput_events.append((now, "viewed_chain_followup", 0))
        return sibling

    def _next_source(self, iteration: int) -> str | None:
        now = time.time()
        with self._lock:
            # Admin requests remain absolute priority, even on cooldown.
            while self._manual_queue:
                url = self._manual_queue.popleft()
                if url in self._sources:
                    # Manual work always preempts and cancels any follow-up.
                    self._context_followup_after = None
                    self._routine_followup_after = None
                    self._routine_followup_from_viewed = False
                    self._routine_batch_context = None
                    self._routine_batch_count = 0
                    self._seat_throughput_events.append((now, "priority", 0))
                    return url

            expired = [url for url, expires in self._interests.items() if expires <= now]
            for url in expired:
                self._interests.pop(url, None)

            # A successful viewed page may be followed by exactly one stale
            # page in the same currently verified PeopleSoft subject.
            followup = self._context_followup_after
            self._context_followup_after = None
            if followup is not None:
                viewed_url, selected_at = followup
                context = self._seat_source_context(self._sources[viewed_url])
                if (
                    context is not None
                    and context == self._worker_context_affinity
                    and self._source_last_success.get(viewed_url, 0.0) >= selected_at
                ):
                    sibling = self._choose_context_followup_locked(context, now)
                    if sibling is not None:
                        self._consecutive_viewed_selections = 0
                        self._routine_batch_context = context
                        self._routine_batch_count = 1
                        self._routine_turn += 1
                        # The very next verified Spring routine slot may
                        # reuse this sibling once, even with viewed work.
                        # This is not an unbounded follow-up chain.
                        self._routine_followup_after = (sibling, now)
                        self._routine_followup_from_viewed = True
                        self._seat_throughput_events.append((now, "routine", 0))
                        self._seat_throughput_events.append((now, "context_followup", 0))
                        return sibling

            # A verified viewed sibling gets at most one extra Spring
            # context-reuse turn before we consider another viewed page.
            # The helper clears its pending state even on failure.
            if self._routine_followup_from_viewed:
                chained = self._take_routine_context_followup_locked(now)
                if chained is not None:
                    return chained

            viewed = [
                url for url in self._interests
                if self._source_retry_after.get(url, 0.0) <= now
                and now - self._source_last_attempt.get(url, 0.0)
                    >= self.priority_interval_seconds
            ]
            if viewed and self._consecutive_viewed_selections < SEAT_MAX_VIEWED_BURST:
                return self._choose_viewed_source_locked(viewed, now)

            # Saved favorites and schedule classes are demand-driven, but may
            # consume only the remaining priority-burst budget. A routine slot
            # is guaranteed after four combined viewed/saved selections.
            # A freshly verified saved source does not need another request.
            for url, expires in tuple(self._saved_interests.items()):
                if expires <= now:
                    self._saved_interests.pop(url, None)
            if self._consecutive_viewed_selections < SEAT_MAX_VIEWED_BURST:
                saved = [
                    url for url in self._saved_interests
                    if url not in self._interests
                    and self._source_retry_after.get(url, 0.0) <= now
                    and now - self._source_last_attempt.get(url, 0.0)
                        >= self.priority_interval_seconds
                    and now - self._source_last_success.get(url, 0.0)
                        >= self.stale_after_seconds
                ]
                if saved:
                    def saved_age(url: str) -> tuple[float, float, str]:
                        return (self._source_last_attempt.get(url, 0.0),
                                self._source_last_success.get(url, 0.0), url)
                    oldest = min(saved, key=saved_age)
                    chosen = oldest
                    # Batch saved classes into the already *verified* subject
                    # context, with the same age/overtake limits as routine work.
                    if self._worker_context_affinity is not None:
                        matching = [url for url in saved if
                            self._seat_source_context(self._sources[url])
                            == self._worker_context_affinity]
                        if matching:
                            candidate = min(matching, key=saved_age)
                            baseline = self._source_last_attempt.get(oldest, 0.0)
                            candidate_age = self._source_last_attempt.get(candidate, 0.0)
                            older = sum(saved_age(url) < saved_age(candidate) for url in saved)
                            if (candidate_age >= baseline
                                and candidate_age - baseline <= SEAT_SUBJECT_BATCH_MAX_AGE_GAP_SECONDS
                                and older <= SEAT_SUBJECT_BATCH_MAX_OVERTAKEN_PAGES):
                                chosen = candidate
                                if chosen != oldest:
                                    self._seat_throughput_events.append((now, "batch", 0))
                    self._consecutive_viewed_selections += 1
                    self._context_followup_after = None
                    self._routine_followup_after = None
                    self._routine_followup_from_viewed = False
                    self._routine_batch_context = None
                    self._routine_batch_count = 0
                    self._seat_throughput_events.append((now, "saved", 0))
                    return chosen

            # Standard routine continuation remains below viewed-class
            # priority. The viewed-origin exception was handled above.
            if self._routine_followup_after is not None:
                sibling = self._take_routine_context_followup_locked(now)
                if sibling is not None:
                    return sibling

            if not self._sources:
                return None
            # Preserve the existing 4:1 Spring-to-other *routine* ratio.
            # One routine slot after a viewed burst prevents indefinite starvation.
            use_preferred = bool(self._preferred_sources) and (
                not self._other_sources or self._routine_turn % 5 != 4
            )
            self._routine_turn += 1
            if use_preferred:
                url = self._next_ready_routine(
                    self._preferred_sources, "_preferred_cursor", now
                )
                selected = url if url is not None else self._next_other_term_source(now)
            else:
                selected = self._next_other_term_source(now)
                if selected is None:
                    selected = self._next_ready_routine(
                        self._preferred_sources, "_preferred_cursor", now
                    )
            if selected is not None:
                self._consecutive_viewed_selections = 0
                # Only the NEXT selection can follow this page, and only
                # after its current attempt has succeeded. Never chain.
                self._routine_followup_after = (selected, now)
                self._routine_followup_from_viewed = False
                self._seat_throughput_events.append((now, "routine", 0))
                return selected
            # If all routine sources are cooling down, do not idle when a
            # viewed source is still eligible for a safe retry.
            if viewed:
                return self._choose_viewed_source_locked(viewed, now)
            return None

    @staticmethod
    def _seat_source_context(source: SeatSource) -> tuple[str, str, str] | None:
        """Term, STRM, subject required to make stored Fluid detail URLs usable.

        Without this context, PeopleSoft can return an unrelated component or
        an empty course grid from an otherwise valid saved course-info URL.
        Do not guess a subject for cross-listed/multi-term sources.
        """
        if (
            len(source.course_codes) == 1
            and source.recovery_term
            and source.recovery_term_code
            and source.recovery_subject
        ):
            return (
                source.recovery_term,
                source.recovery_term_code,
                source.recovery_subject,
            )
        return None

    @staticmethod
    def _reuse_search_proves_target(pages, result, context, source: SeatSource) -> bool:
        """Reject ambiguous/wrong-target searches before using live Fluid state.

        Course identity comes from a freshly returned exact-subject result row,
        not the URL alone. Final seat updates *also* require the existing
        verified course/term/class-number checks in _refresh_source.
        """
        term, strm, subject = context
        if (
            pages.verified_absent or pages.subject != subject
            or pages.term != term or pages.term_code != strm
            or result.term != term or result.term_code != strm
            or result.subject != subject or result.complete is not True
            or not result.courses or not source.recovery_detail_url
        ):
            return False
        identifiers = parse_qs(urlparse(source.recovery_detail_url).query)
        crse_id = (identifiers.get("CRSE_ID") or [""])[0]
        offer = (identifiers.get("CRSE_OFFER_NBR") or [""])[0]
        if not crse_id or not offer:
            return False
        return any(
            hit.term == term and hit.term_code == strm and hit.subject == subject
            and hit.course_code in source.course_codes
            and str(hit.crse_id or "") == crse_id
            and str(hit.crse_offer_nbr or "") == offer
            for hit in result.courses
        )

    def _prepare_worker_subject(
        self, client: SdsuPeopleSoftSession, context: tuple[str, str, str],
        source: SeatSource, *, can_reuse: bool,
    ) -> bool:
        """Use no-reset search only if valid; otherwise run legacy fresh reset.

        Returns True only when the *same* public session was safely reused.
        A failed/incomplete/wrong-target search never reaches a seat update.
        """
        term, strm, subject = context
        if can_reuse:
            with self._lock:
                self._seat_throughput_events.append((time.time(), "reuse_attempt", 0))
            generation_before = client.public_session_generation
            try:
                pages = client.rehydrate_subject_context(
                    term=term, term_code=strm, subject=subject, reset_session=False,
                )
                result = client.parse_subject_pages(pages)
                if not self._reuse_search_proves_target(pages, result, context, source):
                    raise ValueError("Reused subject search did not prove the exact offering")
                if client.public_session_generation != generation_before:
                    # The scraper's internal state-recovery already reset the
                    # session. This was a valid fresh setup, not actual reuse.
                    with self._lock:
                        self._seat_throughput_events.append((time.time(), "reuse_internal_reset", 0))
                    return False
                with self._lock:
                    self._seat_throughput_events.append((time.time(), "reuse_verified", 0))
                return True
            except (PeopleSoftTransientError, PeopleSoftCircuitBreakerOpen):
                # Respect SDSU throttling and circuit breakers: no immediate
                # extra fresh-session request after a rate-limit/final failure.
                raise
            except Exception as exc:
                with self._lock:
                    self._seat_throughput_events.append((time.time(), "reuse_fallback", 0))
                LOGGER.warning(
                    "seat_subject_session_reuse_rejected term=%s subject=%s error_type=%s",
                    term, subject, type(exc).__name__,
                )
                # A failed reused search can leave opaque PeopleSoft state.
                # One fresh reset is mandatory; the existing worker catches
                # failures of this fresh attempt and preserves prior seats.
        client.rehydrate_subject_context(term=term, term_code=strm, subject=subject)
        return False

    def _run(self) -> None:
        # SEAT_WORKER_SUBJECT_CONTEXT_V1: prime PeopleSoft before stored URLs.
        # Preserve manual requests, student interest, and the existing 4:1
        # semester scheduler. Only reuse context for consecutive matching
        # term/subject requests in the *same* live PeopleSoft session.
        iteration = 0
        while not self._stop.is_set():
            try:
                with self._session_factory() as client:
                    client.bootstrap()
                    active_context: tuple[str, str, str] | None = None
                    healthy_session = False
                    self._worker_context_affinity = None
                    while not self._stop.is_set():
                        source_url = self._next_source(iteration)
                        iteration += 1
                        if source_url is None:
                            self._wake.wait(5.0)
                            self._wake.clear()
                            continue
                        source = self._sources[source_url]
                        needed_context = self._seat_source_context(source)
                        if needed_context is None:
                            # Do not trust any previous subject for an ambiguous
                            # source. Existing per-source recovery remains intact.
                            active_context = None
                            healthy_session = False
                            self._worker_context_affinity = None
                        elif needed_context != active_context:
                            started = time.monotonic()
                            try:
                                term, term_code, subject = needed_context
                                reused = self._prepare_worker_subject(
                                    client, needed_context, source,
                                    can_reuse=(
                                        self.subject_session_reuse_enabled
                                        and healthy_session
                                        and active_context is not None
                                        and self._worker_context_affinity == active_context
                                        and isinstance(getattr(client, "public_session_generation", None), int)
                                    ),
                                )
                            except Exception as exc:
                                # A context failure is a source failure, never
                                # a successful seat refresh or removal signal.
                                active_context = None
                                healthy_session = False
                                self._worker_context_affinity = None
                                self._record_source_failure(
                                    source_url,
                                    source,
                                    exc,
                                    duration_seconds=time.monotonic() - started,
                                )
                                self._flush_cache()
                                continue
                            finally:
                                # Include failed context preparations in timing totals.
                                with self._lock:
                                    self._seat_stage_durations.append((
                                        time.time(), "context", time.monotonic() - started,
                                    ))
                            active_context = needed_context
                            self._worker_context_affinity = active_context
                            with self._lock:
                                self._seat_throughput_events.append((time.time(), "context", 0))
                            LOGGER.info(
                                "seat_subject_context_ready term=%s subject=%s reused=%s",
                                term, subject, reused,
                            )
                        failures_before = self._failed_sources
                        successful_before = self._successful_sources
                        page_started = time.monotonic()
                        try:
                            self._refresh_source(client, source_url)
                        finally:
                            # Includes retries, parsing and in-page recovery.
                            with self._lock:
                                self._seat_stage_durations.append((
                                    time.time(), "page", time.monotonic() - page_started,
                                ))
                        if (
                            self._failed_sources != failures_before
                            or self._worker_context_affinity is None
                        ):
                            # Session-reset fallbacks invalidate worker affinity
                            # even when a recovered course page succeeds.
                            active_context = None
                            healthy_session = False
                            self._worker_context_affinity = None
                        else:
                            # Do NOT treat an unverified/unfinished page as a
                            # healthy session for the next subject transition.
                            healthy_session = self._successful_sources > successful_before
                        self._flush_cache()
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self._worker_context_affinity = None
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
            if isinstance(exc, SeatRefreshRecoveryDeferredError):
                delay = SEAT_OPTIONAL_RECOVERY_COOLDOWN_SECONDS
            elif isinstance(exc, (SeatInventoryMismatchError, SeatUnverifiedEmptyGridError)):
                # Empty-but-unverified pages also use a long cooldown, preventing
                # repeated expensive recoveries while SDSU's state is inconsistent.
                delay = min(SEAT_INVENTORY_MISMATCH_COOLDOWN_MAX_SECONDS,
                            SEAT_INVENTORY_MISMATCH_COOLDOWN_SECONDS * (2 ** min(streak - 1, 4)))
                if isinstance(exc, SeatInventoryMismatchError):
                    # Only an independently trustworthy grid supplies inventory evidence.
                    self._missing_numbers_by_source[source_url] = set(exc.expected_class_numbers)
                    self._unlisted_numbers_by_source[source_url] = set(exc.observed_class_numbers)
                else:
                    # Older empty-grid patches may have persisted false missing flags.
                    # This response proves neither removal nor a replacement section.
                    self._missing_numbers_by_source.pop(source_url, None)
                    self._unlisted_numbers_by_source.pop(source_url, None)
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
                "inventory_reason": (
                    exc.reason if isinstance(exc, (SeatInventoryMismatchError, SeatUnverifiedEmptyGridError))
                    else None
                ),
                "empty_recovery_status": (
                    exc.recovery_status if isinstance(exc, SeatUnverifiedEmptyGridError)
                    else None
                ),
                "expected_class_numbers": (
                    list(exc.expected_class_numbers)
                    if isinstance(exc, (SeatInventoryMismatchError, SeatUnverifiedEmptyGridError))
                    else []
                ),
                "observed_class_numbers": (
                    list(exc.observed_class_numbers)
                    if isinstance(exc, (SeatInventoryMismatchError, SeatUnverifiedEmptyGridError))
                    else []
                ),
                "occurrences": occurrences,
                "retry_after_at": datetime.fromtimestamp(next_retry, tz=timezone.utc).isoformat(),
                "last_duration_seconds": round(duration_seconds, 3),
                "resolved": False,
            }
            while len(self._failure_history) > MAX_RETAINED_SEAT_FAILURES:
                self._failure_history.popitem(last=False)
            self._failed_sources += 1
            self._seat_throughput_events.append((failed_at, "failure", 0))
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._last_refresh_duration_seconds = duration_seconds

        if isinstance(exc, SeatInventoryMismatchError):
            # Known inventory drift should not flood journald with tracebacks.
            LOGGER.warning(
                "seat_inventory_mismatch course=%s term=%s source=%s "
                "occurrences=%s retry_seconds=%.0f reason=%s expected=%s observed=%s",
                exc.course_code, exc.term, source_url, occurrences, delay,
                exc.reason, exc.expected_class_numbers, exc.observed_class_numbers,
            )
        elif isinstance(exc, SeatUnverifiedEmptyGridError):
            LOGGER.warning(
                "seat_empty_grid_unverified course=%s term=%s source=%s "
                "occurrences=%s retry_seconds=%.0f recovery_status=%s expected=%s",
                exc.course_code, exc.term, source_url, occurrences, delay,
                exc.recovery_status, exc.expected_class_numbers,
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
            # term and complete grid. An empty grid is meaningful ONLY when the
            # course-option status field explicitly says 'No scheduled classes'.
            term_names = self._source_term_names.get(source.url, ())
            expected_term_codes = set(source.schedule_to_term.values())
            parsed_term_code = getattr(parsed, "term_code", None)
            empty_listing = (
                not returned_schedules
                and not parsed.options
                and _has_explicit_no_scheduled_classes_message(loaded.html)
            )
            visible_different_classes = bool(returned_schedules) and bool(parsed.options)
            safe_inventory_evidence = (
                (visible_different_classes or empty_listing)
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
                    reason=(
                        "no_scheduled_classes" if empty_listing
                        else "different_class_numbers"
                    ),
                )
            # Empty/ambiguous/incomplete/wrong-term grids still need recovery.
            raise ResultParseError(
                "The course-information page contains none of the expected class numbers."
            )
        return parsed

    def _seat_timed_call(self, stage: str, call: Callable[[], object]):
        """Record phase cost even on failure, without changing client retry rules."""
        started = time.monotonic()
        try:
            return call()
        finally:
            with self._lock:
                self._seat_stage_durations.append((
                    time.time(), stage, max(0.0, time.monotonic() - started),
                ))

    def _check_optional_seat_recovery_budget(
        self, started_monotonic: float | None, stage: str,
    ) -> None:
        """Stop BEFORE a new fallback network call, never mid-response.

        An ordinary successful direct page is still accepted even if it took
        longer than this budget; the safeguard only avoids costly escalation.
        """
        if started_monotonic is None:
            return
        elapsed = time.monotonic() - started_monotonic
        if elapsed < SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS:
            return
        with self._lock:
            self._seat_throughput_events.append((time.time(), "recovery_deferred", 0))
        raise SeatRefreshRecoveryDeferredError(stage=stage, elapsed_seconds=elapsed)

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
        deadline_monotonic: float | None = None,
        waitlist_modal_numbers: set[str] | None = None,
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
        expected_numbers = set(source.schedule_to_term)
        grid_verified_numbers = {schedule for _term, schedule in cache_rows}
        missing = expected_numbers & visible_numbers - grid_verified_numbers
        # Verify primary Waitlist rows whose grid lacks authoritative counts.
        waitlist_modals = (
            (waitlist_modal_numbers or set()) & visible_numbers & expected_numbers
        )
        visible_targets = missing | waitlist_modals

        # One explicitly viewed, missing class can be recovered even if absent
        # from the initial page actions: the session client opens its modal from
        # fresh state and expands option pagination until it finds that class.
        # Preserve the exact-class and CA-tab verification below. Do NOT run
        # these extra lookups on routine background course sweeps.
        with self._lock:
            now = time.time()
            viewed = self._interests.get(source.url, 0.0) > now
            viewed_grace = 2 if viewed else 0

            def candidate_age(number: str) -> float:
                key = (source.schedule_to_term[number], number)
                return _parse_iso_timestamp(
                    self._records.get(key, {}).get("updated_at")
                ) or 0.0

            def viewed_first(number: str) -> tuple[bool, float, str]:
                return (
                    self._viewed_class_expirations.get((source.url, number), 0.0) <= now,
                    candidate_age(number), number,
                )

            visible_candidates = sorted(
                (number for number in visible_targets
                 if self._linked_modal_retry_after.get((source.url, number), 0.0) <= now),
                key=lambda number: (number not in missing, *viewed_first(number)),
            )
            hidden_candidates = sorted(
                (number for number in expected_numbers - visible_numbers - grid_verified_numbers
                 if self._linked_modal_retry_after.get((source.url, number), 0.0) <= now),
                key=viewed_first,
            ) if viewed else []

            # Reserve at most ONE of the existing four lookup slots for a viewed
            # hidden section; this cannot increase the max requests per course.
            limit = MAX_LINKED_SEAT_MODAL_LOOKUPS_PER_SOURCE
            candidates = (
                [hidden_candidates[0], *visible_candidates[:limit - 1]]
                if hidden_candidates else visible_candidates[:limit]
            )
        if viewed and (hidden_candidates or visible_candidates):
            LOGGER.info(
                "seat_linked_component_plan course=%s visible_targets=%s "
                "hidden_targets=%s selected=%s",
                ", ".join(source.course_codes[:3]), len(visible_candidates),
                len(hidden_candidates), ",".join(candidates),
            )
        for number in candidates:
            # Preserve already-verified primary seats rather than starting another
            # potentially slow class-modal fetch after the optional work budget.
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if viewed_grace > 0:
                    viewed_grace -= 1
                else:
                    with self._lock:
                        self._seat_throughput_events.append((time.time(), "linked_modal_deferred", 0))
                    break
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
                if (
                    live.status is SeatStatus.WAITLIST
                    and (getattr(live, "waitlist_available", None) is None
                         or getattr(live, "waitlist_capacity", None) is None)
                ):
                    raise ValueError(
                        "Class Availability modal omitted verified waitlist counts"
                    )
            except Exception as exc:  # noqa: BLE001 - one lab cannot block other seats
                with self._lock:
                    # Per-class cooldown: repeated unverified modals must not
                    # starve other linked components of their four lookup slots.
                    self._linked_modal_retry_after[(source.url, number)] = time.time() + 600.0
                LOGGER.warning(
                    "seat_linked_component_modal_failed course=%s class=%s "
                    "error=%s: %s",
                    ", ".join(source.course_codes[:3]),
                    number,
                    type(exc).__name__,
                    exc,
                )
                continue

            with self._lock:
                self._linked_modal_retry_after.pop((source.url, number), None)
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
            waitlist_fields = {
                name: getattr(live, name)
                for name in ("waitlist_available", "waitlist_capacity", "waitlist_total")
                if getattr(live, name, None) is not None
            }
            updates[key] = {
                **waitlist_fields,
                "seat_status": status,
                "seats_available": live.seats_available,
                "seat_capacity": live.seat_capacity,
                "seats_enrolled": enrolled,
                "seat_updated_at": refreshed_at,
            }
            cache_rows[key] = {
                **waitlist_fields,
                "term": term_key,
                "schedule_number": number,
                "seat_status": status.value,
                "seats_available": live.seats_available,
                "seat_capacity": live.seat_capacity,
                "seats_enrolled": enrolled,
                "updated_at": refreshed_at,
                "source_url": source.url,
            }

    def _recover_explicit_empty_grid(
        self, client: SdsuPeopleSoftSession, source_url: str, source: SeatSource,
        *, refresh_started_monotonic: float | None = None,
    ):
        """Try fresh exact-subject discovery once, without trusting an empty URL.

        Only a unique hit for the recorded course offering and a matching,
        complete detail grid can refresh seats. A missing/ambiguous result is
        *unverified*, never a reason to mark stored sections removed.
        """
        course_code = source.course_codes[0] if source.course_codes else "Unknown course"
        term = source.recovery_term or (self._source_term_names.get(source_url, ("",))[0])
        expected = set(source.schedule_to_term)

        def unverified(status: str) -> SeatUnverifiedEmptyGridError:
            return SeatUnverifiedEmptyGridError(
                course_code=course_code, term=term,
                expected_class_numbers=expected, recovery_status=status,
            )

        if (
            len(source.course_codes) != 1 or not source.recovery_detail_url
            or not source.recovery_subject or not source.recovery_term
            or not source.recovery_term_code
        ):
            raise unverified("missing_recovery_metadata")
        if self._source_term_names.get(source_url) != (source.recovery_term,):
            raise unverified("ambiguous_source_term")

        # A source-level 30m+ cooldown and this global guard bound costs even
        # when thousands of saved URLs return misleading empty course grids.
        with self._lock:
            now = time.monotonic()
            if (now - self._last_empty_recovery_attempt_monotonic
                    < SEAT_EMPTY_GRID_RECOVERY_GLOBAL_INTERVAL_SECONDS):
                raise unverified("global_rate_limit")
            self._last_empty_recovery_attempt_monotonic = now

        try:
            self._check_optional_seat_recovery_budget(
                refresh_started_monotonic, "empty_grid_subject_recovery",
            )
            # Rehydration resets cookies/server component state, so the worker's
            # previous context must not remain a valid affinity hint.
            self._worker_context_affinity = None
            pages = self._seat_timed_call(
                "empty_subject", lambda: client.rehydrate_subject_context(
                    term=source.recovery_term,
                    term_code=source.recovery_term_code,
                    subject=source.recovery_subject,
                ),
            )
            result = client.parse_subject_pages(pages)
            if (
                not result.complete or result.term != source.recovery_term
                or result.term_code != source.recovery_term_code
                or result.subject != source.recovery_subject
            ):
                raise unverified("incomplete_or_wrong_subject_search")

            query = parse_qs(urlparse(source.recovery_detail_url).query)
            expected_id = (query.get("CRSE_ID") or [""])[0]
            expected_offer = (query.get("CRSE_OFFER_NBR") or [""])[0]
            expected_career = (query.get("ACAD_CAREER") or [""])[0]
            if not expected_id or not expected_offer:
                raise unverified("missing_course_identity")
            candidates = [
                hit for hit in result.courses
                if hit.course_code == course_code
                and hit.term == source.recovery_term
                and hit.term_code == source.recovery_term_code
                and hit.subject == source.recovery_subject
                and str(hit.crse_id or "") == expected_id
                and str(hit.crse_offer_nbr or "") == expected_offer
                and (not expected_career or not hit.acad_career
                     or str(hit.acad_career) == expected_career)
                and hit.detail_url
                and (hit.section_count is None or hit.section_count > 0)
            ]
            if len(candidates) != 1:
                raise unverified("course_offering_not_unique_or_missing")
            fresh_detail_url = candidates[0].detail_url
            fresh_parts = urlparse(fresh_detail_url)
            fresh_host = (fresh_parts.hostname or "").casefold()
            if not (
                fresh_parts.scheme.casefold() == "https"
                and (fresh_host == "sdsu.edu" or fresh_host.endswith(".sdsu.edu"))
                and "SSR_CS_WRAP_FL.GBL" in fresh_parts.path.upper()
            ):
                raise unverified("unsafe_fresh_detail_url")
            self._check_optional_seat_recovery_budget(
                refresh_started_monotonic, "empty_grid_detail_recovery",
            )
            detail = self._seat_timed_call(
                "empty_detail", lambda: client.fetch_detail_pages(fresh_detail_url),
            )
            loaded = type(
                "RecoveredCourseInfoPage", (),
                {
                    "html": detail.course_info_html,
                    "canonical_url": detail.course_info_url,
                    "expansion_count": detail.course_info_expansion_count,
                },
            )()
            try:
                parsed = self._parse_expected_source_page(loaded, source)
            except SeatInventoryMismatchError as exc:
                if exc.reason == "no_scheduled_classes":
                    raise unverified("fresh_detail_still_empty") from exc
                raise
            if (
                parsed.course_code != course_code
                or parsed.term.strip().casefold() != source.recovery_term.casefold()
                or parsed.term_code != source.recovery_term_code
                or parsed.options_complete is not True
                or not any(
                    str(option.class_number).strip() in expected
                    for option in parsed.options
                )
            ):
                raise unverified("fresh_detail_not_verified")

            if not is_course_info_url(detail.course_info_url):
                raise unverified("invalid_recovered_info_url")
            with self._lock:
                self._resolved_url_by_source[source_url] = detail.course_info_url
                self._empty_grid_recovery_successes += 1
                self._dirty = True
            LOGGER.info(
                "seat_empty_grid_recovered course=%s term=%s source=%s "
                "matched_options=%s",
                course_code, term, source_url,
                sum(str(option.class_number).strip() in expected for option in parsed.options),
            )
            return loaded, parsed
        except SeatRefreshRecoveryDeferredError as exc:
            raise unverified("recovery_budget_exceeded") from exc
        except (SeatUnverifiedEmptyGridError, SeatInventoryMismatchError):
            raise
        except Exception as exc:
            # Fail closed on PeopleSoft timeouts/parse faults; preserve seats.
            raise unverified("fresh_recovery_failed") from exc

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
                loaded = self._seat_timed_call(
                    "direct", lambda: client.fetch_course_info_page(effective_url),
                )
                parsed = self._parse_expected_source_page(loaded, source)
            except SeatInventoryMismatchError as exc:
                if exc.reason != "no_scheduled_classes":
                    # Visible *different* class numbers remain genuine review evidence.
                    raise
                loaded, parsed = self._recover_explicit_empty_grid(
                    client, source_url, source,
                    refresh_started_monotonic=started_monotonic,
                )
            except (PeopleSoftSessionError, ResultParseError, CourseOptionPaginationError) as exc:
                direct_error = exc
                # When the direct call already consumed the budget, defer the
                # entire fallback and let the worker refresh another course.
                self._check_optional_seat_recovery_budget(
                    started_monotonic, "session_retry",
                )
                self._worker_context_affinity = None
                self._seat_timed_call("session_reset", client.reset_public_session)
                self._check_optional_seat_recovery_budget(
                    started_monotonic, "session_bootstrap",
                )
                self._seat_timed_call("bootstrap", client.bootstrap)
                self._check_optional_seat_recovery_budget(
                    started_monotonic, "course_retry",
                )
                try:
                    loaded = self._seat_timed_call(
                        "retry", lambda: client.fetch_course_info_page(effective_url),
                    )
                    parsed = self._parse_expected_source_page(loaded, source)
                except SeatInventoryMismatchError as exc:
                    if exc.reason != "no_scheduled_classes":
                        raise
                    loaded, parsed = self._recover_explicit_empty_grid(
                        client, source_url, source,
                        refresh_started_monotonic=started_monotonic,
                    )
                except (PeopleSoftSessionError, ResultParseError, CourseOptionPaginationError):
                    if source.recovery_detail_url is None:
                        raise direct_error
                    if (
                        source.recovery_subject
                        and source.recovery_term
                        and source.recovery_term_code
                    ):
                        self._check_optional_seat_recovery_budget(
                            started_monotonic, "subject_recovery",
                        )
                        # A canonical detail URL is not enough in a brand-new Fluid
                        # session.  Rebuild the same exact-subject search state that the
                        # production deep scraper uses when resuming persisted detail
                        # URLs, then follow the wrapper/grouplet chain only as far as
                        # Course Information.
                        self._worker_context_affinity = None
                        self._seat_timed_call(
                            "subject_recovery", lambda: client.rehydrate_subject_context(
                                term=source.recovery_term,
                                term_code=source.recovery_term_code,
                                subject=source.recovery_subject,
                            ),
                        )
                    self._check_optional_seat_recovery_budget(
                        started_monotonic, "detail_recovery",
                    )
                    recovered = self._seat_timed_call(
                        "detail_recovery", lambda: client.fetch_detail_pages(source.recovery_detail_url),
                    )
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
                waitlist_fields = {
                    name: getattr(option, name)
                    for name in ("waitlist_available", "waitlist_capacity", "waitlist_total")
                    if getattr(option, name, None) is not None
                }
                if refreshed_status is SeatStatus.WAITLIST and not (
                    "waitlist_available" in waitlist_fields
                    and "waitlist_capacity" in waitlist_fields
                ):
                    # Grid status can identify Waitlist but cannot verify its
                    # numerical capacity. Update the status without falsely
                    # refreshing its timestamp; the exact-class modal follows.
                    old = self._records.get(key, {})
                    updates[key] = {
                        "seat_status": refreshed_status,
                        "seats_available": option.open_seats,
                        "seat_capacity": option.seat_capacity,
                        "seats_enrolled": option.seats_enrolled,
                    }
                    cache_rows[key] = {
                        "term": term_key,
                        "schedule_number": class_number,
                        "seat_status": refreshed_status.value,
                        "seats_available": option.open_seats,
                        "seat_capacity": option.seat_capacity,
                        "seats_enrolled": option.seats_enrolled,
                        "updated_at": old.get("updated_at"),
                        "source_url": source_url,
                        **{
                            name: old[name]
                            for name in ("waitlist_available", "waitlist_capacity", "waitlist_total")
                            if old.get(name) is not None
                        },
                    }
                    continue
                update = {
                    **waitlist_fields,
                    "seat_status": refreshed_status,
                    "seats_available": option.open_seats,
                    "seat_capacity": option.seat_capacity,
                    "seats_enrolled": option.seats_enrolled,
                    "seat_updated_at": refreshed_at,
                }
                updates[key] = update
                cache_rows[key] = {
                    **waitlist_fields,
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
            self._seat_timed_call(
                "linked_modals", lambda: self._refresh_linked_component_seats(
                    client, loaded, source,
                    parsed_term_code=getattr(parsed, "term_code", None),
                    waitlist_modal_numbers={
                        option.class_number.strip()
                        for option in parsed.options
                        if getattr(option, "class_number", None)
                        and option.status is SeatStatus.WAITLIST
                        and not (option.open_seats is not None and option.open_seats > 0)
                        and (getattr(option, "waitlist_available", None) is None
                             or getattr(option, "waitlist_capacity", None) is None)
                    },
                    cache_rows=cache_rows,
                    updates=updates,
                    refreshed_at=refreshed_at,
                    deadline_monotonic=(
                        started_monotonic + SEAT_OPTIONAL_RECOVERY_BUDGET_SECONDS
                    ),
                ),
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
                self._update_freshness_index_locked(cache_rows)
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
                self._seat_throughput_events.append((completed_at, "success", len(cache_rows)))
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
                **{
                    name: raw[name]
                    for name in ("waitlist_available", "waitlist_capacity", "waitlist_total")
                    if name in raw and raw[name] is not None
                },
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

    def _rebuild_freshness_index_locked(self) -> None:
        """Index persisted timestamps once; caller holds the seat-service lock."""
        epochs = {
            key: _parse_iso_timestamp(raw.get("updated_at"))
            for key, raw in self._records.items()
        }
        self._freshness_epochs_by_key = epochs
        self._freshness_sorted_epochs = sorted(
            value for value in epochs.values() if value is not None
        )

    def _update_freshness_index_locked(
        self, cache_rows: Mapping[tuple[str, str], Mapping[str, object]],
    ) -> None:
        """Update only refreshed records, preserving accurate freshness transitions.

        An uninitialized (or externally modified) index is rebuilt lazily on
        the next status request. Neither this index nor its floats are persisted.
        """
        if not cache_rows:
            return
        epochs = self._freshness_epochs_by_key
        if epochs is not None:
            ordered = self._freshness_sorted_epochs
            for key, raw in cache_rows.items():
                previous = epochs.get(key)
                if previous is not None:
                    position = bisect_left(ordered, previous)
                    if position >= len(ordered) or ordered[position] != previous:
                        self._freshness_epochs_by_key = None
                        self._freshness_sorted_epochs = []
                        break
                    ordered.pop(position)
                current = _parse_iso_timestamp(raw.get("updated_at"))
                epochs[key] = current
                if current is not None:
                    insort(ordered, current)
            if len(epochs) != len(self._records):
                self._freshness_epochs_by_key = None
                self._freshness_sorted_epochs = []
        # Avoid reporting an old summary immediately after a verified update.
        self._freshness_counts = None
        self._term_snapshot = None

    def public_status(self) -> dict[str, object]:
        """Publish live status with short-lived cached *summary* counts.

        Actual section seats and last-refresh timestamps remain immediately live.
        Timestamp values are indexed once and updated incrementally.
        Summaries are cached for at most 10 seconds, or invalidated when
        verified rows change, with fresh + stale + pending = cached_sections.
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
                if (
                    self._freshness_epochs_by_key is None
                    or len(self._freshness_epochs_by_key) != len(self._records)
                ):
                    self._rebuild_freshness_index_locked()
                verified = self._freshness_sorted_epochs
                # A timestamp exactly at the threshold is still fresh.
                stale_cached = bisect_left(verified, now - self.stale_after_seconds)
                fresh_cached = len(verified) - stale_cached
                pending_cached = len(self._records) - len(verified)
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
                "subject_session_reuse_enabled": self.subject_session_reuse_enabled,
                "preferred_source_pages": len(self._preferred_sources),
                "saved_priority_sources": len(self._saved_interests),
                "other_source_pages": len(self._other_sources),
                "seat_refresh_throughput": self._seat_refresh_throughput_locked(),
            }

    def public_term_freshness(self) -> dict[str, object]:
        """Public, read-only per-term *verified* coverage (no class identifiers).

        The costlier per-term recount is intentionally outside public_status():
        health/search/seat-status polling stays inexpensive. Existing cached
        snapshots and per-source validation remain authoritative.
        """
        rows = self._semester_freshness()
        return {
            "preferred_term": self.preferred_term,
            "stale_after_seconds": self.stale_after_seconds,
            "terms": [{
                key: row[key]
                for key in (
                    "term", "refreshable_sections", "source_pages",
                    "cached_sections", "fresh_cached_sections",
                    "stale_cached_sections", "pending_cached_sections",
                    "uncached_sections", "fresh_percent",
                )
            } for row in rows],
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

    def _seat_refresh_throughput_locked(self) -> dict[str, object]:
        """Rolling successful/failed page and section rates (service lifetime only).

        Call with ``self._lock`` held. Never scan repository sections here.
        All events are in-memory and the ring buffer is bounded.
        """
        now = time.time()
        minute = now - 60.0
        five_minutes = now - 300.0
        while self._seat_throughput_events and self._seat_throughput_events[0][0] < five_minutes:
            self._seat_throughput_events.popleft()
        recent = [entry for entry in self._seat_throughput_events if entry[0] >= minute]
        while self._seat_stage_durations and self._seat_stage_durations[0][0] < five_minutes:
            self._seat_stage_durations.popleft()
        context_seconds = [
            seconds for _, stage, seconds in self._seat_stage_durations if stage == "context"
        ]
        page_seconds = [
            seconds for _, stage, seconds in self._seat_stage_durations if stage == "page"
        ]
        # Bounded in-memory stage samples; no URLs or student identifiers.
        phase_names = (
            "direct", "session_reset", "bootstrap", "retry",
            "subject_recovery", "detail_recovery", "empty_subject",
            "empty_detail", "linked_modals",
        )
        phase_summary = {}
        for phase in phase_names:
            durations = [
                seconds for _, stage, seconds in self._seat_stage_durations
                if stage == phase
            ]
            phase_summary[phase] = {
                "attempts": len(durations),
                "seconds": round(sum(durations), 3),
                "max_seconds": round(max(durations), 3) if durations else None,
            }
        return {
            "seat_refresh_phases_last_300s": phase_summary,
            "subject_session_reuse": {
                "enabled": self.subject_session_reuse_enabled,
                "attempts_last_300s": sum(kind == "reuse_attempt" for _, kind, _ in self._seat_throughput_events),
                "verified_last_300s": sum(kind == "reuse_verified" for _, kind, _ in self._seat_throughput_events),
                "fallbacks_last_300s": sum(kind == "reuse_fallback" for _, kind, _ in self._seat_throughput_events),
                "internal_resets_last_300s": sum(kind == "reuse_internal_reset" for _, kind, _ in self._seat_throughput_events),
            },
            "recovery_deferred_last_300s": sum(
                kind == "recovery_deferred" for _, kind, _ in self._seat_throughput_events
            ),
            "linked_modal_deferred_last_300s": sum(
                kind == "linked_modal_deferred" for _, kind, _ in self._seat_throughput_events
            ),
            "worker_stage_timing_last_300s": {
                "window_seconds": 300,
                "context_attempts": len(context_seconds),
                "context_seconds": round(sum(context_seconds), 3),
                "context_avg_seconds": (
                    round(sum(context_seconds) / len(context_seconds), 3)
                    if context_seconds else None
                ),
                "context_max_seconds": (round(max(context_seconds), 3) if context_seconds else None),
                "course_page_attempts": len(page_seconds),
                "course_page_seconds": round(sum(page_seconds), 3),
                "course_page_avg_seconds": (
                    round(sum(page_seconds) / len(page_seconds), 3)
                    if page_seconds else None
                ),
                "course_page_max_seconds": (round(max(page_seconds), 3) if page_seconds else None),
            },
            "window_seconds": 60,
            "successful_course_pages_last_60s": sum(kind == "success" for _, kind, _ in recent),
            "failed_course_pages_last_60s": sum(kind == "failure" for _, kind, _ in recent),
            "section_updates_last_60s": sum(
                count for _, kind, count in recent if kind == "success"
            ),
            "subject_context_rehydrates_last_60s": sum(kind == "context" for _, kind, _ in recent),
            "affinity_reorders_last_60s": sum(kind == "batch" for _, kind, _ in recent),
            "priority_preemptions_last_60s": sum(kind == "priority" for _, kind, _ in recent),
            "saved_priority_selections_last_60s": sum(kind == "saved" for _, kind, _ in recent),
            "routine_selections_last_60s": sum(kind == "routine" for _, kind, _ in recent),
            "context_followup_selections_last_60s": sum(
                kind == "context_followup" for _, kind, _ in recent
            ),
            "routine_context_followups_last_60s": sum(
                kind == "routine_context_followup" for _, kind, _ in recent
            ),
            "viewed_chain_followups_last_60s": sum(
                kind == "viewed_chain_followup" for _, kind, _ in recent
            ),
            "successful_course_pages_last_300s": sum(
                kind == "success" for _, kind, _ in self._seat_throughput_events
            ),
            "failed_course_pages_last_300s": sum(
                kind == "failure" for _, kind, _ in self._seat_throughput_events
            ),
            "subject_context_rehydrates_last_300s": sum(
                kind == "context" for _, kind, _ in self._seat_throughput_events
            ),
            "affinity_reorders_last_300s": sum(
                kind == "batch" for _, kind, _ in self._seat_throughput_events
            ),
            "priority_preemptions_last_300s": sum(
                kind == "priority" for _, kind, _ in self._seat_throughput_events
            ),
            "saved_priority_selections_last_300s": sum(
                kind == "saved" for _, kind, _ in self._seat_throughput_events
            ),
            "routine_selections_last_300s": sum(
                kind == "routine" for _, kind, _ in self._seat_throughput_events
            ),
            "context_followup_selections_last_300s": sum(
                kind == "context_followup" for _, kind, _ in self._seat_throughput_events
            ),
            "routine_context_followups_last_300s": sum(
                kind == "routine_context_followup" for _, kind, _ in self._seat_throughput_events
            ),
            "viewed_chain_followups_last_300s": sum(
                kind == "viewed_chain_followup" for _, kind, _ in self._seat_throughput_events
            ),
        }

    def admin_status(self) -> dict[str, object]:
        public = self.public_status()
        all_tba, refreshable_tba, tba_sources = self._tba_instructor_targets()
        freshness = self._semester_freshness()
        with self._lock:
            throughput = self._seat_refresh_throughput_locked()
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
                    "seat_refresh_throughput": throughput,
                    "failed_course_refreshes": self._failed_sources,
                    "requests_completed": self._requests_completed,
                    "priority_sources": len(self._interests),
                    "saved_priority_sources": len(self._saved_interests),
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
                    "inventory_empty_course_sources_since_start": sum(
                        1 for failure in self._failure_history.values()
                        if failure.get("inventory_reason") == "no_scheduled_classes"
                        and not failure.get("resolved", False)
                    ),
                    "unverified_empty_course_sources_since_start": sum(
                        1 for failure in self._failure_history.values()
                        if failure.get("error_type") == "SeatUnverifiedEmptyGridError"
                        and not failure.get("resolved", False)
                    ),
                    "empty_grid_recovery_successes_since_start": self._empty_grid_recovery_successes,
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
                "waitlist_available": section.waitlist_available,
                "waitlist_capacity": section.waitlist_capacity,
                "waitlist_total": section.waitlist_total,
                "instructor": section.instructor,
                "updated_at": section.seat_updated_at or cached.get("updated_at"),
            }
        return records
