from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from classcatalog.instructors import clean_live_instructor_name
from classcatalog.models import GradingType, SeatStatus, Weekday
from classcatalog.scraping.constants import RESULT_LIMIT
from classcatalog.scraping.models import (
    CourseClassOption,
    CourseInfoRecord,
    CourseSearchHit,
)
from classcatalog.scraping.units import parse_units_text
from classcatalog.subjects import SUBJECT_ABBREVIATIONS

_SPACE_RE = re.compile(r"\s+")
_CATALOG_RE = re.compile(r"^[0-9][0-9A-Z.-]*$", re.I)
_TERM_RE = re.compile(r"\b(?:Winter|Spring|Summer|Fall)\s+20\d{2}\b", re.I)
_TERM_CODE_RE = re.compile(r"\b2\d{3}\b")
_DETAIL_URL_RE = re.compile(
    r"(?P<url>(?:https?://[^\"'<>\s]+|/[^\"'<>\s]+)?"
    r"(?:SSR_STUDENT_FL\.)?SSR_CS_WRAP_FL\.GBL[^\"'<>\s]*)",
    re.I,
)
_GROUPLET_URL_RE = re.compile(
    r"(?P<url>https?://[^\"']*SSR_START_PAGE_FL\.GBL\?[^\"']*ICGrouplet=1[^\"']*)",
    re.I,
)
_DEFAULT_URL_RE = re.compile(
    r"getDefaultURL\s*\(\s*(?P<quote>['\"])(?P<url>.*?SSR_CRSE_INFO_FL\.GBL.*?)"
    r"(?P=quote)\s*\)",
    re.I | re.S,
)
_CAP_PATTERNS = (
    "search results have exceeded a limit",
    "exceeded a limit set by your institution",
    f"more than {RESULT_LIMIT} results",
)
_NO_RESULTS_PATTERNS = (
    "no results were returned",
    "no results were found",
    "no classes found",
    "your search returns no results",
)


class ResultParseError(ValueError):
    """Raised when a result page cannot be interpreted safely."""


class CourseCodeParseError(ResultParseError):
    """Raised when a hidden PeopleSoft course code cannot be split safely."""


class GroupletUrlNotFound(ResultParseError):
    """Raised when a Fluid master/detail shell does not expose its lazy-load URL."""


class CourseInfoUrlNotFound(ResultParseError):
    """Raised when a grouplet does not expose the deferred course-information URL."""


def normalize_space(value: str) -> str:
    return _SPACE_RE.sub(" ", html_module.unescape(value).replace("\xa0", " ")).strip()


def normalize_people_soft_response(body: str) -> str:
    """Return renderable HTML from either a full page or a PeopleSoft AJAX XML response."""

    lowered = body.casefold()
    if "<html" in lowered or "ps_grid-row" in lowered:
        return body
    if "<response" not in lowered and "<?xml" not in lowered:
        return body

    soup = BeautifulSoup(body, "xml")
    fragments: list[str] = []
    for field in soup.find_all(["field", "content"]):
        text = field.string if field.string is not None else field.get_text()
        decoded = html_module.unescape(text or "")
        if "<" in decoded and ">" in decoded:
            fragments.append(decoded)
    return "\n".join(fragments) if fragments else body


def parse_subject_catalog(
    raw_value: str,
    known_subjects: Sequence[str] = SUBJECT_ABBREVIATIONS,
) -> tuple[str, str]:
    normalized = normalize_space(raw_value).upper()
    ordered_subjects = sorted(known_subjects, key=lambda value: (len(value), value), reverse=True)
    for subject in ordered_subjects:
        normalized_subject = normalize_space(subject).upper()
        prefix = f"{normalized_subject} "
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix) :].strip()
        if not remainder:
            continue
        catalog_number = remainder.split(" ", maxsplit=1)[0]
        if _CATALOG_RE.fullmatch(catalog_number):
            return subject, catalog_number
    raise CourseCodeParseError(f"Could not split subject and catalog number from {raw_value!r}.")


def _candidate_term_codes(value: str, term_label: str) -> tuple[str, ...]:
    normalized = normalize_space(value)
    if not normalized or normalized == term_label:
        return ()
    year = term_label.rsplit(" ", maxsplit=1)[-1]
    return tuple(
        dict.fromkeys(
            candidate
            for candidate in _TERM_CODE_RE.findall(normalized)
            if candidate != year
        )
    )


