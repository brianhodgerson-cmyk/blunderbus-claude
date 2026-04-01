---
name: system-status
description: Full topology sweep of all HodgeSpot VMs and services. Shows host reachability, Docker container status, and service health across the entire cluster.
allowed-tools: Bash
---

# System Status — Full Topology Sweep

> SSH aliases are defined in ~/.ssh/config — always use aliases (e.g. `ssh cortex`), never `user@IP`.

## What This Does
Checks every VM and service in the HodgeSpot cluster for reachability and health.

## How To Run

### 1. Ping sweep all hosts
```bash
for host in 192.168.50.106 192.168.50.204 192.168.50.136 192.168.50.202 192.168.50.50 192.168.50.206 192.168.50.103 192.168.50.205 192.168.50.207; do
  ping -c 1 -W 2 "$host" > /dev/null 2>&1 && echo "✅ $host UP" || echo "❌ $host DOWN"
done
```

### 2. Docker status on Cortex
```bash
ssh cortex "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### 3. Docker status on Stark
```bash
ssh stark "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### 4. Key service HTTP checks
```bash
# Grafana (via Banner localhost)
ssh banner 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health'

# Home Assistant
curl -s -o /dev/null -w "%{http_code}" http://192.168.50.206:8123/api/

# Frigate
curl -s -o /dev/null -w "%{http_code}" http://192.168.50.205:5000/api/version

# Loki ready
ssh loki 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3100/ready'
```

### 5. Report format
Present results as a table:

| VM | Host | IP | Status | Notes |
|----|------|----|--------|-------|
| 106 | Cortex | 192.168.50.106 | ✅/❌ | Container count, any unhealthy |
| 104 | Stark | 192.168.50.204 | ✅/❌ | Container count |
| 101 | Thor | 192.168.50.136 | ✅/❌ | GPU/Ollama status |
| ... | ... | ... | ... | ... |
