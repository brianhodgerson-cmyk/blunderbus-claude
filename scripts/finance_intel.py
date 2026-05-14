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

import argparse, io, json, os, re, ssl, sys, urllib.request, urllib.error
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

# Source-of-truth view for all spending/income aggregation. The view dedupes by
# (date, merchant, abs(amount), account_id) signature — collapsing the Monarch
# re-ID problem where the overnight ingest occasionally re-pulls the same
# transaction with a fresh id (sequential, +1, OR a re-issued id ~10^14 apart).
# argMax(..., inserted_at) inside the view picks the most-recently-curated
# row per signature so Monarch's latest category wins. Do NOT query
# `finance.transactions FINAL` directly for aggregates — use TRANS_SRC.
# Per memory/finance/learnings.md "Systemic data-quality fixes (2026-05-14)".
TRANS_SRC   = "finance.transactions_deduped"

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
            FROM {TRANS_SRC}
            WHERE {INCOME_CAT}
              AND date >= today() - {months * 31 + 5}
              AND toStartOfMonth(date + INTERVAL 5 DAY) < toStartOfMonth(today())
            GROUP BY month
        ),
        spd AS (
            SELECT toStartOfMonth(date) AS month,
                   round(abs(sum(amount))) AS spending
            FROM {TRANS_SRC}
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
    """Income/spending so far this month (calendar boundary).

    Income looks back 5 extra days before month-start so end-of-prior-month
    pay deposits (VA, DFAS) are counted in the month they represent.
    Spending still uses strict calendar-month boundaries.

    NOTE: This is reported in the daily brief as the calendar-month view
    (clearly labeled, with optional pay-date skew warning). The HEADLINE
    savings rate is driven by `get_trailing_30()` instead — see that
    function's docstring for the rationale.
    """
    rows = q(f"""
        SELECT
            round(sum(if({INCOME_CAT} AND date >= toStartOfMonth(today()) - INTERVAL 5 DAY,
                         amount, 0)))                                  AS income,
            round(abs(sum(if(amount < 0 AND {EXCL} AND date >= toStartOfMonth(today()),
                             amount, 0))))                             AS spending,
            today() - toStartOfMonth(today()) + 1                     AS days_elapsed
        FROM {TRANS_SRC}
        WHERE date >= toStartOfMonth(today()) - INTERVAL 5 DAY
    """)
    return rows[0] if rows else {}


def get_trailing_30():
    """Income/spending over the last 30 days (rolling window).

    Why this exists and why it's the HEADLINE savings-rate metric:

    Brian's income lands on three independent cadences (Nike bi-weekly,
    DFAS last-business-day, VA last-business-day) that all interact with
    calendar-month boundaries. When the calendar boundary lands inside a
    pay cycle, the calendar-month view can swing by $5–8K of income
    without anything actually changing in the household's run-rate. That
    swing is then amplified by the savings-rate ratio (smaller denominator
    → bigger percent move).

    Concrete examples we've already burned cycles on:
      - April 2026 had 3 Nike paychecks + 1 RSU true-up + DFAS+VA on
        Apr-30 → calendar income $24.6K is +63% over March's $16.9K, but
        the household didn't earn 63% more.
      - May 2026 starts with $0 calendar income on day 1 because DFAS+VA
        for May posted on Apr-30 (last business day) — a strict calendar
        view would show a 0% savings rate "crisis" that doesn't exist.

    Trailing 30 days absorbs both: any given 30-day window almost always
    contains 2 Nike + 1 DFAS + 1 VA, regardless of where you start. No
    +5-day pay-date shift is needed; the rolling window does that work.

    This does NOT solve lumpy spending (Mercedes payoff, federal tax
    filing, annual Amex fee). Those are handled by the recurring.md
    suppression list and one-time-event annotations. Trailing-30 is the
    income-side normalization; spending-side normalization is the
    suppression layer.

    Sign + exclusion conventions match get_current_month / get_monthly_summary.
    """
    rows = q(f"""
        SELECT
            round(sum(if({INCOME_CAT}, amount, 0)))                       AS income,
            round(abs(sum(if(amount < 0 AND {EXCL}, amount, 0))))          AS spending,
            30                                                            AS window_days
        FROM {TRANS_SRC}
        WHERE date >= today() - INTERVAL 30 DAY
          AND date <= today()
    """)
    row = rows[0] if rows else {}
    if not row:
        return {}
    income = row.get("income", 0) or 0
    spending = row.get("spending", 0) or 0
    surplus = income - spending
    savings_rate_pct = (surplus / income * 100) if income > 0 else 0.0
    return {
        "income":           income,
        "spending":         spending,
        "surplus":          surplus,
        "savings_rate_pct": savings_rate_pct,
        "window_days":      row.get("window_days", 30),
    }


def detect_paydate_skew(month_start=None):
    """Detect whether a calendar month is distorted by pay-date timing.

    Returns a dict {skewed: bool, reason: str} suitable for appending to
    the calendar-month label in the daily brief. The brief reports
    calendar numbers for continuity but warns when the boundary is doing
    weird things to the income side.

    Two skew conditions (per memory/finance/recurring.md `## Pay-date skew`):
      1. Nike paycheck count in the calendar month is 3 (normal is 2;
         26 paychecks/yr ≈ ~2 per month, with 2 months/yr having 3).
      2. DFAS deposit lands on day 1 or within the last 2 days of the
         calendar month — meaning it represents either the prior or next
         month's pay, not this month's.
    """
    from datetime import timedelta as _td

    if month_start is None:
        month_start = date.today().replace(day=1)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    last_day_of_month = (next_month - _td(days=1)).day

    rows = q(f"""
        SELECT
            countIf(category='Paychecks' AND merchant ILIKE '%nike%') AS nike_count,
            groupArray(if(category='Paychecks' AND merchant ILIKE '%defense%', toDayOfMonth(date), NULL)) AS dfas_days
        FROM {TRANS_SRC}
        WHERE date >= toDate('{month_start.isoformat()}')
          AND date <  toDate('{next_month.isoformat()}')
          AND amount > 0
    """)
    if not rows:
        return {"skewed": False, "reason": ""}
    row = rows[0]
    nike_count = row.get("nike_count", 0) or 0
    dfas_days = [d for d in (row.get("dfas_days") or []) if d]

    reasons = []
    if nike_count >= 3:
        reasons.append(f"3 Nike paychecks")
    dfas_edge = [d for d in dfas_days if d == 1 or d >= last_day_of_month - 1]
    if dfas_edge:
        reasons.append(f"DFAS on day {','.join(str(d) for d in dfas_edge)}")
    if reasons:
        return {"skewed": True, "reason": "; ".join(reasons)}
    return {"skewed": False, "reason": ""}


def get_top_categories(days=30):
    return q(f"""
        SELECT category,
               round(abs(sum(amount))) as total
        FROM {TRANS_SRC}
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
        FROM {TRANS_SRC}
        WHERE date >= today() - 120
          AND amount < 0
          AND {EXCL}
          AND toStartOfMonth(date) < toStartOfMonth(today())
        GROUP BY category, month
        ORDER BY category, month
    """)


# ─── FIRE Calculator ─────────────────────────────────────────────────────────

def fire_calc(balances, monthly_summaries, current_month=None, trailing_30=None):
    """Compute FIRE projections.

    Savings-rate semantics:
      - If `trailing_30` is provided with positive income, its savings rate
        becomes the HEADLINE `savings_rate_pct` returned. Rationale lives
        in `get_trailing_30.__doc__` — short version: rolling 30-day windows
        absorb the pay-date skew that distorts calendar-month income.
      - The 3-month average (avg_income / avg_spending / avg_surplus) is
        still returned and still drives the years-to-FIRE projection — those
        long-horizon calcs are stable against pay-date skew because they
        average over multiple months.
      - For backward compatibility, `savings_rate_pct_3mo` is also returned
        so callers that want the historical metric can opt in.
    """
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

    savings_rate_3mo = (avg_surplus / avg_income * 100) if avg_income > 0 else 0
    # Headline savings rate prefers trailing-30 when available (see docstring).
    if trailing_30 and trailing_30.get("income", 0) > 0:
        headline_savings_rate = trailing_30["savings_rate_pct"]
        savings_rate_basis = "trailing-30"
    else:
        headline_savings_rate = savings_rate_3mo
        savings_rate_basis = "3-month avg"

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
        "savings_rate_pct":     headline_savings_rate,
        "savings_rate_pct_3mo": savings_rate_3mo,
        "savings_rate_basis":   savings_rate_basis,
        "trailing_30":          trailing_30,
    }


