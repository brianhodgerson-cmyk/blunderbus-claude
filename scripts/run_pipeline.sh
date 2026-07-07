#!/usr/bin/env bash
# run_pipeline.sh — Universal Linux launcher for BlunderBus pipelines
#
# Usage: ./run_pipeline.sh <script.py> [--no-telegram] [--dry-run]
#
# Handles: .env loading, vault unlock, secret export, SSH tunnel, venv activation.
# Designed for systemd timers on AI-Workstation — no ambient PATH or env vars assumed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${PROJECT_DIR}/logs"
VENV="${PROJECT_DIR}/.venv/bin/activate"
DEFAULT_BW_BIN="${SCRIPT_DIR}/bw-vaultwarden.sh"
if [[ -x "$DEFAULT_BW_BIN" ]]; then
    BW_BIN="${BW_BIN:-$DEFAULT_BW_BIN}"
else
    BW_BIN="${BW_BIN:-bw}"
fi

# ── Parse args ────────────────────────────────────────────────────────────────
PYTHON_SCRIPT="${1:?Usage: $0 <script.py> [flags...]}"
shift
EXTRA_ARGS="$*"
SCRIPT_NAME="$(basename "$PYTHON_SCRIPT" .py)"
LOG_FILE="${LOG_DIR}/${SCRIPT_NAME}.log"

mkdir -p "$LOG_DIR"

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" | tee -a "$LOG_FILE"
}

log "=== Starting ${SCRIPT_NAME} ==="

# ── Load .env ─────────────────────────────────────────────────────────────────
ENV_FILE="${PROJECT_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    log "ERROR: .env not found at ${ENV_FILE}"
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# The claude CLI prefers ANTHROPIC_API_KEY over subscription OAuth. The key in
# .env is the zero-credit litellm one (parked) — pipeline AI goes through the
# operator's Claude subscription instead. Remove this line to switch back.
unset ANTHROPIC_API_KEY

if [[ -z "${BW_MASTER_PASS:-}" ]]; then
    log "ERROR: BW_MASTER_PASS not set in .env"
    exit 1
fi

# ── Activate venv ─────────────────────────────────────────────────────────────
if [[ -f "$VENV" ]]; then
    # shellcheck disable=SC1090
    source "$VENV"
    log "Venv activated"
else
    log "WARN: No venv found at ${VENV}, using system Python"
fi

# ── Unlock vault and export secrets ───────────────────────────────────────────
log "Unlocking vault..."

# Check BW CLI status and login/unlock as needed
BW_STATUS="$($BW_BIN status 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)" || BW_STATUS="unknown"

if [[ "$BW_STATUS" == "unauthenticated" ]]; then
    log "Logging into Bitwarden..."
    BW_SESSION="$($BW_BIN login "${BW_EMAIL:-${BITWARDEN_EMAIL:-${User:-jarvis@hodgespot.com}}}" "$BW_MASTER_PASS" --raw 2>/dev/null)" || true
elif [[ "$BW_STATUS" == "locked" ]]; then
    BW_SESSION="$($BW_BIN unlock "$BW_MASTER_PASS" --raw 2>/dev/null)" || true
else
    BW_SESSION="$($BW_BIN unlock "$BW_MASTER_PASS" --raw 2>/dev/null)" || true
fi

if [[ -z "$BW_SESSION" ]]; then
    log "WARN: Vault unlock failed (status was: ${BW_STATUS}) — falling back to .env values"
else
    export BW_SESSION
    export BW_BIN
    $BW_BIN sync --session "$BW_SESSION" >/dev/null 2>&1 || true

    # Load secrets via vault.py
    log "Loading secrets from vault..."
    VAULT_OUTPUT="$(python3 "${SCRIPT_DIR}/vault.py" --export 2>/dev/null)" || true
    if [[ -n "$VAULT_OUTPUT" ]]; then
        while IFS='=' read -r key value; do
            [[ -z "$key" || "$key" =~ ^# ]] && continue
            export "$key=$value"
        done <<< "$VAULT_OUTPUT"
        log "Secrets loaded"
    else
        log "WARN: vault.py returned no output"
    fi
fi

# ── Open ClickHouse SSH tunnel if needed ──────────────────────────────────────
if [[ "$SCRIPT_NAME" == "finance_intel" || "$SCRIPT_NAME" == "monarch_ingest" ]]; then
    if python3 -c "import socket; s=socket.create_connection(('localhost',19001),2); s.close()" 2>/dev/null; then
        export CLICKHOUSE_HOST=localhost
        export CLICKHOUSE_PORT=19001
    else
        log "Opening ClickHouse SSH tunnel..."
        if ssh -fNL 19001:localhost:9000 cortex 2>/dev/null; then
            sleep 1
            if python3 -c "import socket; s=socket.create_connection(('localhost',19001),2); s.close()" 2>/dev/null; then
                export CLICKHOUSE_HOST=localhost
                export CLICKHOUSE_PORT=19001
            else
                log "WARN: SSH tunnel started but localhost:19001 is not reachable — using configured CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-unset}:${CLICKHOUSE_PORT:-9000}"
            fi
        else
            log "WARN: SSH tunnel failed — using configured CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-unset}:${CLICKHOUSE_PORT:-9000}"
        fi
    fi
fi

# ── Note store: filesystem mode (works headless; Obsidian not required) ───────────────────
export NOTE_STORE_MODE="${NOTE_STORE_MODE:-filesystem}"

# ── Run the script ────────────────────────────────────────────────────────────
SCRIPT_PATH="${SCRIPT_DIR}/${PYTHON_SCRIPT}"
if [[ ! -f "$SCRIPT_PATH" ]]; then
    log "ERROR: Script not found: ${SCRIPT_PATH}"
    exit 1
fi

log "Running: python3 ${PYTHON_SCRIPT} ${EXTRA_ARGS}"
# shellcheck disable=SC2086
python3 "$SCRIPT_PATH" $EXTRA_ARGS 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

log "=== ${SCRIPT_NAME} finished (exit ${EXIT_CODE}) ==="
exit "$EXIT_CODE"
