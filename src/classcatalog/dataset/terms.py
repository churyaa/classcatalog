from __future__ import annotations

import argparse
from pathlib import Path

from classcatalog.dataset.install import (
    active_term_counts,
    plan_term_retirement,
    retire_term_sections,
)

DEFAULT_ACTIVE_DATA = Path("src/classcatalog/data/sections.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or safely retire terms from ClassCatalog active API data."
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
    return parser


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
    except (OSError, ValueError) as exc:
        print(f"term_management_failed error={type(exc).__name__!r} message={str(exc)!r}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
