#!/usr/bin/env bash
# Home Assistant REST API helper.
# Usage:
#   ./scripts/ha-api.sh states                     # List all entity states
#   ./scripts/ha-api.sh state <entity_id>          # Get single entity state
#   ./scripts/ha-api.sh call <domain> <service> <entity_id>  # Call a service
#   ./scripts/ha-api.sh history <entity_id>        # Recent history

set -euo pipefail

HA_URL="${HA_URL:-http://192.168.50.206:8123}"
HA_TOKEN="${HA_LONG_LIVED_TOKEN:?HA_LONG_LIVED_TOKEN not set}"

ACTION="${1:?Usage: ha-api.sh <states|state|call|history> [args...]}"

case "$ACTION" in
  states)
    DOMAIN="${2:-}"
    if [[ -n "$DOMAIN" ]]; then
      curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states" | jq "[.[] | select(.entity_id | startswith(\"$DOMAIN.\"))] | sort_by(.entity_id) | .[] | {entity_id, state}"
    else
      curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states" | jq '.[].entity_id' | sort
    fi
    ;;
  state)
    ENTITY="${2:?Usage: ha-api.sh state <entity_id>}"
    curl -s -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/states/$ENTITY" | jq '{entity_id, state, attributes: .attributes | del(.friendly_name)}'
    ;;
  call)
    DOMAIN="${2:?Usage: ha-api.sh call <domain> <service> <entity_id>}"
    SERVICE="${3:?Missing service}"
    ENTITY="${4:?Missing entity_id}"
    curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
      -d "{\"entity_id\": \"$ENTITY\"}" \
      "$HA_URL/api/services/$DOMAIN/$SERVICE" | jq '.'
    ;;
  history)
    ENTITY="${2:?Usage: ha-api.sh history <entity_id>}"
    curl -s -H "Authorization: Bearer $HA_TOKEN" \
      "$HA_URL/api/history/period?filter_entity_id=$ENTITY&minimal_response" | jq '.[0][-10:]'
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Usage: ha-api.sh <states|state|call|history> [args...]"
    exit 1
    ;;
esac
