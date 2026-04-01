#!/bin/bash
# Monarch Money nightly ingest — runs on Cortex
# Cron: 0 3 * * * /opt/monarch_ingest.sh >> /var/log/monarch_ingest.log 2>&1

set -e

BW_SERVER="https://vaultwarden.hodgespot.com"
BW_USER="jarvis@hodgespot.com"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOG_PREFIX Starting Monarch Money ingest"

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

# Configure and unlock vault
bw config server "$BW_SERVER" > /dev/null 2>&1 || true
BW_SESSION=$(bw unlock "$BW_MASTER_PASS" --raw 2>/dev/null)

if [ -z "$BW_SESSION" ]; then
    echo "$LOG_PREFIX ERROR: Failed to unlock vault" >&2
    exit 1
fi

# Pull token from vault
MONARCH_TOKEN=$(bw get item "monarch" --session "$BW_SESSION" 2>/dev/null | \
    python3 -c "import sys,json; item=json.loads(sys.stdin.read()); print(next(f['value'] for f in item.get('fields',[]) if f['name']=='api_token'))")

if [ -z "$MONARCH_TOKEN" ]; then
    echo "$LOG_PREFIX ERROR: Could not retrieve Monarch token from vault" >&2
    exit 1
fi

echo "$LOG_PREFIX Token retrieved from vault"

# Run ingest
export MONARCH_TOKEN
export CLICKHOUSE_HOST=172.18.0.4
export CLICKHOUSE_USER=clickhouse
export CLICKHOUSE_PASSWORD=clickhouse

python3 /opt/monarch_ingest.py --days 7

echo "$LOG_PREFIX Ingest complete"

unset MONARCH_TOKEN BW_SESSION BW_MASTER_PASS
