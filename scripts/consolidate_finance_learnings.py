#!/usr/bin/env python3
"""
Finance learnings consolidator (subagent memory refresh).

Runs daily 5:50 AM. Two outputs:
  1. memory/finance/baselines.md   — per-category 12-month baseline (mean/p50/p90)
  2. memory/finance/learnings.md   — finance-section patterns over last N daily notes

Both files are atomically rewritten with .bak rollback. Skipped quietly if
ClickHouse is unreachable (so 5:50 AM cron never fails the morning pipeline).

Usage:
    py scripts/consolidate_finance_learnings.py
    py scripts/consolidate_finance_learnings.py --dry-run
    py scripts/consolidate_finance_learnings.py --days 14
"""
from __future__ import annotations
import argparse
import io
import os
import re
import shutil
import socket
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# UTF-8 stdout for Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "Daily"
MEMORY_DIR = ROOT / "memory" / "finance"
BASELINES_FILE = MEMORY_DIR / "baselines.md"
LEARNINGS_FILE = MEMORY_DIR / "learnings.md"

EXCLUDE_CATEGORIES = ("Transfer", "Credit Card Payment", "Check", "Reimbursement")

# Short-history merchant guard: drop (merchant, category) pairs that have
# fewer than N distinct months of consistent assignment over the last 12
# months. Mirrors `finance_intel.get_unstable_merchant_categories`. The two
# scripts must stay in sync — both produce baselines and both must skip
# the same drifting merchants, else the anomaly detector and the
# baselines.md panel disagree on which categories are stable. See that
# function's docstring for the three-condition rationale.
SHORT_HISTORY_MIN_MONTHS = 6
SHORT_HISTORY_LOOKBACK_DAYS = 365
SHORT_HISTORY_DOMINANCE_PCT = 0.60
SHORT_HISTORY_SMALL_TXN_MEDIAN = 100.0

# ── ClickHouse access (via SSH to cortex) ────────────────────────────────────

