---
name: firewall-check
description: Query pfSense firewall rules, active states, interfaces, and gateway status. Use for network troubleshooting and security auditing.
allowed-tools: Bash
---

# Firewall Check — pfSense

## What This Does
Queries the pfSense firewall at pfsense.hodgespot.com for rules, active connections, interface status, and gateway health.

## How To Run

### List firewall rules
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/firewall/rules" | jq '.data[] | {interface: .interface, action: .type, src: .source, dst: .destination, descr: .descr}'
```

### Active connection states
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/states" | jq '.data | length'
```

### Interface status
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/interfaces" | jq '.data[] | {name: .descr, status: .status, ipaddr: .ipaddr, media: .media}'
```

### Gateway status
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/gateways" | jq '.data[] | {name: .name, status: .status, delay: .delay, loss: .loss}'
```

### System info
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/status/system" | jq '{uptime: .data.uptime, cpu: .data.cpu_usage, mem: .data.mem_usage}'
```

### Search rules for a specific IP/subnet
```bash
curl -sk -u "$PFSENSE_USER:$PFSENSE_PASS" \
  "https://pfsense.hodgespot.com/api/v2/firewall/rules" | jq '.data[] | select(.source.address == "<IP>" or .destination.address == "<IP>")'
```
