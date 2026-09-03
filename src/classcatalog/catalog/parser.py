from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from classcatalog.catalog.models import (
    CatalogProgram,
    CatalogRequirement,
    ProgramCourseMapping,
)
from classcatalog.models import ProgramClassification
from classcatalog.subjects import SUBJECT_ABBREVIATIONS


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


_SUBJECT_PATTERN = "|".join(
    re.escape(subject).replace(r"\ ", r"\s+")
    for subject in sorted(SUBJECT_ABBREVIATIONS, key=len, reverse=True)
)
_COURSE_CODE_RE = re.compile(
    rf"\b(?P<subject>{_SUBJECT_PATTERN})\s+(?P<number>\d{{1,4}}[A-Z]{{0,3}})\b",
    flags=re.IGNORECASE,
)
_BACHELOR_ABBREVIATION_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    r"B\.?\s*A\.?|"
    r"B\.?\s*S\.?|"
    r"B\.?\s*F\.?\s*A\.?|"
    r"B\.?\s*M\.?|"
    r"B\.?\s*B\.?\s*A\.?|"
    r"B\.?\s*A\.?\s*S\.?|"
    r"B\.?\s*S\.?\s*N\.?|"
    r"B\.?\s*S\.?\s*W\.?|"
    r"B\.?\s*ARCH\.?|"
    r"B\.?\s*MUS\.?"
    r")(?![A-Za-z])",
    flags=re.IGNORECASE,
)


def normalize_course_code(value: str) -> str:
    match = _COURSE_CODE_RE.search(_clean(value).upper())
    if match is None:
        raise ValueError(f"No SDSU course code found in {value!r}.")
    subject = " ".join(match.group("subject").upper().split())
    return f"{subject} {match.group('number').upper()}"


def course_code_from_text(value: str) -> str | None:
    match = _COURSE_CODE_RE.search(_clean(value).upper())
    if match is None:
        return None
    subject = " ".join(match.group("subject").upper().split())
    return f"{subject} {match.group('number').upper()}"


def catalog_catoid_from_url(url: str) -> int | None:
    query = parse_qs(urlparse(url).query)
    raw = query.get("catoid", [None])[0]
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_resource_not_found_page(html: str, *, title: str | None = None) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" ", strip=True)).casefold()
    title_text = (title or "").casefold()
    return (
        "resource not found" in title_text
        or "resource not found" in text
        or "unable to locate the resource you attempted to access" in text
    )


def _catalog_year_pattern(catalog_year: str) -> re.Pattern[str] | None:
    years = re.findall(r"\d{4}", catalog_year)
    if len(years) < 2:
        return None
    start, end = years[0], years[1]
    short_end = end[-2:]
    return re.compile(
        rf"\b{re.escape(start)}\s*[-–—/]\s*(?:{re.escape(end)}|{re.escape(short_end)})\b",
        flags=re.IGNORECASE,
    )


def discover_catalog_catoid(
    html: str,
    *,
    base_url: str,
    catalog_year: str,
) -> int | None:
    """Infer the active Acalog catalog id from a catalog landing page.

    Acalog catalog ids are deployment metadata and can change between catalog years.
    Prefer links whose local text names the requested catalog year; otherwise use the
    most frequently referenced catoid on the current catalog page.
    """

    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base_url).netloc.casefold()
    year_pattern = _catalog_year_pattern(catalog_year)
    scores: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)

    for anchor in soup.select("a[href]"):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.netloc.casefold() != host:
            continue
        catoid = catalog_catoid_from_url(url)
        if catoid is None:
            continue
        counts[catoid] += 1
        score = 1
        if any(
            part in parsed.path
            for part in ("index.php", "programs.php", "content.php", "preview_program.php")
        ):
            score += 2
        if year_pattern is not None:
            context = anchor.get_text(" ", strip=True)
            if anchor.parent is not None:
                parent_text = _clean(anchor.parent.get_text(" ", strip=True))
                if len(parent_text) <= 250:
                    context = f"{context} {parent_text}"
            if year_pattern.search(_clean(context)):
                score += 100
        scores[catoid] += score

    if not scores:
        return catalog_catoid_from_url(base_url)

    # Year-labelled links dominate. Frequency is the secondary signal, which makes
    # a normal current-catalog page converge on the catoid used by most navigation.
    return max(scores, key=lambda value: (scores[value], counts[value], value))


