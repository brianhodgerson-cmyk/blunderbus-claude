---
name: security-triage
description: Security triage across SecOnion IDS alerts and pfSense firewall logs. Use when investigating threats, checking for intrusions, or reviewing security posture.
allowed-tools: Bash
---

# Security Triage

## What This Does
Queries Security Onion for IDS/IPS alerts and pfSense for firewall events. Correlates findings into a prioritized security summary.

**IMPORTANT**: SecOnion (192.168.50.103) is READ-ONLY. Never attempt writes.

## How To Run

### 1. SecOnion — Recent alerts via API
```bash
curl -sk -H "Authorization: Bearer $SECONION_API_KEY" \
  "https://192.168.50.103/api/alerts?limit=25&sort=timestamp:desc"
```

### 2. SecOnion — Search for specific IOC
```bash
curl -sk -H "Authorization: Bearer $SECONION_API_KEY" \
  "https://192.168.50.103/api/search" \
  -d '{"query": "<SEARCH_TERM>", "range": "24h"}'
```

### 3. pfSense — Recent firewall logs
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/logs/firewall?limit=50"
```

### 4. pfSense — Active states (connections)
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/states"
```

### 5. Triage format
For each finding, report:
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW / INFO
- **Source IP → Destination IP:Port**
- **Signature/Rule**: What triggered
- **Timestamp**
- **Recommended action**
