"""Local integrity checks for ClassCatalog subject synchronization.

This module never modifies the active production dataset.  Approval is a
record in a local review CSV; deployment has an additional explicit switch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def read_json(path: str | Path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def section_key(section: dict) -> tuple[str, str]:
    return str(section["term_code"]), str(section["schedule_number"])


def identity_map(sections: list[dict]) -> dict[tuple[str, str], dict]:
    result = {}
    for section in sections:
        key = section_key(section)
        if key in result:
            raise ValueError(f"duplicate physical section key {key}")
        result[key] = section
    return result


def proposed_removals(report: dict) -> set[tuple[str, str]]:
    keys = set()
    for value in report.get("removed_list", []):
        term, delim, number = str(value).partition("::")
        if not delim or not term or not number:
            raise ValueError(f"invalid removal key {value!r}")
        keys.add((term, number))
    if len(keys) != report.get("removed_physical_classes"):
        raise ValueError("removal list count does not match report")
    return keys


def validate_preview(report: dict, baseline: str, incoming: str) -> None:
    if report.get("applied") is not False:
        raise ValueError("expected read-only preview (applied=false)")
    if str(report.get("active_sha256_before", "")).lower() != sha256(baseline):
        raise ValueError("baseline hash differs from preview")
    if str(report.get("incoming_sha256", "")).lower() != sha256(incoming):
        raise ValueError("incoming hash differs from preview")
    if int(report.get("identity_conflict_count", -1)) != 0:
        raise ValueError("identity conflicts require separate manual V4 review")
    if int(report.get("unapproved_identity_conflict_count", -1)) != 0:
        raise ValueError("unapproved identity conflicts")
    if int(report.get("metadata_regressions_remaining", -1)) != 0:
        raise ValueError("metadata regressions remain")
    if report.get("metadata_regression_details"):
        raise ValueError("metadata regression details are nonempty")
    proposed_removals(report)


def make_review(baseline: str, incoming: str, preview_path: str, target: str) -> int:
    report = read_json(preview_path)
    validate_preview(report, baseline, incoming)
    old = identity_map(read_json(baseline))
    current = identity_map(read_json(incoming))
    removed = proposed_removals(report)
    by_code = {}
    for section in current.values():
        code = str(section.get("course_code", ""))
        by_code[code] = by_code.get(code, 0) + 1
    rows = []
    for key in sorted(removed):
        if key not in old:
            raise ValueError(f"proposed removal not present in baseline: {key}")
        if key in current:
            raise ValueError(f"proposed removal still present in incoming: {key}")
        section = old[key]
        code = str(section.get("course_code", ""))
        rows.append({
            "term_code": key[0], "class_number": key[1],
            "course_code": code,
            "crse_id": str(section.get("crse_id", "")),
            "career": str(section.get("acad_career", "")),
            "section_number": str(section.get("section_number") or ""),
            "classification": "section absent" if code in by_code else "course absent",
            "decision": "PENDING", "notes": "",
        })
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()) if rows else [
            "term_code", "class_number", "course_code", "crse_id", "career", "section_number",
            "classification", "decision", "notes"
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"REMOVAL_REVIEW_CREATED={destination} count={len(rows)}")
    return len(rows)


def approved_review(path: str, expected: set[tuple[str, str]]) -> None:
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not {"term_code", "class_number", "decision"}.issubset(set(reader.fieldnames or [])):
            raise ValueError("review CSV lacks required headers")
        rows = list(reader)
    actual = [(str(row["term_code"]), str(row["class_number"])) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("review CSV does not match exact approved removal set")
    pending = [(row["term_code"], row["class_number"]) for row in rows
               if str(row["decision"]).strip().upper() != "APPROVE"]
    if pending:
        raise ValueError(f"{len(pending)} removal decisions remain unapproved (first {pending[:3]})")


def verify_candidate(baseline: str, incoming: str, candidate: str, preview_path: str,
                     review: str, term: str, require_approval: bool = False) -> dict:
    report = read_json(preview_path)
    validate_preview(report, baseline, incoming)
    old_list = read_json(baseline)
    new_list = read_json(candidate)
    incoming_list = read_json(incoming)
    if not all(isinstance(x, list) for x in (old_list, new_list, incoming_list)):
        raise ValueError("datasets must contain JSON arrays")
    old = identity_map(old_list)
    new = identity_map(new_list)
    source = identity_map(incoming_list)
    if len(old) != report["original_sections"] or len(new) != report["candidate_sections"]:
        raise ValueError("candidate or baseline counts differ from preview")
    old_others = {k: v for k, v in old.items() if v.get("term") != term}
    new_others = {k: v for k, v in new.items() if v.get("term") != term}
    if old_others != new_others:
        raise ValueError("non-target academic term data changed or disappeared")
    keys_old = set(old)
    keys_new = set(new)
    removed = proposed_removals(report)
    if keys_old - keys_new != removed:
        raise ValueError("candidate removed different class numbers than approved preview")
    if len(keys_new - keys_old) != int(report["added_physical_classes"]):
        raise ValueError("candidate additions differ from preview")
    if len(new) - len(old) != int(report["added_physical_classes"]) - len(removed):
        raise ValueError("net section count mismatch")
    if any(key in source for key in removed):
        raise ValueError("removal is still present in incoming scrape")
    if require_approval:
        approved_review(review, removed)
    return {
        "baseline_sha256": sha256(baseline),
        "incoming_sha256": sha256(incoming),
        "candidate_sha256": sha256(candidate),
        "baseline_sections": len(old),
        "candidate_sections": len(new),
        "removed_sections": len(removed),
        "added_sections": len(keys_new - keys_old),
        "term": term,
        "term_counts": dict(Counter(s.get("term", "UNKNOWN") for s in new_list)),
        "approved": require_approval,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("review", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--baseline", required=True)
        p.add_argument("--incoming", required=True)
        p.add_argument("--preview", required=True)
        p.add_argument("--review", required=True)
        if name == "verify":
            p.add_argument("--candidate", required=True)
            p.add_argument("--term", required=True)
            p.add_argument("--approve", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "review":
            make_review(args.baseline, args.incoming, args.preview, args.review)
        else:
            print(json.dumps(verify_candidate(args.baseline, args.incoming, args.candidate,
                                              args.preview, args.review, args.term,
                                              require_approval=args.approve), indent=2))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"classcatalog_update_check_failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