@dataclass(frozen=True, slots=True)
class CatalogLink:
    title: str
    url: str


def _main_content(soup: BeautifulSoup) -> Tag:
    selectors = (
        "#acalog-content",
        "#acalog-page-content",
        # Current SDSU Acalog pages split their program/requirement content across
        # multiple sibling ``.acalog-core`` blocks.  Selecting the first core block
        # silently drops the rest of the page, so prefer the enclosing content cell.
        ".block_content",
        ".acalog-core",
        ".acalog-program-core",
        "main",
        "article",
        "body",
    )
    for selector in selectors:
        candidate = soup.select_one(selector)
        if isinstance(candidate, Tag):
            return candidate
    raise ValueError("The catalog HTML has no parseable content root.")


_SHOW_COURSE_RE = re.compile(
    r"showCourse\(\s*['\"](?P<catoid>\d+)['\"]\s*,\s*['\"](?P<coid>\d+)['\"]",
    re.IGNORECASE,
)
_POPUP_COURSE_RE = re.compile(
    r"(?P<url>(?:/ajax/)?preview_course(?:_nopop)?\.php\?[^'\"\s,)]+)",
    re.IGNORECASE,
)
_POPUP_PROGRAM_RE = re.compile(
    r"(?P<url>(?:/ajax/)?preview_program\.php\?[^'\"\s,)]+)",
    re.IGNORECASE,
)


def _embedded_anchor_url(
    anchor: Tag,
    *,
    base_url: str,
    allow_fragment: bool = True,
) -> str | None:
    """Resolve an Acalog navigation URL from href or JavaScript-backed attributes.

    Most Acalog navigation uses a normal href, but a small number of SDSU roadmap
    pages expose their canonical program link through ``onclick``/``rel`` while the
    href is merely ``#``.  Treat only known Acalog document URLs as navigation; course
    popovers are filtered by the caller.
    """

    href = str(anchor.get("href") or "").strip()
    if (
        href
        and (allow_fragment or not href.startswith("#"))
        and not href.casefold().startswith("javascript:")
    ):
        return urljoin(base_url, href)

    values: list[str] = []
    for attribute in ("onclick", "rel", "data-url", "data-href"):
        value = anchor.get(attribute) or ""
        if isinstance(value, list):
            values.append(" ".join(str(item) for item in value))
        else:
            values.append(str(value))
    for value in values:
        match = _POPUP_PROGRAM_RE.search(value)
        if match is not None:
            return urljoin(base_url, match.group("url").replace("&amp;", "&"))
    return None


def _is_course_anchor(anchor: Tag) -> bool:
    href = str(anchor.get("href") or "")
    onclick = str(anchor.get("onclick") or "")
    rel_value = anchor.get("rel") or ""
    rel = " ".join(str(item) for item in rel_value) if isinstance(rel_value, list) else str(rel_value)
    parent_course = anchor.find_parent("li", class_="acalog-course") is not None
    joined = f"{href} {onclick} {rel}".casefold()
    return parent_course or "showcourse(" in joined or "preview_course" in joined


def _course_anchors(root: Tag) -> tuple[Tag, ...]:
    """Return course links across old and current SDSU Acalog markup.

    Older fixtures use a normal ``preview_course*.php`` href.  Current SDSU pages
    usually use ``href='#'`` with ``showCourse(...)`` for list rows, or ``#tt...``
    with ``acalogPopup(...)`` for inline course references.
    """

    anchors: list[Tag] = []
    for anchor in root.find_all("a"):
        if isinstance(anchor, Tag) and _is_course_anchor(anchor):
            anchors.append(anchor)
    return tuple(anchors)


def _course_source_url(anchor: Tag, *, base_url: str) -> str:
    href = str(anchor.get("href") or "").strip()
    if href and href != "#" and not href.startswith("#") and "preview_course" in href:
        return urljoin(base_url, href)

    onclick = str(anchor.get("onclick") or "")
    show_course = _SHOW_COURSE_RE.search(onclick)
    if show_course is not None:
        return urljoin(
            base_url,
            "preview_course_nopop.php?"
            f"catoid={show_course.group('catoid')}&coid={show_course.group('coid')}",
        )

    for value in (onclick, anchor.get("rel") or ""):
        if isinstance(value, list):
            text = " ".join(str(item) for item in value)
        else:
            text = str(value)
        popup = _POPUP_COURSE_RE.search(text)
        if popup is not None:
            return urljoin(base_url, popup.group("url"))

    return base_url