def parse_term_codes(html: str) -> dict[str, str]:
    """Discover term labels only when their PeopleSoft STRM association is unambiguous.

    The Fluid landing page can render neighboring semester labels/codes close together.
    A broad regex that simply takes the first nearby four-digit value can therefore bind
    one semester to another semester's STRM. Prefer each element's own attributes/markup
    and use the wider HTML fallback only when exactly one term label and one candidate
    STRM occur in that local segment. Ambiguous markup is deliberately left unresolved.
    """

    soup = BeautifulSoup(html, "html.parser")
    mapping: dict[str, str] = {}

    for element in soup.find_all(["option", "a", "button", "li", "div", "span"]):
        if not isinstance(element, Tag):
            continue
        label_match = _TERM_RE.search(normalize_space(element.get_text(" ", strip=True)))
        if label_match is None:
            continue
        label = " ".join(word.capitalize() for word in label_match.group(0).split())
        values: list[str] = []
        for attribute in ("value", "data-value", "data-strm", "data-term", "onclick", "href"):
            value = element.get(attribute)
            if isinstance(value, str):
                values.append(value)
        values.append(str(element))
        codes = tuple(
            dict.fromkeys(
                code
                for value in values
                for code in _candidate_term_codes(value, label)
            )
        )
        if len(codes) == 1:
            mapping[label] = codes[0]

    source = str(soup)
    for label_match in _TERM_RE.finditer(source):
        label = " ".join(word.capitalize() for word in label_match.group(0).split())
        if label in mapping:
            continue
        start = max(0, label_match.start() - 350)
        end = min(len(source), label_match.end() + 350)
        segment = source[start:end]
        nearby_labels = {
            " ".join(word.capitalize() for word in match.group(0).split())
            for match in _TERM_RE.finditer(segment)
        }
        if nearby_labels != {label}:
            continue
        codes = _candidate_term_codes(segment, label)
        if len(codes) == 1:
            mapping[label] = codes[0]
    return mapping


def parse_grouplet_url(html: str) -> str:
    """Extract the lazy-loaded master-list URL from a Fluid ``agGroupletList`` shell.

    PeopleSoft renders the outer detail page first and places the real side-panel
    request inside JavaScript.  We intentionally extract the URL supplied by the
    page instead of constructing the ``CSDPRD_newwin`` path ourselves.
    """

    soup = BeautifulSoup(html, "html.parser")
    sources: list[str] = []
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        source = script.string if script.string is not None else script.get_text()
        if "agGroupletList" in source:
            sources.append(source)

    # The full HTML fallback makes the parser tolerant of malformed script tags.
    sources.append(html)
    for source in sources:
        for match in _GROUPLET_URL_RE.finditer(source):
            candidate = html_module.unescape(match.group("url"))
            candidate = candidate.replace("\\u0026", "&").replace("\\x26", "&")
            parsed = urlparse(candidate)
            query = parse_qs(parsed.query)
            if query.get("ICGrouplet") == ["1"]:
                return candidate

    raise GroupletUrlNotFound(
        "The detail shell did not expose an SSR_START_PAGE_FL grouplet URL with ICGrouplet=1."
    )


def parse_course_info_url(html: str) -> str:
    """Extract the URL passed to PeopleSoft's ``getDefaultURL(...)`` call.

    The side-panel grouplet does not contain the actual course information. Instead,
    it tells the browser to lazily load ``SSR_CRSE_INFO_FL``. We follow the URL
    supplied by PeopleSoft rather than constructing the component URL ourselves.
    """

    soup = BeautifulSoup(html, "html.parser")
    sources: list[str] = []
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        source = script.string if script.string is not None else script.get_text()
        if "getDefaultURL" in source:
            sources.append(source)

    # Fall back to the whole response in case PeopleSoft emits malformed script markup.
    sources.append(html)
    for source in sources:
        match = _DEFAULT_URL_RE.search(source)
        if match is None:
            continue
        candidate = html_module.unescape(match.group("url"))
        candidate = (
            candidate.replace("\\u0026", "&")
            .replace("\\x26", "&")
            .replace("\\/", "/")
        )
        parsed = urlparse(candidate)
        if "SSR_CRSE_INFO_FL.GBL" in parsed.path.upper():
            return candidate

    raise CourseInfoUrlNotFound(
        "The detail grouplet did not expose an SSR_CRSE_INFO_FL URL via getDefaultURL(...)."
    )


