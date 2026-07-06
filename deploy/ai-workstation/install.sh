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
systemctl --user enable --now blunderbus-dispatcher.service

# Desktop voice stack: Hermes gateway (Discord), warm Canary STT, Stream Deck PTT
systemctl --user enable --now hermes-gateway.service canary-stt.service
systemctl --user enable jarvis-streamdeck.service   # udev starts it when the deck is plugged in

# HA Assist voice bridge: Wyoming shim (:10300 -> Canary), Piper TTS (:10200), local LLM (:11434)
systemctl --user enable --now wyoming-canary.service
docker compose -f "${SRC_DIR}/piper/docker-compose.yml" up -d
docker compose -f "${SRC_DIR}/ollama/docker-compose.yml" up -d
docker exec ollama ollama pull qwen3:4b-instruct || echo "pull qwen3:4b-instruct manually once ollama is up"

# udev: deck access + auto-start jarvis-streamdeck on plug (needs sudo)
if ! cmp -s "${SRC_DIR}/70-streamdeck.rules" /etc/udev/rules.d/70-streamdeck.rules 2>/dev/null; then
  sudo cp "${SRC_DIR}/70-streamdeck.rules" /etc/udev/rules.d/70-streamdeck.rules
  sudo udevadm control --reload
fi

# Timers (06:00 CT brief; 05:15 CT Monarch ingest; 02:30 CT drift sentinel)
systemctl --user enable --now blunderbus-daily-brief.timer
systemctl --user enable --now blunderbus-monarch-ingest.timer
systemctl --user enable --now blunderbus-drift-sentinel.timer

# Shadow dry-run timer is optional; enable for pipeline-change validation:
# systemctl --user enable --now blunderbus-daily-brief-shadow.timer

# Survive logout (user units keep running without an active session)
loginctl enable-linger "$(whoami)"

echo "Done. Timers:"
systemctl --user list-timers | grep -E "blunderbus|NEXT" || true
