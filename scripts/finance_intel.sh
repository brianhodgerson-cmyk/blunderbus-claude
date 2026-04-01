#!/bin/bash
# Finance Intelligence nightly run — runs on Cortex after monarch_ingest.sh
# Cron: 0 4 * * * /opt/finance_intel.sh >> /var/log/finance_intel.log 2>&1
# (runs 1 hour after monarch ingest so fresh data is available)

set -e

BW_SERVER="https://vaultwarden.hodgespot.com"
BW_USER="jarvis@hodgespot.com"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOG_PREFIX Starting Finance Intelligence run"

# Load master password from env file
if [ -f /opt/blunderbus/.env ]; then
    source /opt/blunderbus/.env
elif [ -f /root/.env ]; then
    source /root/.env
fi

if [ -z "$BW_MASTER_PASS" ]; then
    echo "$LOG_PREFIX ERROR: BW_MASTER_PASS not set" >&2
    exit 1
fi

# Unlock vault
bw config server "$BW_SERVER" > /dev/null 2>&1 || true
BW_SESSION=$(bw unlock "$BW_MASTER_PASS" --raw 2>/dev/null)

if [ -z "$BW_SESSION" ]; then
    echo "$LOG_PREFIX ERROR: Failed to unlock vault" >&2
    exit 1
fi

# Pull secrets from vault
ANTHROPIC_API_KEY=$(bw get item "Anthropic Key" --session "$BW_SESSION" 2>/dev/null | \
    python3 -c "import sys,json; item=json.loads(sys.stdin.read()); print(item.get('notes','').strip())")

TELEGRAM_BOT_TOKEN=$(bw get item "telegram-bot" --session "$BW_SESSION" 2>/dev/null | \
    python3 -c "import sys,json; item=json.loads(sys.stdin.read()); print(next(f['value'] for f in item.get('fields',[]) if f['name']=='token'))")

TELEGRAM_CHAT_ID=$(bw get item "telegram-bot" --session "$BW_SESSION" 2>/dev/null | \
    python3 -c "import sys,json; item=json.loads(sys.stdin.read()); print(next(f['value'] for f in item.get('fields',[]) if f['name']=='chat_id'))")

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "$LOG_PREFIX ERROR: Could not retrieve Anthropic key from vault" >&2
    exit 1
fi

echo "$LOG_PREFIX Credentials retrieved from vault"

# Run intelligence suite (no Obsidian — Obsidian is on Brian's local machine)
export ANTHROPIC_API_KEY
export TELEGRAM_BOT_TOKEN
export TELEGRAM_CHAT_ID
export CLICKHOUSE_HOST=172.18.0.4
export CLICKHOUSE_USER=clickhouse
export CLICKHOUSE_PASSWORD=clickhouse

python3 /opt/finance_intel.py --no-obsidian

echo "$LOG_PREFIX Finance intel complete"

unset ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID BW_SESSION BW_MASTER_PASS
