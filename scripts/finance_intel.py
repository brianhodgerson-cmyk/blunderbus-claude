"""
HodgeSpot Financial Intelligence Suite
Runs as a one-off or nightly cron to generate:
  1. AI Monthly Narrative  → Obsidian daily note
  2. FIRE Calculator        → console + Obsidian
  3. Spending Anomaly Alert → Telegram + console
  4. Budget Pace Alert      → Telegram + console
  5. Morning Brief Finance Block → console

Usage:
    py scripts/finance_intel.py [--dry-run] [--no-obsidian] [--no-telegram]

Environment (from .env or shell):
    CLICKHOUSE_HOST         default: 192.168.50.106
    CLICKHOUSE_PASSWORD     default: clickhouse
    BLUNDERBUS_NOTE_BACKEND optional override for note backend selection
    BLUNDERBUS_VAULT_ROOT   filesystem backend root override
    OBSIDIAN_TOKEN          only required when using the obsidian-rest backend
    OBSIDIAN_URL            default: https://127.0.0.1:27124
    TELEGRAM_BOT_TOKEN      Telegram bot token
    TELEGRAM_CHAT_ID        Telegram chat/user ID
"""

import argparse, io, json, os, ssl, sys, urllib.request, urllib.error
from datetime import date, datetime
from math import log

from blunderbus_data import log_life_event
from note_store import NoteStoreError, resolve_note_store, upsert_section
from runtime import configure_utf8_stdio, env_first, resolve_claude_command

configure_utf8_stdio()

try:
    from clickhouse_driver import Client as CHClient
except ImportError:
    print("ERROR: pip install clickhouse-driver")
    sys.exit(1)

# ─── Config ──────────────────────────────────────────────────────────────────
# AI narrative runs via `claude` CLI (Claude Code) — no API key required.

CH_HOST     = os.environ.get("CLICKHOUSE_HOST", "192.168.50.106")
CH_PORT     = int(os.environ.get("CLICKHOUSE_PORT", "9000"))
CH_USER     = os.environ.get("CLICKHOUSE_USER", "clickhouse")
CH_PASS     = env_first("CLICKHOUSE_PASSWORD", "CLICKHOUSE_PASS", default="clickhouse")
OBS_URL     = os.environ.get("OBSIDIAN_URL", "https://127.0.0.1:27124")
OBS_TOKEN   = os.environ.get("OBSIDIAN_TOKEN", "")
TG_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT     = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTE_STORE  = resolve_note_store()

def _fin_callout(ctype, title, body_lines, foldable="+"):
    """Build an Obsidian callout block for finance section."""
    fold = foldable if foldable else ""
    out = [f"> [!{ctype}]{fold} {title}"]
    for line in body_lines:
        out.append(f"> {line}" if line else ">")
    return out

def _money_bar(pct, width=20):
    """Wide progress bar for FIRE tracker."""
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * width)
    return f"`{'█' * filled}{'░' * (width - filled)}`"


EXCL        = "category NOT IN ('Transfer', 'Credit Card Payment')"
INCOME_CAT  = "category IN ('Paychecks', 'Other Income')"
FIXED_CAT   = "category IN ('Mortgage', 'Gas & Electric', 'Insurance', 'Phone', 'Internet & Cable', 'Garbage')"
ESSENTIAL   = "category IN ('Groceries', 'Gas', 'Medical')"

# FIRE assumptions
ANNUAL_INVESTMENT_RETURN = 0.07   # 7% real return
SAFE_WITHDRAWAL_RATE     = 0.04   # 4% SWR
FIRE_MULTIPLIER          = 25     # = 1 / SWR

ANOMALY_ZSCORE_THRESHOLD = 2.0    # flag if spend > mean + 2*stdev


# ─── Clickhouse ──────────────────────────────────────────────────────────────

def ch():
    return CHClient(host=CH_HOST, port=CH_PORT, user=CH_USER, password=CH_PASS, database="finance")


