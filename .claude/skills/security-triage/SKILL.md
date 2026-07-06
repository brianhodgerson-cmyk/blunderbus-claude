---
name: security-triage
description: Security triage across SecOnion IDS alerts and ASUS router firewall logs. Use when investigating threats, checking for intrusions, reviewing security posture, or querying Security Onion for IDS/IPS events, Suricata alerts, Zeek logs, or network detections.
allowed-tools: Bash
---

# Security Triage

## What This Does
Queries Security Onion for IDS/IPS alerts and the ASUS router for firewall/traffic events. Correlates findings into a prioritized security summary.

**NOTE**: pfSense is NOT installed. The edge is an ASUS GT-AXE16000. Use the ASUS router MCP or syslog for firewall data.

**NOTE**: SecOnion currently only sees **east-west** (LAN-to-LAN) traffic. North-south (internet-bound) traffic is not captured — the SPAN/TAP is not on the WAN interface. Keep this in mind when investigating outbound threats or client bypass behavior (e.g. DoT, VPN).

---

## Environment

| Item | Value |
|------|-------|
| SOC URL | `https://soc.hodgespot.com` |
| SOC IP | `192.168.50.103` |
| SO Version | `2.4.201` |
| Auth method | Ory Kratos browser flow (see below) |
| Credentials | `$SECONION_USER` / `$SECONION_PASS` (in `.env`) |
| SSH user | `admin` (same password, key auth preferred) |

---

## Step 1 — Authenticate (Kratos Browser Flow)

Security Onion 2.4 uses **Ory Kratos** for identity management. Basic auth and simple POST do NOT work. You must complete the browser login flow to get a session cookie.

```bash
source /c/blunderbus-claude/.env

# 1a. Configure server (idempotent)
SECONION_SOC="https://soc.hodgespot.com"

# 1b. Init login flow — get flow ID, action URL, and CSRF token
FLOW=$(curl -skL -m 10 \
  -c /tmp/so_cookies.txt \
  -b /tmp/so_cookies.txt \
  -H "Accept: application/json" \
  "$SECONION_SOC/kratos/self-service/login/browser")

FLOW_ID=$(echo "$FLOW" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
ACTION=$(echo "$FLOW" | grep -o '"action":"[^"]*"' | head -1 | cut -d'"' -f4)
CSRF=$(echo "$FLOW" | grep -o '"value":"[a-zA-Z0-9+/=_-]\{20,\}"' | head -1 | cut -d'"' -f4)

# 1c. Submit credentials to action URL
curl -skL -m 10 \
  -b /tmp/so_cookies.txt \
  -c /tmp/so_cookies.txt \
  -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/json" \
  "$ACTION" \
  --data-urlencode "identifier=$SECONION_USER" \
  --data-urlencode "password=$SECONION_PASS" \
  --data-urlencode "method=password" \
  --data-urlencode "csrf_token=$CSRF" > /tmp/so_login_result.json

# 1d. Extract session token from cookie jar
SO_SESSION=$(grep "ory_kratos_session" /tmp/so_cookies.txt | awk '{print $NF}' | tail -1)
echo "Session: ${SO_SESSION:+obtained (${#SO_SESSION} chars)}"
```

Verify auth succeeded — `"active":true` should appear in `/tmp/so_login_result.json`:
```bash
grep -o '"active":[^,]*' /tmp/so_login_result.json
```

All subsequent requests use `-H "Cookie: ory_kratos_session=$SO_SESSION"`.

**For state-changing requests (PUT/POST/DELETE) you also need `X-Srv-Token`:**
```bash
SRV_TOKEN=$(curl -sk -m 10 \
  -H "Cookie: ory_kratos_session=$SO_SESSION" \
  "https://soc.hodgespot.com/api/info" | grep -o '"srvToken":"[^"]*"' | cut -d'"' -f4)
```
Include as `-H "X-Srv-Token: $SRV_TOKEN"` on every write request. Without it, all PUT/POST calls return `400 The request could not be processed.`

---

## Step 2 — Verify connectivity

```bash
curl -sk -m 10 \
  -H "Cookie: ory_kratos_session=$SO_SESSION" \
  -H "Accept: application/json" \
  "https://soc.hodgespot.com/api/info" | grep -o '"version":"[^"]*"'
```

