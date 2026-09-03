from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl, urldefrag, urlencode, urlparse

from classcatalog.catalog.coverage import (
    load_expected_programs,
    validate_program_coverage,
)
from classcatalog.catalog.browser import (
    CatalogBrowser,
    CatalogBrowserConfig,
    CatalogPageSnapshot,
)
from classcatalog.catalog.models import (
    CatalogMappings,
    CatalogProgram,
    CatalogRequirement,
    CatalogSource,
    CatalogSourceKind,
)
from classcatalog.catalog.parser import (
    CatalogLink,
    catalog_catoid_from_url,
    discover_catalog_catoid,
    extract_catalog_navigation_links,
    is_resource_not_found_page,
    is_undergraduate_degree_program,
    likely_requirement_navigation_link,
    parse_program_page,
    parse_requirement_page,
)


@dataclass(frozen=True, slots=True)
class CatalogScrapeConfig:
    catalog_year: str
    catoid: int
    index_url: str
    programs: tuple[str, ...] = ()
    all_undergraduate_programs: bool = False
    program_urls: tuple[str, ...] = ()
    requirement_urls: tuple[str, ...] = ()
    fixture_dir: Path | None = None
    browser: CatalogBrowserConfig = CatalogBrowserConfig()
    max_requirement_pages: int = 40


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "page"


def _save_snapshot(snapshot: CatalogPageSnapshot, *, path: Path | None) -> str | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.html, encoding="utf-8")
    return str(path)


def _program_selection(
    links: tuple[CatalogLink, ...],
    *,
    requested: tuple[str, ...],
    all_undergraduate: bool,
) -> tuple[CatalogLink, ...]:
    if all_undergraduate:
        return tuple(link for link in links if is_undergraduate_degree_program(link.title))
    if not requested:
        requested = ("Computer Science, B.S.",)
    lookup = {link.title.casefold(): link for link in links}
    selected: list[CatalogLink] = []
    missing: list[str] = []
    for name in requested:
        match = lookup.get(name.casefold())
        if match is None:
            missing.append(name)
        else:
            selected.append(match)
    if missing:
        available = "; ".join(link.title for link in links[:20])
        raise ValueError(
            f"Catalog program(s) not found: {', '.join(missing)}. "
            f"First available program labels: {available}"
        )
    return tuple(selected)


def _catalog_document_key(url: str) -> str:
    """Return a stable identity for an Acalog document.

    Acalog appends navigation-only parameters such as ``returnto`` and can emit the
    same page with different query ordering. Those URLs redirect to identical HTML and
    must not be fetched or parsed as separate requirement documents.
    """

    document_url, _ = urldefrag(url)
    parsed = urlparse(document_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    identity_pairs = [
        (key, query[key])
        for key in ("catoid", "poid", "navoid", "ent_oid")
        if key in query
    ]
    normalized_query = urlencode(identity_pairs)
    base = f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}{parsed.path}"
    return f"{base}?{normalized_query}" if normalized_query else base


def _merge_requirements(requirements: list[CatalogRequirement]) -> tuple[CatalogRequirement, ...]:
    merged: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    sources: dict[tuple[str, str, str], str] = {}
    for requirement in requirements:
        key = (requirement.catalog_year, requirement.code, requirement.name)
        merged[key].update(requirement.course_codes)
        sources.setdefault(key, requirement.source_url)
    return tuple(
        CatalogRequirement(
            code=code,
            name=name,
            catalog_year=catalog_year,
            source_url=sources[(catalog_year, code, name)],
            course_codes=tuple(sorted(course_codes)),
        )
        for (catalog_year, code, name), course_codes in sorted(merged.items())
    )



def _catalog_home_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "catalog.sdsu.edu"
    return f"{scheme}://{host}/index.php"


