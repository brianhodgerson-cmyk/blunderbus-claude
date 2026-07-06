---
name: vault-get
description: >
  Retrieve credentials and secrets from Vaultwarden using the Bitwarden CLI (bw).
  Use this skill whenever you need a password, API key, token, or any secret that
  might be stored in the vault — instead of asking the user to type it in chat or
  hardcoding it in .env. Trigger when fetching infrastructure credentials, service
  passwords, API tokens, or any time another skill needs a secret and it isn't already
  in the environment. The vault account is jarvis@hodgespot.com at vaultwarden.hodgespot.com.
allowed-tools: Bash
---

# Vault Get — Bitwarden CLI Credential Retrieval

## What This Does
Unlocks the Vaultwarden instance at `vaultwarden.hodgespot.com` using the `bw` CLI and
retrieves a named credential so it can be used in subsequent commands — without ever
printing the value to the user or storing it in plain text beyond the current shell session.

- **Vault URL:** `https://vaultwarden.hodgespot.com`
- **Account:** `jarvis@hodgespot.com`
- **Master pass env var:** `BW_MASTER_PASS` (in `/c/blunderbus-claude/.env`)

---

## Step 1 — Source credentials and verify bw is installed

```bash
source /c/blunderbus-claude/.env

# Confirm bw is available
if ! command -v bw &>/dev/null; then
  echo "ERROR: Bitwarden CLI (bw) not installed. Install with: npm install -g @bitwarden/cli"
  exit 1
fi
```

---

## Step 2 — Configure server (if not already set)

```bash
bw config server https://vaultwarden.hodgespot.com
```

This is idempotent — safe to run every time.

---

## Step 3 — Login if not authenticated

```bash
STATUS=$(bw status | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

if [ "$STATUS" = "unauthenticated" ]; then
  bw login jarvis@hodgespot.com "$BW_MASTER_PASS" --quiet
fi
```

---

## Step 4 — Unlock vault and capture session token

```bash
export BW_SESSION=$(bw unlock "$BW_MASTER_PASS" --raw)

if [ -z "$BW_SESSION" ]; then
  echo "ERROR: Failed to unlock vault. Check BW_MASTER_PASS in .env"
  exit 1
fi
```

The `BW_SESSION` token is only held in the current shell — it is not written to disk.

---

## Step 5 — Retrieve the credential

To get a password by item name:
```bash
bw get password "ITEM NAME" --session "$BW_SESSION"
```

To get the full item (username, URL, notes, custom fields):
```bash
bw get item "ITEM NAME" --session "$BW_SESSION" | jq '{name: .name, username: .login.username, uris: .login.uris}'
```

To search if the exact name is unknown:
```bash
bw list items --search "keyword" --session "$BW_SESSION" | jq '[.[] | {name: .name, username: .login.username}]'
```

---

## Step 6 — Use the credential inline (never log it)

Assign to a variable and pass directly to the command that needs it:

```bash
SECRET=$(bw get password "Item Name" --session "$BW_SESSION")
curl -s -u "user:$SECRET" https://some-service/api/endpoint
unset SECRET  # clear from shell after use
```

**Never** `echo $SECRET`, log it, or assign it to a variable that gets printed.

---

## Common Vault Item Names

| Item | Used For |
|------|----------|
| `Security Onion` | SOC web login (bh@hodgespot.com) |
| `AdGuard` | AdGuard Home admin |
| `Grafana` | Banner dashboard |
| `Portainer` | Stark container management |
| `TrueNAS` | Heimdall storage admin |
| `Proxmox` | Multiverse hypervisor |

*(Actual item names may vary — use `bw list items` to search if unsure)*

---

## Credential Safety Rules

- Never echo, print, or return raw secrets in conversation output.
- Unset shell variables after use: `unset SECRET`
- The `BW_SESSION` token expires after 15 minutes of inactivity — re-run Step 4 if you get auth errors.
- `BW_MASTER_PASS` lives only in `.env` which is gitignored. Never commit or log it.