Expected: `"version":"2.4.201"`

---

## Step 3 — Query Events

### Known working endpoints

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/info` | GET | SO version, parameters, field schemas |
| `/api/events` | POST | Events/alerts (see query format below) |

### ⚠️ Known broken / wrong endpoints
- `GET /api/alerts` → 404
- `GET /api/hunt` → 404
- `GET /api/cases` → 404
- `GET /api/detections` → 404
- Basic auth (`-u user:pass`) → does not work, always redirects to login

### Events query — CONFIRMED WORKING SCHEMA

The events endpoint is a **GET** request (not POST) with URL query parameters.
The `range` string must use Go reference time format (`2006/01/02 3:04:05 PM`), and the
`format` parameter must be set to exactly that Go reference time string so SO knows how to parse it.

```bash
# Standard 24-hour query — today only
curl -sk -m 15 \
  -H "Cookie: ory_kratos_session=$SO_SESSION" \
  -H "Accept: application/json" \
  -G "https://soc.hodgespot.com/api/events/" \
  --data-urlencode "query=event.module:suricata" \
  --data-urlencode "range=2026/03/19 12:00:00 AM - 2026/03/19 11:59:59 PM" \
  --data-urlencode "format=2006/01/02 3:04:05 PM" \
  --data-urlencode "zone=America/Chicago" \
  --data-urlencode "metricLimit=0" \
  --data-urlencode "eventLimit=25"
```

**Parameter reference:**

| Param | Required | Notes |
|-------|----------|-------|
| `query` | ✅ | Lucene syntax. Use `*` for all events |
| `range` | ✅ | `"YYYY/MM/DD hh:mm:ss AM - YYYY/MM/DD hh:mm:ss PM"` in local time |
| `format` | ✅ | Always `"2006/01/02 3:04:05 PM"` (Go reference time — do not change) |
| `zone` | ✅ | IANA timezone, e.g. `"America/Chicago"` |
| `eventLimit` | ✅ | Max events returned (10–5000) |
| `metricLimit` | ✅ | Set to `0` to skip groupby metrics and speed up response |
| `gridId` | ❌ | Optional — filter by specific grid node |

**Important:**
- Trailing slash on `/api/events/` is required
- `range` uses 12-hour clock (`hh`) not 24-hour (`HH`)
- All POST attempts return 400 — this endpoint is GET only

### IOC search (IP or domain)
```bash
IOC="192.168.50.75"
TODAY_RANGE="$(date +'%Y/%m/%d') 12:00:00 AM - $(date +'%Y/%m/%d') 11:59:59 PM"

curl -sk -m 15 \
  -H "Cookie: ory_kratos_session=$SO_SESSION" \
  -H "Accept: application/json" \
  -G "https://soc.hodgespot.com/api/events/" \
  --data-urlencode "query=event.module:suricata AND (source.ip:$IOC OR destination.ip:$IOC)" \
  --data-urlencode "range=$TODAY_RANGE" \
  --data-urlencode "format=2006/01/02 3:04:05 PM" \
  --data-urlencode "zone=America/Chicago" \
  --data-urlencode "metricLimit=0" \
  --data-urlencode "eventLimit=50"
```

### Parse response
```bash
# Key fields in response
# .totalEvents — total match count
# .elapsedMs   — query time
# .events[]    — array of event objects
# .events[].source    — index source (e.g. "fury:.ds-logs-...")
# .events[].payload   — full event fields

curl ... | grep -o '"totalEvents":[0-9]*\|"elapsedMs":[0-9]*'
```

---

## ASUS Router — Firewall / Traffic Logs

Use the ASUS router MCP tool (no pfSense in this environment):

```bash
# Recent firewall log entries
mcp: blunderbus_asus_router action=get_syslog category=firewall lines=100

# DNS queries from a specific client IP
mcp: blunderbus_asus_router action=get_dns_queries client_ip=192.168.50.X

# Web history for a specific device MAC
mcp: blunderbus_asus_router action=get_web_history mac=AA:BB:CC:DD:EE:FF limit=100
```

---

## Triage Report Format

For each finding:
- **Severity**: CRITICAL / HIGH / MEDIUM / LOW / INFO
- **Source IP → Destination IP:Port**
- **Signature/Rule**: What triggered
- **Timestamp**
- **Recommended action**
