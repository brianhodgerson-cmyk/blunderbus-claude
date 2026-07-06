#!/usr/bin/env bash
# Install BlunderBus systemd user units on AI-Workstation.
# Mirrors the live config in ~/.config/systemd/user/ — this directory is the
# canonical, version-controlled copy. Edit here, then re-run this script.
set -euo pipefail

UNIT_DIR="${HOME}/.config/systemd/user"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "${UNIT_DIR}"
cp -v "${SRC_DIR}"/*.service "${SRC_DIR}"/*.timer "${UNIT_DIR}/"
systemctl --user daemon-reload

# Long-running services
systemctl --user enable --now bb-mcp.service bbm-api.service blunderbus-couchdb-sync.service

# Timers (06:00 CT brief; 05:15 CT Monarch ingest)
systemctl --user enable --now blunderbus-daily-brief.timer
# Monarch ingest: enable once cookie auth in Vaultwarden is fresh (see CLAUDE.md).
# systemctl --user enable --now blunderbus-monarch-ingest.timer

# Shadow dry-run timer is optional; enable for pipeline-change validation:
# systemctl --user enable --now blunderbus-daily-brief-shadow.timer

# Survive logout (user units keep running without an active session)
loginctl enable-linger "$(whoami)"

echo "Done. Timers:"
systemctl --user list-timers | grep -E "blunderbus|NEXT" || true
