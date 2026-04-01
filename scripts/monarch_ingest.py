"""
Monarch Money → Clickhouse ingest pipeline.

Pulls account balances and transactions from Monarch Money and writes
them to the finance database on Cortex (jarvis-clickhouse).

Usage:
    py scripts/monarch_ingest.py [--days 30]

Environment (from .env):
    MONARCH_TOKEN       - Monarch Money auth token (from Vault)
    CLICKHOUSE_HOST     - Clickhouse host (default: 192.168.50.106)
    CLICKHOUSE_USER     - Clickhouse user (default: clickhouse)
    CLICKHOUSE_PASSWORD - Clickhouse password
"""

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

from runtime import configure_utf8_stdio, env_first

configure_utf8_stdio()

try:
    from monarchmoney import MonarchMoney
except ImportError:
    print("ERROR: pip install monarchmoney")
    sys.exit(1)

try:
    from clickhouse_driver import Client as CHClient
except ImportError:
    print("ERROR: pip install clickhouse-driver")
    sys.exit(1)


def get_ch_client():
    return CHClient(
        host=os.environ.get("CLICKHOUSE_HOST", "192.168.50.106"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "9000")),
        user=os.environ.get("CLICKHOUSE_USER", "clickhouse"),
        password=env_first("CLICKHOUSE_PASSWORD", "CLICKHOUSE_PASS", default="clickhouse"),
        database="finance",
    )


def upsert_accounts(ch, accounts: list):
    # Wipe today's snapshot first so re-runs don't accumulate duplicates
    ch.execute("ALTER TABLE accounts DELETE WHERE snapshot_date = today()")
    today = date.today()
    rows = []
    for a in accounts:
        inst = a.get("institution") or {}
        balance = a.get("currentBalance") or 0.0
        acct_type = a.get("type", {}).get("name", "") if isinstance(a.get("type"), dict) else str(a.get("type", ""))
        is_asset = 1 if balance >= 0 else 0
        rows.append((
            today,
            a["id"],
            a.get("displayName", ""),
            inst.get("name", "Manual") if inst else "Manual",
            acct_type,
            float(balance),
            is_asset,
            datetime.utcnow(),
        ))

    ch.execute(
        """INSERT INTO accounts
           (snapshot_date, account_id, display_name, institution, account_type, balance, is_asset, updated_at)
           VALUES""",
        rows,
    )
    print(f"  Accounts: {len(rows)} records written")


def upsert_transactions(ch, transactions: list):
    rows = []
    for t in transactions:
        cat = t.get("category") or {}
        account = t.get("account") or {}
        inst = account.get("institution") or {}
        merchant = t.get("merchant") or t.get("merchantName") or ""
        if isinstance(merchant, dict):
            merchant = merchant.get("name") or ""
        rows.append((
            str(t["id"]),
            datetime.strptime(t["date"], "%Y-%m-%d").date(),
            float(t.get("amount") or 0.0),
            str(merchant),
            cat.get("name", "") if isinstance(cat, dict) else str(cat),
            str(account.get("id", "")),
            str(account.get("displayName", "")),
            inst.get("name", "") if inst else "",
            str(t.get("notes") or ""),
            1 if t.get("pending") else 0,
            datetime.utcnow(),
        ))

    if rows:
        ch.execute(
            """INSERT INTO transactions
               (id, date, amount, merchant, category, account_id, account_name, institution, notes, is_pending, inserted_at)
               VALUES""",
            rows,
        )
    print(f"  Transactions: {len(rows)} records written")


def upsert_budgets(ch, budget_data: list, month: date):
    rows = []
    for item in budget_data:
        rows.append((
            month.replace(day=1),
            item.get("category", ""),
            float(item.get("budgeted") or 0.0),
            float(item.get("actual") or 0.0),
            datetime.utcnow(),
        ))

    if rows:
        ch.execute(
            """INSERT INTO budgets (month, category, budgeted, actual, updated_at) VALUES""",
            rows,
        )
    print(f"  Budgets: {len(rows)} category rows written")


async def run(days: int):
    token = os.environ.get("MONARCH_TOKEN")
    if not token:
        print("ERROR: MONARCH_TOKEN not set")
        sys.exit(1)

    print("Connecting to Monarch Money...")
    mm = MonarchMoney(token=token, timeout=120)

    print("Connecting to Clickhouse...")
    ch = get_ch_client()

    # --- Accounts ---
    print("\n[1/3] Pulling account balances...")
    acct_data = await mm.get_accounts()
    accounts = acct_data.get("accounts", [])
    upsert_accounts(ch, accounts)

    # Net worth summary
    assets = sum(a.get("currentBalance", 0) or 0 for a in accounts if (a.get("currentBalance") or 0) > 0)
    liabilities = sum(a.get("currentBalance", 0) or 0 for a in accounts if (a.get("currentBalance") or 0) < 0)
    net_worth = assets + liabilities
    print(f"  Net worth: ${net_worth:,.2f}  (assets ${assets:,.2f} / liabilities ${liabilities:,.2f})")

    # --- Transactions (paginated by month to avoid timeouts) ---
    print(f"\n[2/3] Pulling transactions (last {days} days, paginated by month)...")
    all_transactions = []
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    cursor = end_date
    while cursor > start_date:
        chunk_start = max(cursor - timedelta(days=30), start_date)
        chunk_end = cursor
        print(f"  Fetching {chunk_start} → {chunk_end}...")
        tx_data = await mm.get_transactions(
            start_date=chunk_start.isoformat(),
            end_date=chunk_end.isoformat(),
        )
        chunk = tx_data.get("allTransactions", {}).get("results", [])
        all_transactions.extend(chunk)
        cursor = chunk_start - timedelta(days=1)
    upsert_transactions(ch, all_transactions)

    # --- Budget vs Actual ---
    print("\n[3/3] Pulling budget data...")
    today = date.today()
    try:
        budget_data = await mm.get_budgets(
            start_date=today.replace(day=1).isoformat(),
            end_date=today.isoformat(),
        )
        budget_rows = []
        for item in budget_data.get("budgets", []):
            cat = item.get("category") or {}
            budget_rows.append({
                "category": cat.get("name", "") if isinstance(cat, dict) else str(cat),
                "budgeted": item.get("plannedAmount") or 0,
                "actual": item.get("actualAmount") or 0,
            })
        upsert_budgets(ch, budget_rows, today)
    except Exception as e:
        print(f"  Budget pull skipped: {e}")

    # Force merge so ReplacingMergeTree deduplication is immediate
    # (prevents inflated numbers if finance_intel queries right after ingest)
    print("\nOptimizing transactions table...")
    ch.execute("OPTIMIZE TABLE finance.transactions FINAL")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Days of transaction history to pull")
    args = parser.parse_args()
    asyncio.run(run(args.days))
