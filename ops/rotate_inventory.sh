#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${CLASSCATALOG_APP_DIR:-/opt/classcatalog/app}"
VENV="${CLASSCATALOG_VENV:-/opt/classcatalog/venv}"
ENV_FILE="${CLASSCATALOG_ENV_FILE:-/etc/classcatalog.env}"
SERVICE="${CLASSCATALOG_SERVICE:-classcatalog}"

usage() {
  cat <<'EOF'
Usage: sudo ops/rotate_inventory.sh --incoming PATH --retire "Fall 2026" [--skip-api-validation]

Safely publishes one already-built term while explicitly retiring one older term.
The script previews and validates the merged candidate, stops ClassCatalog so runtime
caches cannot race the rotation, creates backups, prunes stale seat/instructor cache rows,
starts ClassCatalog, verifies the new inventory, and restores the previous data/caches if
post-start checks fail.
EOF
}

INCOMING=""
RETIRE=""
SKIP_API_VALIDATION=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --incoming)
      INCOMING="${2:-}"
      shift 2
      ;;
    --retire)
      RETIRE="${2:-}"
      shift 2
      ;;
    --skip-api-validation)
      SKIP_API_VALIDATION=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script with sudo so root-managed active data can be replaced safely." >&2
  exit 2
fi
if [ -z "$INCOMING" ] || [ -z "$RETIRE" ]; then
  usage >&2
  exit 2
fi
if [ ! -f "$INCOMING" ]; then
  echo "Incoming dataset not found: $INCOMING" >&2
  exit 2
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

DATA_PATH="${CLASSCATALOG_DATA_PATH:-/var/lib/classcatalog/sections.json}"
SEAT_CACHE_PATH="${CLASSCATALOG_SEAT_CACHE_PATH:-/var/lib/classcatalog/seat_cache.json}"
INSTRUCTOR_CACHE_PATH="${CLASSCATALOG_INSTRUCTOR_CACHE_PATH:-/var/lib/classcatalog/instructor_cache.json}"
BACKUP_DIR="$(dirname "$DATA_PATH")/backups"
REPORT_DIR="$(dirname "$DATA_PATH")/rotation-reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_PATH="$BACKUP_DIR/sections-before-rotation-$STAMP-$$.json"
SEAT_BACKUP_PATH="$BACKUP_DIR/seat-cache-before-rotation-$STAMP-$$.json"
INSTRUCTOR_BACKUP_PATH="$BACKUP_DIR/instructor-cache-before-rotation-$STAMP-$$.json"
REPORT_PATH="$REPORT_DIR/rotation-$STAMP-$$.json"
HEALTH_TMP="/tmp/classcatalog-rotate-health-$$.json"
PYTHON="$VENV/bin/python"

cleanup() {
  rm -f "$HEALTH_TMP"
}
trap cleanup EXIT

if [ ! -x "$PYTHON" ]; then
  echo "ClassCatalog Python is not executable: $PYTHON" >&2
  exit 2
fi
if [ ! -f "$DATA_PATH" ]; then
  echo "Active dataset not found: $DATA_PATH" >&2
  exit 2
fi

DATA_UID="$(stat -c '%u' "$DATA_PATH")"
DATA_GID="$(stat -c '%g' "$DATA_PATH")"
DATA_MODE="$(stat -c '%a' "$DATA_PATH")"

INCOMING_TERM="$($PYTHON - "$INCOMING" <<'PY'
import json
import sys
from pathlib import Path
raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(raw, list):
    raise SystemExit("incoming dataset must be a JSON array")
terms = sorted({str(item.get("term") or "").strip() for item in raw if isinstance(item, dict)})
terms = [term for term in terms if term]
if len(terms) != 1:
    raise SystemExit("incoming dataset must contain exactly one term")
print(terms[0])
PY
)"

COMMON=(
  --api-data-path "$DATA_PATH"
  rotate
  --incoming "$INCOMING"
  --retire "$RETIRE"
  --seat-cache-path "$SEAT_CACHE_PATH"
  --instructor-cache-path "$INSTRUCTOR_CACHE_PATH"
)
if [ "$SKIP_API_VALIDATION" -eq 1 ]; then
  COMMON+=(--skip-api-validation)
fi

echo "=== INVENTORY ROTATION PREVIEW ==="
"$PYTHON" -m classcatalog.dataset.terms "${COMMON[@]}"

mkdir -p "$BACKUP_DIR" "$REPORT_DIR"

echo
echo "=== STOPPING CLASSCATALOG ==="
systemctl stop "$SERVICE"

SEAT_EXISTED=0
INSTRUCTOR_EXISTED=0
SEAT_UID=""
SEAT_GID=""
SEAT_MODE=""
INSTRUCTOR_UID=""
INSTRUCTOR_GID=""
INSTRUCTOR_MODE=""
if [ -f "$SEAT_CACHE_PATH" ]; then
  SEAT_EXISTED=1
  SEAT_UID="$(stat -c '%u' "$SEAT_CACHE_PATH")"
  SEAT_GID="$(stat -c '%g' "$SEAT_CACHE_PATH")"
  SEAT_MODE="$(stat -c '%a' "$SEAT_CACHE_PATH")"
  cp -a "$SEAT_CACHE_PATH" "$SEAT_BACKUP_PATH"
