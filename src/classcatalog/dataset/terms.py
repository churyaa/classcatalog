from __future__ import annotations

import argparse
from pathlib import Path

from classcatalog.dataset.install import (
    active_term_counts,
    plan_term_retirement,
    retire_term_sections,
)
from classcatalog.dataset.lifecycle import (
    InventoryRotationPlan,
    apply_inventory_rotation,
    plan_inventory_rotation,
    write_rotation_report,
)

DEFAULT_ACTIVE_DATA = Path("src/classcatalog/data/sections.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, retire, or safely rotate ClassCatalog active term inventory."
    )
    parser.add_argument(
        "--api-data-path",
        type=Path,
        default=DEFAULT_ACTIVE_DATA,
        help="Active multi-term sections.json file.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List active terms and section counts.")

    retire = subparsers.add_parser(
        "retire",
        help="Preview or remove one exact term while preserving all other terms.",
    )
    retire.add_argument("--term", required=True, help='Exact term label, e.g. "Fall 2026".')
    retire.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the change. Without this flag, retirement is preview-only.",
    )
    retire.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow retiring the final active term. Normally this is refused for safety.",
    )

    rotate = subparsers.add_parser(
        "rotate",
        help=(
            "Validate one already-built incoming term, preserve unrelated active terms, "
            "and explicitly retire one older term. Preview-only unless --apply is supplied."
        ),
    )
    rotate.add_argument(
        "--incoming",
        type=Path,
        required=True,
        help="Single-term sections.json produced by classcatalog-build-dataset.",
    )
    rotate.add_argument(
        "--retire",
        required=True,
        help='Exact active term to retire, e.g. "Fall 2026". Never inferred automatically.',
    )
    rotate.add_argument(
        "--apply",
        action="store_true",
        help="Write the validated rotation. Without this flag no active data changes.",
    )
    rotate.add_argument(
        "--backup-path",
        type=Path,
        help="Optional explicit backup path. A timestamped path is used when omitted.",
    )
    rotate.add_argument(
        "--seat-cache-path",
        type=Path,
        help="Optional seat cache to prune after a successful active-data write.",
    )
    rotate.add_argument(
        "--instructor-cache-path",
        type=Path,
        help="Optional instructor cache to prune after a successful active-data write.",
    )
    rotate.add_argument(
        "--no-prune-caches",
        action="store_true",
        help="Leave seat/instructor caches untouched.",
    )
    rotate.add_argument(
        "--skip-api-validation",
        action="store_true",
        help=(
            "Skip the quick API behavior validation. Schema, duplicate, physical coverage, "
            "and SEO/sitemap checks still run."
        ),
    )
    rotate.add_argument(
        "--report",
        type=Path,
        help="Write a JSON rotation report after --apply succeeds.",
    )
    return parser


def _print_rotation_plan(plan: InventoryRotationPlan) -> None:
    print(
        "inventory_rotation_preview "
        f"incoming_term={plan.incoming_term!r} incoming_term_code={plan.incoming_term_code!r} "
        f"retire_term={plan.retire_term!r} "
        f"active_terms_before={list(plan.active_terms_before)!r} "
        f"candidate_terms={list(plan.candidate_terms)!r} "
        f"active_sections_before={plan.active_sections_before} "
        f"incoming_sections={plan.incoming_sections} "
        f"replaced_incoming_term_sections={plan.replaced_incoming_term_sections} "
        f"retired_sections={plan.retired_sections} "
        f"candidate_sections={plan.candidate_sections} "
        f"physical_sections={plan.candidate_physical_sections} "
        f"displayed_options={plan.candidate_displayed_options} "
        f"coverage_status={plan.coverage_status!r} "
        f"api_validation_status={plan.api_validation_status!r}"
    )
    print(
        "inventory_rotation_diff "
        f"subjects_added={list(plan.subjects_added)!r} "
        f"subjects_removed={list(plan.subjects_removed)!r} "
        f"courses_added={len(plan.courses_added)} courses_removed={len(plan.courses_removed)} "
        f"new_instructors={len(plan.new_instructors)} sitemap_urls={plan.sitemap_urls}"
    )
    if plan.courses_added:
        print(f"inventory_rotation_courses_added examples={list(plan.courses_added[:20])!r}")
    if plan.courses_removed:
        print(f"inventory_rotation_courses_removed examples={list(plan.courses_removed[:20])!r}")
    if plan.new_instructors:
        print(f"inventory_rotation_new_instructors examples={list(plan.new_instructors[:20])!r}")


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "list":
            rows = active_term_counts(args.api_data_path)
            for term, listings, physical in rows:
                print(
                    "active_term "
                    f"term={term!r} listings={listings} physical_sections={physical}"
                )
            return 0

        if args.command == "retire":
            plan = plan_term_retirement(args.api_data_path, args.term)
            print(
                "term_retirement_preview "
                f"term={plan.term!r} remove_sections={plan.removed_sections} "
                f"remaining_sections={plan.remaining_sections} "
                f"remaining_terms={list(plan.remaining_terms)!r}"
            )
            if not args.apply:
                print("No files changed. Re-run with --apply to retire this term.")
                return 0

            applied = retire_term_sections(
                args.api_data_path,
                args.term,
                allow_empty=args.allow_empty,
            )
            print(
                "term_retired "
                f"term={applied.term!r} removed_sections={applied.removed_sections} "
                f"remaining_sections={applied.remaining_sections} "
                f"remaining_terms={list(applied.remaining_terms)!r}"
            )
            return 0

        validate_api = not args.skip_api_validation
        plan = plan_inventory_rotation(
            args.incoming,
            args.api_data_path,
            args.retire,
            validate_api=validate_api,
        )
        _print_rotation_plan(plan)
        if not args.apply:
            print(
                "No files changed. Review the candidate counts/diffs, then re-run with "
                "--apply or use ops/rotate_inventory.sh on production."
            )
            return 0

        applied = apply_inventory_rotation(
            args.incoming,
            args.api_data_path,
            args.retire,
            backup_path=args.backup_path,
            seat_cache_path=args.seat_cache_path,
            instructor_cache_path=args.instructor_cache_path,
            prune_caches=not args.no_prune_caches,
            validate_api=validate_api,
        )
        if args.report is not None:
            write_rotation_report(args.report, applied.plan, apply_summary=applied)
        print(
            "inventory_rotation_applied "
            f"incoming_term={applied.plan.incoming_term!r} "
            f"retired_term={applied.plan.retire_term!r} "
            f"active_terms={list(applied.plan.candidate_terms)!r} "
            f"active_sections={applied.plan.candidate_sections} "
            f"backup={applied.backup_path!r} backup_sha256={applied.backup_sha256!r}"
        )
        print(
            "inventory_rotation_cache_prune "
            f"seat_records_removed={applied.seat_cache.removed_records} "
            f"seat_sources_removed={applied.seat_cache.removed_sources} "
            f"seat_error={applied.seat_cache.error!r} "
            f"instructor_records_removed={applied.instructor_cache.removed_records} "
            f"instructor_error={applied.instructor_cache.error!r}"
        )
        if args.report is not None:
            print(f"inventory_rotation_report path={str(args.report)!r}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"term_management_failed error={type(exc).__name__!r} message={str(exc)!r}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
