from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

from classcatalog.dataset.builder import DatasetBuildConfig, build_production_dataset

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate completed per-subject SDSU deep-scrape outputs, reconcile them "
            "against a search-only inventory, count physical sections, and build "
            "ClassCatalog API-ready JSON."
        )
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help=(
            "Completed deep-run checkpoint. The subject-output directory and aggregate "
            "deep-run path are loaded from it unless explicitly overridden."
        ),
    )
    parser.add_argument(
        "--subject-output-dir",
        type=Path,
        help="Directory containing one completed per-subject JSON file per SDSU subject.",
    )
    parser.add_argument(
        "--deep-run",
        type=Path,
        help="Optional aggregate deep-run JSON to check against the per-subject source of truth.",
    )
    parser.add_argument(
        "--discovery-run",
        type=Path,
        help=(
            "Optional complete search-only run JSON. Supplying it produces an exact "
            "course-level inventory reconciliation report."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Destination for sections.json, courses.json, manifest, diff, and reports. "
            "Defaults to results/<term>-production, inferred from the scrape inputs."
        ),
    )
    parser.add_argument(
        "--install-api-data",
        action="store_true",
        help=(
            "Atomically replace only the built term in the API's active data file, "
            "preserving every other installed term."
        ),
    )
    parser.add_argument(
        "--api-data-path",
        type=Path,
        default=Path("src/classcatalog/data/sections.json"),
        help="Destination used with --install-api-data.",
    )
    parser.add_argument(
        "--verbose-inventory-diff",
        action="store_true",
        help=(
            "Print every offering-number and metadata change. By default the console only "
            "prints true additions/removals, rekeys, and concise change counts."
        ),
    )
    parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        help=(
            "Write candidate courses/sections even when critical validation errors exist. "
            "Invalid candidate data is never installed as the active API file."
        ),
    )
    return parser


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _term_from_json(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read term metadata from {path}: {exc}") from exc
    if isinstance(raw, dict):
        term = str(raw.get("term") or "").strip()
        return term or None
    return None


def _infer_build_term(args: argparse.Namespace) -> str:
    for path in (args.checkpoint, args.deep_run, args.discovery_run):
        if path is None:
            continue
        term = _term_from_json(path)
        if term:
            return term

    if args.subject_output_dir is not None:
        for path in sorted(args.subject_output_dir.glob("*.json")):
            term = _term_from_json(path)
            if term:
                return term

    raise ValueError(
        "Could not infer the scrape term for the default output directory. "
        "Provide --checkpoint/--deep-run/--subject-output-dir or set --output-dir explicitly."
    )


def _resolve_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir is not None:
        return args.output_dir
    term = _infer_build_term(args)
    return Path("results") / f"{_slug(term)}-production"


def main() -> int:
    args = _parser().parse_args()
    try:
        output_dir = _resolve_output_dir(args)
        result = build_production_dataset(
            DatasetBuildConfig(
                checkpoint_path=args.checkpoint,
                subject_output_dir=args.subject_output_dir,
                deep_run_path=args.deep_run,
                discovery_run_path=args.discovery_run,
                output_dir=output_dir,
                api_data_path=args.api_data_path if args.install_api_data else None,
                strict=not args.allow_validation_errors,
            )
        )
    except (OSError, ValueError) as exc:
        LOGGER.error("dataset_build_failed: %s: %s", type(exc).__name__, exc)
        return 2

    report = result.report
    if result.install_summary is not None:
        install = result.install_summary
        print(
            "api_data_term_installed "
            f"term={install.term!r} term_code={install.term_code!r} "
            f"incoming_sections={install.incoming_sections} "
            f"replaced_sections={install.replaced_sections} "
            f"preserved_sections={install.preserved_sections} "
            f"active_sections={install.active_sections} "
            f"active_terms={list(install.active_terms)!r}"
        )
    for item in report.inventory_diff.only_in_discovery:
        print(
            "inventory_only_in_discovery "
            f"course={item.course_code!r} title={item.title!r} "
            f"course_key={item.course_key!r}"
        )
    for item in report.inventory_diff.only_in_deep:
        print(
            "inventory_only_in_deep "
            f"course={item.course_code!r} title={item.title!r} "
            f"course_key={item.course_key!r}"
        )
    for item in report.inventory_diff.rekeyed_courses:
        print(
            "inventory_course_rekeyed "
            f"course={item.discovery.course_code!r} title={item.discovery.title!r} "
            f"discovery_crse_id={item.discovery.crse_id!r} "
            f"deep_crse_id={item.deep.crse_id!r}"
        )
    if args.verbose_inventory_diff:
        for item in report.inventory_diff.offering_number_changes:
            print(
                "inventory_offer_number_changed "
                f"course={item.discovery.course_code!r} "
                f"discovery_offers={item.discovery_offer_numbers!r} "
                f"deep_offers={item.deep_offer_numbers!r}"
            )
        for item in report.inventory_diff.changed_courses:
            print(
                "inventory_metadata_changed "
                f"course={item.discovery.course_code!r} "
                f"changed_fields={item.changed_fields!r}"
            )
    elif report.inventory_diff.offering_number_changes or report.inventory_diff.changed_courses:
        print(
            "inventory_metadata_change_summary "
            f"offering_number_changes={len(report.inventory_diff.offering_number_changes)} "
            f"other_metadata_changes={len(report.inventory_diff.changed_courses)}"
        )
    if report.section_coverage_audit is not None:
        audit = report.section_coverage_audit
        print(
            "class_coverage_audit "
            f"status={audit.status} "
            f"course_section_listings={audit.course_section_listings} "
            f"physical_sections={audit.physical_sections} "
            f"displayed_options={audit.displayed_options} "
            f"standalone_physical_sections={audit.standalone_physical_sections} "
            f"grouped_component_physical_sections={audit.grouped_component_physical_sections} "
            f"physical_option_memberships={audit.physical_option_memberships} "
            f"duplicate_option_memberships={audit.duplicate_option_memberships} "
            f"accounted_physical_sections={audit.accounted_physical_sections} "
            f"unaccounted_physical_sections={audit.unaccounted_physical_sections} "
            f"unaccounted_course_section_listings={audit.unaccounted_course_section_listings}"
        )
        if audit.unaccounted_examples:
            print(
                "class_coverage_audit_unaccounted "
                f"examples={list(audit.unaccounted_examples)!r}"
            )

    print(
        "dataset_build_completed "
        f"status={report.status} "
        f"subjects={report.counts.complete_subjects} "
        f"courses={report.counts.discovered_courses} "
        f"course_section_listings={report.counts.course_section_listings} "
        f"physical_sections={report.counts.unique_physical_sections} "
        f"inventory_status={report.inventory_diff.status} "
        f"only_in_discovery={len(report.inventory_diff.only_in_discovery)} "
        f"only_in_deep={len(report.inventory_diff.only_in_deep)} "
        f"rekeyed={len(report.inventory_diff.rekeyed_courses)} "
        f"offering_number_changes={len(report.inventory_diff.offering_number_changes)} "
        f"metadata_changes={len(report.inventory_diff.changed_courses)} "
        f"errors={report.errors} warnings={report.warnings} "
        f"output_dir={output_dir} "
        f"api_data={report.api_data_installed_to}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
