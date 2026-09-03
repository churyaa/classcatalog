from __future__ import annotations

import argparse
from pathlib import Path

from classcatalog.repository import CourseRepository, resolve_data_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit ClassCatalog section coverage after SDSU enrollment-option grouping. "
            "Every physical section must be represented either standalone or inside a grouped option."
        )
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="sections.json to audit. Defaults to ClassCatalog's active data file.",
    )
    parser.add_argument(
        "--course",
        default=None,
        help="Optionally print grouping details for one exact course code, e.g. NURS 202.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    data_path = args.data or resolve_data_path()
    repository = CourseRepository.from_json(data_path)
    audit = repository.coverage_audit()
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
        print(f"class_coverage_audit_unaccounted examples={list(audit.unaccounted_examples)!r}")

    if args.course:
        course_code = args.course.strip().upper()
        raw = tuple(
            section for section in repository.sections
            if section.course_code.strip().upper() == course_code
        )
        grouped = repository._grouped_sections(raw)  # focused diagnostic for the CLI
        physical = {repository._physical_key(section) for section in raw}
        print(
            "class_grouping_audit "
            f"course={course_code!r} "
            f"listings={len(raw)} "
            f"physical_sections={len(physical)} "
            f"displayed_options={len(grouped)}"
        )
        for option in sorted(grouped, key=lambda item: (item.option_number or 999999, item.schedule_number)):
            components = option.linked_components or ()
            schedules = [component.schedule_number for component in components] or [option.schedule_number]
            component_names = [component.component for component in components] or [option.component]
            print(
                "class_grouping_option "
                f"option={option.option_number!r} "
                f"primary={option.schedule_number!r} "
                f"schedules={schedules!r} "
                f"components={component_names!r}"
            )
    return 0 if audit.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
