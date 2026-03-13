#!/usr/bin/env bash
# BlunderBus — First-run setup validation.
# Checks SSH connectivity, service endpoints, and environment variables.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}✅ PASS${NC}: $1"; }
fail() { echo -e "${RED}❌ FAIL${NC}: $1"; }
warn() { echo -e "${YELLOW}⚠️  WARN${NC}: $1"; }

echo "=================================="
echo "  BlunderBus Setup Validation"
echo "=================================="
echo ""

# Check .env file
echo "--- Environment ---"
if [[ -f .env ]]; then
  pass ".env file exists"
  source .env
else
  fail ".env file not found — copy .env.example to .env and fill in values"
  exit 1
fi

# Check required env vars
REQUIRED_VARS=(
  HA_LONG_LIVED_TOKEN PFSENSE_USER PFSENSE_PASS SECONION_API_KEY
  TRUENAS_API_KEY GRAFANA_API_KEY MQTT_USER MQTT_PASS
)

for var in "${REQUIRED_VARS[@]}"; do
  if [[ -n "${!var:-}" ]]; then
    pass "$var is set"
  else
    warn "$var is not set"
  fi
done

echo ""
echo "--- SSH Connectivity ---"
HOSTS=(
  "192.168.50.106:Cortex"
  "192.168.50.204:Stark"
  "192.168.50.136:Thor"
  "192.168.50.202:Banner"
  "192.168.50.50:TrueNAS"
  "192.168.50.206:HomeAssistant"
  "192.168.50.103:Fury/SecOnion"
)

for entry in "${HOSTS[@]}"; do
  IFS=: read -r ip name <<< "$entry"
  if ssh -o ConnectTimeout=3 -o BatchMode=yes "user@$ip" "echo ok" > /dev/null 2>&1; then
    pass "SSH to $name ($ip)"
  else
    fail "SSH to $name ($ip) — check key auth"
  fi
done

echo ""
echo "--- Service Endpoints ---"
ENDPOINTS=(
  "http://192.168.50.202:3000/api/health:Grafana"
  "http://192.168.50.206:8123/api/:HomeAssistant"
  "http://192.168.50.205:5000/api/version:Frigate"
  "http://192.168.50.207:3100/ready:Loki"
  "http://192.168.50.136:11434/:Ollama"
  "http://192.168.50.106:4000/health:LiteLLM"
)

for entry in "${ENDPOINTS[@]}"; do
  IFS=: read -r proto host path name <<< "$entry"
  url="$proto:$host:$path"
  # Reconstruct URL properly
  url="${entry%:*}"
  name="${entry##*:}"
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    pass "$name (HTTP $code)"
  else
    fail "$name (HTTP $code)"
  fi
done

echo ""
echo "--- Claude Code ---"
if command -v claude > /dev/null 2>&1; then
  pass "Claude Code CLI found: $(claude --version 2>/dev/null || echo 'installed')"
else
  warn "Claude Code CLI not found — install from https://code.claude.com"
fi

echo ""
echo "Setup validation complete."
