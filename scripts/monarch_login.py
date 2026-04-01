"""
One-time interactive login to Monarch Money.
Run this manually in a terminal to authenticate and save a session file.
Subsequent automated scripts use the saved session — no re-login needed.

Usage:
    py scripts/monarch_login.py
"""

import asyncio
import os
import sys
from pathlib import Path

try:
    from monarchmoney import MonarchMoney
    from monarchmoney.monarchmoney import RequireMFAException
except ImportError:
    print("ERROR: monarchmoney not installed. Run: pip install monarchmoney")
    sys.exit(1)

SESSION_FILE = Path(__file__).parent.parent / ".monarch_session"


async def main():
    email = os.environ.get("MONARCH_USER")
    password = os.environ.get("MONARCH_PASS")

    if not email or not password:
        print("ERROR: Set MONARCH_USER and MONARCH_PASS environment variables.")
        print("  These are loaded from .env — make sure it's sourced.")
        sys.exit(1)

    mm = MonarchMoney(session_file=str(SESSION_FILE))

    print(f"Logging in as {email}...")
    try:
        await mm.login(email, password, use_saved_session=False)
    except RequireMFAException:
        print("\nMonarch sent a verification code to your email.")
        code = input("Enter code: ").strip()
        await mm.multi_factor_authenticate(email, password, code)
        mm.save_session(str(SESSION_FILE))

    # Verify it worked
    accounts = await mm.get_accounts()
    account_list = accounts.get("accounts", [])
    print(f"\n✅ Login successful — {len(account_list)} accounts connected:")
    for a in account_list:
        print(f"   {a['displayName']} ({a['institution']['name'] if a.get('institution') else 'Manual'}) — ${a['currentBalance']:,.2f}")

    print(f"\nSession saved to {SESSION_FILE}")
    print("You won't need to log in again unless the session expires.")


if __name__ == "__main__":
    asyncio.run(main())
