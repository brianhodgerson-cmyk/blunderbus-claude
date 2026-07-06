#!/usr/bin/env bash
# Security Onion Connect event query helper.
# Usage: ./scripts/seconion-query.sh <oql_query> [hours_back] [event_limit] [metric_limit] [timezone]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

QUERY="${1:?Usage: seconion-query.sh <oql_query> [hours_back] [event_limit] [metric_limit] [timezone]}"
HOURS="${2:-24}"
EVENT_LIMIT="${3:-50}"
METRIC_LIMIT="${4:-10}"
ZONE="${5:-${TZ:-America/Chicago}}"
RANGE_FORMAT="2006/01/02 3:04:05 PM"

START="$(date -d "$HOURS hours ago" +"%Y/%m/%d %I:%M:%S %p")"
END="$(date +"%Y/%m/%d %I:%M:%S %p")"

exec "$SCRIPT_DIR/seconion-api.sh" /connect/events/ --get \
  --data-urlencode "query=$QUERY" \
  --data-urlencode "range=$START - $END" \
  --data-urlencode "zone=$ZONE" \
  --data-urlencode "format=$RANGE_FORMAT" \
  --data-urlencode "metricLimit=$METRIC_LIMIT" \
  --data-urlencode "eventLimit=$EVENT_LIMIT"
