---
name: adguard-dns
description: Check AdGuard Home DNS filtering status, query logs, top clients, blocked domains, and toggle protection. Manages DNS for *.hodgespot.com.
allowed-tools: Bash
---

# AdGuard DNS — DNS Filtering Management

## What This Does
Interfaces with AdGuard Home for DNS query logs, filtering statistics, and protection management across the HodgeSpot domain.

## How To Run

### Service status
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/status" | jq '{protection_enabled: .protection_enabled, running: .running, version: .version}'
```

### DNS query log (recent)
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/querylog?limit=50" | jq '.data[] | {time: .time, client: .client, domain: .question.name, answer: .status, upstream: .upstream}'
```

### Filtering statistics
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/stats" | jq '{
    total_queries: .num_dns_queries,
    blocked: .num_blocked_filtering,
    replaced_safebrowsing: .num_replaced_safebrowsing,
    avg_processing_time: .avg_processing_time,
    top_queried: .top_queried_domains[:5],
    top_blocked: .top_blocked_domains[:5],
    top_clients: .top_clients[:5]
  }'
```

### Top blocked domains
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/stats" | jq '.top_blocked_domains[:10]'
```

### Toggle protection (CONFIRM FIRST)
```bash
# CONFIRM WITH OPERATOR BEFORE TOGGLING
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  -X POST -H "Content-Type: application/json" \
  -d '{"protection_enabled": <true|false>}' \
  "http://$ADGUARD_HOST/control/dns_config"
```

### Check filter lists
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/filtering/status" | jq '.filters[] | {name: .name, enabled: .enabled, rules_count: .rules_count, last_updated: .last_updated}'
```

### Search query log for a domain
```bash
curl -s -u "$ADGUARD_USER:$ADGUARD_PASS" \
  "http://$ADGUARD_HOST/control/querylog?search=<DOMAIN>&limit=25" | jq '.data[] | {time: .time, client: .client, status: .status}'
```