# ─── Spending Anomaly Detection ──────────────────────────────────────────────

def load_recurring_suppressions():
    """Read memory/finance/recurring.md and extract (month, category) pairs that
    should be suppressed (or annotated) when flagged as anomalies. Returns a dict:
        {(month_int, category_lower): "reason string"}
    Only "Confirmed" + "Annual hits" tables count — "Suspected" still get flagged
    but with a lower-severity prefix."""
    import re as _re
    from pathlib import Path as _Path
    path = _Path(__file__).resolve().parent.parent / "memory" / "finance" / "recurring.md"
    suppressions = {}
    if not path.exists():
        return suppressions
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return suppressions

    month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                 "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    today_year = date.today().year
    today_month = date.today().month

    # Parse the "Annual hits" table — recurring every year, suppress always
    in_annual = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## Annual hits"):
            in_annual = True
            continue
        if in_annual and s.startswith("## "):
            in_annual = False
            continue
        if not in_annual or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if len(cells) < 4:
            continue
        month, cat, amt, why = cells[0], cells[1], cells[2], cells[3]
        if cat in ("Category", "—", "") or month in ("Month", "---", ""):
            continue
        m = month_map.get(month[:3])
        if not m:
            continue
        if cat == "—" or amt == "—":
            continue
        suppressions[(m, cat.lower())] = f"{why} (~{amt})"

    # Parse the "One-time explained events" table — only suppress if
    # the period matches current YYYY-MM (so future years still flag).
    in_onetime = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## One-time explained events"):
            in_onetime = True
            continue
        if in_onetime and s.startswith("## "):
            in_onetime = False
            continue
        if not in_onetime or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if len(cells) < 4:
            continue
        period, cat, amt, why = cells[0], cells[1], cells[2], cells[3]
        if period in ("Period", "---") or cat in ("Category", "—"):
            continue
        # period format: YYYY-MM
        m_period = _re.match(r"^(\d{4})-(\d{2})$", period)
        if not m_period:
            continue
        yr, mo = int(m_period.group(1)), int(m_period.group(2))
        # Only active suppression if entry's period matches current month/year
        if yr == today_year and mo == today_month:
            existing = suppressions.get((mo, cat.lower()), "")
            extra = f"ONE-TIME ({period}): {why} (~{amt})"
            suppressions[(mo, cat.lower())] = (existing + " · " + extra).strip(" ·") if existing else extra
    return suppressions