def q(sql, params=None):
    """Execute a query and return list of dicts."""
    client = ch()
    rows, cols = client.execute(sql, params, with_column_types=True)
    col_names = [c[0] for c in cols]
    return [dict(zip(col_names, row)) for row in rows]


# ─── Data Queries ────────────────────────────────────────────────────────────

def get_balances():
    rows = q("""
        SELECT account_type,
               round(sum(balance)) as total
        FROM finance.accounts FINAL
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM finance.accounts)
        GROUP BY account_type
    """)
    d = {r["account_type"]: r["total"] for r in rows}
    return {
        "investments": d.get("brokerage", 0),
        "cash":        d.get("depository", 0),
        "mortgage":    d.get("loan", 0),
        "credit":      d.get("credit", 0),
        "net_worth":   sum(d.values()),
    }


def get_monthly_summary(months=3):
    """Returns list of {month, income, spending, surplus} for last N complete months.

    Income and spending use different month boundaries to handle pay dates that
    straddle month-end (e.g. VA benefit + DFAS posting on the last business day
    of the prior month for the upcoming month's payment):

      - Income  : attributed to toStartOfMonth(date + 5 days)
                  → a Feb-26 VA payment becomes "March" income
      - Spending: strict calendar month (toStartOfMonth(date))

    Both sub-queries are joined on the normalised month label so the surplus
    is always income-for-that-month minus spending-for-that-month.
    """
    rows = q(f"""
        WITH
        inc AS (
            -- Income shifted +5 days so end-of-prior-month payments (VA, DFAS)
            -- land in the month they represent. Only include normalized months
            -- strictly before the current calendar month.
            SELECT toStartOfMonth(date + INTERVAL 5 DAY) AS month,
                   round(sum(amount)) AS income
            FROM finance.transactions FINAL
            WHERE {INCOME_CAT}
              AND date >= today() - {months * 31 + 5}
              AND toStartOfMonth(date + INTERVAL 5 DAY) < toStartOfMonth(today())
            GROUP BY month
        ),
        spd AS (
            SELECT toStartOfMonth(date) AS month,
                   round(abs(sum(amount))) AS spending
            FROM finance.transactions FINAL
            WHERE amount < 0 AND {EXCL}
              AND date >= today() - {months * 31}
              AND toStartOfMonth(date) < toStartOfMonth(today())
            GROUP BY month
        )
        SELECT inc.month,
               inc.income,
               coalesce(spd.spending, 0) AS spending,
               inc.income - coalesce(spd.spending, 0) AS surplus
        FROM inc LEFT JOIN spd ON inc.month = spd.month
        WHERE inc.income > 2000
        ORDER BY inc.month
    """)
    return rows[-months:]


def get_current_month():
    """Income/spending so far this month.

    Income looks back 5 extra days before month-start so end-of-prior-month
    pay deposits (VA, DFAS) are counted in the month they represent.
    Spending still uses strict calendar-month boundaries.
    """
    rows = q(f"""
        SELECT
            round(sum(if({INCOME_CAT} AND date >= toStartOfMonth(today()) - INTERVAL 5 DAY,
                         amount, 0)))                                  AS income,
            round(abs(sum(if(amount < 0 AND {EXCL} AND date >= toStartOfMonth(today()),
                             amount, 0))))                             AS spending,
            today() - toStartOfMonth(today()) + 1                     AS days_elapsed
        FROM finance.transactions FINAL
        WHERE date >= toStartOfMonth(today()) - INTERVAL 5 DAY
    """)
    return rows[0] if rows else {}


def get_top_categories(days=30):
    return q(f"""
        SELECT category,
               round(abs(sum(amount))) as total
        FROM finance.transactions FINAL
        WHERE date >= today() - {days}
          AND amount < 0
          AND {EXCL}
        GROUP BY category
        ORDER BY total DESC
        LIMIT 12
    """)


