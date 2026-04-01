---
name: home-control
description: Control and query Home Assistant entities — lights, switches, sensors, automations, and climate devices at HodgeSpot.
allowed-tools: Bash
---

# Home Control — Home Assistant API

## What This Does
Interfaces with Home Assistant at 192.168.50.206:8123 to control smart home devices, query sensor states, and manage automations.

## How To Run

### List all entities (filtered by domain)
```bash
ssh homeassistant "curl -s -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' http://localhost:8123/api/states" | jq '[.[] | select(.entity_id | startswith("<DOMAIN>."))]'
```
Domains: `light`, `switch`, `sensor`, `climate`, `automation`, `binary_sensor`, `cover`, `lock`

### Get state of a specific entity
```bash
ssh homeassistant "curl -s -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' http://localhost:8123/api/states/<ENTITY_ID>" | jq '{state: .state, attributes: .attributes}'
```

### Turn on/off a device
```bash
ssh homeassistant "curl -s -X POST -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' -H 'Content-Type: application/json' -d '{\"entity_id\": \"<ENTITY_ID>\"}' http://localhost:8123/api/services/<DOMAIN>/turn_<on|off>"
```

### Set climate temperature
```bash
ssh homeassistant "curl -s -X POST -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' -H 'Content-Type: application/json' -d '{\"entity_id\": \"<ENTITY_ID>\", \"temperature\": <TEMP>}' http://localhost:8123/api/services/climate/set_temperature"
```

### Trigger an automation
```bash
ssh homeassistant "curl -s -X POST -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' -H 'Content-Type: application/json' -d '{\"entity_id\": \"<AUTOMATION_ID>\"}' http://localhost:8123/api/services/automation/trigger"
```

### Get recent history for an entity
```bash
ssh homeassistant "curl -s -H 'Authorization: Bearer $HA_LONG_LIVED_TOKEN' 'http://localhost:8123/api/history/period?filter_entity_id=<ENTITY_ID>&minimal_response'" | jq '.[0][-5:]'
```
