from __future__ import annotations

import argparse
import logging
from pathlib import Path

from classcatalog.api_validation.validator import (
    ApiValidationConfig,
    validate_api_data_file,
    write_api_validation_report,
)

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load ClassCatalog sections.json, exercise FastAPI search/filter/sort/pagination "
            "against the complete dataset, and validate the browser request contract."
        )
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("src/classcatalog/data/sections.json"),
        help="API sections.json to validate.",
    )
    parser.add_argument(
        "--catalog-data",
        type=Path,
        default=Path("src/classcatalog/data/catalog_mappings.json"),
        help="Optional public catalog overlay for program and requirement validation.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/fall-2026-api-validation"),
        help="Destination for JSON and Markdown validation reports.",
    )
    parser.add_argument("--expected-sections", type=int)
    parser.add_argument("--expected-courses", type=int)
    parser.add_argument("--expected-physical-sections", type=int)
    parser.add_argument("--expected-subjects", type=int)
    parser.add_argument(
        "--skip-full-pagination",
        action="store_true",
        help="Skip traversing every result page. Other pagination checks still run.",
    )
    parser.add_argument(
        "--performance-warning-ms",
        type=float,
        default=30_000.0,
        help="Mark a validation check as a warning when it takes longer than this value.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.data.is_file():
        LOGGER.error("api_validation_failed: data file does not exist: %s", args.data)
        return 2
    try:
        result = validate_api_data_file(
            args.data,
            config=ApiValidationConfig(
                data_path=args.data,
                catalog_path=(args.catalog_data if args.catalog_data.is_file() else None),
                expected_sections=args.expected_sections,
                expected_courses=args.expected_courses,
                expected_physical_sections=args.expected_physical_sections,
                expected_subjects=args.expected_subjects,
                full_pagination=not args.skip_full_pagination,
                performance_warning_ms=args.performance_warning_ms,
            ),
        )
    except (OSError, ValueError) as exc:
        LOGGER.error("api_validation_failed: %s: %s", type(exc).__name__, exc)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "api-validation-report.json"
    markdown_path = args.output_dir / "api-validation-report.md"
    write_api_validation_report(
        result.report,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    report = result.report
    print(
        "api_validation_completed "
        f"status={report.status} "
        f"sections={report.counts.course_section_listings} "
        f"courses={report.counts.logical_courses} "
        f"physical_sections={report.counts.unique_physical_sections} "
        f"subjects={report.counts.subjects} "
        f"pages={report.counts.pages_at_50} "
        f"checks={len(report.checks)} "
        f"requests={report.performance.requests} "
        f"p95_ms={report.performance.p95_ms:.2f} "
        f"errors={report.errors} warnings={report.warnings} skipped={report.skipped} "
        f"output_dir={args.output_dir}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