def ch_query(sql: str) -> list[list[str]]:
    """Run a ClickHouse query via ssh→docker and return tab-separated rows.
    Returns [] on any failure (including unreachable ClickHouse) — caller should
    treat empty result as 'unable to refresh' rather than 'no data'."""
    import shlex
    ch_user = os.environ.get("CLICKHOUSE_USER", "clickhouse")
    ch_pass = os.environ.get("CLICKHOUSE_PASS") or os.environ.get("CLICKHOUSE_PASSWORD", "")
    cmd = [
        "ssh", "-o", "ConnectTimeout=10", "cortex",
        "docker exec jarvis-clickhouse clickhouse-client "
        f"--user {shlex.quote(ch_user)} --password {shlex.quote(ch_pass)} "
        f"--query \"{sql}\""
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8")
        if r.returncode != 0:
            print(f"  ClickHouse query failed: {r.stderr[:200]}", file=sys.stderr)
            return []
        return [line.split("\t") for line in r.stdout.strip().splitlines() if line.strip()]
    except Exception as e:
        print(f"  ClickHouse unreachable: {e}", file=sys.stderr)
        return []


# ── Baseline refresh ─────────────────────────────────────────────────────────

def _get_unstable_excl_sql() -> str:
    """Pull unstable (merchant, category) pairs and build a SQL
    ' AND NOT (...)' fragment for refresh_baselines to layer onto the
    existing filters. Returns '' if ClickHouse unreachable — degrades to
    pre-guard behavior rather than crashing.

    A pair (M, C) is unstable IFF (mirrors finance_intel.get_unstable_merchant_categories):
      1. Merchant M has ≥2 distinct in-scope categories AND
         no single category covers ≥SHORT_HISTORY_DOMINANCE_PCT of M's months
      2. The pair (M, C) has < SHORT_HISTORY_MIN_MONTHS consistent months
      3. The pair's median txn amount is < SHORT_HISTORY_SMALL_TXN_MEDIAN
    """
    excl_clause = ", ".join(f"'{c}'" for c in EXCLUDE_CATEGORIES)
    sql = f"""
        WITH merchant_profile AS (
          SELECT merchant,
                 count(DISTINCT category) AS n_cats,
                 count(DISTINCT toStartOfMonth(date)) AS total_months
          FROM finance.transactions_deduped
          WHERE date >= today() - {SHORT_HISTORY_LOOKBACK_DAYS}
            AND amount < 0
            AND category NOT IN ({excl_clause})
            AND is_pending = 0
            AND merchant != ''
          GROUP BY merchant
        ),
        pair_profile AS (
          SELECT merchant,
                 category,
                 count(DISTINCT toStartOfMonth(date)) AS pair_months,
                 quantile(0.5)(abs(amount)) AS median_amt
          FROM finance.transactions_deduped
          WHERE date >= today() - {SHORT_HISTORY_LOOKBACK_DAYS}
            AND amount < 0
            AND category NOT IN ({excl_clause})
            AND is_pending = 0
            AND merchant != ''
          GROUP BY merchant, category
        )
        SELECT p.merchant, p.category
        FROM pair_profile p
        INNER JOIN merchant_profile m USING (merchant)
        WHERE m.n_cats >= 2
          AND (p.pair_months / m.total_months) < {SHORT_HISTORY_DOMINANCE_PCT}
          AND p.pair_months < {SHORT_HISTORY_MIN_MONTHS}
          AND p.median_amt < {SHORT_HISTORY_SMALL_TXN_MEDIAN}
    """.strip()
    rows = ch_query(sql)
    if not rows:
        return ""
    terms = []
    for r in rows:
        if len(r) < 2:
            continue
        m = (r[0] or "").replace("'", "''")
        c = (r[1] or "").replace("'", "''")
        terms.append(f"(merchant = '{m}' AND category = '{c}')")
    if not terms:
        return ""
    return " AND NOT (" + " OR ".join(terms) + ")"


def refresh_baselines():
    """Rewrite memory/finance/baselines.md from 12 months of transaction data.

    Applies the short-history merchant guard so that drifting
    (merchant, category) pairs (e.g. OpenAI bouncing between Misc and
    Internet & Cable) don't pollute the baseline math here OR in the
    anomaly detector. See finance_intel.get_unstable_merchant_categories.
    """
    excl_clause = ", ".join(f"'{c}'" for c in EXCLUDE_CATEGORIES)
    unstable_clause = _get_unstable_excl_sql()
    sql = f"""
        SELECT category,
               round(avg(monthly_spend), 0),
               round(quantile(0.5)(monthly_spend), 0),
               round(quantile(0.9)(monthly_spend), 0),
               count() AS months_seen
        FROM (
          SELECT toStartOfMonth(date) AS month,
                 category,
                 sum(abs(amount)) AS monthly_spend
          FROM finance.transactions_deduped
          WHERE date >= today() - 365 AND date < toStartOfMonth(today())
            AND amount < 0
            AND category NOT IN ({excl_clause})
            AND is_pending = 0
            {unstable_clause}
          GROUP BY month, category
        )
        GROUP BY category
        HAVING months_seen >= 2
        ORDER BY 2 DESC
    """.strip()
    rows = ch_query(sql)
    if not rows:
        print("  baselines: no data (ClickHouse unreachable or empty table)")
        return None

    # Also pull current month for comparison
    current_sql = f"""
        SELECT category, round(sum(abs(amount)), 0)
        FROM finance.transactions_deduped
        WHERE date >= toStartOfMonth(today())
          AND amount < 0
          AND category NOT IN ({excl_clause})
          AND is_pending = 0
          {unstable_clause}
        GROUP BY category
    """.strip()
    current_rows = ch_query(current_sql)
    current = {r[0]: float(r[1]) for r in current_rows if len(r) >= 2}

    today_iso = date.today().isoformat()
    md = [
        "# Category Baselines (auto-generated)",
        "",
        f"_Last refreshed: {today_iso} · 12-month rolling window · excludes {', '.join(EXCLUDE_CATEGORIES)}_",
        "",
        "| Category | 12-mo avg | P50 | P90 | Current MTD | vs avg | Months |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if len(r) < 5:
            continue
        cat, avg, p50, p90, months = r[0], float(r[1]), float(r[2]), float(r[3]), int(r[4])
        cur = current.get(cat, 0.0)
        if avg > 0:
            ratio = cur / avg
            if ratio >= 2.0:
                vs = f"🔴 {ratio:.1f}x"
            elif ratio >= 1.3:
                vs = f"🟡 {ratio:.1f}x"
            elif ratio <= 0.5 and cur > 0:
                vs = f"🟢 {ratio:.1f}x"
            else:
                vs = f"{ratio:.1f}x"
        else:
            vs = "—"
        md.append(f"| {cat} | ${avg:,.0f} | ${p50:,.0f} | ${p90:,.0f} | ${cur:,.0f} | {vs} | {months} |")

    md.append("")
    md.append("## How to use")
    md.append("")
    md.append("- 🔴 = current MTD ≥ 2x baseline → likely real anomaly OR a known recurring hit (check `recurring.md`)")
    md.append("- 🟡 = 1.3-2x → watch")
    md.append("- 🟢 = ≤0.5x with non-zero spend → underspending vs typical")
    md.append("- Only categories with ≥2 months of history are baselined.")
    md.append("- **Always check `recurring.md` before alarming** — annual hits will look anomalous in their renewal month.")
    return "\n".join(md)


# ── Daily-note finance pattern extraction ────────────────────────────────────

def parse_finance_section(note_path: Path) -> list[dict]:
    """Pull observations from the ## Finance section of a daily note."""
    obs = []
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return obs

    # Extract ## Finance section
    m = re.search(r"^## Finance.*?$(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return obs
    fin = m.group(1)

    note_date = note_path.stem
    patterns = [
        (r"Net worth\s+\$?([\d,]+(?:\.\d+)?)", "net-worth"),
        (r"Pace[^\n]*?(\d+%)", "pace"),
        (r"Savings rate[^\n]*?(\d+\.?\d*%)", "savings-rate"),
        (r"🚩\s+(.+)", "flag"),
        (r"⚠️[^\n]*FLAG:?\s+(.+)", "flag"),
        (r"PERSISTENT:?\s+(.+)", "persistent"),
        (r"🔴[^\n]*?(\$[\d,]+)", "red"),
    ]
    for pat, cat in patterns:
        for m2 in re.finditer(pat, fin, flags=re.IGNORECASE):
            obs.append({
                "date": note_date,
                "category": cat,
                "raw": m2.group(0)[:200].strip(),
                "value": m2.group(1).strip() if m2.lastindex else "",
            })
    return obs


def refresh_learnings(days: int):
    notes = sorted(DAILY_DIR.glob("????-??-??.md"), reverse=True)[:days]
    if not notes:
        return None

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for n in notes:
        for o in parse_finance_section(n):
            by_cat[o["category"]].append(o)

    today_iso = date.today().isoformat()
    md = [
        "# Finance Learnings (auto-consolidated)",
        "",
        f"_Last consolidated: {today_iso} · scanned {len(notes)} daily note(s) · window {notes[-1].stem} → {notes[0].stem}_",
        "",
    ]

    # Net worth trend
    nw = sorted([(o["date"], o["value"]) for o in by_cat.get("net-worth", [])])
    if len(nw) >= 2:
        try:
            first = float(nw[0][1].replace(",", ""))
            last = float(nw[-1][1].replace(",", ""))
            delta = last - first
            arrow = "📈" if delta > 0 else "📉" if delta < 0 else "➡️"
            md.append(f"## Net worth trajectory")
            md.append("")
            md.append(f"- {arrow} **${first:,.0f}** ({nw[0][0]}) → **${last:,.0f}** ({nw[-1][0]})  "
                      f"= **{'+' if delta >= 0 else ''}${delta:,.0f}** over {len(nw)} observations")
            md.append("")
        except ValueError:
            pass

    # Recurring flags (same flag in 3+ days = persistent)
    flag_sigs: dict[str, list[str]] = defaultdict(list)
    for o in by_cat.get("flag", []) + by_cat.get("persistent", []):
        sig = re.sub(r"\d+", "<n>", o["raw"]).lower()[:120]
        flag_sigs[sig].append(o["date"])

    persistent = [(sig, sorted(set(dates))) for sig, dates in flag_sigs.items() if len(set(dates)) >= 3]
    if persistent:
        md.append("## Persistent flags (3+ days)")
        md.append("")
        for sig, dates in sorted(persistent, key=lambda x: -len(x[1])):
            md.append(f"- 🔴 **{len(dates)} days** ({dates[0]} → {dates[-1]}): _{sig}_")
        md.append("")

    # Savings rate trajectory
    sr = sorted([(o["date"], o["value"]) for o in by_cat.get("savings-rate", [])])
    if sr:
        md.append("## Savings rate observations")
        md.append("")
        for d, v in sr[-5:]:
            note = " ⚠️ implausible — verify income tracking" if v in ("0.0%", "0%") else ""
            md.append(f"- {d}: {v}{note}")
        md.append("")

    if len(md) <= 4:
        md.append("_No structured finance signals detected in the scanned window._")
        md.append("")

    return "\n".join(md)


# ── Atomic write ─────────────────────────────────────────────────────────────

def atomic_write(path: Path, content: str):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(content, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14, help="lookback window for learnings")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"Finance learnings consolidation @ {datetime.now().isoformat(timespec='seconds')}")

    baselines_md = refresh_baselines()
    learnings_md = refresh_learnings(args.days)

    if args.dry_run:
        print("\n─── BASELINES ───")
        print(baselines_md or "(no data)")
        print("\n─── LEARNINGS ───")
        print(learnings_md or "(no data)")
        return 0

    written = 0
    if baselines_md:
        atomic_write(BASELINES_FILE, baselines_md)
        written += 1
        print(f"  ✓ wrote {BASELINES_FILE.relative_to(ROOT)}")
    if learnings_md:
        atomic_write(LEARNINGS_FILE, learnings_md)
        written += 1
        print(f"  ✓ wrote {LEARNINGS_FILE.relative_to(ROOT)}")

    if written == 0:
        print("  (nothing to write — both refresh paths returned empty)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