def _same_catalog(url: str, *, catoid: int | None) -> bool:
    if catoid is None:
        return True
    query = parse_qs(urlparse(url).query)
    return query.get("catoid", [str(catoid)])[0] == str(catoid)


def extract_program_links(
    html: str,
    *,
    base_url: str,
    catoid: int | None = None,
) -> tuple[CatalogLink, ...]:
    soup = BeautifulSoup(html, "html.parser")
    links: dict[str, CatalogLink] = {}
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        url = _embedded_anchor_url(anchor, base_url=base_url, allow_fragment=False)
        if not title or url is None or "preview_program.php" not in url:
            continue
        if not _same_catalog(url, catoid=catoid):
            continue
        links[url] = CatalogLink(title=title, url=url)
    return tuple(sorted(links.values(), key=lambda link: (link.title.casefold(), link.url)))


def extract_catalog_navigation_links(
    html: str,
    *,
    base_url: str,
    catoid: int | None = None,
    main_content_only: bool = False,
) -> tuple[CatalogLink, ...]:
    soup = BeautifulSoup(html, "html.parser")
    if main_content_only:
        try:
            root: Tag | BeautifulSoup = _main_content(soup)
        except ValueError:
            root = soup
    else:
        root = soup
    links: dict[str, CatalogLink] = {}
    base_host = urlparse(base_url).netloc
    for anchor in root.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        # Course popovers commonly have hrefs such as ``#tt5455``.  Since joining
        # them to a program URL produces a valid-looking preview_program.php URL,
        # they used to leak into requirement-page discovery whenever a course title
        # contained words such as "Social and Behavioral Sciences".
        if _is_course_anchor(anchor):
            continue
        title = _clean(anchor.get_text(" ", strip=True))
        url = _embedded_anchor_url(anchor, base_url=base_url)
        if not title or url is None:
            continue
        if urlparse(url).netloc != base_host:
            continue
        if not _same_catalog(url, catoid=catoid):
            continue
        if not any(
            part in url
            for part in (
                "content.php",
                "programs.php",
                "preview_program.php",
                "preview_entity.php",
                "index.php",
            )
        ):
            continue
        links[url] = CatalogLink(title=title, url=url)
    return tuple(sorted(links.values(), key=lambda link: (link.title.casefold(), link.url)))


def _heading_text(tag: Tag) -> str | None:
    text = _clean(tag.get_text(" ", strip=True))
    if not text or len(text) > 220 or course_code_from_text(text) is not None:
        return None
    return text


def _context_labels(link: Tag, *, max_labels: int = 16) -> tuple[str, ...]:
    """Return the active Acalog heading hierarchy, nearest heading first.

    The key detail is that sibling sections must not leak into one another. For
    example, a course under ``Other Graduation Information`` must not inherit an
    earlier ``Electives`` heading. Once the nearest heading is found, only parent
    heading levels are retained.
    """

    labels: list[str] = []
    seen: set[str] = set()

    def add(candidate: Tag) -> None:
        if candidate.find_parent("a") is not None:
            return
        text = _heading_text(candidate)
        if text is None:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        labels.append(text)

    # Acalog sometimes puts section labels in bold cells rather than headings.
    # Restrict these to the link's local ancestors so unrelated earlier sections
    # cannot become context.
    for ancestor in link.parents:
        if not isinstance(ancestor, Tag):
            continue
        for local in ancestor.find_all(["strong", "b"], recursive=False):
            if isinstance(local, Tag):
                add(local)
        if ancestor.name in {"main", "article", "body"} or len(labels) >= max_labels:
            break

    active_level: int | None = None
    for previous in link.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if len(labels) >= max_labels:
            break
        if not isinstance(previous, Tag):
            continue
        level = int(previous.name[1])
        if active_level is None or level < active_level:
            add(previous)
            active_level = level
        if level == 1:
            break
    return tuple(labels)


_ELECTIVE_RE = re.compile(
    r"\b(elective|electives|option courses|select from|selected from|approved options)\b",
    re.IGNORECASE,
)
_PREP_RE = re.compile(
    r"\b(preparation for the major|preparation for major|pre-major|major preparation|"
    r"lower[- ]division preparation|preparation courses|preparation for the degree|"
    r"required lower[- ]division(?: [a-z&/ -]+)? courses?)\b",
    re.IGNORECASE,
)
_MAJOR_RE = re.compile(
    r"\b(requirements? for the major|major requirements?|required major courses?|"
    r"upper[- ]division major|core courses?|required courses?|major courses?)\b",
    re.IGNORECASE,
)
_MAJOR_ONLY_RE = re.compile(r"^major(?: \([^)]*\))?$", re.IGNORECASE)

