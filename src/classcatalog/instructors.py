from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping, Sequence

from classcatalog.models import CourseSection
from classcatalog.ratings import (
    canonical_instructor_name,
    is_placeholder_instructor,
    normalize_person_name,
)

INSTRUCTOR_CACHE_SCHEMA_VERSION = 1
INSTRUCTOR_CACHE_PATH_ENV = "CLASSCATALOG_INSTRUCTOR_CACHE_PATH"

_PLACEHOLDER_EDGE_RE = re.compile(
    r"^(?:(?:to\s+be\s+announced|instructor\s+tba|tba|staff|unknown)\b[\s,;:/&|\\-]*)+"
    r"|(?:(?:[\s,;:/&|\\-]*)(?:to\s+be\s+announced|instructor\s+tba|tba|staff|unknown)\b)+$",
    re.I,
)


def clean_live_instructor_name(name: object) -> str:
    """Normalize noisy PeopleSoft instructor cells without inventing an identity.

    SDSU can render responsive/hidden values in the same instructor cell. BeautifulSoup
    then sees strings such as ``To Be Announced Vijayanka Nair`` or duplicate values
    such as ``To Be Announced To Be Announced``. Strip only known placeholder text at
    the edges and collapse exact repeated halves; otherwise preserve the SDSU name.
    """

    value = canonical_instructor_name(str(name or ""))
    if not value:
        return ""

    # Some responsive PeopleSoft cells expose the exact same value twice.
    tokens = value.split()
    while len(tokens) >= 2 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        left = " ".join(tokens[:half])
        right = " ".join(tokens[half:])
        if normalize_person_name(left) != normalize_person_name(right):
            break
        tokens = tokens[:half]
    value = canonical_instructor_name(" ".join(tokens))

    if is_placeholder_instructor(value):
        return value

    # Remove one or more known placeholder labels from either edge. Run until stable
    # so combinations such as "TBA - To Be Announced - Jane Smith" are handled.
    previous = None
    cleaned = value
    while cleaned != previous:
        previous = cleaned
        cleaned = _PLACEHOLDER_EDGE_RE.sub("", cleaned).strip(" ,;:/&|-")
        cleaned = canonical_instructor_name(cleaned)

    return cleaned or value


def resolve_instructor_cache_path() -> Path:
    configured = os.getenv(INSTRUCTOR_CACHE_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).parent / "data" / "instructor_cache.json"


def _cache_key(term: str, schedule_number: str) -> str:
    return f"{term}::{schedule_number}"


def load_instructor_cache(
    path: Path | None = None,
) -> dict[tuple[str, str], dict[str, object]]:
    cache_path = path or resolve_instructor_cache_path()
    if not cache_path.is_file():
        return {}
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != INSTRUCTOR_CACHE_SCHEMA_VERSION:
        return {}
    records = payload.get("sections")
    if not isinstance(records, dict):
        return {}

    loaded: dict[tuple[str, str], dict[str, object]] = {}
    for raw in records.values():
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("term") or "").strip()
        schedule = str(raw.get("schedule_number") or "").strip()
        instructor = clean_live_instructor_name(raw.get("instructor"))
        if not term or not schedule or is_placeholder_instructor(instructor):
            continue
        loaded[(term, schedule)] = {
            **raw,
            "term": term,
            "schedule_number": schedule,
            "instructor": instructor,
        }
    return loaded


def apply_cached_instructors(
    sections: Sequence[CourseSection],
    path: Path | None = None,
) -> tuple[tuple[CourseSection, ...], int]:
    records = load_instructor_cache(path)
    if not records:
        return tuple(sections), 0

    changed_physical: set[tuple[str, str]] = set()
    replaced: list[CourseSection] = []
    for section in sections:
        key = (section.term_code or section.term, section.schedule_number)
        raw = records.get(key)
        if raw is None or not is_placeholder_instructor(section.instructor):
            replaced.append(section)
            continue
        instructor = clean_live_instructor_name(raw.get("instructor"))
        if is_placeholder_instructor(instructor):
            replaced.append(section)
            continue
        replaced.append(section.model_copy(update={"instructor": instructor, "professor": None}))
        changed_physical.add(key)
    return tuple(replaced), len(changed_physical)


def write_instructor_cache(
    records: Mapping[tuple[str, str], Mapping[str, object]],
    path: Path | None = None,
) -> None:
    cache_path = path or resolve_instructor_cache_path()
    payload = {
        "schema_version": INSTRUCTOR_CACHE_SCHEMA_VERSION,
        "sections": {
            _cache_key(term, schedule): dict(raw)
            for (term, schedule), raw in sorted(records.items())
        },
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(cache_path)
