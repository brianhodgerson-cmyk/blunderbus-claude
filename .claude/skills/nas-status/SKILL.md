---
name: nas-status
description: Check TrueNAS storage health — ZFS pool status, dataset usage, snapshots, disk health, and shares. Fully MCP-driven, no curl or SSH required.
allowed-tools: mcp__blunderbus__blunderbus_truenas
---

# NAS Status — TrueNAS (Heimdall, 192.168.50.50)

## What This Does
Queries TrueNAS via MCP for all storage health signals. No credentials or SSH needed — the MCP server handles auth.

## MCP Actions

### Pool status (run first)
```
action: get_pool_status
```
Returns: name, status, healthy, scan state, scan errors, size, allocated, free.
Flag ❌ if status != "ONLINE", healthy != true, or scan errors > 0.

### Dataset usage
```
action: list_datasets
```
Returns: dataset name, used bytes, available bytes, compression.
Flag ⚠️ if any dataset used > 80% of (used + available).

### Optional: filter by pool
```
action: list_datasets
pool_name: nas-pool
```

### Snapshot inventory
```
action: list_snapshots
limit: 20
```
Returns: snapshot names and creation times.

### Snapshot on specific dataset
```
action: list_snapshots
dataset: nas-pool/<DATASET>
limit: 10
```

### Disk health (SMART)
```
action: get_disk_health
```
Returns: disk name, model, serial, size, temperature, health status.
Flag ⚠️ if any disk temp > 45°C. Flag ❌ if SMART errors present.

### Shares
```
action: list_shares
```
Returns: NFS and SMB shares, paths, enabled state.

### Create snapshot (confirm with operator first)
```
action: create_snapshot
dataset: nas-pool/<DATASET>
snapshot_name: manual-<DATE>
confirm: true
```

## Thresholds

| Metric | ⚠️ Warn | ❌ Critical |
|--------|---------|------------|
| Pool status | — | != ONLINE |
| Pool healthy | — | false |
| Scrub errors | — | > 0 |
| Pool used | > 75% | > 90% |
| Disk temp | > 45°C | > 55°C |
| SMART status | — | any errors |

## Report Format

| Pool | Status | Used | Free | Scrub | Health |
|------|--------|------|------|-------|--------|
| nas-pool | ONLINE | 8.1TB | 31.8TB | Clean (0 errors) | ✅ |

List any active alerts or datasets approaching capacity below the table.