# SDSU uses several requirement headings that are authoritative but do not contain
# the words ``major``, ``preparation``, or ``elective``.  Map only headings whose
# academic role is clear.  Broad prose containers such as ``Degree Requirements``
# remain unclassified so a generic page wrapper cannot turn every linked course into
# a program requirement.
_PROGRAM_ELECTIVE_REQUIREMENT_RE = re.compile(
    r"\b(?:graduation writing assessment requirement|"
    r"requirements? for (?:specialization|concentration|emphasis)|"
    r"breadth|auxiliary area|capstone requirement|international experience|"
    r"study abroad requirement|two of the following|english honors variation)\b",
    re.IGNORECASE,
)
_PROGRAM_PREP_REQUIREMENT_RE = re.compile(
    r"\b(?:teacher credential program prerequisites?|"
    r"additional lower[- ]division (?:courses?|coursework|requirements?)|"
    r"language requirement(?: for .+)?|mathematics competency requirement)\b",
    re.IGNORECASE,
)
_PROGRAM_MAJOR_REQUIREMENT_RE = re.compile(
    r"\b(?:credential requirements?|teacher education|(?:additional|addtional) requirements?|"
    r"professional experience requirement|seminar requirement|"
    r"upper[- ]division theatre courses?|music courses?|dance courses?)\b",
    re.IGNORECASE,
)
_IGNORED_PROGRAM_CONTEXT_RE = re.compile(
    r"^(?:impacted programs?|recommended|note|general education)$",
    re.IGNORECASE,
)


def _ignore_program_course_context(
    labels: Iterable[str],
    *,
    program_name: str | None = None,
) -> bool:
    """Return whether a linked course appears only in non-curricular catalog prose.

    Current SDSU pages link many courses from impaction/admission explanations, notes,
    recommendations, and generic General Education text.  Those links are useful prose
    references but are not evidence that every linked course belongs to the plan.
    """

    label_values = tuple(labels)
    if any(_IGNORED_PROGRAM_CONTEXT_RE.match(_clean(label)) for label in label_values):
        return True

    # The standard ECL B.A. page links RWS 100 from a prose-only "Selection of
    # Courses" section.  The credential variant lists it authoritatively under
    # Preparation for the Major, but the standard plan does not.  Keep this narrow so
    # similarly named requirement groups on other programs are not discarded.
    standard_ecl_program = "english and comparative literature, b.a."
    if program_name and _clean(program_name).casefold() == standard_ecl_program:
        return any(_clean(label).casefold() == "selection of courses" for label in label_values)
    return False


def _specialization_heading_matches_program(label: str, program_name: str | None) -> bool:
    """Recognize a plan-specific specialization heading without using the page title.

    Urban Studies pages, for example, use a heading such as ``Urban Sustainability``
    under a program named ``Urban Studies, Urban Sustainability Specialization, B.A.``.
    The heading is an option pool, but it contains neither ``elective`` nor the word
    ``specialization``.  Require a multi-word, proper subset of a specialization title
    so the outer program heading itself cannot classify unrelated prose.
    """

    if not program_name or "specialization" not in program_name.casefold():
        return False
    label_clean = _clean(label).casefold()
    program_clean = _clean(program_name).casefold()
    if not label_clean or label_clean == program_clean:
        return False
    label_words = {word for word in re.findall(r"[a-z0-9]+", label_clean) if len(word) > 2}
    program_words = {word for word in re.findall(r"[a-z0-9]+", program_clean) if len(word) > 2}
    generic = {"specialization", "bachelor", "degree", "with", "the"}
    label_words.difference_update(generic)
    program_words.difference_update(generic)
    return len(label_words) >= 2 and label_words <= program_words