def _resolve_catalog_entry(
    browser: CatalogBrowser,
    config: CatalogScrapeConfig,
) -> tuple[CatalogPageSnapshot, int, tuple[str, ...]]:
    """Open the catalog and resolve Acalog's current catoid without trusting a stale id."""

    notes: list[str] = []
    requested = browser.open(config.index_url)
    requested_url_catoid = catalog_catoid_from_url(requested.url)
    requested_not_found = is_resource_not_found_page(
        requested.html,
        title=requested.title,
    )

    index = requested
    if requested_not_found:
        home_url = _catalog_home_url(config.index_url)
        print(
            "catalog_index_resource_not_found "
            f"requested_url={config.index_url!r} fallback_url={home_url!r}"
        )
        index = browser.open(home_url)
        if is_resource_not_found_page(index.html, title=index.title):
            raise ValueError(
                "SDSU returned Resource Not Found for both the requested catalog URL and "
                "the catalog home page."
            )
        notes.append(
            f"Requested catalog URL {config.index_url!r} returned Resource Not Found; "
            "the collector continued from the SDSU catalog home page."
        )

    # When the effective landing page has no catoid (the normal catalog home page), infer
    # the active id from links for the requested catalog year. If a stale CLI --catoid was
    # supplied, this intentionally overrides it rather than constructing broken child URLs.
    effective_url_catoid = catalog_catoid_from_url(index.url)
    discovered_catoid = discover_catalog_catoid(
        index.html,
        base_url=index.url,
        catalog_year=config.catalog_year,
    )

    if effective_url_catoid is not None and not requested_not_found:
        resolved = effective_url_catoid
    elif discovered_catoid is not None:
        resolved = discovered_catoid
    elif requested_url_catoid is not None and not requested_not_found:
        resolved = requested_url_catoid
    else:
        resolved = config.catoid

    if resolved != config.catoid:
        notes.append(
            f"Requested catoid {config.catoid} was replaced with catalog catoid {resolved} "
            f"resolved from the {config.catalog_year} catalog navigation."
        )
    print(
        "catalog_index_resolved "
        f"catalog_year={config.catalog_year!r} requested_catoid={config.catoid} "
        f"resolved_catoid={resolved} url={index.url!r}"
    )
    return index, resolved, tuple(notes)