def detect_anomalies():
    history = get_category_history()
    if not history:
        return []
    suppressions = load_recurring_suppressions()

    # Build per-category list of monthly amounts
    from collections import defaultdict
    cat_history = defaultdict(list)
    for row in history:
        cat_history[row["category"]].append(row["total"])

    # Get current month spend by category
    current_rows = q(f"""
        SELECT category,
               round(abs(sum(amount))) as total
        FROM {TRANS_SRC}
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
    early_month = day_of_month < 7

    for cat, amounts in cat_history.items():
        if len(amounts) < 2:
            continue
        mean = sum(amounts) / len(amounts)
        current_spend = current.get(cat, 0)

        suppression_reason = suppressions.get((today.month, cat.lower()))

        if early_month:
            # Before day 7: only flag categories where actual spend already
            # exceeds the full-month average — a real signal, not a projection.
            if mean > 0 and current_spend > mean:
                pct_over = round((current_spend / mean - 1) * 100)
                anomalies.append({
                    "category":    cat,
                    "current":     current_spend,
                    "projected":   None,
                    "avg":         round(mean),
                    "z_score":     None,
                    "pct_over":    pct_over,
                    "severity":    "HIGH" if current_spend > mean * 2 else "MEDIUM",
                    "anomaly_type": "exceeded",
                    "suppressed":   bool(suppression_reason),
                    "suppression_reason": suppression_reason,
                })
        else:
            # After day 7: use projected extrapolation + z-score as before.
            variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
            stdev = variance ** 0.5
            if stdev == 0:
                continue
            pace_factor = days_in_month / day_of_month
            projected = current_spend * pace_factor
            z = (projected - mean) / stdev
            if z >= ANOMALY_ZSCORE_THRESHOLD:
                anomalies.append({
                    "category":    cat,
                    "current":     current_spend,
                    "projected":   round(projected),
                    "avg":         round(mean),
                    "z_score":     round(z, 1),
                    "pct_over":    None,
                    "severity":    "HIGH" if z >= 3 else "MEDIUM",
                    "anomaly_type": "projected",
                    "suppressed":   bool(suppression_reason),
                    "suppression_reason": suppression_reason,
                })

    # Suppressed anomalies sink to the bottom; non-suppressed sort by severity
    anomalies.sort(key=lambda x: (
        x.get("suppressed", False),         # False (0) first
        -(x["z_score"] or 0),
        -(x.get("pct_over") or 0),
    ))
    return anomalies


# ─── Budget Pace ─────────────────────────────────────────────────────────────

def budget_pace_alert(monthly_summaries, current_month):
    if not monthly_summaries or not current_month:
        return None
    avg_monthly_spend = sum(m["spending"] for m in monthly_summaries) / len(monthly_summaries)
    days_elapsed = current_month.get("days_elapsed", 15)
    days_in_month = 30

    # Early-month guard: linear projection is unreliable before day 7 because
    # lumpy bills (rent, utilities, dentist) dominate and skew the extrapolation.
    # Before day 7, report actual spend vs the pro-rated average instead.
    MIN_DAYS_FOR_PROJECTION = 7
    if days_elapsed < MIN_DAYS_FOR_PROJECTION:
        prorated_avg = avg_monthly_spend * (days_elapsed / days_in_month)
        pct_of_avg = (current_month["spending"] / prorated_avg * 100) if prorated_avg > 0 else 0
        return {
            "current_spend":    current_month["spending"],
            "projected_spend":  None,  # too early to project
            "avg_spend":        round(avg_monthly_spend),
            "prorated_avg":     round(prorated_avg),
            "pct_of_avg":       round(pct_of_avg, 1),
            "days_elapsed":     days_elapsed,
            "status":           "EARLY",
        }

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
        real = [a for a in anomalies if not a.get("suppressed")]
        suppressed = [a for a in anomalies if a.get("suppressed")]
        for a in real:
            if a.get("anomaly_type") == "exceeded":
                anomaly_text += f"  {a['category']}: ${a['current']:,.0f} already spent — exceeds monthly avg ${a['avg']:,.0f} ({a['severity']})\n"
            else:
                anomaly_text += f"  {a['category']}: ${a['current']:,.0f} so far → projected ${a['projected']:,.0f} vs avg ${a['avg']:,.0f} ({a['severity']})\n"
        if suppressed:
            anomaly_text += "\n  Expected (annual/known recurring — do not flag):\n"
            for a in suppressed:
                anomaly_text += f"  {a['category']}: ${a['current']:,.0f} — {a['suppression_reason']}\n"
        if not real and not suppressed:
            anomaly_text = "  None detected."
        elif not real:
            anomaly_text = "  No real anomalies (all flagged categories are known recurring hits).\n" + anomaly_text
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
        months_title = (
            f"📊 Recent Months — avg surplus ${avg_surplus:+,.0f}/mo "
            f"(calendar-month — includes pay-date skew when N=3 Nike paychecks "
            f"OR DFAS lands on day 1 / last day)"
        )
        month_lines = ["| Month | Income | Spending | Surplus | Notes |", "|---|---|---|---|---|"]
        for m in monthly_summaries[-3:]:
            sign = "+" if m["surplus"] >= 0 else ""
            dot  = "🟢" if m["surplus"] > 0 else "🔴"
            # Detect pay-date skew for this calendar month
            month_start = m["month"] if isinstance(m["month"], date) else None
            skew = detect_paydate_skew(month_start) if month_start else {"skewed": False, "reason": ""}
            note = f"⚠️ skew: {skew['reason']}" if skew.get("skewed") else ""
            month_lines.append(
                f"| **{m['month']}** | ${m['income']:,.0f} | ${m['spending']:,.0f} | {dot} {sign}${m['surplus']:,.0f} | {note} |"
            )
        lines += _fin_callout(months_ctype, months_title, month_lines) + [""]

    # ── FIRE Tracker ──────────────────────────────────────────────────────────
    if fire:
        yr     = f"{fire['years_to_fire']:.1f}" if fire["years_to_fire"] is not None else "N/A"
        pct    = fire["progress_pct"]
        bar    = _money_bar(pct)
        sr     = fire["savings_rate_pct"]
        basis  = fire.get("savings_rate_basis", "3-month avg")
        sr_3mo = fire.get("savings_rate_pct_3mo", sr)
        t30    = fire.get("trailing_30") or {}
        fire_ctype = "success" if sr >= 20 else "warning" if sr >= 10 else "danger"
        fire_lines = [
            f"{bar} **{pct:.1f}%** of ${fire['fire_number']:,.0f}",
            f"",
            f"| Metric | Value |",
            f"|---|---|",
            f"| **Savings rate (headline, {basis})** | **{sr:.1f}%** |",
        ]
        if t30:
            fire_lines.append(
                f"| ↳ Trailing-30 income / spend | ${t30.get('income',0):,.0f} / ${t30.get('spending',0):,.0f} |"
            )
        fire_lines += [
            f"| Savings rate (3-month avg) | {sr_3mo:.1f}% |",
            f"| Investable assets | **${fire['investable_assets']:,.0f}** |",
            f"| Monthly surplus (avg) | **${fire['avg_surplus']:,.0f}** |",
            f"| Years to FIRE | **{yr}** |",
            f"| Target year | **{fire['fire_year'] or 'TBD'}** |",
        ]
        lines += _fin_callout(fire_ctype, f"🎯 FIRE Progress — {pct:.1f}% · Target {fire['fire_year'] or 'TBD'}", fire_lines) + [""]

    # ── Budget Pace ───────────────────────────────────────────────────────────
    if pace:
        if pace["status"] == "EARLY":
            # Early month — show actual vs pro-rated average, no wild projections
            p_pct = pace["pct_of_avg"]
            p_icon = "🔴" if p_pct > 150 else "🟡" if p_pct > 110 else "🟢"
            p_ctype = "danger" if p_pct > 150 else "warning" if p_pct > 110 else "success"
            pace_lines = [
                f"| | |",
                f"|---|---|",
                f"| Spent so far | **${pace['current_spend']:,.0f}** ({pace['days_elapsed']}d elapsed) |",
                f"| Pro-rated avg ({pace['days_elapsed']}d) | **${pace['prorated_avg']:,.0f}** |",
                f"| 3-month average | **${pace['avg_spend']:,.0f}/mo** |",
                f"| Pace vs pro-rated | {p_icon} **{p_pct:.0f}%** |",
            ]
            lines += _fin_callout(p_ctype, f"📊 Budget Pace — early month ({pace['days_elapsed']}d)", pace_lines, foldable="-") + [""]
        else:
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
        real_anom = [a for a in anomalies if not a.get("suppressed")]
        suppressed_anom = [a for a in anomalies if a.get("suppressed")]

        if real_anom:
            has_exceeded = any(a.get("anomaly_type") == "exceeded" for a in real_anom)
            high_count = sum(1 for a in real_anom if a["severity"] == "HIGH")
            anom_ctype = "danger" if high_count > 0 else "warning"

            if has_exceeded:
                anom_lines = [
                    "| Category | Spent | Monthly Avg | Over By | Status |",
                    "|---|---|---|---|---|",
                ]
                for a in real_anom:
                    icon = "🔴" if a["severity"] == "HIGH" else "🟡"
                    pct_over = a.get("pct_over", 0)
                    anom_lines.append(
                        f"| **{a['category']}** | ${a['current']:,.0f} | ${a['avg']:,.0f} | +{pct_over}% | {icon} {a['severity']} |"
                    )
                title = f"⚠️ Already Over Budget — {len(real_anom)} Categories Exceeded Monthly Avg"
            else:
                anom_lines = [
                    "| Category | Now | Projected | vs Avg | Status |",
                    "|---|---|---|---|---|",
                ]
                for a in real_anom:
                    icon = "🔴" if a["severity"] == "HIGH" else "🟡"
                    diff_pct = round((a["projected"] / a["avg"] - 1) * 100) if a["avg"] else 0
                    anom_lines.append(
                        f"| **{a['category']}** | ${a['current']:,.0f} | ${a['projected']:,.0f} | +{diff_pct}% | {icon} {a['severity']} |"
                    )
                title = f"⚠️ Spending Anomalies — {len(real_anom)} Categories High"

            lines += _fin_callout(anom_ctype, title, anom_lines) + [""]

        if suppressed_anom:
            sup_lines = [
                "| Category | Spent | vs Avg | Reason |",
                "|---|---|---|---|",
            ]
            for a in suppressed_anom:
                avg = a["avg"] or 1
                ratio = a["current"] / avg
                # Keep the reason terse for the table — full audit trail lives in
                # memory/finance/recurring.md and decisions.md.
                reason = a.get("suppression_reason", "") or ""
                # Strip "(~$X)" amount tails — they're shown in the Spent column
                short = re.sub(r"\s*\(~?\$[\d,]+\)\s*", "", reason).strip()
                # When both annual + one-time hit the same category, show the
                # one-time portion (more relevant to this month's spike); annual
                # presence is implied by the row appearing in this table.
                m_split = re.search(r"\s*·\s*ONE-TIME\s+", short)
                if m_split:
                    onetime_part = short[m_split.end():]
                    onetime_part = re.sub(r"^\(\d{4}-\d{2}\):\s*", "", onetime_part).strip()
                    short = f"one-time event: {onetime_part}"
                elif short.startswith("ONE-TIME "):
                    body = re.sub(r"^ONE-TIME\s+", "", short)
                    body = re.sub(r"^\(\d{4}-\d{2}\):\s*", "", body).strip()
                    short = f"one-time event: {body}"
                short = re.sub(r"\s+", " ", short)[:160]
                sup_lines.append(
                    f"| **{a['category']}** | ${a['current']:,.0f} | {ratio:.1f}x | _{short}_ |"
                )
            sup_title = f"📅 Expected (Annual / Recurring) — {len(suppressed_anom)} Suppressed"
            lines += _fin_callout("info", sup_title, sup_lines, foldable="-") + [""]
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
        if pace["status"] == "EARLY":
            print(f"  Pace: 📊 early month ({pace['days_elapsed']}d) — "
                  f"${pace['current_spend']:,.0f} spent vs "
                  f"${pace['prorated_avg']:,.0f} pro-rated avg ({pace['pct_of_avg']:.0f}%)")
        else:
            status_icon = {"OVER": "🔴", "WATCH": "🟡", "OK": "🟢"}.get(pace["status"], "⬜")
            print(f"  Pace: {status_icon} projected ${pace['projected_spend']:,.0f} "
                  f"vs avg ${pace['avg_spend']:,.0f} ({pace['pct_of_avg']:.0f}%)")

    if fire:
        yr = f"{fire['years_to_fire']:.1f}" if fire["years_to_fire"] is not None else "?"
        print(f"\n  FIRE: {fire['progress_pct']:.1f}% of ${fire['fire_number']:,.0f} "
              f"({yr} yrs → {fire['fire_year'] or 'TBD'})")
        basis = fire.get("savings_rate_basis", "3-month avg")
        print(f"  Savings rate ({basis}): {fire['savings_rate_pct']:.1f}%")
        if fire.get("trailing_30") and basis == "trailing-30":
            t30 = fire["trailing_30"]
            print(f"    ↳ trailing-30: ${t30['income']:,.0f} income / ${t30['spending']:,.0f} spend / ${t30['surplus']:+,.0f} surplus")
        if abs(fire.get("savings_rate_pct_3mo", fire["savings_rate_pct"]) - fire["savings_rate_pct"]) > 5:
            print(f"    (3-month avg basis was {fire.get('savings_rate_pct_3mo', 0):.1f}% — calendar-month skew detected)")

    if anomalies:
        print(f"\n  ⚠️  Anomalies: {len(anomalies)} category(s) trending high")
        for a in anomalies[:3]:
            if a.get("anomaly_type") == "exceeded":
                print(f"     • {a['category']}: ${a['current']:,.0f} already over "
                      f"monthly avg ${a['avg']:,.0f} (+{a['pct_over']}%)")
            else:
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

    print("  Fetching trailing-30 (headline savings-rate basis) ...")
    trailing_30 = get_trailing_30()

    print("  Fetching top categories ...")
    top_cats = get_top_categories(days=30)

    # ── FIRE ─────────────────────────────────────────────────────────────────
    print("  Calculating FIRE ...")
    fire = fire_calc(balances, monthly_summaries, current_month, trailing_30=trailing_30)

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
        print("  SPENDING ANOMALIES:")
        for a in anomalies:
            if a.get("anomaly_type") == "exceeded":
                print(f"    [{a['severity']:6}] {a['category']:<25} "
                      f"${a['current']:>7,.0f} spent — exceeds avg ${a['avg']:>7,.0f}/mo (+{a['pct_over']}%)")
            else:
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
                if a.get("anomaly_type") == "exceeded":
                    alerts.append(
                        f"⚠️ *Over Budget: {a['category']}*\n"
                        f"Already spent *${a['current']:,.0f}* — "
                        f"monthly avg is ${a['avg']:,.0f} (+{a['pct_over']}% over)"
                    )
                else:
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