def has_result_cap_warning(html: str) -> bool:
    text = normalize_space(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)).casefold()
    return any(pattern in text for pattern in _CAP_PATTERNS)


def has_no_results_message(html: str) -> bool:
    """Return whether PeopleSoft explicitly reports an empty class search.

    SDSU's live Fluid page places the authoritative result status in
    ``PTS_SRCH_PTS_INDEXTIME``. Other hidden widgets on the same page contain
    generic text such as "No results to display", so prefer that status field
    and only fall back to full-page text for older/minimal fixtures. A response
    containing actual course rows is never classified as empty.
    """

    normalized_html = normalize_people_soft_response(html)
    if count_result_rows(normalized_html) > 0:
        return False

    soup = BeautifulSoup(normalized_html, "html.parser")
    status = soup.find(id="PTS_SRCH_PTS_INDEXTIME")
    if isinstance(status, Tag):
        status_text = normalize_space(status.get_text(" ", strip=True)).casefold()
        return any(pattern in status_text for pattern in _NO_RESULTS_PATTERNS)

    text = normalize_space(soup.get_text(" ", strip=True)).casefold()
    return any(pattern in text for pattern in _NO_RESULTS_PATTERNS)


def _is_hidden(element: Tag) -> bool:
    if element.has_attr("hidden"):
        return True
    aria_hidden = str(element.get("aria-hidden", "")).casefold()
    style = str(element.get("style", "")).replace(" ", "").casefold()
    return aria_hidden == "true" or "display:none" in style


def _course_code_from_row(
    row: Tag,
    *,
    known_subjects: Sequence[str] = SUBJECT_ABBREVIATIONS,
) -> tuple[str, str, str]:
    candidates: list[str] = []
    for element in row.find_all(["p", "span", "div"]):
        if not isinstance(element, Tag):
            continue
        if _is_hidden(element):
            candidates.append(normalize_space(element.get_text(" ", strip=True)))
    candidates.append(normalize_space(row.get_text(" ", strip=True)))

    for candidate in candidates:
        try:
            subject, catalog_number = parse_subject_catalog(candidate, known_subjects)
        except CourseCodeParseError:
            continue
        return subject, catalog_number, f"{subject} {catalog_number}"
    raise CourseCodeParseError(
        f"No recognized subject/catalog value was found in result row: {candidates[:4]!r}"
    )


def _extract_url_candidate(row: Tag) -> str | None:
    for link in row.find_all("a"):
        if not isinstance(link, Tag):
            continue
        for attribute in ("href", "data-url", "onclick"):
            value = link.get(attribute)
            if not isinstance(value, str):
                continue
            decoded = html_module.unescape(value).replace("\\u0026", "&")
            if "ssr_cs_wrap_fl" in decoded.casefold():
                match = _DETAIL_URL_RE.search(decoded)
                return match.group("url") if match is not None else decoded

    match = _DETAIL_URL_RE.search(html_module.unescape(str(row)).replace("\\u0026", "&"))
    return match.group("url") if match is not None else None


def _clean_detail_url(candidate: str | None, base_url: str) -> str | None:
    if candidate is None:
        return None
    cleaned = candidate.strip(" \"'()")
    if cleaned.casefold().startswith("javascript:"):
        match = _DETAIL_URL_RE.search(cleaned)
        if match is None:
            return None
        cleaned = match.group("url")
    return urljoin(base_url, cleaned)


def _query_values(url: str | None) -> dict[str, str]:
    if url is None:
        return {}
    raw = parse_qs(urlparse(url).query)
    return {key.upper(): values[0] for key, values in raw.items() if values}


def _looks_like_noise(value: str, course_code: str) -> bool:
    lowered = value.casefold()
    return (
        not value
        or value.casefold() == course_code.casefold()
        or lowered in {"view details", "class details", "details", "more"}
        or lowered.startswith("section ")
        or lowered.startswith("class number ")
    )


