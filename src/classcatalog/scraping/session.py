from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import parse_qs, urlparse

import requests
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from classcatalog.scraping.class_detail import (
    build_class_detail_tab_post,
    find_class_detail_tab,
    parse_class_information_page,
)
from classcatalog.scraping.class_post import (
    ClassPostParseError,
    ClassNumberActionNotFound,
    build_class_number_post,
    find_class_number_action,
    find_class_number_actions,
)
from classcatalog.scraping.course_option_pagination import (
    CourseOptionPaginationError,
    build_course_option_expand_post,
    find_course_option_expand_action,
)
from classcatalog.scraping.constants import (
    CONFIRMED_TERM_CODES,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_GET_BACKOFF_SECONDS,
    DEFAULT_IN_RUN_STATE_RECOVERY_ATTEMPTS,
    DEFAULT_HEADERS,
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_POST_RECOVERY_COOLDOWN_SECONDS,
    DEFAULT_STATE_CIRCUIT_BREAKER_THRESHOLD,
    DEFAULT_STATEFUL_POST_RECOVERY_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    DETAIL_URL,
    INSTITUTION_CODE,
    LANDING_PAGE_NAME,
    LANDING_URL,
    RESULTS_URL,
    RESULT_QUERY_TEMPLATE,
    SUBJECT_CAP_PARTITION_FACETS,
    SUBJECT_PRIMARY_SEARCH_TEXT,
    SUBJECT_SEARCH_ALIASES,
)
from classcatalog.scraping.facet_parser import (
    FacetParseError,
    OpenClassesFacetNotFound,
    SubjectFacetNotFound,
    build_facet_choice_post,
    build_open_classes_only_post,
    build_selected_filter_remove_post,
    build_subject_facet_post,
    find_facet_choice,
    find_facet_choices,
    is_facet_choice_selected,
    find_open_classes_only_facet,
    find_subject_facet,
)
from classcatalog.scraping.models import CourseSearchHit, SubjectScrapeResult
from classcatalog.scraping.parser import (
    CourseInfoUrlNotFound,
    GroupletUrlNotFound,
    count_result_rows,
    has_no_results_message,
    has_result_cap_warning,
    normalize_people_soft_response,
    parse_course_info_url,
    parse_course_option_grid_progress,
    parse_grouplet_url,
    parse_result_rows,
    parse_term_codes,
)

_LOGIN_MARKERS: Final[tuple[str, ...]] = (
    "you must have cookies enabled in order to sign in",
    "oracle peoplesoft sign-in",
    "unauthorized token has been detected",
)


class PeopleSoftSessionError(RuntimeError):
    """Raised when the public PeopleSoft session or response is unusable."""


