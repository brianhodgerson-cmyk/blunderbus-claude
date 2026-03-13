---
name: nas-status
description: Check TrueNAS storage health — ZFS pool status, dataset usage, snapshots, disk health, and replication status.
allowed-tools: Bash
---

# NAS Status — TrueNAS

## What This Does
Queries TrueNAS at 192.168.50.50 for storage pool health, dataset usage, snapshot inventory, and disk SMART status.

## How To Run

### Pool status
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/pool" | jq '.[] | {name: .name, status: .status, healthy: .healthy, size: .topology}'
```

### Dataset usage
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/pool/dataset" | jq '.[] | {name: .name, used: .used.parsed, available: .available.parsed, compression: .compression.value}'
```

### List snapshots
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/zfs/snapshot?limit=20&order_by=-name" | jq '.[] | {name: .name, created: .properties.creation.parsed}'
```

### Disk health (SMART)
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/disk" | jq '.[] | {name: .name, model: .model, serial: .serial, size: .size, temp: .temperature}'
```

### Alerts
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/alert/list" | jq '.[] | {level: .level, formatted: .formatted, datetime: .datetime}'
```

### Replication tasks
```bash
curl -s -H "Authorization: Bearer $TRUENAS_API_KEY" \
  "http://192.168.50.50/api/v2.0/replication" | jq '.[] | {name: .name, state: .state, last_snapshot: .state.last_snapshot}'
```

## Report Format
| Pool | Status | Used | Available | Health |
|------|--------|------|-----------|--------|
Flag ⚠️ if pool degraded or > 80% used. Flag ❌ if pool faulted or disk SMART errors.
