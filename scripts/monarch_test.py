import asyncio, os, sys
from monarchmoney import MonarchMoney

async def test():
    email = os.environ.get("MONARCH_USER", "")
    password = os.environ.get("MONARCH_PASS", "")
    if not email or not password:
        print("ERROR: MONARCH_USER or MONARCH_PASS not set")
        sys.exit(1)
    mm = MonarchMoney()
    await mm.login(email, password, use_saved_session=False)
    accounts = await mm.get_accounts()
    account_list = accounts.get("accounts", [])
    print(f"Login OK - {len(account_list)} accounts connected")
    for a in account_list:
        inst = a.get("institution", {})
        name = inst.get("name") if inst else "Manual"
        print(f"  {a['displayName']} ({name})")

asyncio.run(test())