def classified_context(
    labels: Iterable[str],
    *,
    program_name: str | None = None,
) -> tuple[ProgramClassification, str] | None:
    """Return a program classification and the label that justified it.

    Current SDSU program pages commonly use terse section headings such as ``Major``
    and discipline-specific lower-division headings such as ``Required Lower Division
    Nursing Courses``.  Those are authoritative catalog structure, not guesses, so they
    should classify the courses beneath them.
    """

    label_values = tuple(labels)
    for label in label_values:
        if _PROGRAM_ELECTIVE_REQUIREMENT_RE.search(label):
            return ProgramClassification.ELECTIVE, label
        if _PROGRAM_PREP_REQUIREMENT_RE.search(label):
            return ProgramClassification.MAJOR_PREP, label
        if _PROGRAM_MAJOR_REQUIREMENT_RE.search(label):
            return ProgramClassification.MAJOR_COURSE, label
        if _ELECTIVE_RE.search(label):
            return ProgramClassification.ELECTIVE, label
        if _PREP_RE.search(label):
            return ProgramClassification.MAJOR_PREP, label
        if _MAJOR_RE.search(label) or _MAJOR_ONLY_RE.match(_clean(label)):
            return ProgramClassification.MAJOR_COURSE, label
        if _specialization_heading_matches_program(label, program_name):
            return ProgramClassification.ELECTIVE, label
    return None


def classification_from_context(labels: Iterable[str]) -> ProgramClassification | None:
    classified = classified_context(labels)
    return classified[0] if classified is not None else None


def _degree_type(program_name: str) -> str | None:
    match = _BACHELOR_ABBREVIATION_RE.search(program_name)
    if match is None:
        folded = program_name.casefold()
        spelled_out = {
            "bachelor of arts": "B.A.",
            "bachelor of science": "B.S.",
            "bachelor of fine arts": "B.F.A.",
            "bachelor of music": "B.M.",
            "bachelor of social work": "B.S.W.",
            "bachelor of architecture": "B.Arch.",
        }
        return next((value for key, value in spelled_out.items() if key in folded), None)

    letters = re.sub(r"[^A-Za-z]", "", match.group(0)).upper()
    return {
        "BA": "B.A.",
        "BS": "B.S.",
        "BFA": "B.F.A.",
        "BM": "B.M.",
        "BBA": "B.B.A.",
        "BAS": "B.A.S.",
        "BSN": "B.S.N.",
        "BSW": "B.S.W.",
        "BARCH": "B.Arch.",
        "BMUS": "B.Mus.",
    }.get(letters)


