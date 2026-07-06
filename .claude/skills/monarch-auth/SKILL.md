---
name: monarch-auth
description: Monarch Money ingest authentication runbook — session-cookie bootstrap, daily ingest flow, and cookie refresh when the 05:15 ingest 401s. Use for anything touching monarch_ingest.py auth or MONARCH_* vault fields.
allowed-tools: Bash
---

# Monarch ingest authentication (post-2026-05-12)

The Monarch web app rebranded to `api.monarch.com` and switched from Token-auth to **session-cookie auth**. The legacy `monarch_login.py` flow (POST `/auth/login/` → JWT Token) is rate-limited hard once you trip 429 once; recovery is unreliable.

## Production path

1. **Bootstrap cookies** (manual, ~30 seconds):
   - Log into app.monarch.com in any browser
   - DevTools → Network → click any request to `api.monarch.com/graphql` → Headers → copy `Cookie: session_id=…; csrftoken=…` and the `device-uuid` request header
   - Push to Bitwarden `monarch` item custom fields: `session_id`, `csrftoken`, `device_uuid`, `session_refreshed_at`

2. **Daily ingest** (`blunderbus-monarch-ingest.timer`, 05:15 America/Chicago — currently disabled pending cookie refresh):
   - `scripts/monarch_ingest.py` reads cookies via `_mm_from_cookies()`, patches `MonarchMoneyEndpoints.BASE_URL` to `https://api.monarch.com`, calls `get_accounts()` / `get_transactions()` directly — no /login hit
   - Writes to ClickHouse `finance.accounts` and `finance.transactions`
   - Re-enable once cookies are fresh: `systemctl --user enable --now blunderbus-monarch-ingest.timer`

3. **Cookie refresh** (manual when ingest 401s; expected cadence weeks):
   - Re-run step 1 with a fresh browser session
   - Future: `scripts/monarch_refresh.py` with Playwright will automate this

Legacy fallbacks (`.monarch_session` file, `MONARCH_TOKEN`) remain in `monarch_ingest.py` for backward compatibility but should not be relied on.
