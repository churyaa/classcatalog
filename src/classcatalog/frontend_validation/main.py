from __future__ import annotations

import argparse
from contextlib import nullcontext
from pathlib import Path

from classcatalog.frontend_validation.models import FrontendCheckStatus
from classcatalog.frontend_validation.validator import (
    FrontendValidationError,
    local_api,
    validate_frontend,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the real ClassCatalog frontend against normalized production data using "
            "Playwright. The command can start a temporary local Uvicorn process automatically."
        )
    )
    parser.add_argument("--data", type=Path, default=Path("src/classcatalog/data/sections.json"))
    parser.add_argument(
        "--catalog-data",
        type=Path,
        default=Path("src/classcatalog/data/catalog_mappings.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/fall-2026-frontend-validation"),
    )
    parser.add_argument("--base-url")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--chromium-executable",
        type=Path,
        help=(
            "Optional Chromium/Chrome executable. Normally Playwright's installed browser "
            "is used."
        ),
    )
    parser.add_argument("--expected-sections", type=int)
    parser.add_argument("--expected-courses", type=int)
    parser.add_argument("--expected-physical-sections", type=int)
    parser.add_argument("--expected-active-subjects", type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.data.is_file():
        print(f"frontend_validation_failed error='Data file not found: {args.data}'")
        return 2
    catalog_path = args.catalog_data if args.catalog_data.is_file() else None
    project_root = Path.cwd()
    context = (
        nullcontext(args.base_url.rstrip("/"))
        if args.base_url
        else local_api(
            project_root=project_root,
            data_path=args.data,
            catalog_path=catalog_path,
        )
    )
    try:
        with context as base_url:
            report = validate_frontend(
                base_url=base_url,
                data_path=args.data,
                catalog_path=catalog_path,
                output_dir=args.output_dir,
                headless=not args.headed,
                expected_sections=args.expected_sections,
                expected_courses=args.expected_courses,
                expected_physical_sections=args.expected_physical_sections,
                expected_active_subjects=args.expected_active_subjects,
                chromium_executable=args.chromium_executable,
            )
    except (FrontendValidationError, OSError, ValueError) as exc:
        print(
            "frontend_validation_failed "
            f"error_type={type(exc).__name__!r} error={str(exc)!r}"
        )
        return 2

    passed = sum(check.status is FrontendCheckStatus.PASS for check in report.checks)
    failed = sum(check.status is FrontendCheckStatus.FAIL for check in report.checks)
    skipped = sum(check.status is FrontendCheckStatus.SKIPPED for check in report.checks)
    print(
        "frontend_validation_completed "
        f"status={report.status.value} sections={report.counts.sections} "
        f"courses={report.counts.courses} "
        f"physical_sections={report.counts.physical_sections} "
        f"active_subjects={report.counts.active_subjects} "
        f"catalog_programs={report.counts.catalog_programs} "
        f"catalog_requirements={report.counts.catalog_requirements} "
        f"passed={passed} failed={failed} skipped={skipped} "
        f"output_dir={str(args.output_dir)!r}"
    )
    return 0 if report.status is FrontendCheckStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