fi
if [ -f "$INSTRUCTOR_CACHE_PATH" ]; then
  INSTRUCTOR_EXISTED=1
  INSTRUCTOR_UID="$(stat -c '%u' "$INSTRUCTOR_CACHE_PATH")"
  INSTRUCTOR_GID="$(stat -c '%g' "$INSTRUCTOR_CACHE_PATH")"
  INSTRUCTOR_MODE="$(stat -c '%a' "$INSTRUCTOR_CACHE_PATH")"
  cp -a "$INSTRUCTOR_CACHE_PATH" "$INSTRUCTOR_BACKUP_PATH"
fi

restore_permissions() {
  chown "$DATA_UID:$DATA_GID" "$DATA_PATH"
  chmod "$DATA_MODE" "$DATA_PATH"
  if [ "$SEAT_EXISTED" -eq 1 ] && [ -f "$SEAT_CACHE_PATH" ]; then
    chown "$SEAT_UID:$SEAT_GID" "$SEAT_CACHE_PATH"
    chmod "$SEAT_MODE" "$SEAT_CACHE_PATH"
  fi
  if [ "$INSTRUCTOR_EXISTED" -eq 1 ] && [ -f "$INSTRUCTOR_CACHE_PATH" ]; then
    chown "$INSTRUCTOR_UID:$INSTRUCTOR_GID" "$INSTRUCTOR_CACHE_PATH"
    chmod "$INSTRUCTOR_MODE" "$INSTRUCTOR_CACHE_PATH"
  fi
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 45); do
    if curl --fail --silent http://127.0.0.1:8000/api/health >"$HEALTH_TMP" 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback() {
  echo "Post-rotation verification failed. Restoring pre-rotation data." >&2
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  install -o "$DATA_UID" -g "$DATA_GID" -m "$DATA_MODE" "$BACKUP_PATH" "$DATA_PATH"
  if [ "$SEAT_EXISTED" -eq 1 ] && [ -f "$SEAT_BACKUP_PATH" ]; then
    install -o "$SEAT_UID" -g "$SEAT_GID" -m "$SEAT_MODE" \
      "$SEAT_BACKUP_PATH" "$SEAT_CACHE_PATH"
  fi
  if [ "$INSTRUCTOR_EXISTED" -eq 1 ] && [ -f "$INSTRUCTOR_BACKUP_PATH" ]; then
    install -o "$INSTRUCTOR_UID" -g "$INSTRUCTOR_GID" -m "$INSTRUCTOR_MODE" \
      "$INSTRUCTOR_BACKUP_PATH" "$INSTRUCTOR_CACHE_PATH"
  fi
  systemctl start "$SERVICE"
  if wait_for_health; then
    echo "Rollback succeeded; ClassCatalog is healthy on the previous dataset." >&2
  else
    echo "Rollback files were restored, but ClassCatalog did not become healthy." >&2
    systemctl --no-pager --full status "$SERVICE" >&2 || true
    journalctl -u "$SERVICE" -n 100 --no-pager >&2 || true
  fi
  exit 1
}

echo
echo "=== APPLYING VALIDATED ROTATION ==="
if ! "$PYTHON" -m classcatalog.dataset.terms \
  "${COMMON[@]}" \
  --backup-path "$BACKUP_PATH" \
  --report "$REPORT_PATH" \
  --apply; then
  echo "Rotation apply failed before service startup; restarting the previous inventory." >&2
  restore_permissions || true
  systemctl start "$SERVICE"
  if ! wait_for_health; then
    systemctl --no-pager --full status "$SERVICE" >&2 || true
    journalctl -u "$SERVICE" -n 100 --no-pager >&2 || true
  fi
  exit 1
fi

restore_permissions

echo
echo "=== STARTING CLASSCATALOG ==="
systemctl start "$SERVICE"
if ! wait_for_health; then
  rollback
fi

echo "=== POST-ROTATION VERIFICATION ==="
cat "$HEALTH_TMP"
echo

if ! curl --fail --silent http://127.0.0.1:8000/api/options | \
  "$PYTHON" -c '
import json, sys
incoming, retired = sys.argv[1], sys.argv[2]
payload = json.load(sys.stdin)
terms = payload.get("terms") or []
if incoming not in terms:
    raise SystemExit(f"incoming term missing from /api/options: {incoming!r}; terms={terms!r}")
if retired in terms:
    raise SystemExit(f"retired term still present in /api/options: {retired!r}; terms={terms!r}")
print("active_terms=" + repr(terms))
' "$INCOMING_TERM" "$RETIRE"; then
  rollback
fi

curl --fail --silent --output /dev/null http://127.0.0.1:8000/subjects || rollback
curl --fail --silent --output /dev/null http://127.0.0.1:8000/sitemap.xml || rollback

echo
echo "Inventory rotation complete."
echo "Incoming term: $INCOMING_TERM"
echo "Retired term: $RETIRE"
echo "Dataset backup: $BACKUP_PATH"
if [ "$SEAT_EXISTED" -eq 1 ]; then
  echo "Seat cache backup: $SEAT_BACKUP_PATH"
fi
if [ "$INSTRUCTOR_EXISTED" -eq 1 ]; then
  echo "Instructor cache backup: $INSTRUCTOR_BACKUP_PATH"
fi
echo "Report: $REPORT_PATH"
