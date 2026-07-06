#!/usr/bin/env bash
# Launcher for the BlunderBus Discord daemon. Mirrors run_pipeline.sh — loads
# .env + vault secrets, then exec's the bot in the same process so systemd
# can track it cleanly.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${PROJECT_DIR}/.venv/bin/python"

# ── Load .env (BW_MASTER_PASS) ────────────────────────────────────────────────
set -a
# shellcheck disable=SC1090
. "${PROJECT_DIR}/.env"
set +a

if [[ -z "${BW_MASTER_PASS:-}" ]]; then
    echo "ERROR: BW_MASTER_PASS not in .env" >&2
    exit 1
fi

# ── Unlock vault + hydrate secrets ────────────────────────────────────────────
BW_SESSION="$(bw unlock "$BW_MASTER_PASS" --raw 2>/dev/null || true)"
if [[ -z "$BW_SESSION" ]]; then
    echo "ERROR: bw unlock failed" >&2
    exit 1
fi
export BW_SESSION

# Pull all secrets into env
VAULT_OUT="$(python3 "${PROJECT_DIR}/scripts/vault.py" --export 2>/dev/null)"
if [[ -z "$VAULT_OUT" ]]; then
    echo "ERROR: vault.py --export returned empty" >&2
    exit 1
fi
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    export "$key=$value"
done <<< "$VAULT_OUT"

if [[ -z "${DISCORD_BOT_TOKEN:-}" || -z "${DISCORD_GUILD_ID:-}" ]]; then
    echo "ERROR: DISCORD_BOT_TOKEN or DISCORD_GUILD_ID missing after vault hydration" >&2
    exit 1
fi

# ── Run the bot in the foreground (exec so systemd tracks pid) ────────────────
exec "$VENV" "${PROJECT_DIR}/scripts/discord_bot.py"
