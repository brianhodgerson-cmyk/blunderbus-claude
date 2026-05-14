"""
Monarch Money → Clickhouse ingest pipeline.

Pulls account balances and transactions from Monarch Money and writes
them to the finance database on Cortex (jarvis-clickhouse).

Usage:
    py scripts/monarch_ingest.py [--days 30]

Auth precedence:
    1. Saved session file (.monarch_session at repo root) — written by monarch_login.py
    2. MONARCH_TOKEN env var (legacy fallback)

Environment (from .env):
    MONARCH_TOKEN       - Monarch Money auth token (legacy; prefer session file)
    CLICKHOUSE_HOST     - Clickhouse host (default: 192.168.50.106)
    CLICKHOUSE_USER     - Clickhouse user (default: clickhouse)
    CLICKHOUSE_PASSWORD - Clickhouse password
"""

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SESSION_FILE = Path(__file__).parent.parent / ".monarch_session"

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


def upsert_transactions(ch, transactions: list, *, dry_run: bool = False):
    """Insert transactions, skipping rows that are re-IDs of existing ones.

    Why this exists:
        Monarch's overnight pulls occasionally re-pull the same transaction
        with a fresh `id` — either sequential (+1, same batch) or a re-issued
        id ~10^14 apart (the modern api.monarch.com id-space change). Since
        the table is ReplacingMergeTree(inserted_at) ORDERED BY id, different
        ids never merge — so we'd accumulate phantom spend.

    Dedup heuristic:
        Group existing rows by signature (date, merchant, abs(amount),
        account_id, is_pending). For each incoming row, build the same
        signature. If a row with that signature already exists with a
        DIFFERENT id, skip the incoming row (it's a re-ID). The pending-vs-
        cleared bit is part of the signature so that the legitimate
        pending → cleared transition (different is_pending) still inserts
        the cleared row.

    Real twins (two genuine same-merchant-same-amount-same-day-same-account
    transactions with same pending status) would be skipped — but the
    historical 120-day scan on 2026-05-14 found ZERO real twins; every
    signature dup was a re-ID. If a real twin shows up, the operator can
    catch it via the `signature_count` column on `finance.transactions_deduped`
    and we can revisit.

    See: memory/finance/learnings.md "Systemic data-quality fixes (2026-05-14)".
    """
    # ── Normalise incoming rows ──────────────────────────────────────────
    parsed = []
    for t in transactions:
        cat = t.get("category") or {}
        account = t.get("account") or {}
        inst = account.get("institution") or {}
        merchant = t.get("merchant") or t.get("merchantName") or ""
        if isinstance(merchant, dict):
            merchant = merchant.get("name") or ""
        tx_id    = str(t["id"])
        tx_date  = datetime.strptime(t["date"], "%Y-%m-%d").date()
        tx_amt   = float(t.get("amount") or 0.0)
        tx_acct  = str(account.get("id", ""))
        tx_pend  = 1 if t.get("pending") else 0
        signature = (tx_date.isoformat(), str(merchant), round(abs(tx_amt), 2), tx_acct, tx_pend)
        parsed.append({
            "id": tx_id,
            "row": (
                tx_id, tx_date, tx_amt, str(merchant),
                cat.get("name", "") if isinstance(cat, dict) else str(cat),
                tx_acct, str(account.get("displayName", "")),
                inst.get("name", "") if inst else "",
                str(t.get("notes") or ""),
                tx_pend, datetime.utcnow(),
            ),
            "signature": signature,
        })

    if not parsed:
        print("  Transactions: 0 records (no incoming rows)")
        return

    # ── Build existing-signature index (covers the ingest date window) ───
    # Only signatures whose date intersects the incoming batch matter.
    min_date = min(p["row"][1] for p in parsed)
    max_date = max(p["row"][1] for p in parsed)
    existing = ch.execute(
        f"""
        SELECT
            toString(date) AS d,
            merchant,
            round(abs(amount), 2) AS abs_amt,
            account_id,
            is_pending,
            id
        FROM finance.transactions FINAL
        WHERE date >= toDate(%(min_date)s) AND date <= toDate(%(max_date)s)
        """,
        {"min_date": min_date.isoformat(), "max_date": max_date.isoformat()},
    )
    # signature -> set of existing ids with that signature
    sig_index: dict[tuple, set[str]] = {}
    for d, merch, abs_amt, acct, pend, ex_id in existing:
        sig = (d, merch, float(abs_amt), acct, int(pend))
        sig_index.setdefault(sig, set()).add(ex_id)

    # ── Filter incoming: skip if signature exists with a different id ────
    to_insert: list[tuple] = []
    skipped: list[dict] = []
    for p in parsed:
        sig = p["signature"]
        existing_ids = sig_index.get(sig, set())
        # Same signature exists AND incoming id is not one of the existing ids
        if existing_ids and p["id"] not in existing_ids:
            skipped.append({
                "id": p["id"],
                "signature": sig,
                "collides_with": sorted(existing_ids),
            })
            continue
        to_insert.append(p["row"])
        # Treat it as now-existing so a same-batch sequential re-ID also skips
        sig_index.setdefault(sig, set()).add(p["id"])

    if skipped:
        print(f"  Dedup: skipped {len(skipped)} re-ID candidate(s) (same signature, different id already present)")
        for s in skipped[:10]:  # cap log spam
            d, merch, amt, _, pend = s["signature"]
            print(f"    SKIP id={s['id']} sig=({d} {merch!r} ${amt:.2f} pending={pend}) "
                  f"collides_with={s['collides_with'][:2]}")
        if len(skipped) > 10:
            print(f"    ... and {len(skipped) - 10} more skipped (full list omitted)")

    if dry_run:
        print(f"  [DRY RUN] Would insert {len(to_insert)} rows ({len(parsed)} incoming, {len(skipped)} skipped)")
        return

    if to_insert:
        ch.execute(
            """INSERT INTO transactions
               (id, date, amount, merchant, category, account_id, account_name, institution, notes, is_pending, inserted_at)
               VALUES""",
            to_insert,
        )
    print(f"  Transactions: {len(to_insert)} records written ({len(skipped)} skipped as re-ID dupes)")


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


