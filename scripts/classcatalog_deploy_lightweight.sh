#!/usr/bin/env bash
# Lightweight server-side deployment: no large JSON is ever loaded into memory.
# Must be invoked as root by a transient systemd unit after local validation.
set -Eeuo pipefail

if [[ "$#" != 5 ]]; then
  echo 'Usage: deploy.sh EXPECTED_ACTIVE_SHA EXPECTED_CANDIDATE_SHA EXPECTED_SECTIONS EXPECTED_TERMS REMOTE_CANDIDATE' >&2
  exit 2
fi
if [[ "$(id -u)" != 0 ]]; then
  echo 'ERROR: run from a root systemd transient unit' >&2
  exit 2
fi

expected_old="$1"
expected_new="$2"
expected_sections="$3"
expected_terms="$4"
candidate="$5"
active='/var/lib/classcatalog/sections.json'
service='classcatalog.service'
dir='/var/lib/classcatalog'

[[ "$expected_old" =~ ^[0-9a-f]{64}$ && "$expected_new" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid expected SHA256' >&2; exit 2; }
[[ "$expected_sections" =~ ^[0-9]+$ && "$expected_terms" =~ ^[0-9]+$ ]] || { echo 'Invalid expected counts' >&2; exit 2; }
[[ "$candidate" =~ ^/tmp/classcatalog-update-[a-zA-Z0-9_-]+\.json$ ]] || { echo 'Unexpected candidate path' >&2; exit 2; }

mkdir -p "$dir"
exec 9>"$dir/.dataset-update.lock"
flock -n 9 || { echo 'Another dataset deployment is already active' >&2; exit 3; }

installed=0
backend_stopped=0
completed=0
tmp=''
backup=''

recover_on_exit() {
  rc=$?
  trap - EXIT
  if (( rc != 0 && completed == 0 )); then
    echo "DEPLOYMENT_FAILED exit=$rc installed=$installed; starting recovery" >&2
    if (( installed == 1 )); then
      # Stop a newly started service before swapping its dataset back.
      systemctl stop "$service" || echo 'WARNING: stop during rollback failed' >&2
      if [[ -n "$backup" && -f "$backup" && "$(sha256sum "$backup" | awk '{print $1}')" == "$expected_old" ]]; then
        recovery=$(mktemp "$dir/.sections.rollback.XXXXXXXX")
        cp -p -- "$backup" "$recovery" && mv -f -- "$recovery" "$active" && echo 'ROLLBACK_DATASET_RESTORED' >&2
      else
        echo 'CRITICAL: verified backup unavailable; manual restoration required' >&2
      fi
    fi
    if (( backend_stopped == 1 )); then
      systemctl start "$service" || echo 'CRITICAL: could not restart backend; manual intervention required' >&2
    fi
  fi
  if [[ -n "$tmp" && -e "$tmp" ]]; then rm -f -- "$tmp"; fi
  exit "$rc"
}
trap recover_on_exit EXIT

hash_of() { sha256sum -- "$1" | awk '{print $1}'; }

echo '===== VERIFY DATASET HASHES ====='
[[ -f "$active" && -f "$candidate" ]] || { echo 'Active or candidate dataset missing' >&2; exit 4; }
[[ "$(hash_of "$active")" == "$expected_old" ]] || { echo 'Active baseline checksum differs; stop and rebase' >&2; exit 4; }
[[ "$(hash_of "$candidate")" == "$expected_new" ]] || { echo 'Uploaded candidate checksum differs' >&2; exit 4; }
systemctl is-active --quiet "$service" || { echo 'Backend is not active before deployment' >&2; exit 4; }

backup="$dir/sections.json.pre-auto-$(date -u +%Y%m%dT%H%M%SZ)-$$.bak"
[[ ! -e "$backup" ]] || { echo 'Backup name already exists' >&2; exit 4; }
cp -p -- "$active" "$backup"
[[ "$(hash_of "$backup")" == "$expected_old" ]] || { echo 'Backup checksum mismatch' >&2; exit 4; }
echo "VERIFIED_BACKUP=$backup"

echo '===== PREPARE SAME-FILESYSTEM INSTALL ====='
tmp=$(mktemp "$dir/.sections.update.XXXXXXXX")
cp -- "$candidate" "$tmp"
chown --reference="$active" "$tmp"
chmod --reference="$active" "$tmp"
[[ "$(hash_of "$tmp")" == "$expected_new" ]] || { echo 'Staged candidate checksum mismatch' >&2; exit 4; }
# Recheck just before disabling service, in case another process changed active.
[[ "$(hash_of "$active")" == "$expected_old" ]] || { echo 'Active file changed during preparation' >&2; exit 4; }

echo '===== STOP BACKEND ====='
backend_stopped=1
systemctl stop "$service"
[[ "$(hash_of "$active")" == "$expected_old" ]] || { echo 'Active file changed while stopping service' >&2; exit 4; }

echo '===== ATOMIC INSTALL ====='
mv -f -- "$tmp" "$active"
tmp=''
installed=1
[[ "$(hash_of "$active")" == "$expected_new" ]] || { echo 'Installed checksum mismatch' >&2; exit 4; }

echo '===== START AND VERIFY BACKEND ====='
systemctl start "$service"
for attempt in $(seq 1 120); do
  if response=$(curl -fsS --max-time 5 http://127.0.0.1:8000/api/health 2>/dev/null); then
    if printf '%s' "$response" | python3 -c '
import json, sys
payload=json.load(sys.stdin)
expected_sections=int(sys.argv[1]); expected_terms=int(sys.argv[2])
seat=payload.get("seat_refresh") or {}
assert payload.get("status")=="ok"
assert payload.get("classes")==expected_sections
assert payload.get("physical_sections")==expected_sections
assert payload.get("terms")==expected_terms
assert seat.get("enabled") is not True or seat.get("running") is True
' "$expected_sections" "$expected_terms" 2>/dev/null; then
      [[ "$(hash_of "$active")" == "$expected_new" ]] || { echo 'Active checksum changed after restart' >&2; exit 5; }
      systemctl is-active --quiet "$service" || { echo 'Backend not active after health check' >&2; exit 5; }
      completed=1
      backend_stopped=0
      echo '===== DEPLOYMENT VERIFIED ====='
      echo "LIVE_SECTIONS=$expected_sections TERMS=$expected_terms SHA256=$expected_new"
      echo "BACKUP=$backup"
      exit 0
    fi
  fi
  if (( attempt % 10 == 0 )); then echo "Health verification pending (attempt $attempt / 120)"; fi
  sleep 3
done

echo 'Backend did not satisfy health checks; initiating rollback' >&2
exit 5
