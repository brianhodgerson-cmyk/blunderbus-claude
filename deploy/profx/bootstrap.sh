#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=/opt/blunderbus
REPO_ROOT="$APP_ROOT/repo"
VENV_ROOT="$APP_ROOT/venv"
LOG_ROOT="$APP_ROOT/logs"
DEPLOY_ROOT="$REPO_ROOT/deploy/profx"

mkdir -p "$APP_ROOT" "$LOG_ROOT"

if [[ ! -d "$VENV_ROOT" ]]; then
  python3 -m venv "$VENV_ROOT"
fi

"$VENV_ROOT/bin/pip" install --upgrade pip
"$VENV_ROOT/bin/pip" install -r "$REPO_ROOT/requirements.txt"

install -m 0644 "$DEPLOY_ROOT/blunderbus-telegram.service" /etc/systemd/system/blunderbus-telegram.service
systemctl daemon-reload
systemctl enable blunderbus-telegram.service

crontab "$DEPLOY_ROOT/blunderbus.crontab"

echo "BlunderBus bootstrap complete."
