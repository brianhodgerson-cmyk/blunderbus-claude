#!/usr/bin/env python3
"""
Google Fit OAuth2 Setup — one-time authentication flow.
Run this once to get a refresh token, then store it in Vaultwarden.

Usage:
  python google-fit-auth.py --client-id <id> --client-secret <secret>

  Or set env vars:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET

Steps:
  1. Go to https://console.cloud.google.com
  2. Create project → Enable "Fitness API"
  3. Credentials → OAuth 2.0 Client ID → Desktop App
  4. Copy Client ID and Client Secret
  5. Run this script → opens browser → paste the code back
  6. Save the refresh token to vault as "google-fit" item
"""

import io, json, os, sys, urllib.parse, urllib.request, webbrowser

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCOPES = " ".join([
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.blood_pressure.read",
    "https://www.googleapis.com/auth/fitness.oxygen_saturation.read",
    "https://www.googleapis.com/auth/fitness.body_temperature.read",
    "https://www.googleapis.com/auth/fitness.nutrition.read",
])

AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL   = "https://oauth2.googleapis.com/token"
REDIRECT    = "urn:ietf:wg:oauth:2.0:oob"  # copy/paste flow — no local server needed


def get_auth_url(client_id):
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",  # forces refresh token even if previously authorized
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(client_id, client_secret, code):
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--client-id",     default=os.environ.get("GOOGLE_CLIENT_ID"))
    p.add_argument("--client-secret", default=os.environ.get("GOOGLE_CLIENT_SECRET"))
    args = p.parse_args()

    if not args.client_id or not args.client_secret:
        sys.exit(
            "Need --client-id and --client-secret (or GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).\n\n"
            "Get them from: https://console.cloud.google.com\n"
            "  Project → APIs & Services → Credentials → Create OAuth2 Client ID (Desktop App)\n"
        )

    url = get_auth_url(args.client_id)

    print("\nOpening browser for Google authorization...")
    print("If it doesn't open, visit this URL manually:\n")
    print(url)
    print()
    webbrowser.open(url)

    code = input("Paste the authorization code from the browser: ").strip()
    if not code:
        sys.exit("No code entered.")

    print("\nExchanging code for tokens...")
    try:
        tokens = exchange_code(args.client_id, args.client_secret, code)
    except Exception as e:
        sys.exit(f"Token exchange failed: {e}")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        sys.exit(f"No refresh token in response: {tokens}")

    print("\n--- SAVE THESE TO VAULTWARDEN ---")
    print(f"Vault item name : google-fit")
    print(f"Fields to store :")
    print(f"  client_id     : {args.client_id}")
    print(f"  client_secret : (your secret — do not display)")
    print(f"  refresh_token : (your token — do not display)")
    print()
    print("Run now to save:")
    print("  /vault-get  →  create item 'google-fit' with fields:")
    print("    client_id, client_secret, refresh_token")
    print()
    print("Refresh token obtained successfully.")
    print("Length:", len(refresh_token), "chars")

    # Optionally write to a temp file for the vault-save step
    out = {
        "client_id": args.client_id,
        "refresh_token_length": len(refresh_token),
        "scopes": SCOPES.split(),
        "note": "Refresh token not saved to file — store manually in vault"
    }
    print("\nToken info (no secret values):")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
