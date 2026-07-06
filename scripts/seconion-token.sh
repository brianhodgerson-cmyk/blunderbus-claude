#!/usr/bin/env bash
# Security Onion Connect OAuth token helper.
# Usage: ./scripts/seconion-token.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

SECONION_URL="${SECONION_URL:-https://soc.hodgespot.com}"
: "${SECONION_CLIENT_ID:?SECONION_CLIENT_ID not set}"
: "${SECONION_CLIENT_SECRET:?SECONION_CLIENT_SECRET not set}"

extract_access_token() {
  if command -v jq > /dev/null 2>&1; then
    jq -r '.access_token // empty'
    return
  fi

  if command -v python3 > /dev/null 2>&1; then
    python3 -c 'import json, sys; print(json.load(sys.stdin).get("access_token", ""))'
    return
  fi

  if command -v python > /dev/null 2>&1; then
    python -c 'import json, sys; print(json.load(sys.stdin).get("access_token", ""))'
    return
  fi

  sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

curl_args=(
  -sk
  --fail
  -u "$SECONION_CLIENT_ID:$SECONION_CLIENT_SECRET"
  -d "grant_type=client_credentials"
)

if [[ -n "${SECONION_TOKEN_SCOPE:-}" && "${SECONION_TOKEN_SCOPE}" != your_* ]]; then
  curl_args+=(-d "scope=$SECONION_TOKEN_SCOPE")
fi

response="$(curl "${curl_args[@]}" "${SECONION_URL%/}/oauth2/token")"
token="$(printf '%s' "$response" | extract_access_token | tail -n 1)"

if [[ -z "$token" ]]; then
  echo "Failed to parse access token from Security Onion response." >&2
  exit 1
fi

printf '%s\n' "$token"
