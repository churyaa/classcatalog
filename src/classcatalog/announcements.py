from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock


class AnnouncementStore:
    """Small JSON-backed store for public ClassCatalog announcements."""

    def __init__(self, path: Path, *, max_messages: int = 100) -> None:
        self.path = path
        self.max_messages = max(1, max_messages)
        self._lock = RLock()

    def _load_unlocked(self) -> list[dict[str, str]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_messages = payload.get("messages", []) if isinstance(payload, dict) else []
        if not isinstance(raw_messages, list):
            return []
        messages: list[dict[str, str]] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            message_id = str(raw.get("id") or "").strip()
            title = str(raw.get("title") or "").strip()
            body = str(raw.get("message") or "").strip()
            created_at = str(raw.get("created_at") or "").strip()
            if not message_id or not title or not body or not created_at:
                continue
            messages.append(
                {
                    "id": message_id,
                    "title": title,
                    "message": body,
                    "created_at": created_at,
                }
            )
        return sorted(messages, key=lambda item: item["created_at"], reverse=True)

    def _write_unlocked(self, messages: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "messages": messages[: self.max_messages],
        }
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def list(self) -> list[dict[str, str]]:
        with self._lock:
            return self._load_unlocked()

    def create(self, *, title: str, message: str) -> dict[str, str]:
        record = {
            "id": secrets.token_urlsafe(12),
            "title": " ".join(title.strip().split()),
            "message": message.strip(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            messages = self._load_unlocked()
            messages.insert(0, record)
            self._write_unlocked(messages)
        return record

    def delete(self, message_id: str) -> bool:
        target = message_id.strip()
        if not target:
            return False
        with self._lock:
            messages = self._load_unlocked()
            kept = [message for message in messages if message["id"] != target]
            if len(kept) == len(messages):
                return False
            self._write_unlocked(kept)
        return True
