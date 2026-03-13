#!/usr/bin/env bash
# Loki LogQL query wrapper.
# Usage: ./scripts/loki-query.sh <job_name> [search_term] [hours_back]
#
# Examples:
#   ./scripts/loki-query.sh varlogs            # Last 1h of varlogs
#   ./scripts/loki-query.sh docker error 4     # Errors in docker logs, last 4h

set -euo pipefail

LOKI_URL="${LOKI_URL:-http://192.168.50.207:3100}"
JOB="${1:?Usage: loki-query.sh <job> [search_term] [hours_back]}"
SEARCH="${2:-}"
HOURS="${3:-1}"

START=$(date -d "$HOURS hours ago" +%s)000000000
END=$(date +%s)000000000

if [[ -n "$SEARCH" ]]; then
  QUERY="{job=\"$JOB\"} |~ \"(?i)$SEARCH\""
else
  QUERY="{job=\"$JOB\"}"
fi

curl -s -G "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode "query=$QUERY" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "limit=100" | jq -r '.data.result[].values[][1]' 2>/dev/null || echo "No results or Loki unreachable"