def get_category_history():
    """Per-category monthly spend for last 3 full months (for anomaly detection)."""
    return q(f"""
        SELECT category,
               toStartOfMonth(date) as month,
               round(abs(sum(amount))) as total
        FROM finance.transactions FINAL
        WHERE date >= today() - 120
          AND amount < 0
          AND {EXCL}
          AND toStartOfMonth(date) < toStartOfMonth(today())
        GROUP BY category, month
        ORDER BY category, month
    """)


# ─── FIRE Calculator ─────────────────────────────────────────────────────────

def fire_calc(balances, monthly_summaries, current_month=None):
    if not monthly_summaries:
        return None

    # Blend in the current month when it's ≥25 days elapsed — at that point
    # all recurring income (VA, DFAS, both Nike paychecks) has posted, giving
    # a complete picture. This matters especially early in the dataset when
    # only 1-2 historical months are available.
    all_months = list(monthly_summaries)
    if current_month and current_month.get("days_elapsed", 0) >= 25:
        all_months.append(current_month)

    avg_income   = sum(m["income"]   for m in all_months) / len(all_months)
    avg_spending = sum(m["spending"] for m in all_months) / len(all_months)
    avg_surplus  = avg_income - avg_spending

    annual_spending = avg_spending * 12
    fire_number     = annual_spending * FIRE_MULTIPLIER

    investable = balances["investments"] + balances["cash"]
    progress   = investable / fire_number if fire_number > 0 else 0

    # Estimate monthly investment contribution (~60% of surplus, rough heuristic)
    monthly_invest = max(avg_surplus * 0.6, 500)

    # Years to FIRE: FV of current portfolio + PMT stream = fire_number
    # Solve: P*(1+r)^n + PMT*((1+r)^n - 1)/r = FV
    r = ANNUAL_INVESTMENT_RETURN / 12
    P = investable
    PMT = monthly_invest
    FV = fire_number

    if P >= FV:
        years = 0.0
    elif r == 0:
        years = (FV - P) / PMT / 12
    else:
        try:
            n = log((FV * r + PMT) / (P * r + PMT)) / log(1 + r)
            years = n / 12
        except (ValueError, ZeroDivisionError):
            years = None

    fire_year = (datetime.now().year + int(years)) if years is not None else None

    return {
        "avg_monthly_income":   avg_income,
        "avg_monthly_spending": avg_spending,
        "avg_surplus":          avg_surplus,
        "annual_spending":      annual_spending,
        "fire_number":          fire_number,
        "investable_assets":    investable,
        "progress_pct":         progress * 100,
        "monthly_invest_est":   monthly_invest,
        "years_to_fire":        years,
        "fire_year":            fire_year,
        "savings_rate_pct":     (avg_surplus / avg_income * 100) if avg_income > 0 else 0,
    }


# ─── Spending Anomaly Detection ──────────────────────────────────────────────

def detect_anomalies():
    history = get_category_history()
    if not history:
        return []

    # Build per-category list of monthly amounts
    from collections import defaultdict
    cat_history = defaultdict(list)
    for row in history:
        cat_history[row["category"]].append(row["total"])

    # Get current month spend by category
    current_rows = q(f"""
        SELECT category,
               round(abs(sum(amount))) as total
        FROM finance.transactions FINAL
        WHERE date >= toStartOfMonth(today())
          AND amount < 0
          AND {EXCL}
        GROUP BY category
    """)
    current = {r["category"]: r["total"] for r in current_rows}

    anomalies = []
    today = date.today()
    days_in_month = 30
    day_of_month = today.day
    pace_factor = days_in_month / day_of_month  # annualize to full month

    for cat, amounts in cat_history.items():
        if len(amounts) < 2:
            continue
        mean = sum(amounts) / len(amounts)
        variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
        stdev = variance ** 0.5
        if stdev == 0:
            continue

        current_spend = current.get(cat, 0)
        projected = current_spend * pace_factor
        z = (projected - mean) / stdev

        if z >= ANOMALY_ZSCORE_THRESHOLD:
            anomalies.append({
                "category":    cat,
                "current":     current_spend,
                "projected":   round(projected),
                "avg":         round(mean),
                "z_score":     round(z, 1),
                "severity":    "HIGH" if z >= 3 else "MEDIUM",
            })

    anomalies.sort(key=lambda x: x["z_score"], reverse=True)
    return anomalies


