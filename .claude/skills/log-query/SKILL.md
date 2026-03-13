---
name: log-query
description: Query logs from Loki and Docker containers. Use for troubleshooting, searching error patterns, and reviewing recent service activity.
allowed-tools: Bash
---

# Log Query — Loki + Docker Logs

## What This Does
Queries centralized logs in Loki (192.168.50.207:3100) using LogQL, or pulls Docker container logs directly via SSH.

## How To Run

### Loki — Query recent logs by job/service
```bash
curl -s -G "http://192.168.50.207:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="<JOB_NAME>"}' \
  --data-urlencode "start=$(date -d '1 hour ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  --data-urlencode "limit=100" | jq '.data.result[].values[][1]'
```

### Loki — Search for error patterns
```bash
curl -s -G "http://192.168.50.207:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="<JOB_NAME>"} |~ "(?i)(error|fail|panic|critical)"' \
  --data-urlencode "start=$(date -d '1 hour ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  --data-urlencode "limit=50" | jq '.data.result[].values[][1]'
```

### Loki — List available label values
```bash
# List all jobs
curl -s "http://192.168.50.207:3100/loki/api/v1/label/job/values" | jq '.data[]'
```

### Docker logs — Direct from host
```bash
# Cortex containers
ssh -o ConnectTimeout=5 user@192.168.50.106 "docker logs --tail=50 --since=1h <CONTAINER_NAME>"

# Stark containers
ssh -o ConnectTimeout=5 user@192.168.50.204 "docker logs --tail=50 --since=1h <CONTAINER_NAME>"
```

### Loki — Aggregate error count
```bash
curl -s -G "http://192.168.50.207:3100/loki/api/v1/query" \
  --data-urlencode 'query=count_over_time({job="<JOB_NAME>"} |~ "error" [1h])' | jq '.data.result'
```
