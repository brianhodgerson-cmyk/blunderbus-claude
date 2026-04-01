---
name: morning-brief
description: Generate a daily morning briefing aggregating infrastructure health, security events, camera detections, and system alerts from the past 24 hours.
allowed-tools: Bash
---

# Morning Brief — Daily Infrastructure Summary

> SSH aliases are defined in ~/.ssh/config — always use aliases (e.g. `ssh cortex`), never `user@IP`.

## What This Does
Compiles an executive summary of the last 24 hours across all HodgeSpot systems. Designed to be run as a scheduled task in Claude Code Desktop.

## How To Schedule
In Claude Code Desktop, set up as a scheduled task:
```
Every day at 7:00 AM: Run /morning-brief
```

## Data Collection Steps

### 1. Infrastructure health snapshot
Run the `/infra-check` sweep:
```bash
for host in cortex stark banner truenas homeassistant loki; do
  echo "=== $host ==="
  ssh "$host" "uptime && free -h | grep Mem && df -h / | tail -1" 2>&1 || echo "❌ Unreachable"
done
# Thor (192.168.50.136) is the local workstation — run locally
echo "=== thor (local) ==="
uptime && free -h | grep Mem && df -h / | tail -1
```

### 2. Docker container status
```bash
echo "=== Cortex ==="
ssh cortex "docker ps --format '{{.Names}}: {{.Status}}'"
echo "=== Stark ==="
ssh stark "docker ps --format '{{.Names}}: {{.Status}}'"
```

### 3. Security events (24h)
```bash
curl -sk -H "Authorization: Bearer $SECONION_API_KEY" \
  "https://192.168.50.103/api/alerts?limit=50&range=24h&sort=severity:desc"
```

### 4. Firewall summary
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/gateways" | jq '.data[] | {name: .name, status: .status}'
```

### 5. Camera events (24h)
```bash
YESTERDAY=$(date -d '24 hours ago' +%s)
curl -s "http://192.168.50.205:5000/api/events?after=$YESTERDAY&limit=100" | jq 'group_by(.camera) | .[] | {camera: .[0].camera, total_events: length, labels: [.[].label] | group_by(.) | map({label: .[0], count: length})}'
```

### 6. NAS health
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/pool" | jq '.[] | {name: .name, status: .status, healthy: .healthy}'
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/alert/list" | jq '.[] | select(.dismissed == false)'
```

### 7. Prometheus alerts (24h)
```bash
ssh banner 'curl -s "http://localhost:9090/api/v1/alerts"' | jq '.data.alerts[] | {alertname: .labels.alertname, state: .state, severity: .labels.severity}'
```

## Report Format

```
# Morning Brief — <DATE>

## 🏥 Infrastructure
<table of host health>

## 🐳 Containers
<Cortex: X running, Y unhealthy | Stark: X running, Y unhealthy>

## 🔒 Security
<X alerts in 24h — breakdown by severity>
<Notable findings>

## 📷 Cameras
<X total events — breakdown by camera and label>

## 💾 Storage
<Pool status, usage, any alerts>

## ⚠️ Action Items
<Anything requiring attention, ranked by priority>
```