# ─── Budget Pace ─────────────────────────────────────────────────────────────

def budget_pace_alert(monthly_summaries, current_month):
    if not monthly_summaries or not current_month:
        return None
    avg_monthly_spend = sum(m["spending"] for m in monthly_summaries) / len(monthly_summaries)
    days_elapsed = current_month.get("days_elapsed", 15)
    days_in_month = 30
    pace_factor = days_in_month / max(days_elapsed, 1)
    projected = current_month["spending"] * pace_factor
    pct_of_avg = projected / avg_monthly_spend * 100 if avg_monthly_spend > 0 else 0
    return {
        "current_spend":    current_month["spending"],
        "projected_spend":  round(projected),
        "avg_spend":        round(avg_monthly_spend),
        "pct_of_avg":       round(pct_of_avg, 1),
        "days_elapsed":     days_elapsed,
        "status":           "OVER" if pct_of_avg > 110 else ("OK" if pct_of_avg <= 95 else "WATCH"),
    }


# ─── AI Narrative ────────────────────────────────────────────────────────────

def generate_narrative(balances, monthly_summaries, fire, anomalies, top_cats):
    """Generate AI narrative via the local `claude` CLI — no API key needed."""
    import subprocess

    claude_bin = resolve_claude_command()
    if not claude_bin:
        raise RuntimeError("`claude` CLI not found")

    months_text = ""
    for m in monthly_summaries:
        surplus_sign = "+" if m["surplus"] >= 0 else ""
        months_text += f"  {m['month']}: income ${m['income']:,.0f} | spending ${m['spending']:,.0f} | surplus {surplus_sign}${m['surplus']:,.0f}\n"

    cats_text = "\n".join(f"  {r['category']}: ${r['total']:,.0f}" for r in top_cats[:8])

    anomaly_text = ""
    if anomalies:
        for a in anomalies:
            anomaly_text += f"  {a['category']}: ${a['current']:,.0f} so far → projected ${a['projected']:,.0f} vs avg ${a['avg']:,.0f} ({a['severity']})\n"
    else:
        anomaly_text = "  None detected."

    yrs_str = f"{fire['years_to_fire']:.1f}" if fire and fire["years_to_fire"] is not None else "N/A"
    fire_text = f"""
  Savings rate: {fire['savings_rate_pct']:.1f}%
  FIRE number (25x expenses): ${fire['fire_number']:,.0f}
  Investable assets: ${fire['investable_assets']:,.0f}
  Progress to FIRE: {fire['progress_pct']:.1f}%
  Estimated years to FIRE: {yrs_str}
  Projected FIRE year: {fire['fire_year'] or 'TBD'}""" if fire else "  No FIRE data."

    prompt = f"""You are a personal financial advisor writing a monthly financial narrative for Brian Hodgerson.
Speak directly to Brian in a warm but clear tone. Keep it under 250 words.
Focus on: what's going well, what needs attention, and one actionable recommendation.
Do not use bullet points — write in flowing paragraphs.

=== FINANCIAL DATA ===

NET WORTH SNAPSHOT (today):
  Investments (brokerage): ${balances['investments']:,.0f}
  Cash & Savings: ${balances['cash']:,.0f}
  Mortgage: ${balances['mortgage']:,.0f}
  Credit Cards: ${balances['credit']:,.0f}
  Total Net Worth: ${balances['net_worth']:,.0f}

RECENT MONTHS (income vs spending, transfers excluded):
{months_text}
TOP SPENDING CATEGORIES (last 30 days):
{cats_text}

SPENDING ANOMALIES (current month, pace-projected):
{anomaly_text}

FIRE TRACKER:
{fire_text}

Write the narrative now:"""

    # Pipe prompt via stdin — avoids arg length limits and CLAUDE.md role constraints
    result = subprocess.run(
        [claude_bin, "--print", "--output-format", "text"],
        input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=os.path.expanduser("~")
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "claude CLI returned non-zero exit")
    return result.stdout.strip()


