#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/classcatalog/app"
VENV="/opt/classcatalog/venv"
HEALTH_URL="http://127.0.0.1:8000/api/health"
STARTUP_ATTEMPTS=45
STARTUP_DELAY_SECONDS=2

echo "Updating repository..."
git -C "$APP_DIR" pull --ff-only

echo "Installing application..."
"$VENV/bin/pip" install "$APP_DIR"

echo "Restarting ClassCatalog..."
sudo systemctl restart classcatalog

echo "Waiting for ClassCatalog to become healthy..."
health=""
for attempt in $(seq 1 "$STARTUP_ATTEMPTS"); do
    if health="$(curl --fail --silent "$HEALTH_URL" 2>/dev/null)"; then
        echo "$health"
        echo
        echo "ClassCatalog is healthy."
        echo "Deployment complete."
        exit 0
    fi

    sleep "$STARTUP_DELAY_SECONDS"
done

echo "ClassCatalog did not become healthy within $((STARTUP_ATTEMPTS * STARTUP_DELAY_SECONDS)) seconds."
sudo systemctl --no-pager --full status classcatalog || true
sudo journalctl -u classcatalog -n 100 --no-pager || true
exit 1