def parse_program_page(
    html: str,
    *,
    program_name: str,
    catalog_year: str,
    source_url: str,
    department_url: str | None = None,
) -> CatalogProgram:
    soup = BeautifulSoup(html, "html.parser")
    main = _main_content(soup)
    mappings: dict[tuple[str, ProgramClassification], ProgramCourseMapping] = {}
    unmapped: set[str] = set()
    unmapped_contexts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    warnings: list[str] = []

    for anchor in _course_anchors(main):
        if not isinstance(anchor, Tag):
            continue
        code = course_code_from_text(anchor.get_text(" ", strip=True))
        if code is None and anchor.parent is not None:
            code = course_code_from_text(anchor.parent.get_text(" ", strip=True))
        if code is None:
            continue
        labels = _context_labels(anchor)
        if _ignore_program_course_context(labels, program_name=program_name):
            continue
        classified = classified_context(labels, program_name=program_name)
        if classified is None:
            unmapped.add(code)
            unmapped_contexts[labels[:4]].add(code)
            continue
        classification, group = classified
        mappings[(code, classification)] = ProgramCourseMapping(
            course_code=code,
            classification=classification,
            group=group,
            source_url=_course_source_url(anchor, base_url=source_url),
        )

    # Acalog frequently repeats a course in a summary, admission note, roadmap link,
    # or prose block after listing the same course under an authoritative requirement
    # heading. ``unmapped_course_codes`` is intended to mean the course could not be
    # classified anywhere on the page, not merely that one duplicate occurrence lacked
    # a heading. Remove every code that has at least one classified occurrence.
    mapped_codes = {course_code for course_code, _ in mappings}
    unmapped.difference_update(mapped_codes)
    for codes in unmapped_contexts.values():
        codes.difference_update(mapped_codes)

    if not mappings:
        page_title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
        heading = main.find(["h1", "h2"])
        heading_text = _clean(heading.get_text(" ", strip=True)) if isinstance(heading, Tag) else ""
        is_roadmap = "roadmap" in f"{page_title} {heading_text}".casefold()
        if is_roadmap:
            # A small number of SDSU plans (notably Humanities in 2026-2027) have no
            # linked canonical requirements page. Their roadmap still names a handful
            # of explicit major courses while referring readers to the catalog for
            # additional approved courses. Preserve only those explicitly labelled
            # Major/Major Prep courses and warn that the result is partial.
            for item in main.find_all("li"):
                if not isinstance(item, Tag):
                    continue
                text = _clean(item.get_text(" ", strip=True))
                code = course_code_from_text(text)
                if code is None:
                    continue
                folded = text.casefold()
                classification: ProgramClassification | None = None
                group: str | None = None
                if "major prep" in folded:
                    classification = ProgramClassification.MAJOR_PREP
                    group = "Roadmap: Major Prep"
                else:
                    child_labels = {
                        _clean(tag.get_text(" ", strip=True)).casefold()
                        for tag in item.find_all(["p", "li"], recursive=True)
                    }
                    if "major" in child_labels or "/ major" in folded:
                        classification = ProgramClassification.MAJOR_COURSE
                        group = "Roadmap: Major"
                if classification is not None:
                    mappings[(code, classification)] = ProgramCourseMapping(
                        course_code=code,
                        classification=classification,
                        group=group,
                        source_url=source_url,
                        notes=("Explicitly listed on roadmap; full approved-course list is not enumerated on this page.",),
                    )
            if mappings:
                warnings.append(
                    "Roadmap-only program source: explicit major courses were mapped, but the page "
                    "references additional approved courses without enumerating them. Requirements "
                    "for this program are incomplete until SDSU exposes a full requirements page."
                )
            else:
                warnings.append(
                    "No classifiable course mappings were found. The page is a roadmap and does not "
                    "enumerate enough program requirements for a complete mapping."
                )
        else:
            warnings.append(
                "No classifiable course mappings were found. The page may be a roadmap, a bot "
                "challenge, or use headings not covered by the current parser."
            )
    if unmapped:
        context_counts = sorted(
            (
                (len(codes), " > ".join(labels) if labels else "No heading context")
                for labels, codes in unmapped_contexts.items()
                if codes
            ),
            key=lambda item: (-item[0], item[1].casefold()),
        )
        context_summary = "; ".join(
            f"{label} ({count})" for count, label in context_counts[:3]
        )
        warning = (
            f"{len(unmapped)} course codes were present but could not be assigned to major "
            "preparation, major course, or elective without guessing."
        )
        if context_summary:
            warning = f"{warning} Top unclassified contexts: {context_summary}."
        warnings.append(warning)

    return CatalogProgram(
        name=program_name,
        catalog_year=catalog_year,
        source_url=source_url,
        department_url=department_url,
        degree_type=_degree_type(program_name),
        mappings=tuple(
            sorted(
                mappings.values(),
                key=lambda mapping: (
                    mapping.classification.value,
                    mapping.course_code,
                ),
            )
        ),
        unmapped_course_codes=tuple(sorted(unmapped)),
        warnings=tuple(warnings),
    )


_KNOWN_REQUIREMENTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\boral communication\b", re.I), "GE A1", "Oral Communication"),
    (
        re.compile(r"\bwritten communication\b|\benglish composition\b", re.I),
        "GE A2",
        "Written Communication",
    ),
    (re.compile(r"\bcritical thinking\b", re.I), "GE A3", "Critical Thinking"),
    (re.compile(r"\bphysical science\b", re.I), "GE B1", "Physical Science"),
    (
        re.compile(r"\blife science\b|\bbiological science\b", re.I),
        "GE B2",
        "Life Science",
    ),
    (
        re.compile(r"\blaboratory activity\b|\bscience laboratory\b", re.I),
        "GE B3",
        "Laboratory Activity",
    ),
    (
        re.compile(
            r"\bmathematics\b|\bquantitative reasoning\b|\bmathematical concepts\b",
            re.I,
        ),
        "GE B4",
        "Mathematics/Quantitative Reasoning",
    ),
    (re.compile(r"\barts\b", re.I), "GE C1", "Arts"),
    (re.compile(r"\bhumanities\b", re.I), "GE C2", "Humanities"),
    (
        re.compile(r"\bsocial and behavioral sciences\b|\bsocial sciences\b", re.I),
        "GE D",
        "Social and Behavioral Sciences",
    ),
    (
        re.compile(r"\blifelong learning\b|\bself-development\b", re.I),
        "GE E",
        "Lifelong Learning and Self-Development",
    ),
    (re.compile(r"\bethnic studies\b", re.I), "GE F", "Ethnic Studies"),
    (re.compile(r"\bamerican institutions\b", re.I), "AI", "American Institutions"),
    (
        re.compile(r"\bgraduation writing assessment requirement\b|\bgwar\b", re.I),
        "GWAR",
        "Graduation Writing Assessment Requirement",
    ),
)
_AREA_CODE_RE = re.compile(
    r"^(?:ge\s+)?(?:area\s+)?(?P<code>[A-F]\d?|[1-6][A-C]?)\b"
    r"\s*[:.\-–—]?\s*(?P<name>.+)$",
    re.IGNORECASE,
)


