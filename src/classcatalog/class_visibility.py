"""Durable per-physical-class visibility records. Never modifies sections.json."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 1


def read_hidden(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
        raise ValueError(f"Hidden-class file has an unsupported format: {path}")
    records = raw.get("hidden")
    if not isinstance(records, list):
        raise ValueError(f"Hidden-class file has no valid hidden list: {path}")
    found: dict[str, dict[str, object]] = {}
    for entry in records:
        if not isinstance(entry, dict):
            raise ValueError("Hidden-class file contains a malformed entry.")
        key = entry.get("key")
        ids = entry.get("section_ids")
        if (not isinstance(key, str) or "::" not in key or not isinstance(ids, list)
                or not ids or not all(isinstance(value, str) and value for value in ids)
                or not isinstance(entry.get("course_code"), str)
                or not isinstance(entry.get("term"), str)
                or not isinstance(entry.get("hidden_at"), str)
                or not isinstance(entry.get("verified_at"), str)
                or key in found):
            raise ValueError(f"Hidden-class file contains an invalid/duplicate entry: {key!r}.")
        found[key] = entry
    return found


def write_hidden(path: Path, records: dict[str, dict[str, object]]) -> None:
    """Atomically save a complete visibility snapshot, leaving source dataset alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"version": SCHEMA_VERSION, "hidden": list(records.values())},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