def _title_from_row(row: Tag, course_code: str) -> str:
    candidates: list[str] = []
    for selector in (
        '[class*="title" i]',
        '[data-label*="title" i]',
        "h1",
        "h2",
        "h3",
        "h4",
        "a",
    ):
        for element in row.select(selector):
            if not isinstance(element, Tag) or _is_hidden(element):
                continue
            text = normalize_space(element.get_text(" ", strip=True))
            text = re.sub(
                rf"^{re.escape(course_code)}\s*[-:–—]?\s*",
                "",
                text,
                flags=re.I,
            )
            if not _looks_like_noise(text, course_code):
                candidates.append(text)

    if candidates:
        return min(candidates, key=lambda value: (len(value.split()) == 1, len(value)))

    visible_text = normalize_space(row.get_text(" ", strip=True))
    visible_text = re.sub(re.escape(course_code), "", visible_text, count=1, flags=re.I).strip()
    return visible_text or "Untitled course"


def _section_count(text: str) -> int | None:
    match = re.search(
        r"\b(\d+)\s+(?:sections?|class(?:es)?(?:\s+options?)?)\b",
        text,
        re.I,
    )
    return int(match.group(1)) if match is not None else None


def _clean_result_title(value: str, course_code: str) -> str:
    cleaned = normalize_space(value)
    # Some live rows repeat the course code in both the result wrapper and title link.
    while re.match(rf"^{re.escape(course_code)}(?:\s|$)", cleaned, re.I):
        cleaned = re.sub(
            rf"^{re.escape(course_code)}\s*[-:–—]?\s*",
            "",
            cleaned,
            count=1,
            flags=re.I,
        ).strip()
    cleaned = re.sub(
        r"\s+\d+\s+Class(?:es)?\s+Options?\s+Available\s*$",
        "",
        cleaned,
        flags=re.I,
    )
    return normalize_space(cleaned) or "Untitled course"


def _result_rows(html: str) -> tuple[Tag, ...]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("li.ps_grid-row.psc_rowact")
    if not rows:
        rows = soup.select(".ps_grid-row.psc_rowact")
    return tuple(row for row in rows if isinstance(row, Tag))


def count_result_rows(html: str) -> int:
    return len(_result_rows(normalize_people_soft_response(html)))


def parse_result_rows(
    html: str,
    *,
    term: str,
    term_code: str,
    base_url: str,
    expected_subject: str | None = None,
    strict_subject: bool = False,
) -> tuple[CourseSearchHit, ...]:
    normalized_html = normalize_people_soft_response(html)
    rows = _result_rows(normalized_html)
    hits: list[CourseSearchHit] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    known_subjects: Sequence[str] = SUBJECT_ABBREVIATIONS
    if expected_subject is not None and expected_subject not in SUBJECT_ABBREVIATIONS:
        # A live-discovered subject is authoritative for this exact-subject parse. Keep
        # the global parser strict while allowing newly introduced SDSU codes to be
        # scraped before ClassCatalog's static seed is updated.
        known_subjects = (*SUBJECT_ABBREVIATIONS, expected_subject)

    for index, row in enumerate(rows):
        subject, catalog_number, course_code = _course_code_from_row(
            row,
            known_subjects=known_subjects,
        )
        if expected_subject is not None and subject != expected_subject:
            if strict_subject:
                raise ResultParseError(
                    f"Filtered result for {expected_subject!r} contained {course_code!r}."
                )
            continue

        detail_url = _clean_detail_url(_extract_url_candidate(row), base_url)
        query = _query_values(detail_url)
        raw_text = normalize_space(row.get_text(" ", strip=True))
        title = _clean_result_title(_title_from_row(row, course_code), course_code)
        dedupe_key = (course_code, query.get("CLASS_NBR"), detail_url)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        hits.append(
            CourseSearchHit(
                term=term,
                term_code=term_code,
                subject=subject,
                catalog_number=catalog_number,
                course_code=course_code,
                title=title,
                detail_url=detail_url,
                crse_id=query.get("CRSE_ID"),
                crse_offer_nbr=query.get("CRSE_OFFER_NBR"),
                acad_career=query.get("ACAD_CAREER"),
                class_number=query.get("CLASS_NBR"),
                section_count=_section_count(raw_text),
                source_row_index=index,
                raw_text=raw_text,
            )
        )
    return tuple(hits)


