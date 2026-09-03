from __future__ import annotations

import json
import re
from pathlib import Path


REQUIREMENT_ORDER: tuple[str, ...] = (
    "GWAR",
    "American Institutions",
    "Ethnic Studies",
    "Cultural Diversity",
    "1A English Composition",
    "1B Critical Thinking",
    "1C Oral Communication",
    "2 Mathematical/Quantitative Reasoning",
    "3A Arts",
    "3B Humanities",
    "4 Social and Behavioral Sciences",
    "5A Physical Science",
    "5B Biological Science",
    "5C Laboratory",
    "EXPLORATIONS - PHYS, BIO, MATH/QUANT",
    "EXPLORATIONS - ARTS & HUMANITIES",
    "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES",
)

_REQUIREMENT_RANK = {label: index for index, label in enumerate(REQUIREMENT_ORDER)}


def canonical_requirement_label(code: str, name: str) -> str | None:
    """Convert raw SDSU catalog requirement records into the compact UI taxonomy."""

    code_key = " ".join(code.upper().split())
    name_key = " ".join(name.casefold().split())

    # Legacy GE labels can use older area codes; prefer an unambiguous name when present.
    if code_key == "GE A1" and "oral communication" in name_key:
        return "1C Oral Communication"
    if code_key == "GE A3" and "critical thinking" in name_key:
        return "1B Critical Thinking"
    if code_key == "GE B2" and "life science" in name_key:
        return "5B Biological Science"
    if code_key == "GE C2" and "humanities" in name_key:
        return "3B Humanities"
    if code_key == "GE D" and "social and behavioral" in name_key:
        return "4 Social and Behavioral Sciences"

    if code_key == "GWAR":
        return "GWAR"
    if code_key == "AI":
        return "American Institutions"
    if code_key in {"GE 6", "GE F"}:
        return "Ethnic Studies"
    if code_key == "GE 1A":
        return "1A English Composition"
    if code_key == "GE 1B":
        return "1B Critical Thinking"
    if code_key == "GE 1C":
        return "1C Oral Communication"
    if code_key in {"GE 2", "GE B4"}:
        if "or 5" in name_key or "physical and biological sciences" in name_key:
            return "EXPLORATIONS - PHYS, BIO, MATH/QUANT"
        return "2 Mathematical/Quantitative Reasoning"
    if code_key == "GE 3A":
        return "3A Arts"
    if code_key == "GE 3B":
        return "3B Humanities"
    if code_key == "GE 3":
        return "EXPLORATIONS - ARTS & HUMANITIES"
    if code_key == "GE 4":
        if "6 units" in name_key or "sciences" in name_key:
            return "4 Social and Behavioral Sciences"
        return "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES"
    if code_key == "GE 5A":
        return "5A Physical Science"
    if code_key == "GE 5B":
        return "5B Biological Science"
    if code_key == "GE 5C":
        return "5C Laboratory"
    return None


def canonicalize_existing_requirement_tag(value: str) -> str | None:
    """Normalize legacy section tags and raw catalog display labels."""

    text = " ".join(value.strip().split())
    folded = text.casefold()

    exact = {
        "gwar": "GWAR",
        "graduation writing assessment requirement": "GWAR",
        "american institutions": "American Institutions",
        "american institutions requirement": "American Institutions",
        "ethnic studies": "Ethnic Studies",
        "cultural diversity": "Cultural Diversity",
        "ge a1: oral communication": "1C Oral Communication",
        "ge a3: critical thinking": "1B Critical Thinking",
        "ge b2: life science": "5B Biological Science",
        "ge b4: mathematics/quantitative reasoning": "2 Mathematical/Quantitative Reasoning",
        "ge c2: humanities": "3B Humanities",
        "ge d: social and behavioral sciences": "4 Social and Behavioral Sciences",
    }
    if folded in exact:
        return exact[folded]

    # Current catalog display labels.
    match = re.match(r"^ge\s+([^:]+):\s*(.*)$", folded)
    if match:
        code = f"GE {match.group(1).upper()}"
        name = match.group(2)
        return canonical_requirement_label(code, name)
    if folded.startswith("ai:"):
        return "American Institutions"
    if folded.startswith("gwar:"):
        return "GWAR"

    # Tags that can come directly from SDSU class/course tables.
    if "explorations" in folded:
        if "arts and humanities" in folded:
            return "EXPLORATIONS - ARTS & HUMANITIES"
        if "social and behavioral" in folded:
            return "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES"
        if any(token in folded for token in ("physical", "biological", "mathemat", "quantitative")):
            return "EXPLORATIONS - PHYS, BIO, MATH/QUANT"
    if "ethnic studies" in folded:
        return "Ethnic Studies"
    if "american institutions" in folded:
        return "American Institutions"
    return None


def sort_requirement_labels(values: set[str] | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    unique = set(values)
    return tuple(
        sorted(
            unique,
            key=lambda value: (_REQUIREMENT_RANK.get(value, len(REQUIREMENT_ORDER)), value.casefold()),
        )
    )


def load_requirement_overlays(catalog_year: str) -> dict[str, tuple[str, ...]]:
    """Load small, reviewed requirement overlays not represented by the scraper output."""

    path = Path(__file__).parent / "data" / f"requirement_overlays_{catalog_year.replace('-', '_')}.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    requirements = raw.get("requirements", {})
    return {
        str(label): tuple(str(code) for code in course_codes)
        for label, course_codes in requirements.items()
        if label in REQUIREMENT_ORDER and isinstance(course_codes, list)
    }
