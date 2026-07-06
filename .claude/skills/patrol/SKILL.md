---
name: patrol
description: Continuous monitoring loop using Claude's /loop feature. Cycles through system health, security, and service checks at regular intervals. MCP-first for fast, low-context checks.
allowed-tools: Bash, mcp__blunderbus__blunderbus_proxmox, mcp__blunderbus__blunderbus_truenas, mcp__blunderbus__blunderbus_asus_router
---

# Patrol - Continuous Monitoring via /loop

## What This Does
Runs a continuous monitoring cycle. MCP tools handle compute/storage/network (fast, structured, low context). SSH/curl used only for containers, security, and cameras which have no MCP equivalent.

## How To Use
```text
/loop Monitor the HodgeSpot infrastructure. Every cycle run /patrol and report only anomalies and changes since the last cycle.
```

---

## Each Cycle (run in order)

### 1. VM Reachability — Proxmox MCP
```
action: list_vms
```
Flag any VM not in expected state. Baseline: all running except VM 105 (hawkeye QEMU, stopped).
This replaces the previous ping sweep — one MCP call covers all 13 VMs.

### 2. NAS Pool — TrueNAS MCP
```
action: get_pool_status
```
Flag if status != ONLINE, healthy != true, or scan errors > 0.

### 3. Router Health — Router MCP
```
action: get_system_status
```
Flag if CPU temp > 75°C or RAM > 80%.

### 4. Container anomalies — SSH (filter to unhealthy only)
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new cortex \
  "docker ps --format '{{.Names}} {{.Status}}' | grep -v ' Up'" 2>&1
```
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new stark \
  "docker ps --format '{{.Names}} {{.Status}}' | grep -v ' Up'" 2>&1
```
Empty output = all containers healthy. Only report if something appears.

### 5. Prometheus firing alerts
```bash
curl -s "http://192.168.50.202:9090/api/v1/alerts" | \
  jq '[.data.alerts[] | select(.state=="firing")] | length'
```
If count > 0, pull details:
```bash
curl -s "http://192.168.50.202:9090/api/v1/alerts" | \
  jq '.data.alerts[] | select(.state=="firing") | {alert: .labels.alertname, instance: .labels.instance, severity: .labels.severity}'
```

### 6. SecOnion critical alerts (last 1h)
```bash
bash ./scripts/seconion-query.sh 'event.module:suricata AND event.severity_label:critical' 1 10 | jq '.'
```

### 7. Frigate recent events (last 5)
```bash
curl -s "http://192.168.50.205:5000/api/events?limit=5" | \
  jq '.[] | {camera: .camera, label: .label, score: .top_score, time: .start_time}'
```

---

## Reporting Rules

- **Report only anomalies and changes** since the last cycle.
- If everything is healthy: `✅ All systems nominal — <timestamp>`
- Status indicators: ✅ OK, ⚠️ WARN, ❌ FAIL
- Do not repeat the same anomaly twice unless it has worsened.
- For security findings, include severity: CRITICAL, HIGH, MEDIUM, LOW.