def unique_subjects(hits: Iterable[CourseSearchHit]) -> tuple[str, ...]:
    return tuple(sorted({hit.subject for hit in hits}))


def dump_debug_summary(hits: Sequence[CourseSearchHit]) -> str:
    return json.dumps(
        {
            "count": len(hits),
            "subjects": unique_subjects(hits),
            "courses": [hit.course_code for hit in hits[:10]],
        },
        indent=2,
    )


_DAY_NAMES: tuple[tuple[str, Weekday], ...] = (
    ("Monday", Weekday.MON),
    ("Tuesday", Weekday.TUE),
    ("Wednesday", Weekday.WED),
    ("Thursday", Weekday.THU),
    ("Friday", Weekday.FRI),
    ("Saturday", Weekday.SAT),
    ("Sunday", Weekday.SUN),
)
_TIME_RANGE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*(?:to|[-–—])\s*"
    r"(?P<end>\d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)
_SEAT_COUNT_RE = re.compile(
    r"(?:Open\s+)?Seats?\s+(?P<open>\d+)\s+of\s+(?P<capacity>\d+)",
    re.I,
)


def _tag_text_by_id(soup: BeautifulSoup, element_id: str) -> str | None:
    element = soup.find(id=element_id)
    if not isinstance(element, Tag):
        return None
    text = normalize_space(element.get_text(" ", strip=True))
    return text or None


def _tag_text_by_id_pattern(row: Tag, pattern: str) -> str | None:
    compiled = re.compile(pattern, re.I)
    element = row.find(id=compiled)
    if not isinstance(element, Tag):
        return None
    text = normalize_space(element.get_text(" ", strip=True))
    return text or None


def _parse_time_value(value: str) -> time | None:
    normalized = normalize_space(value).upper()
    for fmt in ("%I:%M%p", "%I:%M %p"):
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    return None


def _parse_days_times(
    value: str | None,
) -> tuple[tuple[Weekday, ...], time | None, time | None]:
    if value is None:
        return (), None, None
    days = tuple(
        weekday
        for name, weekday in _DAY_NAMES
        if name.casefold() in value.casefold()
    )
    match = _TIME_RANGE_RE.search(value)
    if match is None:
        return days, None, None
    return (
        days,
        _parse_time_value(match.group("start")),
        _parse_time_value(match.group("end")),
    )


def _parse_grading(value: str | None) -> GradingType:
    if value is None:
        return GradingType.OTHER
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    has_letter = "letter" in normalized
    has_crnc = "cr nc" in normalized or "credit no credit" in normalized
    if has_letter and has_crnc:
        return GradingType.LETTER_OR_CREDIT_NO_CREDIT
    if has_letter:
        return GradingType.LETTER
    if has_crnc:
        return GradingType.CREDIT_NO_CREDIT
    return GradingType.OTHER


def _parse_seat_status(value: str | None) -> SeatStatus:
    normalized = normalize_space(value or "").casefold()
    if "wait" in normalized:
        return SeatStatus.WAITLIST
    if "closed" in normalized:
        return SeatStatus.CLOSED
    if "open" in normalized:
        return SeatStatus.OPEN
    return SeatStatus.UNKNOWN


def _parse_seat_counts(
    value: str | None,
) -> tuple[int | None, int | None, int | None]:
    if value is None:
        return None, None, None
    match = _SEAT_COUNT_RE.search(value)
    if match is None:
        return None, None, None
    open_seats = int(match.group("open"))
    capacity = int(match.group("capacity"))
    enrolled = max(capacity - open_seats, 0)
    return open_seats, capacity, enrolled


