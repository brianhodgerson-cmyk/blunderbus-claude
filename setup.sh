#!/usr/bin/env bash
# BlunderBus - First-run setup validation.
# Checks SSH connectivity, service endpoints, and environment variables.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; }
warn() { echo -e "${YELLOW}WARN${NC}: $1"; }

is_placeholder() {
  local value="${1:-}"
  [[ -z "$value" || "$value" == your_* ]]
}

check_endpoint() {
  local name="$1"
  local url="$2"
  local header="${3:-}"
  local code

  if [[ -n "$header" ]]; then
    code=$(curl -sk -o /dev/null -w "%{http_code}" --connect-timeout 3 -H "$header" "$url" 2>/dev/null || true)
  else
    code=$(curl -sk -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || true)
  fi

  if [[ -z "$code" ]]; then
    code="000"
  fi

  if [[ "$code" == "200" ]]; then
    pass "$name (HTTP $code)"
  else
    fail "$name (HTTP $code)"
  fi
}

check_seconion() {
  if is_placeholder "${SECONION_CLIENT_ID:-}" || is_placeholder "${SECONION_CLIENT_SECRET:-}" || [[ -z "${SECONION_URL:-}" ]]; then
    warn "SecurityOnion Connect skipped - SECONION_URL / client credentials are missing or still set to template values"
    return
  fi

  if token=$(bash ./scripts/seconion-token.sh 2>/dev/null); then
    pass "SecurityOnion OAuth token exchange"
    check_endpoint "SecurityOnion Connect" "${SECONION_URL%/}/connect/info" "Authorization: Bearer $token"
  else
    fail "SecurityOnion OAuth token exchange"
  fi
}

echo "=================================="
echo "  BlunderBus Setup Validation"
echo "=================================="
echo ""

echo "--- Environment ---"
if [[ -f .env ]]; then
  pass ".env file exists"
  # shellcheck disable=SC1091
  source .env
else
  fail ".env file not found - copy .env.example to .env and fill in values"
  exit 1
fi

REQUIRED_VARS=(
  HA_LONG_LIVED_TOKEN PFSENSE_USER PFSENSE_PASS
  SECONION_URL SECONION_CLIENT_ID SECONION_CLIENT_SECRET
  TRUENAS_API_KEY GRAFANA_TOKEN MQTT_USER MQTT_PASS
)

for var in "${REQUIRED_VARS[@]}"; do
  if is_placeholder "${!var:-}"; then
    warn "$var is missing or still set to a template value"
  else
    pass "$var is set"
  fi
done

echo ""
echo "--- SSH Connectivity ---"
HOSTS=(
  "cortex:192.168.50.106:Cortex"
  "stark:192.168.50.204:Stark"
  "thor:192.168.50.136:Thor"
  "banner:192.168.50.202:Banner"
  "heimdall:192.168.50.50:TrueNAS"
  "homeassistant:192.168.50.206:HomeAssistant"
  "fury:192.168.50.103:Fury/SecOnion"
)

for entry in "${HOSTS[@]}"; do
  IFS=: read -r alias ip name <<< "$entry"
  if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$alias" "echo ok" > /dev/null 2>&1; then
    pass "SSH to $name ($alias -> $ip)"
  else
    fail "SSH to $name ($alias -> $ip) - check ~/.ssh/config and the local SSH agent"
  fi
done

echo ""
echo "--- Service Endpoints ---"
check_endpoint "Grafana" "http://192.168.50.202:3000/api/health"
check_endpoint "Frigate" "http://192.168.50.205:5000/api/version"
check_endpoint "Loki" "http://192.168.50.207:3100/ready"
check_endpoint "Ollama" "http://192.168.50.136:11434/"
check_endpoint "LiteLLM" "http://192.168.50.106:4000/health"

if is_placeholder "${HA_LONG_LIVED_TOKEN:-}"; then
  warn "HomeAssistant skipped - HA_LONG_LIVED_TOKEN is missing or still set to a template value"
else
  check_endpoint "HomeAssistant" "${HA_URL:-http://192.168.50.206:8123}/api/" "Authorization: Bearer $HA_LONG_LIVED_TOKEN"
fi

check_seconion

echo ""
echo "--- Claude Code ---"
if command -v claude > /dev/null 2>&1; then
  pass "Claude Code CLI found: $(claude --version 2>/dev/null || echo installed)"
else
  warn "Claude Code CLI not found - install from https://code.claude.com"
fi

echo ""
echo "Setup validation complete."
