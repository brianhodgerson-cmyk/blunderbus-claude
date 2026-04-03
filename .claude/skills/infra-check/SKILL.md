---
name: infra-check
description: Check VM health across the cluster — CPU, memory, disk, uptime, and load averages via SSH.
allowed-tools: Bash, mcp__obsidian__obsidian_read, mcp__obsidian__obsidian_append, mcp__obsidian__obsidian_write
---

# Infra Check — VM Health via SSH

> SSH aliases are defined in ~/.ssh/config — always use aliases (e.g. `ssh cortex`), never `user@IP`.

## What This Does
SSH into each VM to check system resources: uptime, CPU load, memory usage, and disk space.

## Obsidian Integration

After collecting and formatting the health table, offer to write results to today's daily note:

```
obsidian_append(
    path="Daily/YYYY-MM-DD.md",
    content="<formatted infra health table>",
    heading="Infrastructure"
)
```

If the user passes `--save` or asks to save/log the results, append automatically without prompting.

## How To Run

### Quick health check on a single host
```bash
ssh <ALIAS> "echo '--- Uptime ---' && uptime && echo '--- Memory ---' && free -h && echo '--- Disk ---' && df -h / && echo '--- Load ---' && cat /proc/loadavg"
```

### Sweep all VMs
```bash
for host in cortex stark banner truenas homeassistant loki; do
  echo "=== $host ==="
  ssh "$host" "uptime && free -h | grep Mem && df -h / | tail -1" 2>&1 || echo "❌ Unreachable"
  echo ""
done
# Thor (192.168.50.136) is the local workstation — run commands locally, not over SSH
echo "=== thor (local) ==="
uptime && free -h | grep Mem && df -h / | tail -1
```

### Check specific metrics

**Top processes by CPU:**
```bash
ssh <ALIAS> "ps aux --sort=-%cpu | head -10"
```

**Top processes by memory:**
```bash
ssh <ALIAS> "ps aux --sort=-%mem | head -10"
```

**Disk I/O:**
```bash
ssh <ALIAS> "iostat -x 1 3 2>/dev/null || cat /proc/diskstats"
```

## Report Format
| Host | Uptime | Load (1/5/15) | Memory Used | Disk Used | Status |
|------|--------|---------------|-------------|-----------|--------|
| Cortex | ... | ... | ... | ... | ✅/⚠️/❌ |

Flag ⚠️ if: load > CPU count, memory > 85%, disk > 90%.
Flag ❌ if: unreachable or disk > 95%.
