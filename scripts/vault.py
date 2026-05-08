"""
vault.py — BlunderBus Vaultwarden secret loader

Pulls API keys and tokens from Vaultwarden (self-hosted Bitwarden) into
os.environ at runtime, replacing plaintext .env values for sensitive keys.

NOTE: ANTHROPIC_API_KEY is intentionally NOT loaded here.
All AI generation runs via the `claude` CLI (Claude Code), which handles
its own auth. No raw API key is ever needed in scripts.

Vault item → env var mapping:
  homeassistant-token  / token      → HA_LONG_LIVED_TOKEN
  homeassistant-token  / base_url   → HA_URL
  Obsidian API         / Token      → OBSIDIAN_TOKEN
  grafana-api          / api_key    → GRAFANA_API_KEY
  soc.hodgespot.com    / username   → SECONION_USER
  soc.hodgespot.com    / password   → SECONION_PASS
  seconion-api         / base_url   → SECONION_URL
  telegram-bot         / token      → TELEGRAM_BOT_TOKEN
  telegram-bot         / chat_id    → TELEGRAM_CHAT_ID
  truenas-api          / api_key    → TRUENAS_API_KEY
  adguard-api          / username   → ADGUARD_USER
  adguard-api          / password   → ADGUARD_PASS
  adguard-api          / base_url   → ADGUARD_HOST
  loki-endpoint        / base_url   → LOKI_URL
  monarch              / api_token  → MONARCH_TOKEN
  monarch              / username   → MONARCH_USER
  monarch              / password   → MONARCH_PASS
  jarvis-postgres      / password   → BLUNDERBUS_DB_PASSWORD

Non-vault (stays in .env):
  BW_MASTER_PASS, PFSENSE_*, MQTT_*, PORTAINER_*, NPM_*,
  CLICKHOUSE_*, LITELLM_*, GITHUB_TOKEN, OBSIDIAN_URL

Usage:
    from vault import load_secrets
    load_secrets()              # silent — populates os.environ
    load_secrets(verbose=True)  # prints status

    # Or as a script:
    python scripts/vault.py --export      → KEY=VALUE lines for PowerShell eval
    python scripts/vault.py --check       → show which vars were loaded
"""

import json, os, subprocess, sys

from runtime import configure_utf8_stdio, read_env_file

configure_utf8_stdio()

BW_BIN = "bw"

# (vault_item_name, field_name, env_var_name)
VAULT_MAP = [
    ("homeassistant-token",   "token",    "HA_LONG_LIVED_TOKEN"),
    ("homeassistant-token",   "base_url", "HA_URL"),
    ("Obsidian API",          "Token",    "OBSIDIAN_TOKEN"),
    ("grafana-api",           "api_key",  "GRAFANA_TOKEN"),
    ("soc.hodgespot.com",     "username", "SECONION_USER"),
    ("soc.hodgespot.com",     "password", "SECONION_PASS"),
    ("seconion-api",          "base_url", "SECONION_URL"),
    ("telegram-bot",          "token",    "TELEGRAM_BOT_TOKEN"),
    ("telegram-bot",          "chat_id",  "TELEGRAM_CHAT_ID"),
    ("truenas-api",           "api_key",  "TRUENAS_API_KEY"),
    ("adguard-api",           "username", "ADGUARD_USER"),
    ("adguard-api",           "password", "ADGUARD_PASS"),
    ("adguard-api",           "base_url", "ADGUARD_HOST"),
    ("loki-endpoint",         "base_url", "LOKI_URL"),
    ("monarch",               "api_token","MONARCH_TOKEN"),
    ("monarch",               "username", "MONARCH_USER"),
    ("monarch",               "password", "MONARCH_PASS"),
    # Login-type item — vault.py reads `login.password` when field_name is "password"
    # and the item has no custom field of that name.
    ("jarvis-postgres",       "password", "BLUNDERBUS_DB_PASSWORD"),
]


def _unlock():
    """Return a BW session token, reusing BW_SESSION if already set."""
    if os.environ.get("BW_SESSION"):
        return os.environ["BW_SESSION"]
    master = os.environ.get("BW_MASTER_PASS", "")
    if not master:
        return None
    r = subprocess.run(
        [BW_BIN, "unlock", "--passwordenv", "BW_MASTER_PASS", "--raw"],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "BW_MASTER_PASS": master},
    )
    if r.returncode == 0:
        token = r.stdout.strip()
        os.environ["BW_SESSION"] = token
        return token
    return None


def _fetch_all(session):
    """Return all vault items as a list of dicts."""
    r = subprocess.run(
        [BW_BIN, "list", "items"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "BW_SESSION": session},
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except Exception:
        return []


def load_secrets(verbose=False):
    """
    Load vault secrets into os.environ.
    Returns dict of {env_var: source_item} for loaded vars, or {} on failure.
    Falls back silently if vault is unavailable — .env values remain in place.
    """
    session = _unlock()
    if not session:
        if verbose:
            print("⚠️  Vault: could not unlock (BW_MASTER_PASS not set or bw unavailable)")
        return {}

    items = _fetch_all(session)
    if not items:
        if verbose:
            print("⚠️  Vault: no items returned from bw list")
        return {}

    # Build lookup by lowercase name
    by_name = {}
    for item in items:
        by_name[item["name"].lower()] = item

    loaded = {}
    for item_name, field_name, env_var in VAULT_MAP:
        item = by_name.get(item_name.lower())
        if not item:
            continue

        # Search custom fields first
        value = None
        for f in item.get("fields", []):
            if f.get("name", "").lower() == field_name.lower():
                value = f.get("value", "") or ""
                break

        # Fall back to login.username / login.password for login-type items
        if value is None and item.get("login"):
            if field_name.lower() in ("username", "user"):
                value = item["login"].get("username", "")
            elif field_name.lower() in ("password", "pass"):
                value = item["login"].get("password", "")

        if value:
            os.environ[env_var] = value
            if env_var == "GRAFANA_TOKEN":
                os.environ["GRAFANA_API_KEY"] = value
            loaded[env_var] = item_name

    if verbose:
        print(f"✅ Vault: {len(loaded)} secret(s) loaded")
        for var, src in sorted(loaded.items()):
            print(f"   {var} ← {src}")

    return loaded


# ─── CLI mode ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, re

    # Load .env so BW_MASTER_PASS is available
    for key, value in read_env_file().items():
        os.environ.setdefault(key, value)

    parser = argparse.ArgumentParser(description="BlunderBus Vaultwarden loader")
    parser.add_argument("--export", action="store_true",
                        help="Print KEY=VALUE lines for PowerShell $env: assignment")
    parser.add_argument("--check",  action="store_true",
                        help="Print which vars were loaded and their sources")
    args = parser.parse_args()

    loaded = load_secrets(verbose=args.check)

    if args.export:
        for var in sorted(loaded.keys()):
            val = os.environ.get(var, "")
            # Escape single quotes for PowerShell
            val_esc = val.replace("'", "''")
            print(f"{var}={val_esc}")

    if not args.export and not args.check:
        print(f"Loaded {len(loaded)} secret(s) from Vaultwarden.")
        print("Use --export for PowerShell KEY=VALUE output, --check for details.")
