---
name: infra-check
description: Check VM health across the cluster - CPU, memory, uptime, and status. Uses Proxmox MCP as primary source. SSH only for per-VM disk usage or process drill-down when requested.
allowed-tools: Bash, mcp__blunderbus__blunderbus_proxmox
---

# Infra Check - VM Health

## What This Does
Checks all VMs on Multiverse via the Proxmox MCP (one call, no SSH, no credentials). CPU%, RAM%, uptime, and status for all 13 VMs instantly. Falls back to SSH only for disk space or process inspection on a specific host.

## Primary: Proxmox MCP

### Full cluster sweep
```
action: list_vms
```

Returns for each VM: vmid, name, type, status, cpu (0–1 fraction), mem/maxmem (bytes), uptime (seconds).

Compute for display:
- CPU%: `cpu * 100` → round to 1 decimal
- RAM%: `(mem / maxmem) * 100` → round to 1 decimal
- Uptime: convert seconds → `Xd Xh`

### Single VM status
```
action: get_vm_status
vmid: <VMID>
```

### Snapshots on a VM
```
action: list_snapshots
vmid: <VMID>
```

## Secondary: SSH (only for disk/process drill-down)

Disk usage on a specific VM:
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <HOST_ALIAS> \
  "df -h / | tail -1"
```

Top CPU processes:
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <HOST_ALIAS> \
  "ps aux --sort=-%cpu | head -5"
```

Disk I/O:
```bash
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <HOST_ALIAS> \
  "iostat -x 1 2 2>/dev/null || cat /proc/diskstats | head -20"
```

## Thresholds

| Metric | ⚠️ Warn | ❌ Critical |
|--------|---------|------------|
| CPU% | > 80% | > 95% |
| RAM% | > 85% | > 95% |
| Status | — | != running (except VM 105, expected stopped) |

## Report Format

| VM | Host | Type | Status | CPU% | RAM% | Uptime |
|----|------|------|--------|------|------|--------|
| 100 | Heimdall | qemu | ✅ running | 0.3% | 99% | 22h |
| 101 | Thor | qemu | ✅ running | 10.1% | 99% | 22h |
| 102 | Jarvis | qemu | ✅ running | 0.7% | 67% | 22h |
| 103 | Fury | qemu | ✅ running | 3.3% | 94% | 22h |
| 104 | Stark | qemu | ✅ running | 1.3% | 91% | 13h |
| 105 | hawkeye | qemu | ⚠️ stopped | — | — | — |
| 106 | Cortex | qemu | ✅ running | 3.0% | 33% | 13h |
| 200 | Groot | lxc | ✅ running | 0.0% | 29% | 22h |
| 202 | Banner | lxc | ✅ running | 0.1% | 24% | 22h |
| 205 | Hawkeye | lxc | ✅ running | 0.0% | 2% | 22h |
| 207 | Loki | lxc | ✅ running | 0.1% | 8% | 22h |
| 209 | Ultron | lxc | ✅ running | 0.0% | 1% | 22h |
| 210 | Vision | lxc | ✅ running | 4.8% | 7% | 22h |

Note: Heimdall and Thor RAM% will show ~99% — normal, Proxmox reports all allocated RAM as used for these VMs.
