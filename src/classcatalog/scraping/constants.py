from __future__ import annotations

from typing import Final

PEOPLESOFT_ROOT: Final[str] = "https://cmsweb.cms.sdsu.edu/psc/CSDPRD/EMPLOYEE/SA/c"
LANDING_COMPONENT: Final[str] = "SSR_STUDENT_FL.SSR_CLSRCH_MAIN_FL.GBL"
RESULTS_COMPONENT: Final[str] = "SSR_STUDENT_FL.SSR_CLSRCH_ES_FL.GBL"
DETAIL_COMPONENT: Final[str] = "SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL"

LANDING_URL: Final[str] = f"{PEOPLESOFT_ROOT}/{LANDING_COMPONENT}"
RESULTS_URL: Final[str] = f"{PEOPLESOFT_ROOT}/{RESULTS_COMPONENT}"
DETAIL_URL: Final[str] = f"{PEOPLESOFT_ROOT}/{DETAIL_COMPONENT}"

LANDING_PAGE_NAME: Final[str] = "SSR_CLSRCH_MAIN_FL"
RESULTS_PAGE_NAME: Final[str] = "SSR_CLSRCH_ES_FL"
SEARCH_GROUP: Final[str] = "SSR_CLASS_SEARCH_LFF"
INSTITUTION_CODE: Final[str] = "SDCMP"
SEARCH_AGAIN_ACTION: Final[str] = "PTSF_GBLSRCH_FLUID"
RESULT_LIMIT: Final[int] = 75

# Ordered fallback dimensions used only when an exact-subject search still hits the
# PeopleSoft 75-result cap.  Course-level dimensions come first because they produce
# naturally disjoint branches.  Section-level dimensions are later fallbacks; their
# branches can overlap, so the scraper merges/deduplicates course stubs afterwards.
# Keyword search is fuzzy in PeopleSoft. Some subject abbreviations are themselves
# common words that match text rendered on nearly every course result. Use a more
# specific discovery phrase for those subjects from the very first request, while
# still requiring the exact requested Subject facet before accepting rows.
SUBJECT_PRIMARY_SEARCH_TEXT: Final[dict[str, str]] = {
    "CLASS": "CLASSICS",
    "STAT": "STATISTICS",
}

# Very short/new subject prefixes can also be swallowed by unrelated matches before
# their Subject facet is exposed. Retry those known prefixes with a human-readable
# subject phrase before concluding that the term has no offerings. Keep this list
# small and source-backed.
SUBJECT_SEARCH_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "JS": ("Jewish Studies",),
    "PUB": ("Public Affairs", "Public Affairs and Service"),
}

SUBJECT_CAP_PARTITION_FACETS: Final[tuple[str, ...]] = (
    "Course Career",
    "Number of Units",
    "Class Status",
    "Academic Session",
    "Instruction Mode",
    "Location",
    # MUSIC can still exceed the 75-result cap after all dimensions above.
    # Keep splitting with additional facets that SDSU exposes on the same
    # PeopleSoft results page.  These later dimensions are section-level and
    # can overlap, so parse_subject_pages() continues to merge/dedupe by the
    # stable course identity after all leaves are collected.
    "Class Component",
    "Campus",
    "Class Meeting Days",
    "Class Start Times",
    "Class End Times",
    "Shift",
    "Requirement Designation",
    "Class Attribute",
    "CAF Option 1",
    "CAF Option 2",
    "CAF Option 3",
    "CAF Option 4",
    "CAF Option 5",
)

# Only values observed directly should live here. Other terms are discovered from the landing page
# or supplied explicitly with --term-code.
CONFIRMED_TERM_CODES: Final[dict[str, str]] = {
    "Fall 2026": "2267",
    "Spring 2027": "2273",
}

DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_DELAY_SECONDS: Final[float] = 2.0
DEFAULT_JITTER_SECONDS: Final[float] = 0.75
DEFAULT_MAX_RETRIES: Final[int] = 4
DEFAULT_GET_BACKOFF_SECONDS: Final[tuple[float, ...]] = (2.0, 5.0, 10.0, 20.0)
DEFAULT_POST_RECOVERY_COOLDOWN_SECONDS: Final[float] = 20.0
DEFAULT_STATEFUL_POST_RECOVERY_ATTEMPTS: Final[int] = 1
DEFAULT_IN_RUN_STATE_RECOVERY_ATTEMPTS: Final[int] = 1
DEFAULT_STATE_CIRCUIT_BREAKER_THRESHOLD: Final[int] = 3
DEFAULT_USER_AGENT: Final[str] = (
    "ClassCatalog/0.2 educational course-search prototype "
    "(public SDSU schedule pages; low-rate requests)"
)

DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": DEFAULT_USER_AGENT,
}

RESULT_QUERY_TEMPLATE: Final[dict[str, str]] = {
    "Page": RESULTS_PAGE_NAME,
    "SEARCH_GROUP": SEARCH_GROUP,
    "ES_INST": INSTITUTION_CODE,
    "ES_ADV": "N",
    "INVOKE_SEARCHAGAIN": SEARCH_AGAIN_ACTION,
}