def _parse_class_cell(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    match = re.search(
        r"^(?P<component>.*?)\s*[-–—]\s*(?P<class_number>\d+)\s*$",
        value,
    )
    if match is not None:
        component = normalize_space(match.group("component")) or None
        return component, match.group("class_number")
    number = re.search(r"\b(\d{3,})\b", value)
    return None, number.group(1) if number is not None else None


_COURSE_INFO_ROW_RE = re.compile(
    r"<tr\b[^>]*id=[\"']SSR_CLS_DTLS_VW[^\"']*_row_(?P<index>\d+)[\"'][^>]*>"
    r"(?P<body>.*?)(?:</tr>)",
    re.I | re.S,
)


def _course_info_option_rows(html: str) -> tuple[Tag, ...]:
    """Return class-option rows from PeopleSoft's mildly malformed table markup."""

    rows: list[Tag] = []
    for match in _COURSE_INFO_ROW_RE.finditer(html):
        fragment = BeautifulSoup(
            f"<table><tr>{match.group('body')}</tr></table>",
            "html.parser",
        )
        row = fragment.find("tr")
        if isinstance(row, Tag):
            rows.append(row)
    return tuple(rows)



_OPTION_WINDOW_RE = re.compile(
    r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s+of\s+(?P<total>\d+)\s+options?",
    re.I,
)
_OPTION_TOTAL_RE = re.compile(r"(?P<total>\d+)\s+options?", re.I)


@dataclass(frozen=True, slots=True)
class CourseOptionGridProgress:
    displayed_rows: int
    start: int | None
    end: int | None
    total: int | None
    complete: bool


def _course_option_window(
    soup: BeautifulSoup,
    displayed_rows: int,
) -> tuple[int | None, int | None, int | None, bool]:
    text = _tag_text_by_id(soup, "SSR_CLSRCH_F_WK_SSR_MSG_TEXT")
    if text:
        match = _OPTION_WINDOW_RE.search(text)
        if match is not None:
            start = int(match.group("start"))
            end = int(match.group("end"))
            total = int(match.group("total"))
            # The explicit PeopleSoft window is authoritative.  During class-modal
            # recovery PeopleSoft can render ``1 - 1 of 1 options`` while omitting the
            # ordinary result-row wrapper that ``displayed_rows`` counts.  Requiring
            # both signals made a fully loaded one-option course look truncated and
            # incorrectly demanded a nonexistent Display More action.
            return start, end, total, start == 1 and end >= total
        total_match = _OPTION_TOTAL_RE.search(text)
        if total_match is not None:
            total = int(total_match.group("total"))
            end = displayed_rows if displayed_rows else total
            return (1 if total else None), end, total, displayed_rows >= total
    if displayed_rows:
        return 1, displayed_rows, displayed_rows, True
    return None, None, None, True


def parse_course_option_grid_progress(html: str) -> CourseOptionGridProgress:
    """Return the visible/total enrollment-option window from Course Information."""

    normalized_html = normalize_people_soft_response(html)
    soup = BeautifulSoup(normalized_html, "html.parser")
    displayed_rows = len(_course_info_option_rows(normalized_html))
    start, end, total, complete = _course_option_window(soup, displayed_rows)
    return CourseOptionGridProgress(
        displayed_rows=displayed_rows,
        start=start,
        end=end,
        total=total,
        complete=complete,
    )


def parse_course_info_page(
    html: str,
    *,
    source_url: str | None = None,
) -> CourseInfoRecord:
    """Parse the fully loaded SDSU ``SSR_CRSE_INFO_FL`` page.

    The page contains course-level metadata plus one grid row per available class
    option. ``source_url`` is optional, but when supplied it lets us retain the
    selected section number carried in the PeopleSoft query string.
    """

    normalized_html = normalize_people_soft_response(html)
    soup = BeautifulSoup(normalized_html, "html.parser")

    course_code = _tag_text_by_id(soup, "SSR_CRSE_INFO_V_SSS_SUBJ_CATLG")
    if course_code is None:
        raise ResultParseError(
            "The course-information page is missing its subject/catalog field."
        )
    subject, catalog_number = parse_subject_catalog(course_code)

    term = _tag_text_by_id(soup, "TERM_VAL_TBL_DESCR")
    title = _tag_text_by_id(soup, "SSR_CRSE_INFO_V_COURSE_TITLE_LONG")
    units_text = _tag_text_by_id(soup, "SSR_CLSRCH_F_WK_UNITS_RANGE")
    if term is None or title is None or units_text is None:
        raise ResultParseError(
            "The course-information page is missing required term, title, or units data."
        )
    try:
        parsed_units = parse_units_text(units_text)
    except ValueError as exc:
        raise ResultParseError(f"Invalid course units value: {units_text!r}") from exc

    query = _query_values(source_url)
    selected_class_number = query.get("CLASS_NBR")
    selected_section_number = query.get("SEC")
    term_code = query.get("STRM")

    options: list[CourseClassOption] = []
    current_option_number: int | None = None
    current_option_group_index: int | None = None
    option_group_count = 0
    rows = _course_info_option_rows(normalized_html)
    grid_progress = parse_course_option_grid_progress(normalized_html)
    options_start = grid_progress.start
    options_end = grid_progress.end
    options_total = grid_progress.total
    options_complete = grid_progress.complete
    for row_index, row in enumerate(rows):
        option_text = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_OPTION_DESCR",
        )
        option_match = re.search(r"\d+", option_text or "")
        if option_match is not None:
            current_option_number = int(option_match.group(0))
            option_group_count += 1
            current_option_group_index = option_group_count
        elif current_option_group_index is None:
            # Defensive fallback for malformed PeopleSoft markup where the first
            # component row omits the visible option label.
            option_group_count += 1
            current_option_group_index = option_group_count
        option_number = current_option_number
        option_group_index = current_option_group_index

        raw_status = _tag_text_by_id_pattern(
            row,
            r"SSR_DER_CS_GRP_SSR_DESCR\$\d+$",
        )
        session = _tag_text_by_id_pattern(
            row,
            r"SSR_DER_CS_GRP_SESSION_CODE",
        )
        class_text = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_CMPNT_DESCR_1",
        )
        component, class_number = _parse_class_cell(class_text)
        if class_number is None:
            raise ResultParseError(
                f"Class option row {row_index} is missing a class number."
            )

        meeting_dates = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_MTG_DT_LONG_1",
        )
        days_times_text = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_MTG_SCHED_L_1",
        )
        days, start_time, end_time = _parse_days_times(days_times_text)
        location = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_MTG_LOC_LONG_1",
        )
        instructor = clean_live_instructor_name(
            _tag_text_by_id_pattern(
                row,
                r"SSR_CLSRCH_F_WK_SSR_INSTR_LONG_1",
            )
        ) or None
        seats_text = _tag_text_by_id_pattern(
            row,
            r"SSR_CLSRCH_F_WK_SSR_DESCR50_1",
        )
        open_seats, seat_capacity, seats_enrolled = _parse_seat_counts(seats_text)

        section_number = (
            selected_section_number
            if selected_class_number is not None
            and class_number == selected_class_number
            else None
        )
        options.append(
            CourseClassOption(
                option_number=option_number,
                option_group_index=option_group_index,
                status=_parse_seat_status(raw_status),
                raw_status=raw_status,
                session=session,
                component=component,
                class_number=class_number,
                section_number=section_number,
                meeting_dates=meeting_dates,
                days_times_text=days_times_text,
                days=days,
                start_time=start_time,
                end_time=end_time,
                location=location,
                instructor=instructor,
                open_seats=open_seats,
                seat_capacity=seat_capacity,
                seats_enrolled=seats_enrolled,
                source_row_index=row_index,
            )
        )

    grading_text = _tag_text_by_id(
        soup,
        "SSR_CLSRCH_F_WK_SSR_GRAD_BASIS_LNG",
    )
    return CourseInfoRecord(
        term=term,
        term_code=term_code,
        subject=subject,
        catalog_number=catalog_number,
        course_code=f"{subject} {catalog_number}",
        title=title,
        description=_tag_text_by_id(soup, "SSR_CRSE_INFO_V_DESCRLONG"),
        units=parsed_units.units,
        units_min=parsed_units.units_min,
        units_max=parsed_units.units_max,
        units_text=parsed_units.units_text,
        grading=_parse_grading(grading_text),
        grading_text=grading_text,
        components=_tag_text_by_id(soup, "SSR_CLSRCH_F_WK_SSR_COMPONENT_LONG"),
        course_career=_tag_text_by_id(soup, "SSR_CLSRCH_F_WK_SSR_ACADCAR_LONG"),
        selected_class_number=selected_class_number,
        selected_section_number=selected_section_number,
        source_url=source_url,
        options_start=options_start,
        options_end=options_end,
        options_total=options_total,
        options_complete=options_complete,
        options=tuple(options),
    )
