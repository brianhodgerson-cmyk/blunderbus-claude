---
name: infra-check
description: Check VM health across the cluster — CPU, memory, disk, uptime, and load averages via SSH.
allowed-tools: Bash
---

# Infra Check — VM Health via SSH

## What This Does
SSH into each VM to check system resources: uptime, CPU load, memory usage, and disk space.

## How To Run

### Quick health check on a single host
```bash
ssh -o ConnectTimeout=5 user@<HOST_IP> "echo '--- Uptime ---' && uptime && echo '--- Memory ---' && free -h && echo '--- Disk ---' && df -h / && echo '--- Load ---' && cat /proc/loadavg"
```

### Sweep all VMs
```bash
for host in 192.168.50.106 192.168.50.204 192.168.50.136 192.168.50.202 192.168.50.50 192.168.50.206 192.168.50.207; do
  echo "=== $host ==="
  ssh -o ConnectTimeout=5 user@"$host" "uptime && free -h | grep Mem && df -h / | tail -1" 2>&1 || echo "❌ Unreachable"
  echo ""
done
```

### Check specific metrics

**Top processes by CPU:**
```bash
ssh -o ConnectTimeout=5 user@<HOST_IP> "ps aux --sort=-%cpu | head -10"
```

**Top processes by memory:**
```bash
ssh -o ConnectTimeout=5 user@<HOST_IP> "ps aux --sort=-%mem | head -10"
```

**Disk I/O:**
```bash
ssh -o ConnectTimeout=5 user@<HOST_IP> "iostat -x 1 3 2>/dev/null || cat /proc/diskstats"
```

## Report Format
| Host | Uptime | Load (1/5/15) | Memory Used | Disk Used | Status |
|------|--------|---------------|-------------|-----------|--------|
| Cortex | ... | ... | ... | ... | ✅/⚠️/❌ |

Flag ⚠️ if: load > CPU count, memory > 85%, disk > 90%.
Flag ❌ if: unreachable or disk > 95%.