def _mm_from_cookies(session_id: str, csrftoken: str, device_uuid: str | None = None) -> "MonarchMoney":
    """Build a MonarchMoney client authenticated via web-session cookies.

    Modern Monarch web auth uses session cookies + an x-csrftoken header instead
    of the legacy /login Token flow. The library only knows about the legacy
    path, but its `_headers` dict is injected straight into the AIOHTTPTransport
    — so populating it ourselves and skipping login() works perfectly. This is
    the route we want long-term: it doesn't hit /login (no 429s) and mirrors
    exactly what the browser does.

    Additional gotcha: the library's hardcoded BASE_URL is the legacy
    `api.monarchmoney.com` host, but the modern web actually talks to
    `api.monarch.com` (rebranded). Our cookies are scoped to `.monarch.com`,
    so we have to patch the endpoint class to the new host or the requests
    come back 401.
    """
    from monarchmoney.monarchmoney import MonarchMoneyEndpoints
    MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

    mm = MonarchMoney(timeout=120)
    mm._headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Client-Platform": "web",
        "Content-Type": "application/json",
        "Cookie": f"session_id={session_id}; csrftoken={csrftoken}",
        "x-csrftoken": csrftoken,
        "Origin": "https://app.monarch.com",
        "Referer": "https://app.monarch.com/",
        "monarch-client": "monarch-core-web-app-graphql",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    }
    if device_uuid:
        mm._headers["device-uuid"] = device_uuid
    return mm


async def run(days: int, *, dry_run: bool = False):
    sid       = os.environ.get("MONARCH_SESSION_ID")
    csrf      = os.environ.get("MONARCH_CSRFTOKEN")
    dev_uuid  = os.environ.get("MONARCH_DEVICE_UUID")
    token     = os.environ.get("MONARCH_TOKEN")

    # Auth precedence: cookies (current) > session file (legacy) > token (oldest fallback)
    if sid and csrf:
        print("Connecting to Monarch Money (web-session cookies)...")
        mm = _mm_from_cookies(sid, csrf, dev_uuid)
    elif SESSION_FILE.exists():
        print(f"Connecting to Monarch Money (session file: {SESSION_FILE.name})...")
        mm = MonarchMoney(session_file=str(SESSION_FILE), timeout=120)
        mm.load_session(str(SESSION_FILE))
    elif token:
        print("Connecting to Monarch Money (MONARCH_TOKEN — legacy)...")
        mm = MonarchMoney(token=token, timeout=120)
    else:
        print(f"ERROR: no auth available. Provide one of:")
        print(f"       1. MONARCH_SESSION_ID + MONARCH_CSRFTOKEN env vars (preferred — set via Bitwarden 'monarch' item)")
        print(f"       2. {SESSION_FILE} (legacy — created by scripts/monarch_login.py)")
        print(f"       3. MONARCH_TOKEN env var (legacy — Token-auth)")
        sys.exit(1)

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
    upsert_transactions(ch, all_transactions, dry_run=dry_run)

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
        if dry_run:
            print(f"  [DRY RUN] Would write {len(budget_rows)} budget rows")
        else:
            upsert_budgets(ch, budget_rows, today)
    except Exception as e:
        print(f"  Budget pull skipped: {e}")

    if dry_run:
        print("\n[DRY RUN] Skipping OPTIMIZE TABLE.")
    else:
        # Force merge so ReplacingMergeTree deduplication is immediate
        # (prevents inflated numbers if finance_intel queries right after ingest)
        print("\nOptimizing transactions table...")
        ch.execute("OPTIMIZE TABLE finance.transactions FINAL")
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Days of transaction history to pull")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and analyze but don't write to ClickHouse "
                             "(useful for validating the dedup logic)")
    args = parser.parse_args()
    asyncio.run(run(args.days, dry_run=args.dry_run))
