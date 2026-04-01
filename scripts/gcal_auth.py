#!/usr/bin/env python3
"""
gcal_auth.py — One-time Google Calendar OAuth2 setup for BlunderBus.

Run this once interactively to authorize Google Calendar access.
Stores a refresh token in .gcal_token.json which morning_prep.py uses daily.

Steps:
1. Go to https://console.cloud.google.com/
2. Create a project → Enable Google Calendar API
3. Credentials → Create OAuth 2.0 Client ID (Desktop app)
4. Download the JSON → save as scripts/.gcal_client_secret.json
5. Run: python scripts/gcal_auth.py
"""

import io, json, os, sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR  = os.path.dirname(SCRIPT_DIR)
SECRET_FILE  = os.path.join(SCRIPT_DIR, ".gcal_client_secret.json")
TOKEN_FILE   = os.path.join(PROJECT_DIR, ".gcal_token.json")
SCOPES       = ["https://www.googleapis.com/auth/calendar"]

def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
    except ImportError:
        sys.exit("ERROR: Run: pip install google-auth-oauthlib google-api-python-client")

    if not os.path.exists(SECRET_FILE):
        print(f"""
ERROR: Client secret file not found.

Steps to get it:
  1. Go to https://console.cloud.google.com/
  2. Create project → APIs & Services → Library → search "Google Calendar API" → Enable
  3. APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID
  4. Application type: Desktop app → Create
  5. Download JSON → save as:
     {SECRET_FILE}
  6. Re-run this script
""")
        sys.exit(1)

    print("Opening browser for Google Calendar authorization...")
    flow = InstalledAppFlow.from_client_secrets_file(SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"\n✅ Authorization complete. Token saved to:")
    print(f"   {TOKEN_FILE}")
    print(f"\nmorning_prep.py will now pull your calendar automatically.")
    print(f"\nNote: .gcal_token.json is gitignored — keep it safe.")

if __name__ == "__main__":
    main()