def scrape_catalog(config: CatalogScrapeConfig) -> CatalogMappings:
    sources: list[CatalogSource] = []
    warnings: list[str] = []
    programs: list[CatalogProgram] = []
    requirements: list[CatalogRequirement] = []

    fixture_root = config.fixture_dir
    with CatalogBrowser(config.browser) as browser:
        index, resolved_catoid, resolution_notes = _resolve_catalog_entry(browser, config)
        warnings.extend(resolution_notes)
        effective_index_url = index.url
        index_fixture = _save_snapshot(
            index,
            path=(fixture_root / "catalog-index.html") if fixture_root else None,
        )
        sources.append(
            CatalogSource(
                kind=CatalogSourceKind.CATALOG_INDEX,
                url=index.url,
                title=index.title,
                fetched_at=datetime.now(UTC).isoformat(),
                fixture_path=index_fixture,
            )
        )

        discovered = browser.discover_programs(
            index_url=effective_index_url,
            catoid=resolved_catoid,
        )
        explicit_links = tuple(
            CatalogLink(title=f"Explicit program {index + 1}", url=url)
            for index, url in enumerate(config.program_urls)
        )
        if explicit_links:
            selected = explicit_links
        else:
            selected = _program_selection(
                discovered,
                requested=config.programs,
                all_undergraduate=config.all_undergraduate_programs,
            )
        print(
            "catalog_program_selection "
            f"discovered_programs={len(discovered)} selected_programs={len(selected)} "
            f"all_undergraduate={config.all_undergraduate_programs}"
        )
        if config.all_undergraduate_programs and len(selected) != len(discovered):
            selected_urls = {link.url for link in selected}
            rejected = [link.title for link in discovered if link.url not in selected_urls]
            print(
                "catalog_program_rejected "
                f"count={len(rejected)} titles={rejected[:20]!r}"
            )

        if config.all_undergraduate_programs:
            registry_path = (
                Path(__file__).resolve().parents[1]
                / "data"
                / "sdsu"
                / f"programs_{config.catalog_year.replace('-', '_')}.json"
            )
            if registry_path.exists():
                expected_programs = load_expected_programs(registry_path)
                coverage = validate_program_coverage(
                    expected_programs,
                    (link.title for link in selected),
                )
                print(
                    "catalog_program_validation "
                    f"registry_plan_codes={coverage.expected_count} "
                    f"catalog_pages={coverage.scraped_count} "
                    f"unique_catalog_titles={coverage.unique_scraped_count} "
                    f"matched_plan_codes={coverage.matched_count} "
                    f"matched_catalog_programs={coverage.matched_catalog_count} "
                    f"plan_match_rate={coverage.coverage_percent:.1f}% "
                    f"registry_only_plans={len(coverage.missing)} "
                    f"ambiguous_plans={len(coverage.ambiguous)} "
                    f"catalog_only_programs={len(coverage.unexpected)}"
                )
                if coverage.missing:
                    print(
                        "catalog_program_validation_registry_only "
                        f"examples={[f'{plan.plan_code}: {plan.name}' for plan in coverage.missing[:15]]!r}"
                    )
                if coverage.ambiguous:
                    print(
                        "catalog_program_validation_ambiguous "
                        f"examples={[
                            (f'{plan.plan_code}: {plan.name}', list(candidates))
                            for plan, candidates in coverage.ambiguous[:10]
                        ]!r}"
                    )
                if coverage.unexpected:
                    print(
                        "catalog_program_validation_catalog_only "
                        f"examples={list(coverage.unexpected[:15])!r}"
                    )

        program_snapshots: list[CatalogPageSnapshot] = []
        program_scrape_started = monotonic()
        total_programs = len(selected)
        for position, link in enumerate(selected, start=1):
            elapsed_seconds = int(monotonic() - program_scrape_started)
            print(
                "catalog_program_scrape_progress "
                f"position={position} total={total_programs} "
                f"elapsed_seconds={elapsed_seconds} title={link.title!r}"
            )
            snapshot = browser.open(link.url)
            program_snapshots.append(snapshot)
            fixture = _save_snapshot(
                snapshot,
                path=(fixture_root / "programs" / f"{_slug(link.title)}.html")
                if fixture_root
                else None,
            )
            sources.append(
                CatalogSource(
                    kind=CatalogSourceKind.PROGRAM,
                    url=snapshot.url,
                    title=link.title,
                    fetched_at=datetime.now(UTC).isoformat(),
                    fixture_path=fixture,
                )
            )
            program_name = link.title
            if program_name.startswith("Explicit program"):
                title = snapshot.title.split("-")[0].strip()
                if title:
                    program_name = title
            programs.append(
                parse_program_page(
                    snapshot.html,
                    program_name=program_name,
                    catalog_year=config.catalog_year,
                    source_url=snapshot.url,
                    department_url=(
                        "https://cs.sdsu.edu/computer-science-degree-programs/"
                        if program_name.casefold() == "computer science, b.s."
                        else None
                    ),
                )
            )

        requirement_queue: deque[CatalogLink] = deque()
        visited: set[str] = set()
        parsed_documents: set[str] = set()
        for url in config.requirement_urls:
            requirement_queue.append(CatalogLink(title="Explicit requirement", url=url))
        if not requirement_queue:
            for link in browser.discover_requirement_pages(
                index_url=effective_index_url,
                catoid=resolved_catoid,
                additional_html=program_snapshots,
            ):
                requirement_queue.append(link)

        while requirement_queue and len(parsed_documents) < config.max_requirement_pages:
            link = requirement_queue.popleft()
            # URL fragments select a section inside an already-downloaded Acalog page;
            # they are not separate server resources.  Fetch each underlying document
            # once while still allowing the parser to discover every section in it.
            document_url, _ = urldefrag(link.url)
            document_key = _catalog_document_key(document_url)
            if document_key in visited:
                continue
            visited.add(document_key)
            snapshot = browser.open(document_url)
            resolved_document_key = _catalog_document_key(snapshot.url)
            # Navigation aliases can redirect to a page that was already parsed.
            # Deduplicate after navigation as well as before it so the same GE page
            # is not saved and reported twice under two catalog labels.
            if resolved_document_key in parsed_documents:
                continue
            parsed_documents.add(resolved_document_key)
            visited.add(resolved_document_key)
            fixture = _save_snapshot(
                snapshot,
                path=(
                    fixture_root
                    / "requirements"
                    / f"{len(parsed_documents):02d}-{_slug(link.title)}.html"
                )
                if fixture_root
                else None,
            )
            parsed = parse_requirement_page(
                snapshot.html,
                catalog_year=config.catalog_year,
                source_url=snapshot.url,
            )
            requirements.extend(parsed)
            sources.append(
                CatalogSource(
                    kind=CatalogSourceKind.REQUIREMENT,
                    url=snapshot.url,
                    title=link.title,
                    fetched_at=datetime.now(UTC).isoformat(),
                    fixture_path=fixture,
                )
            )
            for child in extract_catalog_navigation_links(
                snapshot.html,
                base_url=snapshot.url,
                catoid=resolved_catoid,
                main_content_only=True,
            ):
                child_document_key = _catalog_document_key(child.url)
                if (
                    child_document_key not in visited
                    and child_document_key not in parsed_documents
                    and likely_requirement_navigation_link(child)
                ):
                    requirement_queue.append(child)

    if not programs:
        warnings.append("No program pages were collected.")
    if not requirements:
        warnings.append(
            "No course-to-requirement mappings were collected. Supply one or more "
            "--requirement-url values if the catalog navigation does not expose the GE pages."
        )
    for program in programs:
        warnings.extend(f"{program.name}: {warning}" for warning in program.warnings)

    return CatalogMappings(
        catalog_year=config.catalog_year,
        catoid=resolved_catoid,
        generated_at=datetime.now(UTC).isoformat(),
        source_index_url=effective_index_url,
        sources=tuple(sources),
        programs=tuple(sorted(programs, key=lambda item: item.name.casefold())),
        requirements=_merge_requirements(requirements),
        warnings=tuple(warnings),
    )