class PeopleSoftTransientError(PeopleSoftSessionError):
    """Raised for a temporary upstream response that may succeed after cooling down."""

    def __init__(
        self,
        *,
        method: str,
        url: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        message: str | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        if message is None:
            if status_code is None:
                message = f"{method} {url} failed with a temporary network error."
            else:
                message = f"{method} {url} returned HTTP {status_code}."
        super().__init__(message)


class PeopleSoftCircuitBreakerOpen(PeopleSoftSessionError):
    """Raised after repeated unrecoverable PeopleSoft state-shape failures.

    Continuing a university-wide scrape after the public Fluid session starts
    returning structurally invalid pages can quickly turn hundreds of subjects into
    false failures or false completions.  The circuit breaker stops the process while
    the atomic checkpoint is still usable, so a later ``--resume`` can continue from
    the last trustworthy state.
    """


def is_recoverable_people_soft_state_error(error: BaseException) -> bool:
    """Return whether ``error`` indicates stale/invalid PeopleSoft component state."""

    return isinstance(
        error,
        (
            PeopleSoftSessionError,
            FacetParseError,
            GroupletUrlNotFound,
            CourseInfoUrlNotFound,
            ClassNumberActionNotFound,
            ClassPostParseError,
            CourseOptionPaginationError,
        ),
    )


@dataclass(frozen=True, slots=True)
class SdsuHttpConfig:
    landing_url: str = LANDING_URL
    results_url: str = RESULTS_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    jitter_seconds: float = DEFAULT_JITTER_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    get_backoff_seconds: tuple[float, ...] = DEFAULT_GET_BACKOFF_SECONDS
    post_recovery_cooldown_seconds: float = DEFAULT_POST_RECOVERY_COOLDOWN_SECONDS
    stateful_post_recovery_attempts: int = DEFAULT_STATEFUL_POST_RECOVERY_ATTEMPTS
    in_run_state_recovery_attempts: int = DEFAULT_IN_RUN_STATE_RECOVERY_ATTEMPTS
    state_circuit_breaker_threshold: int = DEFAULT_STATE_CIRCUIT_BREAKER_THRESHOLD


@dataclass(frozen=True, slots=True)
class RawSubjectPages:
    subject: str
    term: str
    term_code: str
    search_url: str
    initial_html: str
    filtered_html: str
    exact_facet_applied: bool
    partition_pages: tuple[RawSubjectPartitionPage, ...] = ()


@dataclass(frozen=True, slots=True)
class RawSubjectPartitionPage:
    filters: tuple[tuple[str, str], ...]
    url: str
    html: str
    result_count: int
    capped: bool


@dataclass(frozen=True, slots=True)
class RawDetailPages:
    source_url: str
    shell_url: str
    grouplet_url: str
    course_info_url: str
    shell_html: str
    grouplet_html: str
    course_info_html: str
    course_info_expansion_count: int = 0


@dataclass(frozen=True, slots=True)
class _LoadedCourseInfoPage:
    canonical_url: str
    state_url: str
    html: str
    expansion_count: int


@dataclass(frozen=True, slots=True)
class RawClassDetailPage:
    class_number: str
    action_id: str
    course_info_url: str
    post_url: str
    response_url: str
    html: str


@dataclass(frozen=True, slots=True)
class RawClassDetailTabPage:
    class_number: str
    tab_value: str
    tab_label: str
    action_id: str
    post_url: str
    response_url: str
    html: str


class SdsuPeopleSoftSession:
    """Low-rate, stateful HTTP client for SDSU's public Fluid class search."""

    def __init__(
        self,
        config: SdsuHttpConfig | None = None,
        *,
        http_session: requests.Session | None = None,
    ) -> None:
        self.config = config or SdsuHttpConfig()
        self._session = http_session or requests.Session()
        self._owns_session = http_session is None
        self._session.headers.update(DEFAULT_HEADERS)
        self._configure_retries()
        self._bootstrapped = False
        self._landing_html: str | None = None
        self._last_request_finished_at: float | None = None
        self._consecutive_state_failures = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def reset_public_session(self) -> None:
        """Discard cookies/component state and force a fresh public bootstrap.

        Merely setting ``_bootstrapped`` to ``False`` is not sufficient after a long
        Fluid session becomes stale: the PeopleSoft cookie can continue pointing at
        the invalid server-side component state.  Real scraper sessions therefore get
        a brand-new ``requests.Session``.  Injected test sessions retain their object
        but have cookies cleared when they expose a cookie jar.
        """

        if self._owns_session:
            self._session.close()
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
            self._configure_retries()
        else:
            cookies = getattr(self._session, "cookies", None)
            clear = getattr(cookies, "clear", None)
            if callable(clear):
                clear()
        self._bootstrapped = False
        self._landing_html = None

    def record_state_success(self) -> None:
        """Close the state-failure circuit after a structurally valid operation."""

        self._consecutive_state_failures = 0

    def record_state_failure(
        self,
        *,
        context: str,
        error: BaseException,
    ) -> None:
        """Count a final state failure and open the circuit when the threshold is met."""

        self._consecutive_state_failures += 1
        threshold = max(self.config.state_circuit_breaker_threshold, 0)
        if threshold and self._consecutive_state_failures >= threshold:
            raise PeopleSoftCircuitBreakerOpen(
                "PeopleSoft state recovery failed "
                f"{self._consecutive_state_failures} consecutive times; circuit breaker "
                f"opened during {context}. Last error: {error}"
            ) from error

    def _configure_retries(self) -> None:
        # Retry policy is implemented explicitly in _request so GET backoff is predictable
        # and stateful PeopleSoft POSTs are never replayed automatically by urllib3.
        retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _pace(self) -> None:
        if self._last_request_finished_at is None or self.config.delay_seconds <= 0:
            return
        jitter = random.uniform(0.0, max(self.config.jitter_seconds, 0.0))
        minimum_interval = self.config.delay_seconds + jitter
        elapsed = time.monotonic() - self._last_request_finished_at
        remaining = minimum_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _retry_after_seconds(response: Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(float(value.strip()), 0.0)
        except ValueError:
            return None

    def _get_backoff_seconds(self, retry_index: int, response: Response | None = None) -> float:
        configured = self.config.get_backoff_seconds
        if configured:
            base = configured[min(retry_index, len(configured) - 1)]
        else:
            base = 0.0
        retry_after = self._retry_after_seconds(response) if response is not None else None
        if retry_after is not None:
            base = max(base, retry_after)
        if base <= 0:
            return 0.0
        return base + random.uniform(0.0, max(self.config.jitter_seconds, 0.0))

    def _sleep_get_backoff(self, retry_index: int, response: Response | None = None) -> None:
        delay = self._get_backoff_seconds(retry_index, response)
        if delay > 0:
            time.sleep(delay)

    def _cool_down_after_stateful_post_failure(
        self,
        error: PeopleSoftTransientError,
    ) -> None:
        delay = max(self.config.post_recovery_cooldown_seconds, 0.0)
        if error.retry_after_seconds is not None:
            delay = max(delay, error.retry_after_seconds)
        if delay > 0:
            delay += random.uniform(0.0, max(self.config.jitter_seconds, 0.0))
            time.sleep(delay)

    def _request(self, method: str, url: str, **kwargs: Any) -> Response:
        method = method.upper()
        retryable_statuses = {429, 500, 502, 503, 504}
        get_retries = max(self.config.max_retries, 0) if method == "GET" else 0

        for attempt in range(get_retries + 1):
            self._pace()
            response: Response | None = None
            try:
                response = self._session.request(
                    method,
                    url,
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                )
            except requests.RequestException as exc:
                self._last_request_finished_at = time.monotonic()
                if method == "GET" and attempt < get_retries:
                    self._sleep_get_backoff(attempt)
                    continue
                if method == "GET":
                    raise PeopleSoftTransientError(
                        method=method,
                        url=url,
                        message=f"{method} {url} failed after retries: {exc}",
                    ) from exc
                raise PeopleSoftTransientError(
                    method=method,
                    url=url,
                    message=(
                        f"{method} {url} failed with a temporary network error: {exc}"
                    ),
                ) from exc

            self._last_request_finished_at = time.monotonic()
            if response.status_code in retryable_statuses:
                retry_after = self._retry_after_seconds(response)
                if method == "GET" and attempt < get_retries:
                    self._sleep_get_backoff(attempt, response)
                    continue
                raise PeopleSoftTransientError(
                    method=method,
                    url=response.url,
                    status_code=response.status_code,
                    retry_after_seconds=retry_after,
                )
            if response.status_code >= 400:
                raise PeopleSoftSessionError(
                    f"{method} {response.url} returned HTTP {response.status_code}."
                )
            self._validate_public_response(response)
            return response

        raise AssertionError("GET retry loop exited without returning or raising")

    @staticmethod
    def _validate_public_response(response: Response) -> None:
        text = response.text.casefold()
        final_url = response.url.casefold()
        if "cmd=login" in final_url or any(marker in text for marker in _LOGIN_MARKERS):
            raise PeopleSoftSessionError(
                "SDSU redirected the request to a PeopleSoft login/cookie error page. "
                "Do not add credentials. Save the response and verify the public landing URL "
                "in a normal browser before retrying."
            )

    def bootstrap(self) -> str:
        if self._bootstrapped and self._landing_html is not None:
            return self._landing_html
        response = self._request(
            "GET",
            self.config.landing_url,
            params={"Page": LANDING_PAGE_NAME},
        )
        self._landing_html = response.text
        self._bootstrapped = True
        return response.text

    def discovered_term_codes(self) -> dict[str, str]:
        return parse_term_codes(self.bootstrap())

    def resolve_term_code(self, term: str, explicit_code: str | None = None) -> str:
        if explicit_code is not None:
            if len(explicit_code) != 4 or not explicit_code.isdigit():
                raise ValueError("--term-code must be a four-digit PeopleSoft STRM value.")
            return explicit_code

        discovered = self.discovered_term_codes()
        discovered_code = discovered.get(term)
        confirmed = CONFIRMED_TERM_CODES.get(term)
        if discovered_code is not None and confirmed is not None and discovered_code != confirmed:
            raise PeopleSoftSessionError(
                f"Discovered term code {discovered_code!r} for {term!r} conflicts with "
                f"the confirmed value {confirmed!r}. Verify SDSU and pass --term-code "
                "explicitly instead of guessing."
            )
        if discovered_code is not None:
            return discovered_code
        if confirmed is not None:
            return confirmed
        available = ", ".join(f"{name}={code}" for name, code in sorted(discovered.items()))
        raise PeopleSoftSessionError(
            f"Could not discover a term code for {term!r}. Detected: {available or 'none'}. "
            "Pass the verified value with --term-code."
        )

    def discover_subjects(
        self,
        *,
        term_code: str,
        probes: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ) -> tuple[str, ...]:
        """Discover live SDSU Subject facet codes without trusting static seed data.

        This is a preflight inventory check, not the production scrape itself. Broad
        alphabetic probes expose Subject facet values. When the result page defaults to
        Open Classes, remove that filter first so subjects with only waitlisted/closed
        offerings are not omitted. The caller still performs exact-subject searches for
        every effective subject before accepting course rows.
        """

        self.bootstrap()
        discovered: set[str] = set()
        for raw_probe in probes:
            probe = " ".join(str(raw_probe).strip().split())
            if not probe:
                continue
            params = {**RESULT_QUERY_TEMPLATE, "SEARCH_TEXT": probe, "ES_STRM": term_code}
            response = self._request("GET", self.config.results_url, params=params)
            html = normalize_people_soft_response(response.text)
            current_url = response.url

            try:
                open_only_facet = find_open_classes_only_facet(html)
            except OpenClassesFacetNotFound:
                open_only_facet = None

            if open_only_facet is not None and open_only_facet.checked:
                post = build_open_classes_only_post(
                    html,
                    current_url=current_url,
                    enabled=False,
                )
                inclusive = self._request(
                    "POST",
                    post.action_url,
                    data=post.fields,
                    headers=self._partition_headers(current_url),
                )
                html = normalize_people_soft_response(inclusive.text)
                current_url = inclusive.url
                self._assert_open_only_disabled(
                    html,
                    context="The live-subject inventory response",
                )
            elif open_only_facet is None and self._open_only_selected(html):
                post = build_selected_filter_remove_post(
                    html,
                    current_url=current_url,
                    labels=("Open Classes", "Open Classes Only"),
                )
                inclusive = self._request(
                    "POST",
                    post.action_url,
                    data=post.fields,
                    headers=self._partition_headers(current_url),
                )
                html = normalize_people_soft_response(inclusive.text)
                self._assert_open_only_disabled(
                    html,
                    context="The live-subject inventory response",
                )

            for choice in find_facet_choices(html, "Subject"):
                code = " ".join(choice.label.split("/", maxsplit=1)[0].strip().upper().split())
                if re.fullmatch(r"[A-Z]+(?: [A-Z]+)*", code):
                    discovered.add(code)

        if not discovered:
            raise PeopleSoftSessionError(
                "Could not discover any SDSU Subject facet values for the requested term. "
                "Do not treat an empty live inventory as authoritative; retry later or use "
                "--known-subjects-only explicitly."
            )
        return tuple(sorted(discovered))

    def build_search_request(self, *, subject: str, term_code: str) -> requests.Request:
        params = {**RESULT_QUERY_TEMPLATE, "SEARCH_TEXT": subject, "ES_STRM": term_code}
        return requests.Request("GET", self.config.results_url, params=params)

    @staticmethod
    def _open_only_selected(html: str) -> bool:
        return (
            is_facet_choice_selected(html, group="Class Status", label="Open Classes")
            or is_facet_choice_selected(
                html,
                group="Class Status",
                label="Open Classes Only",
            )
        )

    @classmethod
    def _assert_open_only_disabled(cls, html: str, *, context: str) -> None:
        """Verify that PeopleSoft is not representing Open Classes as selected.

        A selected value can survive only as a Selected Filters breadcrumb, and sparse
        result sets can omit the Class Status fieldset entirely. Therefore the absence of
        the checkbox is valid after we have explicitly removed/toggled the filter; the
        authoritative failure condition is positive evidence that Open Classes remains
        selected.
        """

        if cls._open_only_selected(html):
            raise PeopleSoftSessionError(
                f"{context} still reports SDSU's open-only Class Status filter as selected."
            )
        try:
            facet = find_open_classes_only_facet(html)
        except OpenClassesFacetNotFound:
            return
        if facet.checked:
            raise PeopleSoftSessionError(
                f"{context} still reports SDSU's open-only Class Status filter as selected."
            )

    def fetch_subject_pages(
        self,
        *,
        term: str,
        term_code: str,
        subject: str,
        page_observer: Callable[[str, str], None] | None = None,
        _search_text: str | None = None,
        _allow_aliases: bool = True,
        _observer_prefix: str = "",
    ) -> RawSubjectPages:
        self.bootstrap()
        search_text = _search_text or SUBJECT_PRIMARY_SEARCH_TEXT.get(subject, subject)
        params = {**RESULT_QUERY_TEMPLATE, "SEARCH_TEXT": search_text, "ES_STRM": term_code}
        initial_response = self._request("GET", self.config.results_url, params=params)
        initial_html = normalize_people_soft_response(initial_response.text)
        if page_observer is not None:
            page_observer(f"{_observer_prefix}initial", initial_html)

        # A true PeopleSoft no-results response has no result rows and may omit the
        # Class Status/Subject facet UI entirely. In that case there is no stateful
        # facet action to perform. Treat the subject as a valid zero-offering term
        # result instead of failing while trying to locate the Open Classes control.
        #
        # Keep this deliberately narrower than merely "zero exact-subject rows": a
        # fuzzy keyword search can return rows for other subjects, and those pages
        # still need normal exact-Subject filtering. Requiring PeopleSoft's explicit
        # no-results message prevents JS/PUB-style fuzzy matches from being mistaken
        # for an empty subject.
        initial_hits = parse_result_rows(
            initial_html,
            term=term,
            term_code=term_code,
            base_url=initial_response.url,
        )
        if not initial_hits and has_no_results_message(initial_html):
            return RawSubjectPages(
                subject=subject,
                term=term,
                term_code=term_code,
                search_url=initial_response.url,
                initial_html=initial_html,
                filtered_html=initial_html,
                exact_facet_applied=False,
            )

        # SDSU defaults the result page to its open-only Class Status filter.
        # ClassCatalog needs
        # open, waitlisted, and closed sections, so make turning that facet OFF a
        # distinct PeopleSoft action before applying the exact Subject facet.
        # Doing this as a separate POST is more reliable than hoping PeopleSoft
        # processes two changed facet values while ICAction points at only one.
        inclusive_html = initial_html
        inclusive_url = initial_response.url
        try:
            open_only_facet = find_open_classes_only_facet(initial_html)
        except OpenClassesFacetNotFound as exc:
            open_only_facet = None
            if not self._open_only_selected(initial_html):
                raise PeopleSoftSessionError(
                    "Could not find SDSU's Open Classes/open-only Class Status facet or "
                    "a matching Selected Filters breadcrumb, so the scraper cannot "
                    "safely prove that waitlisted/closed classes are included."
                ) from exc

        if open_only_facet is not None and open_only_facet.checked:
            open_only_post = build_open_classes_only_post(
                initial_html,
                current_url=initial_response.url,
                enabled=False,
            )
            parsed_initial = urlparse(initial_response.url)
            open_only_headers = {
                "Referer": initial_response.url,
                "Origin": f"{parsed_initial.scheme}://{parsed_initial.netloc}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            inclusive_response = self._request(
                "POST",
                open_only_post.action_url,
                data=open_only_post.fields,
                headers=open_only_headers,
            )
            inclusive_html = normalize_people_soft_response(inclusive_response.text)
            inclusive_url = inclusive_response.url
            if page_observer is not None:
                page_observer(f"{_observer_prefix}all-statuses", inclusive_html)

            self._assert_open_only_disabled(
                inclusive_html,
                context="SDSU's response after disabling the open-only filter",
            )
        elif open_only_facet is None and self._open_only_selected(initial_html):
            # Sparse result sets can hide the Class Status fieldset while retaining only
            # the Selected Filters breadcrumb. Click that removal action using fresh form
            # state instead of assuming the invisible default filter is harmless.
            remove_post = build_selected_filter_remove_post(
                initial_html,
                current_url=initial_response.url,
                labels=("Open Classes", "Open Classes Only"),
            )
            parsed_initial = urlparse(initial_response.url)
            remove_headers = {
                "Referer": initial_response.url,
                "Origin": f"{parsed_initial.scheme}://{parsed_initial.netloc}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            inclusive_response = self._request(
                "POST",
                remove_post.action_url,
                data=remove_post.fields,
                headers=remove_headers,
            )
            inclusive_html = normalize_people_soft_response(inclusive_response.text)
            inclusive_url = inclusive_response.url
            if page_observer is not None:
                page_observer(f"{_observer_prefix}all-statuses", inclusive_html)
            self._assert_open_only_disabled(
                inclusive_html,
                context="SDSU's response after removing the Open Classes breadcrumb",
            )

        try:
            facet = find_subject_facet(inclusive_html, subject)
        except SubjectFacetNotFound:
            inclusive_hits = parse_result_rows(
                inclusive_html,
                term=term,
                term_code=term_code,
                base_url=inclusive_url,
            )
            if inclusive_hits and all(hit.subject == subject for hit in inclusive_hits):
                return RawSubjectPages(
                    subject=subject,
                    term=term,
                    term_code=term_code,
                    search_url=inclusive_url,
                    initial_html=initial_html,
                    filtered_html=inclusive_html,
                    exact_facet_applied=False,
                )
            if not inclusive_hits and has_no_results_message(inclusive_html):
                return RawSubjectPages(
                    subject=subject,
                    term=term,
                    term_code=term_code,
                    search_url=inclusive_url,
                    initial_html=initial_html,
                    filtered_html=inclusive_html,
                    exact_facet_applied=False,
                )
            if _allow_aliases:
                aliases = SUBJECT_SEARCH_ALIASES.get(subject, ())
                last_alias_error: SubjectFacetNotFound | None = None
                for alias_index, alias in enumerate(aliases, start=1):
                    try:
                        return self.fetch_subject_pages(
                            term=term,
                            term_code=term_code,
                            subject=subject,
                            page_observer=page_observer,
                            _search_text=alias,
                            _allow_aliases=False,
                            _observer_prefix=f"fallback-{alias_index}-",
                        )
                    except SubjectFacetNotFound as alias_error:
                        last_alias_error = alias_error
                if last_alias_error is not None:
                    raise last_alias_error
            raise

        post = build_subject_facet_post(
            inclusive_html,
            current_url=inclusive_url,
            facet=facet,
        )
        parsed = urlparse(inclusive_url)
        post_headers = {
            "Referer": inclusive_url,
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        filtered_response = self._request(
            "POST",
            post.action_url,
            data=post.fields,
            headers=post_headers,
        )
        filtered_html = normalize_people_soft_response(filtered_response.text)
        self._assert_open_only_disabled(
            filtered_html,
            context="The exact-subject response",
        )
        if page_observer is not None:
            page_observer(f"{_observer_prefix}filtered", filtered_html)
        return RawSubjectPages(
            subject=subject,
            term=term,
            term_code=term_code,
            search_url=initial_response.url,
            initial_html=initial_html,
            filtered_html=filtered_html,
            exact_facet_applied=True,
        )

    @staticmethod
    def _partition_headers(current_url: str) -> dict[str, str]:
        parsed = urlparse(current_url)
        return {
            "Referer": current_url,
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    @staticmethod
    def _verify_partition_open_status(
        html: str,
        *,
        intentionally_open_only: bool,
    ) -> None:
        """Verify that a partition did not silently re-enable SDSU's open-only filter.

        PeopleSoft may remove ``Open Classes`` from the available Class Status facet
        values when the current partition contains no open classes, or when that value
        is selected and represented only by the Selected Filters breadcrumb. Absence of
        the checkbox is therefore not itself an error. Treat a visible checked control
        or a matching breadcrumb as authoritative evidence that open-only is selected;
        otherwise an absent control means the filter is not selected.
        """

        open_selected = (
            is_facet_choice_selected(html, group="Class Status", label="Open Classes")
            or is_facet_choice_selected(
                html,
                group="Class Status",
                label="Open Classes Only",
            )
        )
        try:
            open_only = find_open_classes_only_facet(html)
        except OpenClassesFacetNotFound:
            open_only = None

        if open_only is not None:
            open_selected = open_selected or open_only.checked

        if open_selected != intentionally_open_only:
            raise PeopleSoftSessionError(
                "A result-cap partition left SDSU's Open Classes status in an "
                "unexpected state."
            )

    def _fetch_subject_partition_path(
        self,
        *,
        term: str,
        term_code: str,
        subject: str,
        filters: tuple[tuple[str, str], ...],
    ) -> tuple[str, str]:
        """Replay a result-cap branch from fresh PeopleSoft state.

        Facet POSTs are stateful.  A sibling branch cannot safely reuse the previous
        branch's ``ICStateNum``/``ICSID``, so each logical branch starts from a fresh
        exact-subject page and reapplies its filter path.  If one of those read-only
        facet POSTs receives a transient 429/5xx, the stale POST body is discarded and
        the whole branch is rebuilt from a fresh GET after the configured cooldown.
        """

        recoveries = max(self.config.stateful_post_recovery_attempts, 0)
        for attempt in range(recoveries + 1):
            try:
                base = self.fetch_subject_pages(
                    term=term,
                    term_code=term_code,
                    subject=subject,
                    page_observer=None,
                )
                html = base.filtered_html
                current_url = base.search_url

                for filter_index, (group, label) in enumerate(filters):
                    choice = find_facet_choice(html, group=group, label=label)
                    post = build_facet_choice_post(
                        html,
                        current_url=current_url,
                        choice=choice,
                    )
                    response = self._request(
                        "POST",
                        post.action_url,
                        data=post.fields,
                        headers=self._partition_headers(current_url),
                    )
                    html = normalize_people_soft_response(response.text)
                    current_url = response.url

                    if not is_facet_choice_selected(html, group=group, label=label):
                        available = "; ".join(
                            choice.label for choice in find_facet_choices(html, group)
                        )
                        raise PeopleSoftSessionError(
                            f"PeopleSoft did not retain result-cap partition "
                            f"{group}={label!r}. Available values after the POST: "
                            f"{available or 'none'}."
                        )
                    applied_filters = filters[: filter_index + 1]
                    intentionally_open_only = any(
                        partition_group.casefold() == "class status"
                        and partition_label.casefold() in {"open classes", "open classes only"}
                        for partition_group, partition_label in applied_filters
                    )
                    self._verify_partition_open_status(
                        html,
                        intentionally_open_only=intentionally_open_only,
                    )
                    exact_subject = find_subject_facet(html, subject)
                    if not exact_subject.checked:
                        raise PeopleSoftSessionError(
                            "A result-cap partition unexpectedly removed the exact "
                            f"Subject={subject} facet."
                        )

                return html, current_url
            except PeopleSoftTransientError as exc:
                if attempt >= recoveries:
                    raise
                self._cool_down_after_stateful_post_failure(exc)
                self.reset_public_session()

        raise AssertionError("unreachable result-cap partition recovery state")

    def _partition_capped_subject_pages(
        self,
        pages: RawSubjectPages,
    ) -> tuple[RawSubjectPartitionPage, ...]:
        """Recursively split a capped exact-subject page into uncapped facet leaves."""

        leaves: list[RawSubjectPartitionPage] = []

        def walk(
            *,
            filters: tuple[tuple[str, str], ...],
            html: str,
            url: str,
            next_group_index: int,
        ) -> None:
            result_count = count_result_rows(html)
            capped = has_result_cap_warning(html)
            if not capped:
                leaves.append(
                    RawSubjectPartitionPage(
                        filters=filters,
                        url=url,
                        html=html,
                        result_count=result_count,
                        capped=False,
                    )
                )
                return

            for group_index in range(next_group_index, len(SUBJECT_CAP_PARTITION_FACETS)):
                group = SUBJECT_CAP_PARTITION_FACETS[group_index]
                choices = find_facet_choices(html, group)
                # A single available value cannot reduce the current capped branch.
                if len(choices) < 2:
                    continue

                branch_pages: list[tuple[tuple[tuple[str, str], ...], str, str]] = []
                for choice in choices:
                    branch_filters = (*filters, (group, choice.label))
                    branch_html, branch_url = self._fetch_subject_partition_path(
                        term=pages.term,
                        term_code=pages.term_code,
                        subject=pages.subject,
                        filters=branch_filters,
                    )
                    branch_pages.append((branch_filters, branch_html, branch_url))

                # If PeopleSoft ignored the facet and every branch is byte-identical to
                # the parent, skip this dimension rather than recursing pointlessly.
                if branch_pages and all(branch_html == html for _, branch_html, _ in branch_pages):
                    continue

                for branch_filters, branch_html, branch_url in branch_pages:
                    walk(
                        filters=branch_filters,
                        html=branch_html,
                        url=branch_url,
                        next_group_index=group_index + 1,
                    )
                return

            # We exhausted the known facet dimensions.  Keep the capped leaf so callers
            # can retain the visible rows, but mark the overall subject incomplete.
            leaves.append(
                RawSubjectPartitionPage(
                    filters=filters,
                    url=url,
                    html=html,
                    result_count=result_count,
                    capped=True,
                )
            )

        walk(
            filters=(),
            html=pages.filtered_html,
            url=pages.search_url,
            next_group_index=0,
        )
        return tuple(leaves)

    @staticmethod
    def _course_hit_key(hit: CourseSearchHit) -> tuple[str, ...]:
        if hit.crse_id:
            return (
                "id",
                hit.crse_id,
                hit.crse_offer_nbr or "",
                hit.acad_career or "",
            )
        return ("code", hit.subject, hit.catalog_number, hit.course_code)

    @staticmethod
    def _catalog_sort_key(hit: CourseSearchHit) -> tuple[object, ...]:
        parts = re.split(r"(\d+)", hit.catalog_number.casefold())
        natural: list[tuple[int, object]] = []
        for part in parts:
            if not part:
                continue
            natural.append((0, int(part)) if part.isdigit() else (1, part))
        return (hit.subject.casefold(), tuple(natural), hit.course_code.casefold())

    @staticmethod
    def _canonical_detail_hit(hit: CourseSearchHit) -> CourseSearchHit:
        """Rebuild a stable course-detail URL from explicit PeopleSoft identifiers."""

        if (
            hit.crse_id is None
            or hit.crse_offer_nbr is None
            or hit.acad_career is None
            or hit.class_number is None
        ):
            return hit
        existing_query = (
            {
                key.upper(): values
                for key, values in parse_qs(urlparse(hit.detail_url).query).items()
            }
            if hit.detail_url is not None
            else {}
        )
        section = next(iter(existing_query.get("SEC", ())), None)
        params: list[tuple[str, str]] = [
            ("Page", "SSR_CS_WRAP_FL"),
            ("Action", "U"),
            ("CRSE_ID", hit.crse_id),
            ("CRSE_OFFER_NBR", hit.crse_offer_nbr),
            ("STRM", hit.term_code),
            ("INSTITUTION", INSTITUTION_CODE),
            ("ACAD_CAREER", hit.acad_career),
            ("CLASS_NBR", hit.class_number),
        ]
        if section:
            params.append(("SEC", section))
        params.extend(
            (
                ("pts_Portal", "EMPLOYEE"),
                ("pts_PortalHostNode", "SA"),
                ("pts_Market", "GBL"),
            )
        )
        prepared = requests.Request("GET", DETAIL_URL, params=params).prepare()
        if prepared.url is None:
            return hit
        return hit.model_copy(update={"detail_url": prepared.url})

    @staticmethod
    def _assert_course_detail_urls(result: SubjectScrapeResult) -> None:
        missing = tuple(hit.course_code for hit in result.courses if hit.detail_url is None)
        if missing:
            preview = ", ".join(missing[:8])
            suffix = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
            raise PeopleSoftSessionError(
                "PeopleSoft returned scheduled course rows without usable detail URLs: "
                f"{preview}{suffix}. The search/session state is not safe for a deep scrape."
            )

    def prepare_partitioned_detail_result(
        self,
        result: SubjectScrapeResult,
    ) -> SubjectScrapeResult:
        """Re-prime capped-subject state and refresh canonical detail seeds.

        Recursive partitioning leaves the Fluid session positioned on the final leaf.
        That leaf can be an empty ``Closed Classes`` branch, which caused valid ART
        course URLs to load Course Information shells with zero option rows.  Before
        deep-scraping a partitioned subject, reset to a fresh exact-subject context and
        replace any matching course seeds with those from the fresh base page.  Courses
        that live beyond the base 75-result window retain their explicit identifiers,
        but their URLs are rebuilt canonically and used against the newly primed state.
        """

        if not result.partitioned:
            canonical = tuple(self._canonical_detail_hit(hit) for hit in result.courses)
            refreshed = result.model_copy(update={"courses": canonical})
            self._assert_course_detail_urls(refreshed)
            return refreshed

        self.reset_public_session()
        pages = self.rehydrate_subject_context(
            term=result.term,
            term_code=result.term_code,
            subject=result.subject,
            reset_session=False,
            mark_success=False,
        )
        fresh_result = self.parse_subject_pages(pages)
        fresh_by_key = {
            self._course_hit_key(hit): self._canonical_detail_hit(hit)
            for hit in fresh_result.courses
        }
        refreshed_courses: list[CourseSearchHit] = []
        for original in result.courses:
            fresh = fresh_by_key.get(self._course_hit_key(original))
            if fresh is None:
                refreshed_courses.append(self._canonical_detail_hit(original))
                continue
            section_count = original.section_count
            if fresh.section_count is not None and (
                section_count is None or fresh.section_count > section_count
            ):
                section_count = fresh.section_count
            refreshed_courses.append(
                fresh.model_copy(update={"section_count": section_count})
            )

        refreshed = result.model_copy(update={"courses": tuple(refreshed_courses)})
        try:
            self._assert_course_detail_urls(refreshed)
        except PeopleSoftSessionError as exc:
            self.record_state_failure(
                context=f"preparing partitioned details for {result.subject}",
                error=exc,
            )
            raise
        self.record_state_success()
        return refreshed

    def parse_subject_pages(self, pages: RawSubjectPages) -> SubjectScrapeResult:
        initial_count = count_result_rows(pages.initial_html)
        filtered_count = count_result_rows(pages.filtered_html)
        cap_initial = has_result_cap_warning(pages.initial_html)
        cap_filtered = has_result_cap_warning(pages.filtered_html)

        if pages.partition_pages:
            merged: dict[tuple[str, ...], CourseSearchHit] = {}
            for partition in pages.partition_pages:
                hits = parse_result_rows(
                    partition.html,
                    term=pages.term,
                    term_code=pages.term_code,
                    base_url=partition.url,
                    expected_subject=pages.subject,
                    strict_subject=True,
                )
                for hit in hits:
                    key = self._course_hit_key(hit)
                    previous = merged.get(key)
                    if previous is None:
                        merged[key] = hit
                    elif hit.section_count is not None and (
                        previous.section_count is None
                        or hit.section_count > previous.section_count
                    ):
                        # Overlapping section-level partitions can expose the same course
                        # more than once. Keep the richer visible section count while
                        # retaining a single stable course record.
                        merged[key] = hit
            courses = tuple(sorted(merged.values(), key=self._catalog_sort_key))
            partition_facets = tuple(
                dict.fromkeys(
                    group
                    for partition in pages.partition_pages
                    for group, _label in partition.filters
                )
            )
            unresolved_partitions = sum(
                1 for partition in pages.partition_pages if partition.capped
            )
            complete = unresolved_partitions == 0
        else:
            courses = parse_result_rows(
                pages.filtered_html,
                term=pages.term,
                term_code=pages.term_code,
                base_url=pages.search_url,
                expected_subject=pages.subject,
                strict_subject=True,
            )
            partition_facets = ()
            unresolved_partitions = 0
            complete = not cap_filtered

        return SubjectScrapeResult(
            term=pages.term,
            term_code=pages.term_code,
            subject=pages.subject,
            fetched_at=datetime.now(UTC).isoformat(),
            search_url=pages.search_url,
            initial_result_count=initial_count,
            filtered_result_count=filtered_count,
            exact_facet_applied=pages.exact_facet_applied,
            initial_result_cap_warning=cap_initial,
            filtered_result_cap_warning=cap_filtered,
            complete=complete,
            partitioned=bool(pages.partition_pages),
            partition_facets=partition_facets,
            partition_leaf_count=len(pages.partition_pages),
            unresolved_partition_count=unresolved_partitions,
            courses=courses,
        )

    def rehydrate_subject_context(
        self,
        *,
        term: str,
        term_code: str,
        subject: str,
        reset_session: bool = True,
        mark_success: bool = True,
    ) -> RawSubjectPages:
        """Rebuild PeopleSoft session/search state for resumed detail scraping.

        A checkpoint can safely persist parsed search results and detail output, but the
        PeopleSoft cookies and server-side component state that produced those results do
        not survive a new process. Resuming directly from a saved detail URL can therefore
        return an incomplete wrapper shell with no deferred ``SSR_START_PAGE_FL`` grouplet.

        This method performs only the normal initial/all-status/exact-subject search flow.
        It intentionally does *not* recurse through capped-subject partitions because the
        caller already has the persisted course inventory; the goal here is only to
        establish a fresh, valid PeopleSoft context before retrying unfinished details.
        """

        if reset_session:
            self.reset_public_session()

        pages: RawSubjectPages | None = None
        recoveries = max(self.config.in_run_state_recovery_attempts, 0)
        for recovery_index in range(recoveries + 1):
            try:
                pages = self.fetch_subject_pages(
                    term=term,
                    term_code=term_code,
                    subject=subject,
                )
                if mark_success:
                    self.record_state_success()
                break
            except PeopleSoftTransientError as exc:
                if recovery_index >= recoveries:
                    self.record_state_failure(
                        context=f"rehydrating subject {subject}",
                        error=exc,
                    )
                    raise
                self._cool_down_after_stateful_post_failure(exc)
                self.reset_public_session()
            except Exception as exc:
                if not is_recoverable_people_soft_state_error(exc):
                    raise
                if recovery_index >= recoveries:
                    self.record_state_failure(
                        context=f"rehydrating subject {subject}",
                        error=exc,
                    )
                    raise
                self.reset_public_session()
        if pages is None:
            raise AssertionError(
                "resume subject-context recovery loop exited without pages"
            )
        return pages

    def scrape_subject(
        self,
        *,
        term: str,
        term_code: str,
        subject: str,
        page_observer: Callable[[str, str], None] | None = None,
        require_detail_urls: bool = False,
    ) -> tuple[RawSubjectPages, SubjectScrapeResult]:
        pages: RawSubjectPages | None = None
        recoveries = max(self.config.in_run_state_recovery_attempts, 0)
        for recovery_index in range(recoveries + 1):
            try:
                pages = self.fetch_subject_pages(
                    term=term,
                    term_code=term_code,
                    subject=subject,
                    page_observer=page_observer,
                )
                if has_result_cap_warning(pages.filtered_html):
                    partition_pages = self._partition_capped_subject_pages(pages)
                    pages = RawSubjectPages(
                        subject=pages.subject,
                        term=pages.term,
                        term_code=pages.term_code,
                        search_url=pages.search_url,
                        initial_html=pages.initial_html,
                        filtered_html=pages.filtered_html,
                        exact_facet_applied=pages.exact_facet_applied,
                        partition_pages=partition_pages,
                    )
                result = self.parse_subject_pages(pages)
                if require_detail_urls:
                    self._assert_course_detail_urls(result)
                self.record_state_success()
                return pages, result
            except PeopleSoftTransientError as exc:
                if recovery_index >= recoveries:
                    self.record_state_failure(
                        context=f"scraping subject {subject}",
                        error=exc,
                    )
                    raise
                self._cool_down_after_stateful_post_failure(exc)
                self.reset_public_session()
            except Exception as exc:
                if not is_recoverable_people_soft_state_error(exc):
                    raise
                if recovery_index >= recoveries:
                    self.record_state_failure(
                        context=f"scraping subject {subject}",
                        error=exc,
                    )
                    raise
                self.reset_public_session()
        if pages is None:
            raise AssertionError("subject recovery loop exited without pages")
        raise AssertionError("subject recovery loop exited without returning")

    def fetch_detail_page(self, url: str, *, referer: str | None = None) -> str:
        """Fetch only the outer PeopleSoft detail shell.

        Kept for backwards compatibility.  New scraper code should use
        :meth:`fetch_detail_pages` so the deferred grouplet is fetched too.
        """

        headers = {"Referer": referer} if referer else None
        response = self._request("GET", url, headers=headers)
        return normalize_people_soft_response(response.text)

    def _course_info_has_class_action(self, html: str, class_number: str) -> bool:
        normalized = class_number.strip()
        return any(
            action.class_number == normalized for action in find_class_number_actions(html)
        )

    def _load_course_info_with_option_expansion(
        self,
        course_info_url: str,
        *,
        referer: str | None,
        target_class_number: str | None = None,
    ) -> _LoadedCourseInfoPage:
        """Load Course Information and follow every required ``Display N More`` action.

        ``target_class_number`` is an optimization used by class-modal navigation. If the
        requested class is already visible in the initial 50 options, the scraper does not
        expand the grid. If it is only present in a later option window, the grid is expanded
        until that physical class appears.

        A failed stateful expansion POST is never replayed with stale IC state. The logical
        expansion is restarted from a fresh GET after the configured cooldown.
        """

        recoveries = max(self.config.stateful_post_recovery_attempts, 0)
        for recovery_index in range(recoveries + 1):
            response = self._request(
                "GET",
                course_info_url,
                headers={"Referer": referer} if referer else None,
            )
            canonical_url = response.url
            current_url = response.url
            current_html = normalize_people_soft_response(response.text)
            expansion_count = 0

            try:
                while True:
                    if (
                        target_class_number is not None
                        and self._course_info_has_class_action(
                            current_html, target_class_number
                        )
                    ):
                        return _LoadedCourseInfoPage(
                            canonical_url=canonical_url,
                            state_url=current_url,
                            html=current_html,
                            expansion_count=expansion_count,
                        )

                    progress = parse_course_option_grid_progress(current_html)
                    if progress.complete:
                        return _LoadedCourseInfoPage(
                            canonical_url=canonical_url,
                            state_url=current_url,
                            html=current_html,
                            expansion_count=expansion_count,
                        )

                    action = find_course_option_expand_action(current_html)
                    if action is None:
                        raise PeopleSoftSessionError(
                            "PeopleSoft reports an incomplete Course Information option grid "
                            f"({progress.start}-{progress.end} of {progress.total}) but did not "
                            "expose a Display More action."
                        )

                    post = build_course_option_expand_post(
                        current_html,
                        current_url=current_url,
                        action=action,
                    )
                    parsed = urlparse(current_url)
                    post_headers = {
                        "Referer": current_url,
                        "Origin": f"{parsed.scheme}://{parsed.netloc}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    }
                    expanded_response = self._request(
                        "POST",
                        post.action_url,
                        data=post.fields,
                        headers=post_headers,
                    )
                    expanded_html = normalize_people_soft_response(expanded_response.text)
                    expanded_progress = parse_course_option_grid_progress(expanded_html)
                    advanced = (
                        expanded_progress.complete
                        or (
                            progress.end is not None
                            and expanded_progress.end is not None
                            and expanded_progress.end > progress.end
                        )
                        or expanded_progress.displayed_rows > progress.displayed_rows
                    )
                    if not advanced:
                        raise PeopleSoftSessionError(
                            "PeopleSoft accepted the Course Information Display More action "
                            "but the option grid did not advance."
                        )

                    current_html = expanded_html
                    current_url = expanded_response.url
                    expansion_count += 1
                    if expansion_count > 100:
                        raise PeopleSoftSessionError(
                            "Course Information option-grid expansion exceeded 100 POSTs; "
                            "refusing to continue an apparent pagination loop."
                        )
            except PeopleSoftTransientError as exc:
                if exc.method != "POST" or recovery_index >= recoveries:
                    raise
                self._cool_down_after_stateful_post_failure(exc)

        raise AssertionError(
            "Course-option expansion recovery loop exited without returning or raising"
        )

    def fetch_course_info_page(
        self,
        course_info_url: str,
        *,
        referer: str | None = None,
    ) -> _LoadedCourseInfoPage:
        """Fetch only a complete ``SSR_CRSE_INFO_FL`` enrollment-option grid.

        This is intentionally lighter than :meth:`fetch_detail_pages`: callers that
        already persisted the Course Information URL can refresh volatile seat data
        without reopening the outer course shell or any individual class modal.
        """

        return self._load_course_info_with_option_expansion(
            course_info_url,
            referer=referer,
        )

    def fetch_detail_pages(
        self,
        url: str,
        *,
        referer: str | None = None,
    ) -> RawDetailPages:
        """Follow the full Fluid lazy-load chain through complete Course Information."""

        shell_headers = {"Referer": referer} if referer else None
        shell_response = self._request("GET", url, headers=shell_headers)
        shell_html = normalize_people_soft_response(shell_response.text)
        grouplet_url = parse_grouplet_url(shell_response.text)

        grouplet_response = self._request(
            "GET",
            grouplet_url,
            headers={"Referer": shell_response.url},
        )
        grouplet_html = normalize_people_soft_response(grouplet_response.text)
        course_info_url = parse_course_info_url(grouplet_response.text)
        course_info = self._load_course_info_with_option_expansion(
            course_info_url,
            referer=grouplet_response.url,
        )

        return RawDetailPages(
            source_url=url,
            shell_url=shell_response.url,
            grouplet_url=grouplet_response.url,
            course_info_url=course_info.canonical_url,
            shell_html=shell_html,
            grouplet_html=grouplet_html,
            course_info_html=course_info.html,
            course_info_expansion_count=course_info.expansion_count,
        )

    def _open_class_number_page_once(
        self,
        course_info_url: str,
        class_number: str,
        *,
        referer: str | None = None,
    ) -> RawClassDetailPage:
        course_info = self._load_course_info_with_option_expansion(
            course_info_url,
            referer=referer,
            target_class_number=class_number,
        )
        action = find_class_number_action(course_info.html, class_number)
        post = build_class_number_post(
            course_info.html,
            current_url=course_info.state_url,
            action=action,
        )

        parsed = urlparse(course_info.state_url)
        post_headers = {
            "Referer": course_info.state_url,
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        response = self._request(
            "POST",
            post.action_url,
            data=post.fields,
            headers=post_headers,
        )
        return RawClassDetailPage(
            class_number=action.class_number,
            action_id=action.action_id,
            course_info_url=course_info.canonical_url,
            post_url=post.action_url,
            response_url=response.url,
            html=normalize_people_soft_response(response.text),
        )

    def fetch_class_number_page(
        self,
        course_info_url: str,
        class_number: str,
        *,
        referer: str | None = None,
    ) -> RawClassDetailPage:
        """Refresh course state and safely open one class-number modal.

        GETs can be retried with backoff. A stateful PeopleSoft POST is never replayed
        with stale form data. If opening the class modal receives a temporary 429/5xx
        response, the client cools down, reloads the course page, re-discovers the
        dynamic class action and fresh IC state, then attempts the logical operation
        again.
        """

        recoveries = max(self.config.stateful_post_recovery_attempts, 0)
        for recovery_index in range(recoveries + 1):
            try:
                return self._open_class_number_page_once(
                    course_info_url,
                    class_number,
                    referer=referer,
                )
            except PeopleSoftTransientError as exc:
                if exc.method != "POST" or recovery_index >= recoveries:
                    raise
                self._cool_down_after_stateful_post_failure(exc)

        raise AssertionError("Class-number recovery loop exited without returning or raising")

    def _walk_class_detail_tabs_once(
        self,
        class_page: RawClassDetailPage,
        tab_selectors: tuple[str, ...] | list[str],
    ) -> tuple[RawClassDetailTabPage, ...]:
        current_html = class_page.html
        current_url = class_page.response_url
        pages: list[RawClassDetailTabPage] = []

        initial_record = parse_class_information_page(current_html)
        if initial_record.class_number != class_page.class_number:
            raise PeopleSoftSessionError(
                "Class modal context mismatch before tab walk: "
                f"requested {class_page.class_number}, received {initial_record.class_number}."
            )

        for tab_selector in tab_selectors:
            tab = find_class_detail_tab(current_html, tab_selector)
            post = build_class_detail_tab_post(
                current_html,
                current_url=current_url,
                tab=tab,
            )
            parsed_url = urlparse(current_url)
            post_headers = {
                "Referer": current_url,
                "Origin": f"{parsed_url.scheme}://{parsed_url.netloc}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            response = self._request(
                "POST",
                post.action_url,
                data=post.fields,
                headers=post_headers,
            )
            response_html = normalize_people_soft_response(response.text)

            response_record = parse_class_information_page(response_html)
            if response_record.class_number != class_page.class_number:
                raise PeopleSoftSessionError(
                    "Class modal context changed during tab walk: "
                    f"requested {class_page.class_number}, tab {tab.value} returned "
                    f"{response_record.class_number}."
                )

            page = RawClassDetailTabPage(
                class_number=class_page.class_number,
                tab_value=tab.value,
                tab_label=tab.label,
                action_id=tab.action_id,
                post_url=post.action_url,
                response_url=response.url,
                html=response_html,
            )
            pages.append(page)
            current_html = response_html
            current_url = response.url

        return tuple(pages)

    def fetch_class_detail_tab_pages_from_class_page(
        self,
        class_page: RawClassDetailPage,
        tab_selectors: tuple[str, ...] | list[str],
    ) -> tuple[RawClassDetailTabPage, ...]:
        """Follow a class modal's tabs, rebuilding fresh state after transient POST failure.

        Each successful tab POST advances PeopleSoft's ICStateNum/ICSID state. If one
        tab POST receives a temporary 429/5xx response, the partially walked chain is
        discarded. After a cooldown, the class modal is reopened from the course page
        and the complete requested tab sequence is restarted against fresh state.
        """

        recoveries = max(self.config.stateful_post_recovery_attempts, 0)
        current_class_page = class_page
        for recovery_index in range(recoveries + 1):
            try:
                return self._walk_class_detail_tabs_once(
                    current_class_page,
                    tab_selectors,
                )
            except PeopleSoftTransientError as exc:
                if exc.method != "POST" or recovery_index >= recoveries:
                    raise
                self._cool_down_after_stateful_post_failure(exc)
                current_class_page = self.fetch_class_number_page(
                    class_page.course_info_url,
                    class_page.class_number,
                    referer=class_page.course_info_url,
                )

        raise AssertionError("Class-tab recovery loop exited without returning or raising")

    def fetch_class_detail_tab_page(
        self,
        course_info_url: str,
        class_number: str,
        tab_selector: str,
        *,
        referer: str | None = None,
    ) -> RawClassDetailTabPage:
        """Open one class modal, then follow one requested tab from that modal state."""

        class_page = self.fetch_class_number_page(
            course_info_url,
            class_number,
            referer=referer,
        )
        return self.fetch_class_detail_tab_pages_from_class_page(
            class_page,
            [tab_selector],
        )[0]

