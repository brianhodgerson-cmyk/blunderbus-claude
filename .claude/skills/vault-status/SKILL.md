---
name: vault-status
description: Check Vaultwarden service health, user count, and organization status at vaultwarden.hodgespot.com.
allowed-tools: Bash
---

# Vault Status — Vaultwarden

## What This Does
Checks the Vaultwarden password manager at vaultwarden.hodgespot.com for service health and basic status.

**Service account**: jarvis@hodgespot.com

## How To Run

### Health check
```bash
curl -s -o /dev/null -w "%{http_code}" https://vaultwarden.hodgespot.com/alive
```
Expected: `200`

### Service status (if running as Docker on Cortex)
```bash
ssh -o ConnectTimeout=5 user@192.168.50.106 "docker ps --filter name=vaultwarden --format '{{.Names}}: {{.Status}}'"
```

### Check Vaultwarden admin panel (if enabled)
```bash
curl -s -H "Authorization: Bearer $VAULTWARDEN_ADMIN_TOKEN" \
  "https://vaultwarden.hodgespot.com/admin/users" | jq 'length'
```

### Vaultwarden version
```bash
curl -s "https://vaultwarden.hodgespot.com/api/config" | jq '{version: .version, signup_allowed: .signupsAllowed, server_installed: .server_installed}'
```

### Check container logs for errors
```bash
ssh -o ConnectTimeout=5 user@192.168.50.106 "docker logs --tail=30 --since=24h vaultwarden 2>&1 | grep -i 'error\|warn\|fail'"
```

## Report Format
| Service | Status | Version | Users | Errors (24h) |
|---------|--------|---------|-------|---------------|
| Vaultwarden | ✅/❌ | x.x.x | N | count |
