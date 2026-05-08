"""
One-time login to Monarch Money. Saves a session file that all
automated scripts reuse — no re-login needed until session expires.

Usage:
    py scripts/monarch_login.py                  # interactive (TTY) — prompts for MFA code
    py scripts/monarch_login.py --mfa-code 123456 # non-interactive — supply code from CLI

Two-phase non-interactive flow (cron-friendly):
    1. Run with no args → Monarch sends the email code, script exits with MFA_REQUIRED.
    2. Run again with --mfa-code <code> → completes auth and saves the session.
"""

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--mfa-code", help="MFA code from email (skips interactive prompt)")
    args = parser.parse_args()

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
        if args.mfa_code:
            code = args.mfa_code.strip()
        elif sys.stdin.isatty():
            print("\nMonarch sent a verification code to your email.")
            code = input("Enter code: ").strip()
        else:
            print("\nMFA_REQUIRED: Monarch sent a verification code to your email.")
            print("Re-run with: py scripts/monarch_login.py --mfa-code <code>")
            sys.exit(2)
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