def requirement_identity(labels: Iterable[str]) -> tuple[str, str] | None:
    for label in labels:
        folded = _clean(label).casefold()
        # Degree-program and roadmap titles are outer page headings, not GE section
        # labels. Without this guard, a program such as "Classics, Emphasis in
        # Classical Humanities, B.A." can be misread as GE C2 merely because the page
        # title contains the word "Humanities".
        if "roadmap" in folded or _BACHELOR_ABBREVIATION_RE.search(label):
            continue
        match = _AREA_CODE_RE.match(label)
        if match:
            code = match.group("code").upper()
            name = _clean(match.group("name"))
            return f"GE {code}", name
        for pattern, code, name in _KNOWN_REQUIREMENTS:
            if pattern.search(label):
                return code, name
    return None


def parse_requirement_page(
    html: str,
    *,
    catalog_year: str,
    source_url: str,
) -> tuple[CatalogRequirement, ...]:
    soup = BeautifulSoup(html, "html.parser")
    main = _main_content(soup)
    courses_by_requirement: dict[tuple[str, str], set[str]] = defaultdict(set)

    for anchor in _course_anchors(main):
        if not isinstance(anchor, Tag):
            continue
        code = course_code_from_text(anchor.get_text(" ", strip=True))
        if code is None and anchor.parent is not None:
            code = course_code_from_text(anchor.parent.get_text(" ", strip=True))
        if code is None:
            continue
        identity = requirement_identity(_context_labels(anchor))
        if identity is None:
            continue
        courses_by_requirement[identity].add(code)

    return tuple(
        CatalogRequirement(
            code=code,
            name=name,
            catalog_year=catalog_year,
            source_url=source_url,
            course_codes=tuple(sorted(course_codes)),
        )
        for (code, name), course_codes in sorted(courses_by_requirement.items())
    )


def is_undergraduate_degree_program(title: str) -> bool:
    """Return whether an Acalog program label represents a bachelor's degree.

    SDSU is inconsistent about punctuation and degree spelling across catalog pages:
    labels can contain ``B.S.``, ``BS``, ``BFA``, ``BSN``, or a spelled-out
    ``Bachelor of ...``.  Detect the bachelor's marker first rather than excluding a
    title merely because it also mentions a graduate degree (for example a combined
    B.S./M.S. pathway).
    """

    normalized = _clean(title)
    folded = normalized.casefold()
    if "minor" in folded:
        return False
    has_bachelors_marker = "bachelor" in folded or bool(
        _BACHELOR_ABBREVIATION_RE.search(normalized)
    )
    # Some SDSU undergraduate roadmaps combine a bachelor's degree with a teaching
    # credential. The explicit bachelor's marker is authoritative and must win over
    # the word "credential"; credential-only and certificate-only pages remain excluded.
    if has_bachelors_marker:
        return True
    if any(word in folded for word in ("certificate", "credential")):
        return False
    return False


def likely_requirement_navigation_link(link: CatalogLink) -> bool:
    text = _clean(link.title).casefold()
    if "roadmap" in text or _BACHELOR_ABBREVIATION_RE.search(link.title):
        return False

    if any(
        phrase in text
        for phrase in (
            "general education",
            "graduation requirements",
            "american institutions",
            "graduation writing",
            "gwar",
            "cal-getc",
            "ethnic studies requirement",
        )
    ):
        return True

    # Current SDSU GE anchors use labels such as "1B. Critical Thinking" and
    # "Area 5. Physical and Biological Sciences". Matching the leading area code
    # is safer than treating any program title containing a word such as Humanities
    # or Ethnic Studies as a university-wide requirement page.
    return bool(
        re.match(
            r"^(?:ge\s+)?(?:area\s+)?(?:[1-6][a-c]?|[a-f][1-4]?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
