#!/usr/bin/env python3
"""
Google Fit OAuth2 — localhost redirect flow.
Starts a local server on port 8765, opens the browser, catches the code,
exchanges it for tokens, and saves the refresh token to Vaultwarden.

Usage:
  python scripts/google-fit-auth-local.py

Prerequisites:
  - bw CLI installed and configured to vaultwarden.hodgespot.com
  - blunderbus_fit vault item has Client ID and Client_secret fields
  - http://localhost:8765 added as authorized redirect URI in Google Cloud Console
  - BW_MASTER_PASS set in /c/blunderbus-claude/.env
"""

import io, json, os, sys, subprocess, urllib.parse, urllib.request, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PORT       = 8765
REDIRECT   = f"http://localhost:{PORT}"
TOKEN_URL  = "https://oauth2.googleapis.com/token"
AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"

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

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization successful! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Error: no code received.</h2>")

    def log_message(self, format, *args):
        pass  # suppress server logs


def get_vault_credentials():
    env_file = "/c/blunderbus-claude/.env"
    if not os.path.exists(env_file):
        env_file = r"C:\blunderbus-claude\.env"

    # Source env to get BW_MASTER_PASS
    result = subprocess.run(
        ["bash", "-c", f"source {env_file} && echo $BW_MASTER_PASS"],
        capture_output=True, text=True
    )
    bw_pass = result.stdout.strip()
    if not bw_pass:
        sys.exit("ERROR: Could not read BW_MASTER_PASS from .env")

    subprocess.run(["bw", "config", "server", "https://vaultwarden.hodgespot.com"],
                   capture_output=True)

    session_result = subprocess.run(
        ["bw", "unlock", bw_pass, "--raw"],
        capture_output=True, text=True
    )
    session = session_result.stdout.strip()
    if not session:
        sys.exit("ERROR: Failed to unlock vault")

    subprocess.run(["bw", "sync", "--session", session], capture_output=True)

    item_result = subprocess.run(
        ["bw", "get", "item", "blunderbus_fit", "--session", session],
        capture_output=True, text=True
    )
    item = json.loads(item_result.stdout)
    fields = {f["name"]: f["value"] for f in item.get("fields", [])}

    client_id     = fields.get("Client ID") or fields.get("client_id")
    client_secret = fields.get("Client_secret") or fields.get("client_secret")

    if not client_id or not client_secret:
        sys.exit(f"ERROR: Missing Client ID or Client_secret in vault. Found fields: {list(fields.keys())}")

    return client_id, client_secret, session, item["id"]


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


def save_refresh_token_to_vault(session, item_id, refresh_token):
    """Add refresh_token as a hidden field to the vault item."""
    # Get current item JSON
    item_result = subprocess.run(
        ["bw", "get", "item", "blunderbus_fit", "--session", session],
        capture_output=True, text=True
    )
    item = json.loads(item_result.stdout)

    # Remove any existing refresh_token field, then add new one
    fields = [f for f in item.get("fields", []) if f.get("name", "").lower() != "refresh_token"]
    fields.append({"name": "refresh_token", "value": refresh_token, "type": 1})  # type 1 = hidden
    item["fields"] = fields

    # Encode and save
    encoded = subprocess.run(
        ["bw", "encode"],
        input=json.dumps(item), capture_output=True, text=True
    ).stdout.strip()

    result = subprocess.run(
        ["bw", "edit", "item", item_id, "--session", session],
        input=encoded, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"WARNING: vault save failed: {result.stderr}")
        return False
    return True


def main():
    global auth_code

    print("Fetching credentials from vault...")
    client_id, client_secret, session, item_id = get_vault_credentials()
    print(f"Client ID: {client_id[:20]}...")

    # Build auth URL
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT,
        "response_type": "code",
        "scope":         SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    # Start local callback server
    server = HTTPServer(("localhost", PORT), CallbackHandler)
    t = Thread(target=server.handle_request)
    t.daemon = True
    t.start()

    print(f"\nOpening browser for Google authorization...")
    print(f"If it doesn't open, visit:\n{url}\n")
    webbrowser.open(url)

    print("Waiting for Google to redirect back...")
    t.join(timeout=120)
    server.server_close()

    if not auth_code:
        sys.exit("ERROR: No authorization code received within 2 minutes.")

    print("Code received. Exchanging for tokens...")
    try:
        tokens = exchange_code(client_id, client_secret, auth_code)
    except Exception as e:
        sys.exit(f"Token exchange failed: {e}")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        sys.exit(f"No refresh token in response: {tokens}")

    print(f"Refresh token obtained ({len(refresh_token)} chars). Saving to vault...")
    if save_refresh_token_to_vault(session, item_id, refresh_token):
        print("Saved to vault as 'refresh_token' (hidden field) on blunderbus_fit.")
    else:
        print(f"\nVault save failed — refresh token length: {len(refresh_token)} chars")
        print("Save it manually to blunderbus_fit → refresh_token field.")

    print("\nDone. Google Fit ingestion is ready to run.")


if __name__ == "__main__":
    main()
