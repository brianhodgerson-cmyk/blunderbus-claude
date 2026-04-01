#!/usr/bin/env bash
# Security Onion Connect API wrapper.
# Usage: ./scripts/seconion-api.sh <path-or-url> [curl args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

REQUEST_PATH="${1:?Usage: seconion-api.sh <path-or-url> [curl args...]}"
shift || true

SECONION_URL="${SECONION_URL:-https://soc.hodgespot.com}"
TOKEN="$("$SCRIPT_DIR/seconion-token.sh")"

if [[ "$REQUEST_PATH" =~ ^https?:// ]]; then
  URL="$REQUEST_PATH"
else
  URL="${SECONION_URL%/}/${REQUEST_PATH#/}"
fi

exec curl -sk -H "Authorization: Bearer $TOKEN" "$URL" "$@"
