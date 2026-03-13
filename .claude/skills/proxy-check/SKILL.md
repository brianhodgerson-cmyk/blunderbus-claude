---
name: proxy-check
description: Check Nginx Proxy Manager hosts, SSL certificate status, access logs, and routing for *.hodgespot.com reverse proxy on Stark.
allowed-tools: Bash
---

# Proxy Check — Nginx Proxy Manager

## What This Does
Queries Nginx Proxy Manager on Stark (192.168.50.204) for proxy host configuration, SSL certificate health, and routing status.

## How To Run

### Authenticate and get token
```bash
NPM_TOKEN=$(curl -s -X POST \
  "http://192.168.50.204:81/api/tokens" \
  -H "Content-Type: application/json" \
  -d "{\"identity\": \"$NPM_USER\", \"secret\": \"$NPM_PASS\"}" | jq -r '.token')
```

### List all proxy hosts
```bash
curl -s -H "Authorization: Bearer $NPM_TOKEN" \
  "http://192.168.50.204:81/api/nginx/proxy-hosts" | jq '.[] | {id: .id, domain: .domain_names, forward_host: .forward_host, forward_port: .forward_port, ssl: .certificate_id, enabled: .enabled}'
```

### Check SSL certificates
```bash
curl -s -H "Authorization: Bearer $NPM_TOKEN" \
  "http://192.168.50.204:81/api/nginx/certificates" | jq '.[] | {id: .id, domain: .domain_names, provider: .provider, expires: .expires_on}'
```

### Check a specific proxy host
```bash
curl -s -H "Authorization: Bearer $NPM_TOKEN" \
  "http://192.168.50.204:81/api/nginx/proxy-hosts/<HOST_ID>" | jq '{domain: .domain_names, forward: "\(.forward_host):\(.forward_port)", ssl_forced: .ssl_forced, http2: .http2_support}'
```

### List redirection hosts
```bash
curl -s -H "Authorization: Bearer $NPM_TOKEN" \
  "http://192.168.50.204:81/api/nginx/redirection-hosts" | jq '.[] | {domain: .domain_names, forward_domain: .forward_domain_name}'
```

### SSL cert expiry check (direct)
```bash
echo | openssl s_client -connect <DOMAIN>:443 -servername <DOMAIN> 2>/dev/null | openssl x509 -noout -dates
```

### Test external accessibility
```bash
for domain in pfsense.hodgespot.com vaultwarden.hodgespot.com; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://$domain")
  echo "$domain: $STATUS"
done
```

## Report Format
| Domain | Backend | SSL Expires | Status |
|--------|---------|-------------|--------|
| *.hodgespot.com | host:port | date | ✅/⚠️/❌ |

Flag ⚠️ if SSL expires within 14 days. Flag ❌ if expired or unreachable.
