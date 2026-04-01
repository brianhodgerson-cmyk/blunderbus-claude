#!/usr/bin/env python3
"""
BlunderBus Unified Daily Report Orchestrator.

Single entry point that replaces the 3-script pipeline (morning_prep → morning_brief
→ finance_intel). Collects all data sources in parallel, builds the complete daily
note, delivers to Obsidian + Telegram, and self-validates.

Usage:
    py scripts/daily_report.py [--dry-run] [--no-telegram] [--force] [--date YYYY-MM-DD]

Environment (set by run_daily_report.ps1):
    OBSIDIAN_TOKEN, OBSIDIAN_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    CLICKHOUSE_HOST, CLICKHOUSE_PORT, CLICKHOUSE_PASSWORD, MONARCH_TOKEN,
    SECONION_API_KEY, TRUENAS_API_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta

# ── Project imports (existing code, not rewritten) ───────────────────────────
from runtime import configure_utf8_stdio, resolve_claude_command, project_root
from note_store import resolve_note_store, upsert_section, NoteStoreError
from blunderbus_data import log_life_event

configure_utf8_stdio()

# ── Config ───────────────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")


# ─── CollectorResult ─────────────────────────────────────────────────────────

@dataclass
class CollectorResult:
    source: str
    status: str = "pending"          # ok | partial | failed
    data: dict = field(default_factory=dict)
    error: str | None = None
    latency_ms: int = 0


# ─── Telegram helper ─────────────────────────────────────────────────────────

def tg_send(text: str) -> str | int:
    """Send a Telegram message. Returns status code or error string."""
    if not TG_TOKEN or not TG_CHAT:
        return "no credentials"
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except Exception as e:
        return f"Error: {e}"


# ─── Collectors ──────────────────────────────────────────────────────────────

def collect_tasks(today: date) -> CollectorResult:
    """Scan prior days for carried-forward tasks."""
    t0 = time.monotonic()
    try:
        from morning_prep import (
            read_note, extract_open_tasks, extract_closed_tasks, LOOKBACK_DAYS
        )

        all_closed: set[str] = set()
        open_by_key: dict[str, tuple] = {}

        for offset in range(1, LOOKBACK_DAYS + 1):
            scan_date = today - timedelta(days=offset)
            note_text = read_note(scan_date)
            if not note_text:
                continue
            all_closed.update(extract_closed_tasks(note_text))
            for task in extract_open_tasks(note_text):
                key = task.lower()
                if key not in open_by_key:
                    open_by_key[key] = (task, scan_date, offset)

        carried = [v for k, v in open_by_key.items() if k not in all_closed]
        carried.sort(key=lambda item: item[2], reverse=True)

        return CollectorResult(
            source="tasks", status="ok",
            data={"carried": carried},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        return CollectorResult(
            source="tasks", status="failed", error=str(exc),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


def collect_calendar(today: date) -> CollectorResult:
    """Fetch today's calendar events."""
    t0 = time.monotonic()
    try:
        from morning_prep import get_today_events
        events = get_today_events(today)
        return CollectorResult(
            source="calendar", status="ok",
            data={"events": events},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        return CollectorResult(
            source="calendar", status="failed", error=str(exc),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


def collect_infra(today: date) -> CollectorResult:
    """Collect infrastructure health via morning_brief_push.build_block()."""
    t0 = time.monotonic()
    try:
        from morning_brief_push import build_block
        block = build_block(today)
        return CollectorResult(
            source="infra", status="ok",
            data={"block": block},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        return CollectorResult(
            source="infra", status="failed", error=str(exc),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


def collect_finance(today: date) -> CollectorResult:
    """Run Monarch ingest + ClickHouse queries + build finance block."""
    t0 = time.monotonic()
    ingest_status = "skipped"
    try:
        # Step 1: Monarch ingest (non-fatal)
        if os.environ.get("MONARCH_TOKEN"):
            try:
                import monarch_ingest
                asyncio.run(monarch_ingest.run(90))
                ingest_status = "ok"
            except Exception as exc:
                ingest_status = f"failed: {exc}"
                print(f"  [finance] Monarch ingest failed (non-fatal): {exc}")

        # Step 2: Query ClickHouse
        import finance_intel as fi
        balances          = fi.get_balances()
        monthly_summaries = fi.get_monthly_summary(months=4)
        current_month     = fi.get_current_month()
        top_cats          = fi.get_top_categories(days=30)
        fire              = fi.fire_calc(balances, monthly_summaries, current_month)
        anomalies         = fi.detect_anomalies()
        pace              = fi.budget_pace_alert(monthly_summaries, current_month)

        # Step 3: AI narrative (non-fatal)
        narrative = ""
        try:
            narrative = fi.generate_narrative(balances, monthly_summaries, fire, anomalies, top_cats)
        except Exception as exc:
            print(f"  [finance] AI narrative failed (non-fatal): {exc}")

        # Step 4: Build the Obsidian markdown block
        block = fi.build_finance_note(
            balances, monthly_summaries, fire, anomalies, pace, narrative, top_cats, today
        )

        return CollectorResult(
            source="finance", status="ok",
            data={
                "block": block,
                "balances": balances,
                "fire": fire,
                "anomalies": anomalies,
                "pace": pace,
                "monthly_summaries": monthly_summaries,
                "ingest_status": ingest_status,
            },
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        return CollectorResult(
            source="finance", status="failed", error=str(exc),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


PRIOR_LOOKBACK_DAYS = 3


def collect_prior_context(today: date) -> CollectorResult:
    """Read the prior N days of daily notes for AI continuity."""
    t0 = time.monotonic()
    try:
        note_store = resolve_note_store()
        summaries = []
        for offset in range(1, PRIOR_LOOKBACK_DAYS + 1):
            d = today - timedelta(days=offset)
            try:
                note = note_store.read_daily(d)
            except Exception:
                continue
            # Extract key sections — trim to keep prompt budget reasonable
            lines = note.split("\n")
            summary_parts = [f"--- {d.strftime('%A %b %d')} ---"]
            in_section = None
            section_lines = []
            for line in lines:
                if line.startswith("## "):
                    # Flush previous section
                    if in_section and section_lines:
                        # Keep first ~8 lines per section
                        summary_parts.append(f"[{in_section}]")
                        summary_parts.extend(section_lines[:8])
                        if len(section_lines) > 8:
                            summary_parts.append(f"  ... ({len(section_lines) - 8} more lines)")
                    in_section = line[3:].strip()
                    section_lines = []
                elif in_section in ("Infrastructure", "Finance", "Morning Intentions", "Tasks"):
                    stripped = line.strip()
                    # Skip callout prefixes and empty lines for brevity
                    if stripped and stripped != ">" and not stripped.startswith("*pending"):
                        # Remove Obsidian callout prefix for cleaner context
                        clean = stripped.lstrip("> ").rstrip()
                        if clean:
                            section_lines.append(f"  {clean}")
            # Flush last section
            if in_section and section_lines:
                summary_parts.append(f"[{in_section}]")
                summary_parts.extend(section_lines[:8])

            if len(summary_parts) > 1:  # more than just the date header
                summaries.append("\n".join(summary_parts))

        return CollectorResult(
            source="prior_context", status="ok" if summaries else "partial",
            data={"summaries": summaries, "days_found": len(summaries)},
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:
        return CollectorResult(
            source="prior_context", status="failed", error=str(exc),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


# ─── Cross-Validation ────────────────────────────────────────────────────────

def cross_validate(results: dict, carried_tasks: list, today: date) -> list[str]:
    """Pre-compute factual cross-checks across data sources. Returns a list of
    validated findings and flagged discrepancies the AI should address."""
    findings: list[str] = []

    finance = results.get("finance")
    prior   = results.get("prior_context")

    # ── Finance: net worth delta vs prior day ─────────────────────────────
    if finance and finance.status == "ok" and prior and prior.status in ("ok", "partial"):
        today_bal = (finance.data.get("balances") or {})
        today_nw = today_bal.get("net_worth", 0) if isinstance(today_bal, dict) else 0

        # Try to extract prior net worth from prior notes
        for summary in (prior.data.get("summaries") or []):
            import re as _re
            nw_match = _re.search(r"Net Worth.*?\$\s*([\d,.-]+)", summary)
            if nw_match:
                try:
                    prior_nw = float(nw_match.group(1).replace(",", ""))
                    delta = today_nw - prior_nw
                    if abs(delta) > 0:
                        direction = "up" if delta > 0 else "down"
                        findings.append(
                            f"VALIDATED: Net worth ${today_nw:,.0f} ({direction} ${abs(delta):,.0f} "
                            f"from prior ${prior_nw:,.0f})"
                        )
                        # Flag suspicious jumps (>$10k in a day without explanation)
                        if abs(delta) > 10_000:
                            findings.append(
                                f"FLAG: Net worth moved ${abs(delta):,.0f} in one day — "
                                f"verify this is real (market swing? data lag? account sync issue?)"
                            )
                except (ValueError, TypeError):
                    pass
                break  # only compare against most recent prior day

    # ── Finance: spending pace vs actual transactions ─────────────────────
    if finance and finance.status == "ok":
        pace = finance.data.get("pace") or {}
        monthly = finance.data.get("monthly_summaries") or []
        if pace and monthly:
            current_spend = pace.get("current_spend", 0)
            avg_spend = pace.get("avg_spend", 0)
            if avg_spend > 0 and current_spend > 0:
                ratio = current_spend / avg_spend
                if ratio > 1.5:
                    findings.append(
                        f"FLAG: Current month spending ${current_spend:,.0f} is {ratio:.1f}x "
                        f"the monthly average ${avg_spend:,.0f} — investigate largest transactions"
                    )
                elif ratio < 0.3 and pace.get("days_elapsed", 0) > 10:
                    findings.append(
                        f"FLAG: Spending unusually low (${current_spend:,.0f} vs avg "
                        f"${avg_spend:,.0f}) — possible data sync delay from Monarch"
                    )

        # FIRE sanity check
        fire = finance.data.get("fire") or {}
        if fire:
            savings_rate = fire.get("savings_rate", 0)
            years = fire.get("years_to_fire", 0)
            if savings_rate < 0:
                findings.append(
                    f"FLAG: Negative savings rate ({savings_rate:.1f}%) — "
                    f"spending exceeds income this period"
                )
            if years and years > 50:
                findings.append(
                    f"FLAG: FIRE timeline {years:.0f} years is unrealistic — "
                    f"likely a low-sample-size month skewing the calculation"
                )

    # ── Infra: persistent issues across days ─────────────────────────────
    if prior and prior.status in ("ok", "partial"):
        import re as _re
        offline_hosts_today = set()
        infra = results.get("infra")
        if infra and infra.status == "ok":
            block = infra.data.get("block", "")
            # Find offline hosts in today's block
            for match in _re.finditer(r"~~(\w+)~~.*?offline", block, _re.IGNORECASE):
                offline_hosts_today.add(match.group(1).lower())
            # Find high-memory hosts
            for match in _re.finditer(r"\*\*(\w+)\*\*.*?(\d+)%\s*🔴", block):
                host = match.group(1).lower()
                pct = int(match.group(2))
                # Check if this was also flagged in prior days
                prior_mentions = sum(
                    1 for s in (prior.data.get("summaries") or [])
                    if host in s.lower() and ("🔴" in s or "critical" in s.lower())
                )
                if prior_mentions > 0:
                    findings.append(
                        f"PERSISTENT: {host.capitalize()} at {pct}% — flagged {prior_mentions + 1} "
                        f"consecutive days. This needs action, not monitoring."
                    )

        # Check if offline hosts were also offline in prior days
        for host in offline_hosts_today:
            prior_offline = sum(
                1 for s in (prior.data.get("summaries") or [])
                if f"~~{host}" in s.lower() or (host in s.lower() and "offline" in s.lower())
            )
            if prior_offline > 0:
                findings.append(
                    f"PERSISTENT: {host.capitalize()} has been offline for {prior_offline + 1}+ days. "
                    f"Is this intentional or does it need investigation?"
                )

    # ── Tasks: stale task detection ──────────────────────────────────────
    if carried_tasks:
        stale = [t for t in carried_tasks if t[2] >= 3]
        if stale:
            findings.append(
                f"STALE TASKS: {len(stale)} task(s) carried 3+ days without progress: "
                + ", ".join(f'"{t[0][:50]}" ({t[2]}d)' for t in stale[:3])
            )
        if len(carried_tasks) > 6:
            findings.append(
                f"FLAG: {len(carried_tasks)} carried tasks is a lot — consider trimming "
                f"tasks that are no longer relevant rather than carrying them indefinitely"
            )

    return findings


# ─── AI Synthesis ────────────────────────────────────────────────────────────

def ai_synthesis(carried_tasks, calendar_events, infra_result, finance_result, prior_context, validations, today):
    """Single Claude CLI call for morning focus items + Telegram summary."""
    claude_cmd = resolve_claude_command()
    if not claude_cmd:
        return [], "", []

    # Build context for the AI
    context_parts = [f"Today is {today.strftime('%A, %B %d, %Y')}. San Antonio, TX."]

    if carried_tasks:
        task_list = "\n".join(f"- {t[0]} ({t[2]}d old)" for t in carried_tasks[:8])
        context_parts.append(f"Carried tasks:\n{task_list}")

    if calendar_events:
        context_parts.append(f"Calendar:\n" + "\n".join(calendar_events))
    else:
        context_parts.append("Calendar: No events scheduled.")

    if infra_result.status == "ok":
        # Extract key stats from the block (VM count, alerts) rather than sending the whole block
        block = infra_result.data.get("block", "")
        # Send a trimmed summary to keep prompt short
        block_lines = block.split("\n")[:15]
        context_parts.append(f"Infra summary:\n" + "\n".join(block_lines))
    else:
        context_parts.append(f"Infra: unavailable ({infra_result.error})")

    if finance_result.status == "ok":
        fd = finance_result.data
        fire = fd.get("fire") or {}
        pace = fd.get("pace") or {}
        anomalies = fd.get("anomalies") or []

        finance_summary = []
        # Balances is a dict: {investments, cash, mortgage, credit, net_worth}
        bal = fd.get("balances") or {}
        if isinstance(bal, dict) and bal:
            finance_summary.append(f"  Net Worth: ${bal.get('net_worth', 0):,.0f}")
            finance_summary.append(f"  Investments: ${bal.get('investments', 0):,.0f}")
            finance_summary.append(f"  Cash: ${bal.get('cash', 0):,.0f}")
            finance_summary.append(f"  Mortgage: ${bal.get('mortgage', 0):,.0f}")
        if fire:
            finance_summary.append(f"  FIRE: {fire.get('progress_pct',0):.1f}% → {fire.get('fire_year','?')}")
            finance_summary.append(f"  Savings rate: {fire.get('savings_rate',0):.1f}%")
        if pace:
            finance_summary.append(f"  Spending pace: {pace.get('pct_of_avg',0):.0f}% of avg")
        if anomalies:
            anom_str = ", ".join(str(a.get('category','?')) if isinstance(a, dict) else str(a) for a in anomalies[:3])
            finance_summary.append(f"  Anomalies: {anom_str}")
        context_parts.append("Finance:\n" + "\n".join(finance_summary))
    else:
        context_parts.append(f"Finance: unavailable ({finance_result.error})")

    # Prior days context
    if prior_context and prior_context.status in ("ok", "partial"):
        summaries = prior_context.data.get("summaries", [])
        if summaries:
            context_parts.append("Prior days (for continuity):\n" + "\n\n".join(summaries))

    # Cross-validation findings — these are pre-computed factual checks
    if validations:
        context_parts.append(
            "Cross-validation findings (THESE ARE FACTUAL — address each one):\n"
            + "\n".join(f"- {v}" for v in validations)
        )

    context = "\n\n".join(context_parts)

    prompt = f"""You are BlunderBus, Brian's personal AI assistant and infrastructure operator.
You are NOT a report formatter. You are an analyst. Your job is to validate data, flag what doesn't add up, and give Brian real insight — not summaries of numbers he can read himself.

Given today's context, prior days, and cross-validation findings, produce EXACTLY this JSON (no markdown fences, no explanation):

{{"focus": ["item 1", "item 2", "item 3"], "telegram": "brief 2-3 sentence morning brief", "flags": ["discrepancy or concern 1", "..."]}}

Rules:
- "focus": 3 actionable items prioritized by urgency. Persistent issues (flagged multiple days) rank higher than new ones. Stale tasks should be called out directly.
- "telegram": Condensed push (under 300 chars). Lead with the most critical finding. Include specific numbers. If something doesn't add up, say so.
- "flags": List any data discrepancies, suspicious changes, or things that need human verification. Empty list if everything checks out. Examples: "Net worth jumped $15k overnight — verify account sync", "FIRE timeline changed from 16y to 18y — check if income data is stale", "Spending shows $0 with 10 days elapsed — Monarch sync may be broken".
- Cross-validation findings marked VALIDATED are confirmed facts — use them confidently. Findings marked FLAG need your judgment — explain whether it's a real concern or expected.
- NEVER parrot numbers without context. Always compare to prior days or averages.
- Be direct. No encouragement, no "great job", no filler.

Context:
{context}"""

    try:
        result = subprocess.run(
            [claude_cmd, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            cwd=os.path.expanduser("~"),
        )
        if result.returncode != 0:
            print(f"  [ai] Claude CLI returned {result.returncode}")
            return [], ""

        raw = result.stdout.strip()
        # Parse JSON from response (handle potential markdown fences)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        focus = parsed.get("focus", [])[:3]
        telegram = parsed.get("telegram", "")
        flags = parsed.get("flags", [])
        return focus, telegram, flags
    except json.JSONDecodeError as exc:
        print(f"  [ai] JSON parse failed: {exc}")
        return [], "", []
    except Exception as exc:
        print(f"  [ai] Synthesis failed: {exc}")
        return [], "", []


# ─── Telegram Delivery ───────────────────────────────────────────────────────

def build_telegram_brief(today, results, focus_items, ai_summary, ai_flags, elapsed_s):
    """Build condensed Telegram push message."""
    day_str = today.strftime("%a %b %d").replace(" 0", " ")
    lines = [f"📋 *BlunderBus Morning Brief — {day_str}*\n"]

    # Finance
    fr = results.get("finance")
    if fr and fr.status == "ok":
        fd = fr.data
        fire = fd.get("fire") or {}
        pace = fd.get("pace") or {}
        # Balances is a dict: {investments, cash, mortgage, credit, net_worth}
        bal = fd.get("balances") or {}
        net_worth = bal.get("net_worth", 0) if isinstance(bal, dict) else 0
        pace_icon = "🔴" if pace and pace.get("status") == "OVER" else "🟢"
        pace_pct = f"{pace['pct_of_avg']:.0f}%" if pace else "?"
        lines.append(f"💰 Finance: Net worth ${net_worth:,.0f} · Pace {pace_icon} {pace_pct}")
    elif fr:
        lines.append(f"💰 Finance: ❌ {fr.error or 'unavailable'}")

    # Infra
    ir = results.get("infra")
    if ir and ir.status == "ok":
        lines.append("🖥️ Infra: ✅ Report generated")
    elif ir:
        lines.append(f"🖥️ Infra: ❌ {ir.error or 'unavailable'}")

    # Calendar
    cr = results.get("calendar")
    if cr and cr.status == "ok":
        events = cr.data.get("events", [])
        lines.append(f"📅 Calendar: {len(events)} event(s)")
    else:
        lines.append("📅 Calendar: unavailable")

    # AI summary
    if ai_summary:
        lines.append(f"\n{ai_summary}")

    # Data quality flags
    if ai_flags:
        lines.append("")
        for flag in ai_flags[:3]:
            lines.append(f"🚩 {flag}")

    # Status
    failed = [r.source for r in results.values() if r.status == "failed"]
    if failed:
        lines.append(f"\n⚠️ Failed: {', '.join(failed)}")
        lines.append(f"⏱️ Report completed in {elapsed_s:.0f}s (partial)")
    else:
        lines.append(f"\n✅ Report delivered in {elapsed_s:.0f}s")

    return "\n".join(lines)


# ─── Orchestrator ────────────────────────────────────────────────────────────

def orchestrate(args):
    t_start = time.monotonic()
    today = date.fromisoformat(args.date) if args.date else date.today()
    note_store = resolve_note_store()

    print(f"\n{'='*60}")
    print(f"  BlunderBus Daily Report — {today.isoformat()}")
    print(f"{'='*60}\n")

    # ── Step 1: Idempotency check ────────────────────────────────────────────
    if not args.force and note_store.daily_exists(today):
        try:
            existing = note_store.read_daily(today)
            pending_sections = []
            for marker in [
                "*pending - BlunderBus will populate at 06:30*",
                "*pending - BlunderBus will populate at 07:30*",
            ]:
                if marker in existing:
                    pending_sections.append(marker)
            if not pending_sections:
                print("  Note exists and all sections populated — skipping (use --force to override)")
                return
            print(f"  Note exists but {len(pending_sections)} section(s) still pending — continuing")
        except (NoteStoreError, FileNotFoundError):
            pass  # note doesn't exist yet, proceed normally

    # ── Step 2: Collect tasks + calendar (needed for note template) ──────────
    print("  [step 2] Collecting tasks and calendar...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        ft_tasks = pool.submit(collect_tasks, today)
        ft_cal   = pool.submit(collect_calendar, today)
        task_result = ft_tasks.result(timeout=60)
        cal_result  = ft_cal.result(timeout=60)

    carried = task_result.data.get("carried", []) if task_result.status == "ok" else []
    events  = cal_result.data.get("events", []) if cal_result.status == "ok" else []

    print(f"    Tasks: {len(carried)} carried | Calendar: {len(events)} events")

    # ── Step 3: Create daily note if it doesn't exist ────────────────────────
    note_created = False
    if not note_store.daily_exists(today):
        print("  [step 3] Creating daily note...")
        from morning_prep import build_note, schedule_task_review
        # Pass intentions=None — we'll inject AI focus items in Step 6
        note_content = build_note(today, carried, events, intentions=None)
        try:
            note_store.write_daily(today, note_content)
            note_created = True
            print(f"    Created: {note_store.daily_path(today)}")
        except NoteStoreError as exc:
            print(f"    ERROR: Failed to create note: {exc}")
            tg_send(f"❌ Daily report failed: could not create note\n{exc}")
            sys.exit(1)

        # Schedule task review on calendar if there are carried tasks
        if carried:
            try:
                schedule_task_review(carried, today)
                print("    Task Review event scheduled")
            except Exception as exc:
                print(f"    WARN: Task Review scheduling failed: {exc}")
    else:
        print("  [step 3] Note already exists — skipping creation")

    # ── Step 4: Parallel data collection (infra + finance) ───────────────────
    print("  [step 4] Collecting infrastructure and finance data...")
    results: dict[str, CollectorResult] = {
        "tasks": task_result,
        "calendar": cal_result,
    }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(collect_infra, today): "infra",
            pool.submit(collect_finance, today): "finance",
            pool.submit(collect_prior_context, today): "prior_context",
        }
        for future in as_completed(futures, timeout=300):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = CollectorResult(source=name, status="failed", error=str(exc))

    for name in ["infra", "finance", "prior_context"]:
        r = results[name]
        icon = "✅" if r.status == "ok" else ("⚠️" if r.status == "partial" else "❌")
        extra = ""
        if name == "prior_context" and r.status in ("ok", "partial"):
            extra = f" ({r.data.get('days_found', 0)} days)"
        print(f"    {icon} {name}: {r.status} ({r.latency_ms}ms){extra}{' — ' + r.error if r.error else ''}")

    # ── Step 4b: Cross-validate data ────────────────────────────────────────
    print("  [step 4b] Cross-validating data sources...")
    validations = cross_validate(results, carried, today)
    for v in validations:
        prefix = "    🔍" if v.startswith("VALIDATED") else "    ⚠️"
        print(f"{prefix} {v}")
    if not validations:
        print("    No cross-validation findings")

    # ── Step 5: AI synthesis ─────────────────────────────────────────────────
    print("  [step 5] Running AI synthesis...")
    focus_items, ai_summary, ai_flags = ai_synthesis(
        carried, events,
        results.get("infra", CollectorResult(source="infra", status="failed")),
        results.get("finance", CollectorResult(source="finance", status="failed")),
        results.get("prior_context", CollectorResult(source="prior_context", status="failed")),
        validations,
        today,
    )
    print(f"    Focus items: {len(focus_items)} | Telegram summary: {'yes' if ai_summary else 'no'}")
    if ai_flags:
        print(f"    AI flags: {len(ai_flags)}")
        for flag in ai_flags:
            print(f"      🚩 {flag}")

    # ── Step 6: Inject all sections into the note ────────────────────────────
    if not args.dry_run:
        print("  [step 6] Injecting sections into daily note...")
        try:
            note_body = note_store.read_daily(today)
        except (NoteStoreError, FileNotFoundError) as exc:
            print(f"    ERROR: Cannot read note: {exc}")
            tg_send(f"❌ Daily report failed: cannot read note\n{exc}")
            sys.exit(1)

        modified = False

        # Infrastructure
        if results.get("infra") and results["infra"].status == "ok":
            block = results["infra"].data.get("block", "")
            if block:
                note_body = upsert_section(note_body, "## Infrastructure", block)
                modified = True
                print("    ✅ Infrastructure section injected")
        else:
            infra_err = results.get("infra", CollectorResult(source="infra")).error or "collector failed"
            note_body = upsert_section(
                note_body, "## Infrastructure",
                f"*Infrastructure data unavailable: {infra_err}*\n",
            )
            modified = True
            print(f"    ⚠️ Infrastructure section: unavailable ({infra_err})")

        # Finance
        if results.get("finance") and results["finance"].status == "ok":
            block = results["finance"].data.get("block", "")
            if block:
                note_body = upsert_section(note_body, "## Finance", block)
                modified = True
                print("    ✅ Finance section injected")
        else:
            fin_err = results.get("finance", CollectorResult(source="finance")).error or "collector failed"
            note_body = upsert_section(
                note_body, "## Finance",
                f"*Finance data unavailable: {fin_err}*\n",
            )
            modified = True
            print(f"    ⚠️ Finance section: unavailable ({fin_err})")

        # Morning Intentions (AI focus items)
        if focus_items:
            intentions_block = "> [!tip]+ Today's Focus - AI Suggested\n"
            intentions_block += "\n".join(f"> - [ ] {item}" for item in focus_items)
            note_body = upsert_section(note_body, "## Morning Intentions", intentions_block)
            modified = True
            print(f"    ✅ Morning Intentions injected ({len(focus_items)} items)")

        # Write back
        if modified:
            try:
                note_store.write_daily(today, note_body)
                print(f"    Note saved: {note_store.daily_path(today)}")
            except NoteStoreError as exc:
                print(f"    ERROR: Failed to write note: {exc}")
    else:
        print("  [step 6] DRY RUN — skipping note injection")
        if results.get("infra") and results["infra"].status == "ok":
            print(f"    Would inject Infrastructure ({len(results['infra'].data.get('block', ''))} chars)")
        if results.get("finance") and results["finance"].status == "ok":
            print(f"    Would inject Finance ({len(results['finance'].data.get('block', ''))} chars)")

    # ── Step 7: Validate ─────────────────────────────────────────────────────
    validation_ok = True
    if not args.dry_run:
        print("  [step 7] Validating note...")
        try:
            final_note = note_store.read_daily(today)
            # Check for remaining pending markers (Health is expected to stay pending)
            remaining = []
            for marker, section in [
                ("*pending - BlunderBus will populate at 06:30*", "Infrastructure"),
                ("*pending - BlunderBus will populate at 07:30*", "Finance"),
            ]:
                if marker in final_note:
                    remaining.append(section)
            if remaining:
                print(f"    ⚠️ Still pending: {', '.join(remaining)}")
                validation_ok = False
            else:
                print("    ✅ All sections populated")
        except Exception as exc:
            print(f"    ⚠️ Validation read failed: {exc}")
            validation_ok = False

    # ── Step 8: Telegram delivery ────────────────────────────────────────────
    elapsed = time.monotonic() - t_start
    if not args.dry_run and not args.no_telegram:
        print("  [step 8] Sending Telegram brief...")
        msg = build_telegram_brief(today, results, focus_items, ai_summary, ai_flags, elapsed)
        status = tg_send(msg)
        print(f"    Telegram: {status}")

        # Send anomaly alerts
        fr = results.get("finance")
        if fr and fr.status == "ok":
            anomalies = fr.data.get("anomalies", [])
            pace = fr.data.get("pace")
            if pace and pace.get("status") == "OVER":
                tg_send(
                    f"🔴 *Budget Pace Alert*\n"
                    f"On track to spend *${pace['projected_spend']:,.0f}* this month "
                    f"(avg ${pace['avg_spend']:,.0f}, {pace['pct_of_avg']:.0f}% of normal)."
                )
            for a in anomalies:
                if a.get("severity") == "HIGH":
                    tg_send(
                        f"⚠️ *Spending Spike: {a['category']}*\n"
                        f"${a['current']:,.0f} so far → projected ${a['projected']:,.0f} "
                        f"vs avg ${a['avg']:,.0f}"
                    )
    elif args.dry_run:
        print("  [step 8] DRY RUN — Telegram message preview:")
        msg = build_telegram_brief(today, results, focus_items, ai_summary, ai_flags, elapsed)
        print(msg)
    else:
        print("  [step 8] Telegram: skipped (--no-telegram)")

    # ── Step 9: Log to ClickHouse ────────────────────────────────────────────
    try:
        log_life_event(
            domain="projects",
            event_type="daily_report",
            source="daily_report",
            summary=f"Daily report for {today.isoformat()} — "
                    f"{'complete' if validation_ok else 'partial'}",
            detail={
                "date": today.isoformat(),
                "collectors": {
                    name: {"status": r.status, "latency_ms": r.latency_ms, "error": r.error}
                    for name, r in results.items()
                },
                "focus_items": len(focus_items),
                "elapsed_s": round(elapsed, 1),
                "validation_ok": validation_ok,
            },
            tags=["daily-report", "projects"],
        )
    except Exception:
        pass  # non-fatal

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    failed = [r.source for r in results.values() if r.status == "failed"]
    if failed:
        print(f"  ⚠️ PARTIAL — failed: {', '.join(failed)} ({elapsed:.1f}s)")
    else:
        print(f"  ✅ COMPLETE ({elapsed:.1f}s)")
    print(f"{'='*60}\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BlunderBus Unified Daily Report")
    parser.add_argument("--date", default=None, help="Override date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print output without writing")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram notifications")
    parser.add_argument("--force", action="store_true", help="Overwrite already-populated sections")
    args = parser.parse_args()

    try:
        orchestrate(args)
    except SystemExit:
        raise
    except Exception:
        # Safety net — even crashes send a Telegram notification
        err = traceback.format_exc()
        print(f"\n❌ FATAL ERROR:\n{err}")
        tg_send(f"❌ *Daily report crashed*\n```\n{err[-400:]}\n```")
        sys.exit(2)


if __name__ == "__main__":
    main()
