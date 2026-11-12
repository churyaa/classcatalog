"""Durable, expiring cache for *discovery* branches of capped SDSU subjects.

Never use an HTML response loaded from this cache to construct/replay a
PeopleSoft POST. The scraper rebuilds every new facet action from fresh GET
responses. Cached HTML is only used to inspect facet labels and parse rows.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_CACHE_FORMAT = 1
_MAX_ENCODED_BYTES = 12 * 1024 * 1024
_MAX_DECODED_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CachedPartition:
    html: str
    url: str
    row_count: int
    capped: bool
    age_seconds: float


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


class PartitionBranchCache:
    """Checkpoint individual validated branch responses with a bounded lifetime."""

    def __init__(self, root: Path, *, max_age_seconds: float = 6 * 3600) -> None:
        self.root = Path(root)
        self.max_age_seconds = max(float(max_age_seconds), 0.0)

    @staticmethod
    def _identity(
        *, term: str, term_code: str, subject: str,
        filters: tuple[tuple[str, str], ...],
    ) -> dict[str, object]:
        return {
            "version": _CACHE_FORMAT,
            "term": term,
            "term_code": term_code,
            "subject": subject,
            "filters": [list(pair) for pair in filters],
        }

    def _path(
        self, *, term: str, term_code: str, subject: str,
        filters: tuple[tuple[str, str], ...],
    ) -> Path:
        identity = self._identity(
            term=term, term_code=term_code, subject=subject, filters=filters,
        )
        key = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{_slug(term)}-{term_code}" / _slug(subject) / f"{digest}.json.gz"

    def load(
        self, *, term: str, term_code: str, subject: str,
        filters: tuple[tuple[str, str], ...],
        validate: Callable[[str, str, int, bool], None],
    ) -> CachedPartition | None:
        """Return only fresh, intact, semantically verified entries; otherwise miss."""
        if self.max_age_seconds <= 0:
            return None
        path = self._path(
            term=term, term_code=term_code, subject=subject, filters=filters,
        )
        try:
            if path.stat().st_size > _MAX_ENCODED_BYTES:
                return None
            with gzip.open(path, "rb") as stream:
                data = stream.read(_MAX_DECODED_BYTES + 1)
            if len(data) > _MAX_DECODED_BYTES:
                return None
            document = json.loads(data.decode("utf-8"))
            identity = self._identity(
                term=term, term_code=term_code, subject=subject, filters=filters,
            )
            if not isinstance(document, dict) or document.get("identity") != identity:
                return None
            age = time.time() - float(document["created_at"])
            if age < -60 or age > self.max_age_seconds:
                return None
            html, url = document["html"], document["url"]
            if not isinstance(html, str) or not isinstance(url, str):
                return None
            if hashlib.sha256(html.encode("utf-8")).hexdigest() != document["sha256"]:
                return None
            row_count, capped = document["row_count"], document["capped"]
            if type(row_count) is not int or row_count < 0 or type(capped) is not bool:
                return None
            # Validate live-equivalent invariants *before* the entry is reused.
            validate(html, url, row_count, capped)
            return CachedPartition(html, url, row_count, capped, age)
        except Exception:  # Cache is advisory: invalid entries always become a miss.
            return None

    def save(
        self, *, term: str, term_code: str, subject: str,
        filters: tuple[tuple[str, str], ...], html: str, url: str,
        row_count: int, capped: bool,
    ) -> Path:
        """Write an entire validated branch atomically; no partial cache entries."""
        identity = self._identity(
            term=term, term_code=term_code, subject=subject, filters=filters,
        )
        document = {
            "identity": identity,
            "created_at": time.time(),
            "url": url,
            "row_count": row_count,
            "capped": capped,
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "html": html,
        }
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_DECODED_BYTES:
            raise ValueError("Partition branch exceeds safe cache size limit")
        destination = self._path(
            term=term, term_code=term_code, subject=subject, filters=filters,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".partition-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(gzip.compress(encoded, compresslevel=6))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return destination
