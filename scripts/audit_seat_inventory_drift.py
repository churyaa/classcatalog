#!/usr/bin/env python3
"""Read-only, targeted Spring 2027 inventory drift audit.

Default mode is offline. --live makes five (or fewer) sequential, publicly
available PeopleSoft subject discovery attempts with conservative HTTP pacing.
Never changes active data, seat caches, class numbers, or production service.
Results are *review evidence*, never instructions for automatic remapping.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TARGETS = ("C P 730", "CFD 278", "CY 533", "DANCE 390", "ECL 503")
DEFAULT_TERM = "Spring 2027"


def _identity(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("crse_id") or "").strip(),
        str(row.get("crse_offer_nbr") or "").strip(),
        str(row.get("acad_career") or "").strip(),
    )


def inventory(path: Path, *, term: str = DEFAULT_TERM, courses=TARGETS) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Expected an active-data JSON array; refusing to audit another structure")
    requested = set(courses)
    groups: dict[tuple[str, str, str, str, str], dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("course_code") or "").strip().upper()
        if str(raw.get("term") or "") != term or code not in requested:
            continue
        subject = str(raw.get("subject") or "").strip()
        strm = str(raw.get("term_code") or "").strip()
        crse_id, offer, career = _identity(raw)
        key = (code, subject, strm, crse_id, offer)
        item = groups.setdefault(key, {
            "course_code": code, "subject": subject, "term": term,
            "term_code": strm, "crse_id": crse_id,
            "crse_offer_nbr": offer, "acad_careers": set(),
            "expected_class_numbers": set(), "source_urls": set(),
        })
        if career:
            item["acad_careers"].add(career)
        number = str(raw.get("schedule_number") or "").strip()
        if number:
            item["expected_class_numbers"].add(number)
        src = str(raw.get("source_url") or "").strip()
        if src:
            item["source_urls"].add(src)
    result = []
    for item in groups.values():
        item = dict(item)
        for key in ("acad_careers", "expected_class_numbers", "source_urls"):
            item[key] = sorted(item[key])
        item["assessment"] = "inventory_only_unverified"
        result.append(item)
    for code in sorted(requested - {item["course_code"] for item in result}):
        result.append({"course_code": code, "term": term,
                       "assessment": "not_in_local_inventory"})
    return sorted(result, key=lambda x: (x["course_code"], str(x.get("crse_id", "")), str(x.get("crse_offer_nbr", ""))))


def safe_public_detail_url(url: str) -> bool:
    parts = urlparse(str(url or ""))
    host = (parts.hostname or "").lower()
    return (
        parts.scheme.lower() == "https"
        and (host == "sdsu.edu" or host.endswith(".sdsu.edu"))
        and "SSR_CS_WRAP_FL.GBL" in parts.path.upper()
        and not parts.username and not parts.password
    )


def assess_hits(item: dict, discovery) -> tuple[str, object | None]:
    """Only an exact stable identity from a complete subject grid is actionable."""
    if not item.get("subject") or not item.get("term_code") or not item.get("crse_id") or not item.get("crse_offer_nbr"):
        return "missing_identity_unverified", None
    if (
        discovery.term != item["term"]
        or discovery.term_code != item["term_code"]
        or discovery.subject != item["subject"]
        or discovery.complete is not True
    ):
        return "incomplete_subject_unverified", None
    matches = [
        hit for hit in discovery.courses
        if hit.course_code == item["course_code"]
        and hit.term == item["term"]
        and hit.term_code == item["term_code"]
        and hit.subject == item["subject"]
        and str(hit.crse_id or "") == item["crse_id"]
        and str(hit.crse_offer_nbr or "") == item["crse_offer_nbr"]
        and (
            not item["acad_careers"] or not hit.acad_career
            or str(hit.acad_career) in item["acad_careers"]
        )
    ]
    if len(matches) != 1:
        return ("identity_not_found_unverified" if not matches
                else "multiple_offerings_unverified"), None
    hit = matches[0]
    if not safe_public_detail_url(str(hit.detail_url or "")):
        return "unsafe_detail_url_unverified", None
    return "unique_identity_found", hit


def assess_detail(item: dict, parsed, raw_html: str) -> dict:
    """Compare numbers only; never write to dataset or treat absence as removal."""
    from classcatalog.scraping.class_post import find_class_number_actions
    if (
        parsed.course_code != item["course_code"]
        or parsed.term != item["term"]
        or parsed.term_code != item["term_code"]
        or parsed.options_complete is not True
    ):
        return {"assessment": "detail_not_verified", "observed_class_numbers": []}
    observed = {
        str(option.class_number).strip()
        for option in parsed.options
        if getattr(option, "class_number", None)
    } | {action.class_number.strip() for action in find_class_number_actions(raw_html)}
    expected = set(item["expected_class_numbers"])
    if not observed:
        assessment = "empty_detail_unverified"
    elif observed == expected:
        assessment = "verified_exact_match"
    elif expected & observed:
        assessment = "partial_overlap_requires_manual_review"
    else:
        assessment = "different_class_numbers_requires_manual_review"
    return {
        "assessment": assessment,
        "observed_class_numbers": sorted(observed),
        "expected_but_not_observed": sorted(expected - observed),
        "observed_but_not_expected": sorted(observed - expected),
    }


def audit_live(items: list[dict], *, session_factory=None) -> list[dict]:
    """Sequential independent searches; all errors are reported, not applied."""
    from classcatalog.scraping.parser import parse_course_info_page
    from classcatalog.scraping.session import SdsuHttpConfig, SdsuPeopleSoftSession
    create = session_factory or (lambda: SdsuPeopleSoftSession(SdsuHttpConfig(
        delay_seconds=2.0, jitter_seconds=0.25, max_retries=1,
        in_run_state_recovery_attempts=0,
    )))
    # Group only identical subject contexts. Never assume two subjects share
    # PeopleSoft component state. A new client is created per context.
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for item in items:
        if item.get("subject") and item.get("term_code"):
            context = (item["term"], item["term_code"], item["subject"])
            groups.setdefault(context, []).append(item)
    for (term, strm, subject), group in sorted(groups.items()):
        try:
            with create() as client:
                pages = client.rehydrate_subject_context(
                    term=term, term_code=strm, subject=subject,
                )
                discovery = client.parse_subject_pages(pages)
                for item in group:
                    status, hit = assess_hits(item, discovery)
                    item["assessment"] = status
                    if hit is None:
                        continue
                    try:
                        detail = client.fetch_detail_pages(hit.detail_url)
                        parsed = parse_course_info_page(
                            detail.course_info_html, source_url=detail.course_info_url,
                        )
                        item.update(assess_detail(item, parsed, detail.course_info_html))
                    except Exception as exc:
                        item["assessment"] = "detail_request_unverified"
                        item["error_type"] = type(exc).__name__
        except Exception as exc:
            for item in group:
                if item["assessment"] == "inventory_only_unverified":
                    item["assessment"] = "subject_request_unverified"
                    item["error_type"] = type(exc).__name__
    return items


def build_report(path: Path, *, term: str, courses, live: bool, session_factory=None) -> dict:
    items = inventory(path, term=term, courses=courses)
    if live:
        audit_live(items, session_factory=session_factory)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_public_read_only" if live else "offline_inventory_only",
        "data_file": str(path.resolve()),
        "term": term,
        "requested_courses": list(courses),
        "auto_modifications": False,
        "entries": items,
    }


def main(argv=None) -> int:
    from classcatalog.repository import ACTIVE_DATA_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ACTIVE_DATA_PATH)
    parser.add_argument("--term", default=DEFAULT_TERM)
    parser.add_argument("--course", action="append", choices=TARGETS)
    parser.add_argument("--live", action="store_true", help="Explicitly contact public SDSU pages sequentially")
    parser.add_argument("--output", type=Path, default=Path("results/seat-inventory-drift-audit-20261124.json"))
    args = parser.parse_args(argv)
    courses = tuple(dict.fromkeys(args.course or TARGETS))
    report = build_report(args.data, term=args.term, courses=courses, live=args.live)
    if args.output.resolve() == args.data.resolve():
        parser.error("Report output must never overwrite active dataset")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for item in report["entries"]:
        print(item["course_code"], item["assessment"],
              "expected=" + ",".join(item.get("expected_class_numbers", [])),
              "observed=" + ",".join(item.get("observed_class_numbers", [])))
    print("AUDIT_WRITTEN", args.output)
    print("NO_DATA_OR_SEAT_CACHE_MODIFICATIONS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
