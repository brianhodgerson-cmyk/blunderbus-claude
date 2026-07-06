---
name: morning-brief
description: Generate a daily morning briefing aggregating infrastructure health, security events, camera detections, and system alerts from the past 24 hours. Optionally appends a personal workspace summary (email, calendar, tasks) via the workspace-brief skill.
allowed-tools: Bash, mcp__blunderbus__blunderbus_proxmox, mcp__blunderbus__blunderbus_truenas, mcp__blunderbus__blunderbus_home_assistant, mcp__blunderbus__blunderbus_asus_router
---

# Morning Brief - Daily Infrastructure Summary

## What This Does
Compiles an executive summary of the last 24 hours across all HodgeSpot systems. MCP tools handle the heavy infrastructure sections (fast, no credentials, low context cost). SSH/curl used only for Docker containers, security events, cameras, and Prometheus — which have no MCP equivalent.

## How To Schedule
In Claude Code Desktop scheduled tasks:
```
Every day at 7:00 AM: Run /morning-brief
```

---

## Data Collection (run in this order)

### 1. VM Health — Proxmox MCP
```
action: list_vms
```
Summarize: how many running, any unexpected stops, flag high CPU/RAM.

### 2. NAS Health — TrueNAS MCP
```
action: get_pool_status
```
```
action: list_datasets
```
Flag pool degraded or any dataset > 80% used. Also check undismissed alerts:
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/alert/list" | \
  jq '[.[] | select(.dismissed == false)] | length, .[].formatted'
```

### 3. Router Status — Router MCP
```
action: get_system_status
```
```
action: get_traffic_stats
```
Flag if CPU temp > 75°C or RAM > 80%.

### 4. Home Status — HA MCP
```
action: get_state
entity_id: person.brian_hodgerson
```
```
action: get_state
entity_id: weather.forecast_home
```
```
action: get_state
entity_id: climate.master_bedroom_thermostat
```

### 5. Container anomalies — SSH (anomaly-only filter)
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new cortex \
  "docker ps --format '{{.Names}}: {{.Status}}' | grep -v ': Up'"
```
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new stark \
  "docker ps --format '{{.Names}}: {{.Status}}' | grep -v ': Up'"
```
If both return empty output → all containers healthy.

### 6. Security events (24h) — SecOnion
```bash
bash ./scripts/seconion-query.sh 'event.module:suricata' 24 50 | jq '.'
```

### 7. Camera events (24h) — Frigate
```bash
YESTERDAY=$(date -d '24 hours ago' +%s)
curl -s "http://192.168.50.205:5000/api/events?after=$YESTERDAY&limit=100" | \
  jq 'group_by(.camera) | .[] | {camera: .[0].camera, total: length, labels: [.[].label] | group_by(.) | map({label: .[0], count: length})}'
```

### 8. Prometheus firing alerts
```bash
curl -s "http://192.168.50.202:9090/api/v1/alerts" | \
  jq '[.data.alerts[] | select(.state=="firing")] | length, .[].labels | {alert: .alertname, instance: .instance, severity: .severity}'
```

---

## Report Format

```
# Morning Brief — <DATE> <TIME>

## Infrastructure  ✅/⚠️/❌
| VM | Status | CPU% | RAM% | Uptime |
<table from Proxmox MCP — flag anomalies only, summarize healthy count>

## Storage  ✅/⚠️/❌
Pool: nas-pool — ONLINE, 8.1TB / 36.4TB used, scrub clean
<Any undismissed alerts or datasets > 80%>

## Network  ✅/⚠️/❌
Router: CPU XX%, RAM XX%, temps normal | Gateways: all online

## Home
Brian: home/away | Weather: <conditions, temp> | Thermostat: <temp>°F <mode>

## Containers  ✅/⚠️/❌
Cortex: X running, 0 unhealthy | Stark: X running, 0 unhealthy
<List any non-Up containers>

## Security  ✅/⚠️/❌
<X IDS alerts in 24h — CRITICAL: X, HIGH: X, MEDIUM: X>
<Notable findings if any>

## Cameras
<X total events — by camera and label>
<Any notable detections>

## Action Items
<Ranked list of anything requiring operator attention>
<"None" if everything is clean>
```
