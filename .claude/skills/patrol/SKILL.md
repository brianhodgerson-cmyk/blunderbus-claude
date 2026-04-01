---
name: patrol
description: Continuous monitoring loop using Claude's /loop feature. Cycles through system health, security, and service checks at regular intervals.
allowed-tools: Bash
---

# Patrol — Continuous Monitoring via /loop

> SSH aliases are defined in ~/.ssh/config — always use aliases (e.g. `ssh cortex`), never `user@IP`.

## What This Does
Runs a continuous monitoring cycle using Claude's `/loop` feature. Each iteration checks system health, security posture, and service availability, then reports anomalies.

## How To Use

Tell Claude to enter loop mode:
```
/loop Monitor the HodgeSpot infrastructure. Every cycle:
1. Ping all hosts
2. Check Docker containers on Cortex and Stark
3. Query Prometheus for CPU/memory/disk alerts
4. Check pfSense gateway status
5. Pull recent SecOnion alerts
6. Check Frigate for new detections
Report only anomalies and changes since last cycle.
```

## What Each Cycle Should Run

### 1. Host reachability (fast)
```bash
for host in 192.168.50.106 192.168.50.204 192.168.50.136 192.168.50.202 192.168.50.50 192.168.50.206 192.168.50.103; do
  ping -c 1 -W 2 "$host" > /dev/null 2>&1 && echo "UP $host" || echo "DOWN $host"
done
```

### 2. Container health (Cortex + Stark)
```bash
ssh cortex "docker ps --format '{{.Names}} {{.Status}}' | grep -v 'Up'" 2>&1
ssh stark "docker ps --format '{{.Names}} {{.Status}}' | grep -v 'Up'" 2>&1
```

### 3. Prometheus firing alerts
```bash
ssh banner 'curl -s "http://localhost:9090/api/v1/alerts"' | jq '.data.alerts[] | select(.state=="firing") | {alertname: .labels.alertname, instance: .labels.instance, severity: .labels.severity}'
```

### 4. Gateway status
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/gateways" | jq '.data[] | select(.status != "online")'
```

### 5. SecOnion recent critical alerts
```bash
curl -sk -H "Authorization: Bearer $SECONION_API_KEY" \
  "https://192.168.50.103/api/alerts?limit=10&severity=critical&sort=timestamp:desc"
```

### 6. Frigate recent events
```bash
curl -s "http://192.168.50.205:5000/api/events?limit=5" | jq '.[] | {camera: .camera, label: .label, score: .top_score, time: .start_time}'
```

## Reporting
- Only report **changes** and **anomalies** between cycles.
- Use status indicators: ✅ healthy, ⚠️ degraded, ❌ down.
- If everything is healthy, respond with a one-line "All clear" summary.
