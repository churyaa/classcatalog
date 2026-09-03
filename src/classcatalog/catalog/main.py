from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from classcatalog.catalog.browser import CatalogBrowserConfig, CatalogBrowserError
from classcatalog.catalog.scraper import CatalogScrapeConfig, scrape_catalog
from classcatalog.scraping.progress import write_model

DEFAULT_CATALOG_YEAR = "2026-2027"
DEFAULT_CATOID = 11
DEFAULT_INDEX_URL = "https://catalog.sdsu.edu/index.php"
DEFAULT_OUTPUT = Path("results/sdsu-catalog-2026-2027/catalog-mappings.json")
DEFAULT_INSTALL_PATH = Path("src/classcatalog/data/catalog_mappings.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open SDSU's JavaScript-protected Acalog catalog with Playwright, parse "
            "bachelor-program course classifications and public requirement mappings, and "
            "write the catalog overlay consumed by ClassCatalog."
        )
    )
    parser.add_argument("--catalog-year", default=DEFAULT_CATALOG_YEAR)
    parser.add_argument(
        "--catoid",
        type=int,
        default=DEFAULT_CATOID,
        help=(
            "Expected Acalog catalog id. The collector verifies this against the live "
            "catalog landing page and automatically replaces a stale id."
        ),
    )
    parser.add_argument("--index-url", default=DEFAULT_INDEX_URL)
    parser.add_argument(
        "--program",
        action="append",
        default=[],
        help=(
            "Exact catalog program label to scrape. Repeat as needed. When omitted, "
            "Computer Science, B.S. is used unless --all-programs is set."
        ),
    )
    parser.add_argument(
        "--all-programs",
        action="store_true",
        help="Scrape every discovered undergraduate bachelor's program.",
    )
    parser.add_argument(
        "--program-url",
        action="append",
        default=[],
        help="Explicit preview_program.php URL. Repeat to bypass program-list discovery.",
    )
    parser.add_argument(
        "--requirement-url",
        action="append",
        default=[],
        help=(
            "Explicit current-catalog requirement/GE page URL. Repeat when the catalog "
            "navigation does not expose the page automatically."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=Path("fixtures/sdsu/catalog/2026-2027"),
        help="Directory for raw catalog HTML snapshots used for regression tests.",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--browser-channel",
        choices=("chromium", "chrome", "msedge"),
        default="chromium",
        help=(
            "Browser executable used by Playwright. The default uses Playwright Chromium; "
            "use msedge on Windows if the catalog verification closes the Chromium tab."
        ),
    )
    parser.add_argument(
        "--page-recovery-attempts",
        type=int,
        default=2,
        help="Number of automatic recoveries when the catalog replaces or closes a tab.",
    )
    parser.add_argument(
        "--pause-for-human",
        action="store_true",
        help="Pause when SDSU presents a browser-verification challenge.",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=Path("results/sdsu-catalog-browser-state.json"),
        help="Playwright storage state reused after a one-time catalog challenge.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-requirement-pages", type=int, default=40)
    parser.add_argument("--install-api-data", action="store_true")
    parser.add_argument("--install-path", type=Path, default=DEFAULT_INSTALL_PATH)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Return exit code 0 even if no program or requirement mappings were parsed.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.timeout_seconds <= 0:
        print("catalog_scrape_failed error='--timeout-seconds must be positive'")
        return 2
    if args.all_programs and args.program:
        print("catalog_scrape_failed error='Use --all-programs or --program, not both.'")
        return 2
    if args.page_recovery_attempts < 0:
        print("catalog_scrape_failed error='--page-recovery-attempts cannot be negative'")
        return 2

    config = CatalogScrapeConfig(
        catalog_year=args.catalog_year,
        catoid=args.catoid,
        index_url=args.index_url,
        programs=tuple(args.program),
        all_undergraduate_programs=args.all_programs,
        program_urls=tuple(args.program_url),
        requirement_urls=tuple(args.requirement_url),
        fixture_dir=args.fixture_dir,
        browser=CatalogBrowserConfig(
            headless=not args.headed,
            timeout_ms=int(args.timeout_seconds * 1_000),
            pause_for_human=args.pause_for_human,
            storage_state=args.storage_state,
            browser_channel=(None if args.browser_channel == "chromium" else args.browser_channel),
            page_recovery_attempts=args.page_recovery_attempts,
        ),
        max_requirement_pages=max(1, args.max_requirement_pages),
    )

    try:
        mappings = scrape_catalog(config)
    except (CatalogBrowserError, OSError, ValueError) as exc:
        print(f"catalog_scrape_failed error_type={type(exc).__name__!r} error={str(exc)!r}")
        return 2

    write_model(args.output, mappings)
    mapped_program_courses = len(
        {
            (program.name, program.catalog_year, mapping.course_code)
            for program in mappings.programs
            for mapping in program.mappings
        }
    )
    mapped_requirement_courses = len(
        {
            (requirement.display_name, course_code)
            for requirement in mappings.requirements
            for course_code in requirement.course_codes
        }
    )
    complete = bool(mappings.programs) and bool(mappings.requirements)

    if args.install_api_data and (complete or args.allow_partial):
        args.install_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.install_path.with_suffix(f"{args.install_path.suffix}.tmp")
        shutil.copyfile(args.output, temporary)
        temporary.replace(args.install_path)

    status = "passed" if complete else "partial"
    installed_path = str(args.install_path) if args.install_api_data else None
    print(
        "catalog_scrape_completed "
        f"status={status} catalog_year={mappings.catalog_year!r} catoid={mappings.catoid} "
        f"programs={len(mappings.programs)} requirements={len(mappings.requirements)} "
        f"program_course_mappings={mapped_program_courses} "
        f"requirement_course_mappings={mapped_requirement_courses} "
        f"warnings={len(mappings.warnings)} output={str(args.output)!r} "
        f"api_data={installed_path!r}"
    )
    if complete or args.allow_partial:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
