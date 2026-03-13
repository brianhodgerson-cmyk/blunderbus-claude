---
name: health-summary
description: Query Grafana dashboards and Prometheus metrics for infrastructure health — CPU, memory, disk, network, and custom metrics.
allowed-tools: Bash
---

# Health Summary — Grafana + Prometheus

## What This Does
Pulls metrics from Prometheus (via Grafana on Banner at 192.168.50.202) for infrastructure health monitoring.

## How To Run

### Prometheus — Instant query
```bash
curl -s "http://192.168.50.202:9090/api/v1/query" \
  --data-urlencode "query=<PROMQL>" | jq '.data.result'
```

### Common PromQL queries

**CPU usage per host:**
```bash
curl -s "http://192.168.50.202:9090/api/v1/query" \
  --data-urlencode "query=100 - (avg by(instance)(rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)" | jq '.data.result[] | {instance: .metric.instance, cpu_pct: .value[1]}'
```

**Memory usage per host:**
```bash
curl -s "http://192.168.50.202:9090/api/v1/query" \
  --data-urlencode "query=(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100" | jq '.data.result[] | {instance: .metric.instance, mem_pct: .value[1]}'
```

**Disk usage per host:**
```bash
curl -s "http://192.168.50.202:9090/api/v1/query" \
  --data-urlencode "query=(1 - node_filesystem_avail_bytes{mountpoint=\"/\"} / node_filesystem_size_bytes{mountpoint=\"/\"}) * 100" | jq '.data.result[] | {instance: .metric.instance, disk_pct: .value[1]}'
```

**Up/down status:**
```bash
curl -s "http://192.168.50.202:9090/api/v1/query" \
  --data-urlencode "query=up" | jq '.data.result[] | {job: .metric.job, instance: .metric.instance, up: .value[1]}'
```

### Grafana — List dashboards
```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  http://192.168.50.202:3000/api/search?type=dash-db | jq '.[].title'
```

### Grafana — Get dashboard by UID
```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "http://192.168.50.202:3000/api/dashboards/uid/<UID>" | jq '.dashboard.title, .dashboard.panels[].title'
```

## Report Format
| Host | CPU % | Mem % | Disk % | Status |
|------|-------|-------|--------|--------|
Flag ⚠️ if any metric > 80%. Flag ❌ if > 95% or target is down.