# ─── Obsidian ────────────────────────────────────────────────────────────────

def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def obsidian_append(content, token):
    """Append to daily note by GET → append → PUT via vault file API."""
    ctx = ssl_ctx()
    base = f"{OBS_URL}"

    # Step 1: resolve daily note path
    req = urllib.request.Request(
        f"{base}/periodic/daily/",
        headers={"Authorization": f"Bearer {token}"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            path = resp.headers.get("Content-Location", "")
    except urllib.error.HTTPError as e:
        # HEAD not supported — try GET
        path = ""
        try:
            req2 = urllib.request.Request(
                f"{base}/periodic/daily/",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            with urllib.request.urlopen(req2, timeout=10, context=ctx) as r:
                path = r.headers.get("Content-Location", "")
        except Exception as e2:
            return None, str(e2)
    except Exception as e:
        return None, str(e)

    if not path:
        return None, "Could not resolve daily note path"

    # Step 2: GET current content
    req = urllib.request.Request(
        f"{base}/vault/{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            current = resp.read().decode("utf-8")
    except Exception as e:
        return None, str(e)

    # Skip if Finance block already present
    if "## Finance" in current:
        return 200, "already present"

    # Step 3: PUT updated content
    updated = current.rstrip() + "\n" + content
    req = urllib.request.Request(
        f"{base}/vault/{path}",
        data=updated.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/markdown",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return resp.status, "OK"
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)


def build_finance_note(balances, monthly_summaries, fire, anomalies, pace, narrative, top_cats, today):
    lines = []
    lines.append(f"*{today.strftime('%B %Y')} — via BlunderBus · live data as of {today.isoformat()}*")
    lines.append("")

    # ── Net Worth Banner ──────────────────────────────────────────────────────
    nw = balances['net_worth']
    nw_ctype = "success" if nw > 0 else "warning"
    nw_lines = [
        f"| Account | Balance |",
        f"|---|---|",
        f"| 📈 Investments | **${balances['investments']:,.0f}** |",
        f"| 🏦 Cash & Savings | **${balances['cash']:,.0f}** |",
        f"| 🏠 Mortgage | **-${abs(balances['mortgage']):,.0f}** |",
        f"| 💳 Credit Cards | **-${abs(balances['credit']):,.0f}** |",
        f"|  | |",
        f"| **Net Worth** | **${nw:,.0f}** |",
    ]
    lines += _fin_callout(nw_ctype, f"💰 Net Worth — ${nw:,.0f}", nw_lines, foldable="") + [""]

    # ── Recent Months ─────────────────────────────────────────────────────────
    if monthly_summaries:
        avg_surplus = sum(m["surplus"] for m in monthly_summaries) / len(monthly_summaries)
        months_ctype = "success" if avg_surplus > 0 else "danger"
        months_title = f"📊 Recent Months — avg surplus ${avg_surplus:+,.0f}/mo"
        month_lines = ["| Month | Income | Spending | Surplus |", "|---|---|---|---|"]
        for m in monthly_summaries[-3:]:
            sign = "+" if m["surplus"] >= 0 else ""
            dot  = "🟢" if m["surplus"] > 0 else "🔴"
            month_lines.append(
                f"| **{m['month']}** | ${m['income']:,.0f} | ${m['spending']:,.0f} | {dot} {sign}${m['surplus']:,.0f} |"
            )
        lines += _fin_callout(months_ctype, months_title, month_lines) + [""]

    # ── FIRE Tracker ──────────────────────────────────────────────────────────
    if fire:
        yr     = f"{fire['years_to_fire']:.1f}" if fire["years_to_fire"] is not None else "N/A"
        pct    = fire["progress_pct"]
        bar    = _money_bar(pct)
        sr     = fire["savings_rate_pct"]
        fire_ctype = "success" if sr >= 20 else "warning" if sr >= 10 else "danger"
        fire_lines = [
            f"{bar} **{pct:.1f}%** of ${fire['fire_number']:,.0f}",
            f"",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Savings rate | **{sr:.1f}%** |",
            f"| Investable assets | **${fire['investable_assets']:,.0f}** |",
            f"| Monthly surplus (avg) | **${fire['avg_surplus']:,.0f}** |",
            f"| Years to FIRE | **{yr}** |",
            f"| Target year | **{fire['fire_year'] or 'TBD'}** |",
        ]
        lines += _fin_callout(fire_ctype, f"🎯 FIRE Progress — {pct:.1f}% · Target {fire['fire_year'] or 'TBD'}", fire_lines) + [""]

    # ── Budget Pace ───────────────────────────────────────────────────────────
    if pace:
        pace_icons = {"OVER": "🔴", "WATCH": "🟡", "OK": "🟢"}
        pace_types = {"OVER": "danger", "WATCH": "warning", "OK": "success"}
        p_icon  = pace_icons.get(pace["status"], "⬜")
        p_ctype = pace_types.get(pace["status"], "note")
        p_pct   = pace["pct_of_avg"]
        pace_lines = [
            f"| | |",
            f"|---|---|",
            f"| Spent so far | **${pace['current_spend']:,.0f}** ({pace['days_elapsed']}d elapsed) |",
            f"| Projected full month | **${pace['projected_spend']:,.0f}** |",
            f"| 3-month average | **${pace['avg_spend']:,.0f}** |",
            f"| Pace vs average | {p_icon} **{p_pct:.0f}%** |",
        ]
        lines += _fin_callout(p_ctype, f"{p_icon} Budget Pace — {p_pct:.0f}% of average", pace_lines, foldable="-") + [""]

    # ── Spending Anomalies ────────────────────────────────────────────────────
    if anomalies:
        high_count = sum(1 for a in anomalies if a["severity"] == "HIGH")
        anom_ctype = "danger" if high_count > 0 else "warning"
        anom_lines = [
            "| Category | Now | Projected | vs Avg | Status |",
            "|---|---|---|---|---|",
        ]
        for a in anomalies:
            icon = "🔴" if a["severity"] == "HIGH" else "🟡"
            diff_pct = round((a["projected"] / a["avg"] - 1) * 100) if a["avg"] else 0
            anom_lines.append(
                f"| **{a['category']}** | ${a['current']:,.0f} | ${a['projected']:,.0f} | +{diff_pct}% | {icon} {a['severity']} |"
            )
        lines += _fin_callout(anom_ctype, f"⚠️ Spending Anomalies — {len(anomalies)} Categories High", anom_lines) + [""]
    else:
        lines += _fin_callout("success", "✅ Spending — All Categories Normal", ["No anomalies detected this month."], foldable="-") + [""]

    # ── AI Narrative ──────────────────────────────────────────────────────────
    if narrative:
        # Wrap narrative paragraphs with > prefix for callout
        narr_lines = narrative.strip().split("\n")
        lines += _fin_callout("abstract", "🤖 AI Analysis", narr_lines, foldable="+") + [""]

    return "\n".join(lines) + "\n"


# ─── Telegram ────────────────────────────────────────────────────────────────

def telegram_send(text, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception as e:
        return f"Error: {e}"


# ─── Morning Brief Block ──────────────────────────────────────────────────────

def print_morning_brief(balances, monthly_summaries, fire, pace, anomalies, current_month):
    print("\n" + "═" * 60)
    print("  MORNING BRIEF — FINANCES")
    print("═" * 60)

    print(f"\n  Net Worth:   ${balances['net_worth']:>12,.0f}")
    print(f"  Investments: ${balances['investments']:>12,.0f}")
    print(f"  Cash:        ${balances['cash']:>12,.0f}")
    print(f"  Mortgage:    ${balances['mortgage']:>12,.0f}")

    if current_month:
        print(f"\n  This month: ${current_month['spending']:,.0f} spent "
              f"/ ${current_month['income']:,.0f} income "
              f"({current_month['days_elapsed']} days)")

    if pace:
        status_icon = {"OVER": "🔴", "WATCH": "🟡", "OK": "🟢"}.get(pace["status"], "⬜")
        print(f"  Pace: {status_icon} projected ${pace['projected_spend']:,.0f} "
              f"vs avg ${pace['avg_spend']:,.0f} ({pace['pct_of_avg']:.0f}%)")

    if fire:
        yr = f"{fire['years_to_fire']:.1f}" if fire["years_to_fire"] is not None else "?"
        print(f"\n  FIRE: {fire['progress_pct']:.1f}% of ${fire['fire_number']:,.0f} "
              f"({yr} yrs → {fire['fire_year'] or 'TBD'})")
        print(f"  Savings rate: {fire['savings_rate_pct']:.1f}%")

    if anomalies:
        print(f"\n  ⚠️  Anomalies: {len(anomalies)} category(s) trending high")
        for a in anomalies[:3]:
            print(f"     • {a['category']}: ${a['current']:,.0f} so far "
                  f"(proj ${a['projected']:,.0f}, avg ${a['avg']:,.0f})")

    print("\n" + "═" * 60 + "\n")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",      action="store_true", help="Print output, don't push anywhere")
    parser.add_argument("--no-obsidian",  action="store_true", help="Skip Obsidian push")
    parser.add_argument("--no-telegram",  action="store_true", help="Skip Telegram alerts")
    parser.add_argument("--no-narrative", action="store_true", help="Skip AI narrative generation")
    args = parser.parse_args()

    today = date.today()
    print(f"[finance_intel] Running for {today} ...")

    # ── Gather data ──────────────────────────────────────────────────────────
    print("  Fetching balances ...")
    balances = get_balances()

    print("  Fetching monthly summaries ...")
    monthly_summaries = get_monthly_summary(months=4)

    print("  Fetching current month ...")
    current_month = get_current_month()

    print("  Fetching top categories ...")
    top_cats = get_top_categories(days=30)

    # ── FIRE ─────────────────────────────────────────────────────────────────
    print("  Calculating FIRE ...")
    fire = fire_calc(balances, monthly_summaries, current_month)

    # ── Anomaly detection ────────────────────────────────────────────────────
    print("  Detecting anomalies ...")
    anomalies = detect_anomalies()

    # ── Budget pace ──────────────────────────────────────────────────────────
    pace = budget_pace_alert(monthly_summaries, current_month)

    # ── Print morning brief ──────────────────────────────────────────────────
    print_morning_brief(balances, monthly_summaries, fire, pace, anomalies, current_month)

    # ── AI Narrative ─────────────────────────────────────────────────────────
    narrative = ""
    if not args.no_narrative:
        print("  Generating AI narrative ...")
        try:
            narrative = generate_narrative(balances, monthly_summaries, fire, anomalies, top_cats)
            print("\n" + "─" * 60)
            print("  AI MONTHLY NARRATIVE")
            print("─" * 60)
            print(narrative)
            print("─" * 60 + "\n")
        except Exception as e:
            print(f"  ⚠️  AI narrative skipped: {e}")
            narrative = ""

    # ── FIRE detail printout ─────────────────────────────────────────────────
    if fire:
        print("  FIRE CALCULATOR")
        print(f"    Monthly income (avg):    ${fire['avg_monthly_income']:>10,.0f}")
        print(f"    Monthly spending (avg):  ${fire['avg_monthly_spending']:>10,.0f}")
        print(f"    Monthly surplus (avg):   ${fire['avg_surplus']:>10,.0f}")
        print(f"    Annual spending:         ${fire['annual_spending']:>10,.0f}")
        print(f"    FIRE number (25x):       ${fire['fire_number']:>10,.0f}")
        print(f"    Investable assets:       ${fire['investable_assets']:>10,.0f}")
        print(f"    Progress:                {fire['progress_pct']:>9.1f}%")
        yr = f"{fire['years_to_fire']:.1f}" if fire["years_to_fire"] is not None else "N/A"
        print(f"    Years to FIRE:           {yr:>10}")
        print(f"    Target FIRE year:        {str(fire['fire_year']):>10}")
        print()

    # ── Anomaly detail printout ──────────────────────────────────────────────
    if anomalies:
        print("  SPENDING ANOMALIES (current month, pace-projected):")
        for a in anomalies:
            print(f"    [{a['severity']:6}] {a['category']:<25} "
                  f"${a['current']:>7,.0f} now → ${a['projected']:>7,.0f} projected "
                  f"(avg ${a['avg']:>7,.0f}, z={a['z_score']})")
        print()
    else:
        print("  No spending anomalies detected.\n")

    # ── Obsidian push ────────────────────────────────────────────────────────
    block = build_finance_note(balances, monthly_summaries, fire, anomalies, pace, narrative, top_cats, today)

    if args.dry_run:
        print("  [DRY RUN] Finance block that would be pushed:")
        print(block)
    elif not args.no_obsidian:
        print(f"  Updating daily note via {NOTE_STORE.backend_name} ...")
        try:
            note_body = NOTE_STORE.read_daily(today)
            finance_placeholder = "*pending - BlunderBus will populate at 07:30*"
            if "## Finance" in note_body and finance_placeholder not in note_body:
                print("  ✅ Finance section already populated — skipping")
            else:
                updated = upsert_section(note_body, "## Finance", block)
                NOTE_STORE.write_daily(today, updated)
                print(f"  ✅ Finance block injected → {NOTE_STORE.daily_path(today)}")
        except (FileNotFoundError, NoteStoreError) as exc:
            print(f"  ⚠️  Finance note update failed: {exc}")

    # ── Telegram alerts ──────────────────────────────────────────────────────
    if not args.no_telegram and not args.dry_run and TG_TOKEN and TG_CHAT:
        alerts = []

        if pace and pace["status"] == "OVER":
            alerts.append(
                f"🔴 *Budget Pace Alert*\n"
                f"On track to spend *${pace['projected_spend']:,.0f}* this month "
                f"(avg ${pace['avg_spend']:,.0f}, {pace['pct_of_avg']:.0f}% of normal).\n"
                f"So far: ${pace['current_spend']:,.0f} with {pace['days_elapsed']} days elapsed."
            )

        for a in anomalies:
            if a["severity"] == "HIGH":
                alerts.append(
                    f"⚠️ *Spending Spike: {a['category']}*\n"
                    f"${a['current']:,.0f} so far → projected ${a['projected']:,.0f} "
                    f"vs avg ${a['avg']:,.0f} (z={a['z_score']})"
                )

        if alerts:
            for msg in alerts:
                status = telegram_send(msg, TG_TOKEN, TG_CHAT)
                print(f"  Telegram: {msg[:50]}... → {status}")
        else:
            print("  No Telegram alerts triggered (all looks normal).")
    elif args.no_telegram or args.dry_run:
        print("  Telegram alerts: skipped (--dry-run or --no-telegram)")

    log_life_event(
        domain="finance",
        event_type="daily_summary",
        source="finance_intel",
        summary=f"Finance summary generated for {today.isoformat()}",
        detail={
            "net_worth": balances["net_worth"],
            "anomaly_count": len(anomalies),
            "budget_status": pace["status"] if pace else None,
            "note_backend": NOTE_STORE.backend_name,
        },
        tags=["finance", "daily-note"],
    )

    print("[finance_intel] Done.")


if __name__ == "__main__":
    main()
