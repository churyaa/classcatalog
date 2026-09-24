
def _inventory_operations_snapshot(
    repository: CourseRepository,
    *,
    data_path: Path,
) -> dict[str, object]:
    term_counts: dict[str, int] = {}
    for section in repository.sections:
        term_counts[section.term] = term_counts.get(section.term, 0) + 1

    latest_published_at: str | None = None
    try:
        latest_published_at = datetime.fromtimestamp(
            data_path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except OSError:
        pass

    previous_sections: list[dict[str, object]] | None = None
    previous_path: Path | None = None
    backup_candidates: list[Path] = []
    for directory in (data_path.parent, data_path.parent / "backups"):
        if not directory.is_dir():
            continue
        for pattern in (
            f"{data_path.stem}-before-*.json",
            "sections-before-*.json",
        ):
            backup_candidates.extend(path for path in directory.glob(pattern) if path != data_path)
    if backup_candidates:
        try:
            previous_path = max(backup_candidates, key=lambda path: path.stat().st_mtime)
            raw_previous = json.loads(previous_path.read_text(encoding="utf-8"))
            if isinstance(raw_previous, list):
                previous_sections = [item for item in raw_previous if isinstance(item, dict)]
        except (OSError, ValueError, json.JSONDecodeError):
            previous_sections = None
            previous_path = None

    previous_term_counts: dict[str, int] = {}
    current_subjects = {section.subject for section in repository.sections}
    previous_subjects: set[str] = set()
    if previous_sections is not None:
        for item in previous_sections:
            term = str(item.get("term") or "").strip()
            subject = str(item.get("subject") or "").strip()
            if term:
                previous_term_counts[term] = previous_term_counts.get(term, 0) + 1
            if subject:
                previous_subjects.add(subject)

    section_change: int | None = None
    subjects_added: list[str] = []
    subjects_removed: list[str] = []
    if previous_sections is not None:
        section_change = repository.physical_section_total - len(previous_sections)
        subjects_added = sorted(current_subjects - previous_subjects)
        subjects_removed = sorted(previous_subjects - current_subjects)

    latest_rotation: dict[str, object] | None = None
    report_directory = data_path.parent / "rotation-reports"
    if report_directory.is_dir():
        reports = list(report_directory.glob("rotation-*.json"))
        if reports:
            try:
                report_path = max(reports, key=lambda path: path.stat().st_mtime)
                raw_report = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(raw_report, dict):
                    plan = raw_report.get("plan")
                    if isinstance(plan, dict):
                        latest_rotation = {
                            "generated_at": raw_report.get("generated_at"),
                            "incoming_term": plan.get("incoming_term"),
                            "retire_term": plan.get("retire_term"),
                        }
                        if isinstance(plan.get("candidate_sections"), int) and isinstance(
                            plan.get("active_sections_before"), int
                        ):
                            section_change = int(plan["candidate_sections"]) - int(plan["active_sections_before"])
                        if isinstance(plan.get("subjects_added"), list):
                            subjects_added = sorted(str(value) for value in plan["subjects_added"])
                        if isinstance(plan.get("subjects_removed"), list):
                            subjects_removed = sorted(str(value) for value in plan["subjects_removed"])
            except (OSError, ValueError, json.JSONDecodeError):
                latest_rotation = None

    ordered_terms = list(repository.options().terms)
    for term in term_counts:
        if term not in ordered_terms:
            ordered_terms.append(term)
    term_coverage = [
        {
            "term": term,
            "sections": term_counts.get(term, 0),
            "previous_sections": previous_term_counts.get(term),
            "change": (
                term_counts.get(term, 0) - previous_term_counts[term]
                if term in previous_term_counts
                else None
            ),
        }
        for term in ordered_terms
    ]

    return {
        "latest_published_at": latest_published_at,
        "latest_rotation": latest_rotation,
        "incomplete_subjects": 0,
        "incomplete_subjects_note": "Only validated complete subject inventories are published.",
        "section_count_change": section_change,
        "previous_snapshot": str(previous_path) if previous_path is not None else None,
        "subjects_added": subjects_added,
        "subjects_removed": subjects_removed,
        "term_coverage": term_coverage,
    }
