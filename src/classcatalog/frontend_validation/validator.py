from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import requests
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from classcatalog.catalog.models import CatalogMappings
from classcatalog.frontend_validation.models import (
    FrontendCheck,
    FrontendCheckStatus,
    FrontendCounts,
    FrontendValidationReport,
)
from classcatalog.models import CourseSection



class FrontendValidationError(RuntimeError):
    pass


class BrowserRuntime:
    def __init__(
        self,
        *,
        headless: bool,
        executable_path: Path | None = None,
    ) -> None:
        self._headless = headless
        self._executable_path = executable_path
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> Browser:
        self._playwright = sync_playwright().start()
        try:
            launch_args: dict[str, object] = {"headless": self._headless}
            if self._executable_path is not None:
                launch_args["executable_path"] = str(self._executable_path)
            self._browser = self._playwright.chromium.launch(**launch_args)
        except Exception as exc:
            self._playwright.stop()
            raise FrontendValidationError(
                "Playwright Chromium could not start. Run `python -m playwright install "
                "chromium` with the ClassCatalog virtual environment, or pass "
                "--chromium-executable with a local Chromium path."
            ) from exc
        return self._browser

    def __exit__(self, *_: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def local_api(
    *,
    project_root: Path,
    data_path: Path,
    catalog_path: Path | None,
) -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment["CLASSCATALOG_DATA_PATH"] = str(data_path.resolve())
    if catalog_path is not None:
        environment["CLASSCATALOG_CATALOG_PATH"] = str(catalog_path.resolve())
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "classcatalog.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=project_root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise FrontendValidationError(
                    f"Local Uvicorn process exited with code {process.returncode}."
                )
            try:
                response = requests.get(f"{base_url}/api/health", timeout=1)
                if response.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        else:
            raise FrontendValidationError("Local ClassCatalog API did not become ready in 30s.")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _load_sections(path: Path) -> tuple[CourseSection, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(CourseSection.model_validate(item) for item in raw)


def _load_catalog(path: Path | None) -> CatalogMappings | None:
    if path is None or not path.is_file():
        return None
    return CatalogMappings.model_validate_json(path.read_text(encoding="utf-8"))


def _logical_course_count(sections: tuple[CourseSection, ...]) -> int:
    return len(
        {
            (
                section.term_code or section.term,
                section.subject,
                section.crse_id or section.course_code,
                section.crse_offer_nbr or "",
                section.acad_career or "",
            )
            for section in sections
        }
    )


def _physical_section_count(sections: tuple[CourseSection, ...]) -> int:
    return len(
        {(section.term_code or section.term, section.schedule_number) for section in sections}
    )


def _active_subject_count(sections: tuple[CourseSection, ...]) -> int:
    return len({section.subject for section in sections})


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _write_markdown(path: Path, report: FrontendValidationReport) -> None:
    lines = [
        "# ClassCatalog frontend production validation",
        "",
        f"- Status: **{report.status.value}**",
        f"- Base URL: `{report.base_url}`",
        f"- Sections: {report.counts.sections:,}",
        f"- Logical courses: {report.counts.courses:,}",
        f"- Physical sections: {report.counts.physical_sections:,}",
        f"- Active subjects: {report.counts.active_subjects:,}",
        f"- Catalog programs: {report.counts.catalog_programs:,}",
        f"- Catalog requirements: {report.counts.catalog_requirements:,}",
        "",
        "## Checks",
        "",
    ]
    for check in report.checks:
        lines.append(
            f"- **{check.status.value.upper()}** `{check.name}` — {check.details} "
            f"({check.duration_ms:.1f} ms)"
        )
    if report.errors:
        lines.extend(["", "## Errors", "", *[f"- {item}" for item in report.errors]])
    if report.warnings:
        lines.extend(["", "## Warnings", "", *[f"- {item}" for item in report.warnings]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _timed_check(
    checks: list[FrontendCheck],
    name: str,
    action: Callable[[], str],
) -> None:
    started = time.perf_counter()
    try:
        details = action()
    except Exception as exc:
        checks.append(
            FrontendCheck(
                name=name,
                status=FrontendCheckStatus.FAIL,
                details=str(exc),
                duration_ms=(time.perf_counter() - started) * 1_000,
            )
        )
    else:
        checks.append(
            FrontendCheck(
                name=name,
                status=FrontendCheckStatus.PASS,
                details=details,
                duration_ms=(time.perf_counter() - started) * 1_000,
            )
        )


def _skipped(checks: list[FrontendCheck], name: str, details: str) -> None:
    checks.append(
        FrontendCheck(
            name=name,
            status=FrontendCheckStatus.SKIPPED,
            details=details,
            duration_ms=0,
        )
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_for_results(page: Page) -> None:
    page.locator("#loading").wait_for(state="hidden")
    page.locator("#course-list .course-card, #course-list .empty").first.wait_for(
        state="visible"
    )


def _pick_program(
    catalog: CatalogMappings,
    scheduled_codes: set[str],
) -> tuple[str, str, str] | None:
    for program in catalog.programs:
        available = {
            mapping.course_code
            for mapping in program.mappings
            if mapping.course_code in scheduled_codes
        }
        if available:
            classification = next(
                (
                    mapping.classification.value
                    for mapping in program.mappings
                    if mapping.course_code in scheduled_codes
                ),
                "",
            )
            return program.name, program.catalog_year, classification
    return None


def _pick_requirement(
    catalog: CatalogMappings,
    scheduled_codes: set[str],
) -> str | None:
    for requirement in catalog.requirements:
        if scheduled_codes.intersection(requirement.course_codes):
            return requirement.display_name
    return None


def validate_frontend(
    *,
    base_url: str,
    data_path: Path,
    catalog_path: Path | None,
    output_dir: Path,
    headless: bool = True,
    expected_sections: int | None = None,
    expected_courses: int | None = None,
    expected_physical_sections: int | None = None,
    expected_active_subjects: int | None = None,
    chromium_executable: Path | None = None,
) -> FrontendValidationReport:
    sections = _load_sections(data_path)
    catalog = _load_catalog(catalog_path)
    scheduled_codes = {section.course_code for section in sections}
    actual_counts = FrontendCounts(
        sections=len(sections),
        courses=_logical_course_count(sections),
        physical_sections=_physical_section_count(sections),
        active_subjects=_active_subject_count(sections),
        catalog_programs=len(catalog.programs) if catalog is not None else 0,
        catalog_requirements=len(catalog.requirements) if catalog is not None else 0,
    )
    checks: list[FrontendCheck] = []
    browser_console_errors: list[str] = []
    page_errors: list[str] = []
    screenshots: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    with BrowserRuntime(
        headless=headless,
        executable_path=chromium_executable,
    ) as browser:
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on(
            "console",
            lambda message: browser_console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            page.goto(base_url, wait_until="networkidle")
            _wait_for_results(page)
        except Exception as exc:
            raise FrontendValidationError(
                f"The browser could not open the ClassCatalog frontend at {base_url!r}: {exc}"
            ) from exc

        def health_check() -> str:
            response = requests.get(f"{base_url}/api/health", timeout=10)
            response.raise_for_status()
            payload = response.json()
            expected = {
                "classes": (
                    actual_counts.sections
                    if expected_sections is None
                    else expected_sections
                ),
                "courses": (
                    actual_counts.courses if expected_courses is None else expected_courses
                ),
                "physical_sections": (
                    actual_counts.physical_sections
                    if expected_physical_sections is None
                    else expected_physical_sections
                ),
                "subjects": (
                    actual_counts.active_subjects
                    if expected_active_subjects is None
                    else expected_active_subjects
                ),
            }
            for key, value in expected.items():
                _assert(
                    payload.get(key) == value,
                    f"health {key}={payload.get(key)!r}, expected {value}",
                )
            return "Health counts match the normalized production data."

        _timed_check(checks, "health_counts", health_check)

        def initial_page_check() -> str:
            cards = page.locator("#course-list .course-card").count()
            _assert(
                cards == min(50, actual_counts.sections),
                f"initial page rendered {cards} cards",
            )
            return f"Initial page rendered {cards} course-section cards."

        _timed_check(checks, "initial_50_record_page", initial_page_check)

        def prefix_search_check() -> str:
            query = page.locator("#query")
            query.fill("CS 15")
            page.wait_for_timeout(350)
            _wait_for_results(page)
            codes = page.locator("#course-list .course-code").all_text_contents()
            _assert(bool(codes), "CS 15 search returned no visible course cards")
            invalid = [
                code
                for code in codes
                if not " ".join(code.upper().split()).startswith("CS 15")
            ]
            _assert(not invalid, f"prefix search included unrelated course codes: {invalid[:5]}")
            query.fill("")
            page.wait_for_timeout(350)
            _wait_for_results(page)
            return f"CS 15 returned {len(codes)} visible prefix matches and no unrelated codes."

        _timed_check(checks, "course_code_prefix_search", prefix_search_check)

        def reset_check() -> str:
            starts = page.locator("#time-from")
            ends = page.locator("#time-to")
            starts.fill("09:00")
            ends.fill("17:00")
            _assert(
                page.locator('[data-time-target="time-from"]').is_visible(),
                "Starts after Reset is hidden",
            )
            _assert(
                page.locator('[data-time-target="time-to"]').is_visible(),
                "Ends before Reset is hidden",
            )
            page.locator('[data-time-target="time-from"]').click()
            page.locator('[data-time-target="time-to"]').click()
            _assert(starts.input_value() == "", "Starts after did not reset")
            _assert(ends.input_value() == "", "Ends before did not reset")
            return "Both time reset controls are visible and clear only their own input."

        _timed_check(checks, "time_reset_controls", reset_check)

        def sticky_filter_check() -> str:
            result = page.evaluate(
                """
                () => {
                  const filters = document.querySelector('.filters');
                  const style = getComputedStyle(filters);
                  const firstTop = filters.getBoundingClientRect().top;
                  window.scrollTo(0, 900);
                  const secondTop = filters.getBoundingClientRect().top;
                  window.scrollTo(0, 1300);
                  const thirdTop = filters.getBoundingClientRect().top;
                  const beforeWindow = window.scrollY;
                  const canScroll = filters.scrollHeight > filters.clientHeight;
                  if (canScroll) {
                    filters.scrollTop = Math.min(120, filters.scrollHeight - filters.clientHeight);
                  }
                  const afterWindow = window.scrollY;
                  return {
                    position: style.position,
                    firstTop,
                    secondTop,
                    thirdTop,
                    canScroll,
                    filterScrollTop: filters.scrollTop,
                    beforeWindow,
                    afterWindow,
                  };
                }
                """
            )
            _assert(result["position"] == "sticky", f"filter position is {result['position']!r}")
            _assert(
                abs(result["secondTop"] - result["thirdTop"]) <= 3,
                f"filter top moved while scrolling: {result}",
            )
            if result["canScroll"]:
                _assert(result["filterScrollTop"] > 0, "filter pane could not scroll internally")
                _assert(
                    result["beforeWindow"] == result["afterWindow"],
                    "filter scroll moved the page",
                )
            page.evaluate("window.scrollTo(0, 0)")
            return (
                "Desktop filter panel remains sticky and supports independent internal "
                "scrolling."
            )

        _timed_check(checks, "independent_filter_scrolling", sticky_filter_check)

        def pagination_check() -> str:
            page.locator("#top-next-page").click()
            _wait_for_results(page)
            _assert(
                page.locator("#top-showing-count").inner_text().startswith("Page 2"),
                "top pagination did not advance",
            )
            page.locator("#previous-page").click()
            _wait_for_results(page)
            _assert(
                page.locator("#top-showing-count").inner_text().startswith("Page 1"),
                "bottom pagination did not synchronize",
            )
            return "Top and bottom pagination remain synchronized."

        _timed_check(checks, "synchronized_pagination", pagination_check)

        if catalog is None:
            _skipped(
                checks,
                "catalog_program_filters",
                (
                    "No catalog overlay was supplied; program, requirement, and profile "
                    "checks were skipped."
                ),
            )
        else:
            selected_program = _pick_program(catalog, scheduled_codes)
            requirement = _pick_requirement(catalog, scheduled_codes)

            def catalog_status_check() -> str:
                _assert(
                    page.locator("#catalog-status")
                    .inner_text()
                    .startswith("SDSU catalog mappings loaded"),
                    "catalog-loaded status is not visible",
                )
                _assert(
                    page.locator("#program option").count() > 1,
                    "program dropdown is empty",
                )
                _assert(
                    page.locator('input[name="requirement"]:not(:disabled)').count() > 0,
                    "requirements are disabled",
                )
                return (
                    f"Catalog UI exposes {len(catalog.programs)} programs and "
                    f"{len(catalog.requirements)} requirements."
                )

            _timed_check(checks, "catalog_controls_loaded", catalog_status_check)

            if selected_program is None:
                _skipped(
                    checks,
                    "catalog_program_filters",
                    "No catalog program mapping intersects the loaded schedule.",
                )
            else:
                program_name, catalog_year, classification = selected_program

                def program_filter_check() -> str:
                    page.locator("#program").select_option(program_name)
                    page.locator("#catalog-year").select_option(catalog_year)
                    page.wait_for_timeout(500)
                    _wait_for_results(page)
                    page.locator("#program-summary").wait_for(state="visible")
                    _assert(
                        page.locator('input[name="classification"]:not(:disabled)').count() > 0,
                        "program classifications stayed disabled",
                    )
                    if classification:
                        classification_input = page.locator(
                            f'input[name="classification"][value="{classification}"]'
                        )
                        classification_input.check()
                        page.wait_for_timeout(350)
                        _wait_for_results(page)
                        _assert(
                            page.locator("#course-list .course-card").count() > 0,
                            "classification filter returned no visible classes",
                        )
                        classification_input.uncheck()
                    page.locator("#program").select_option("")
                    page.locator("#catalog-year").select_option("")
                    page.wait_for_timeout(350)
                    _wait_for_results(page)
                    return (
                        "Program, catalog-year, classification, and profile summary worked "
                        f"for {program_name}."
                    )

                _timed_check(checks, "catalog_program_filters", program_filter_check)

            if requirement is None:
                _skipped(
                    checks,
                    "catalog_requirement_filter",
                    "No requirement mapping intersects the loaded schedule.",
                )
            else:
                def requirement_filter_check() -> str:
                    locator = page.locator(f'input[name="requirement"][value="{requirement}"]')
                    _assert(locator.count() == 1, f"requirement checkbox not found: {requirement}")
                    locator.check()
                    page.wait_for_timeout(350)
                    _wait_for_results(page)
                    _assert(
                        page.locator("#course-list .course-card").count() > 0,
                        "requirement filter returned no visible classes",
                    )
                    locator.uncheck()
                    page.wait_for_timeout(350)
                    _wait_for_results(page)
                    return f"Requirement filter returned schedule matches for {requirement}."

                _timed_check(checks, "catalog_requirement_filter", requirement_filter_check)

        screenshot_path = output_dir / "classcatalog-production.png"
        page.screenshot(path=str(screenshot_path), full_page=True)
        screenshots.append(str(screenshot_path))
        context.close()

    failures = [check for check in checks if check.status is FrontendCheckStatus.FAIL]
    errors = tuple(check.details for check in failures)
    if browser_console_errors:
        errors += tuple(f"Browser console: {item}" for item in browser_console_errors)
    if page_errors:
        errors += tuple(f"Page error: {item}" for item in page_errors)
    status = FrontendCheckStatus.FAIL if errors else FrontendCheckStatus.PASS
    report = FrontendValidationReport(
        status=status,
        base_url=base_url,
        generated_at=datetime.now(UTC).isoformat(),
        counts=actual_counts,
        checks=tuple(checks),
        browser_console_errors=tuple(browser_console_errors),
        page_errors=tuple(page_errors),
        screenshots=tuple(screenshots),
        errors=errors,
    )
    _write_json(output_dir / "frontend-validation-report.json", report.model_dump(mode="json"))
    _write_markdown(output_dir / "frontend-validation-report.md", report)
    return report
