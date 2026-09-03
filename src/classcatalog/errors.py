from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock


RECENT_ERROR_LIMIT = 50
ERROR_DETAIL_LIMIT = 600


def public_error_for_path(path: str) -> tuple[str, str]:
    """Return a stable code and safe message for a failed API resource."""

    normalized = (path or "").lower()
    if normalized.startswith("/api/seats") or normalized.startswith("/api/admin/seats"):
        return "seat_data_unavailable", "Seat data is temporarily unavailable."
    if "ratings" in normalized or "professor" in normalized:
        return "professor_ratings_unavailable", "Professor ratings could not be loaded."
    if normalized.startswith("/api/catalog") or normalized.startswith("/api/profile"):
        return "catalog_data_unavailable", "Catalog information is temporarily unavailable."
    if normalized.startswith("/api/classes"):
        return "class_results_unavailable", "Class results are temporarily unavailable. Please try again."
    if normalized.startswith("/api/options"):
        return "filter_options_unavailable", "Class filters are temporarily unavailable. Please try again."
    if normalized.startswith("/api/admin"):
        return "admin_data_unavailable", "Admin data is temporarily unavailable."
    return "service_unavailable", "Something went wrong. Please try again."


def compact_error_detail(value: object) -> str:
    detail = " ".join(str(value or "No additional detail").split())
    if len(detail) <= ERROR_DETAIL_LIMIT:
        return detail
    return f"{detail[: ERROR_DETAIL_LIMIT - 1]}…"


class RecentErrorStore:
    """Thread-safe, bounded request-error history for the private Admin view."""

    def __init__(self, max_entries: int = RECENT_ERROR_LIMIT) -> None:
        self._items: deque[dict[str, object]] = deque(maxlen=max(1, max_entries))
        self._lock = Lock()

    def record(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        code: str,
        public_message: str,
        exception_type: str,
        detail: object,
    ) -> None:
        occurred_at = datetime.now(timezone.utc).isoformat()
        clean_detail = compact_error_detail(detail)
        item: dict[str, object] = {
            "occurred_at": occurred_at,
            "first_occurred_at": occurred_at,
            "occurrences": 1,
            "request_id": request_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "code": code,
            "public_message": public_message,
            "exception_type": exception_type,
            "detail": clean_detail,
        }
        with self._lock:
            for existing in self._items:
                if all(
                    existing.get(key) == item.get(key)
                    for key in ("method", "path", "status_code", "code", "exception_type", "detail")
                ):
                    existing["occurred_at"] = occurred_at
                    existing["request_id"] = request_id
                    existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
                    self._items.remove(existing)
                    self._items.appendleft(existing)
                    return
            self._items.appendleft(item)

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(item) for item in self._items]
