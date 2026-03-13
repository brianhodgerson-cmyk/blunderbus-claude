---
name: stack-deploy
description: Deploy, restart, or manage Docker Compose stacks on Cortex (192.168.50.106). Use for container lifecycle management, stack updates, and service restarts.
allowed-tools: Bash
disable-model-invocation: true
---

# Stack Deploy — Cortex Docker Management

## What This Does
Manages the Docker Compose stack on Cortex. Supports deploy, restart, stop, logs, and status operations.

**IMPORTANT**: Always confirm with the operator before running destructive operations (down, restart, prune).

## How To Run

### Check current stack status
```bash
ssh -o ConnectTimeout=5 user@192.168.50.106 "cd /opt/blunderbus && docker compose ps"
```

### View logs for a service
```bash
ssh -o ConnectTimeout=5 user@192.168.50.106 "cd /opt/blunderbus && docker compose logs --tail=100 <SERVICE_NAME>"
```

### Restart a specific service
```bash
# CONFIRM WITH OPERATOR FIRST
ssh -o ConnectTimeout=5 user@192.168.50.106 "cd /opt/blunderbus && docker compose restart <SERVICE_NAME>"
```

### Pull latest images and redeploy
```bash
# CONFIRM WITH OPERATOR FIRST
ssh -o ConnectTimeout=5 user@192.168.50.106 "cd /opt/blunderbus && docker compose pull && docker compose up -d"
```

### Stop a specific service
```bash
# CONFIRM WITH OPERATOR FIRST
ssh -o ConnectTimeout=5 user@192.168.50.106 "cd /opt/blunderbus && docker compose stop <SERVICE_NAME>"
```

### Check resource usage
```bash
ssh -o ConnectTimeout=5 user@192.168.50.106 "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}'"
```

## Services on Cortex
postgres, redis, litellm, langfuse, minio, clickhouse, mcp-gateway, pixel-dashboard
