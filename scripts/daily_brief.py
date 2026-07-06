#!/usr/bin/env python3
"""
BlunderBus DailyBrief orchestrator.

Single entry point that fans out to domain agents in parallel, collects their
AgentReports, runs ONE AI synthesis pass, and composes:
  - one Obsidian note section ("## Briefing")
  - one Telegram summary message

Replaces the old daily_report.py / morning_brief_push.py / finance_intel.py
output paths. Memory + suppression + anomaly-detection logic all stays in the
agents — the orchestrator is purely composition.

Usage:
    py scripts/daily_brief.py                # full run
    py scripts/daily_brief.py --dry-run      # don't write/send, just print
    py scripts/daily_brief.py --no-ai        # skip AI synthesis
    py scripts/daily_brief.py --date 2026-04-15
    py scripts/daily_brief.py --agents finance,infra   # subset
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "agents"))

# UTF-8 stdout for Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from base import AgentReport, Concern  # noqa: E402

# Registry: agent name → import path. Add a new domain by adding one line here.
AGENT_REGISTRY = {
    "finance":   "agents.finance",
    "infra":     "agents.infra",
    "workspace": "agents.workspace",
    # "security":  "agents.security",    # phase 4
}


# ── Fan-out ──────────────────────────────────────────────────────────────────


def run_agents(agent_names: list[str], today: date, parallel: bool = True) -> list[AgentReport]:
    """Invoke each requested agent's run(today). Returns reports in registry order."""
    import importlib

    def _invoke(name: str) -> AgentReport:
        try:
            mod = importlib.import_module(AGENT_REGISTRY[name])
            return mod.run(today)
        except Exception as exc:
            return AgentReport.failed(name, f"orchestrator import/invoke error: {exc}")

    if not parallel:
        return [_invoke(n) for n in agent_names]

    reports: dict[str, AgentReport] = {}
    with ThreadPoolExecutor(max_workers=len(agent_names)) as pool:
        futures = {pool.submit(_invoke, n): n for n in agent_names}
        for fut in as_completed(futures):
            name = futures[fut]
            reports[name] = fut.result()
    return [reports[n] for n in agent_names if n in reports]


# ── AI synthesis ─────────────────────────────────────────────────────────────


SYNTHESIS_PROMPT = """You are Brian's chief of staff for his home AI ops platform — a sharp,
familiar assistant who's been watching the system overnight and is giving him a
morning update over coffee. You know the lab inside out: the VMs, the people, the
finances, the projects. You've been doing this for months.

Below are structured reports from each domain agent (finance, infra, workspace, etc.).
Each agent has already done its anomaly detection and suppressed known/explained events.

Produce the briefing in EXACTLY this format with these literal markers:

===TLDR===
<One conversational sentence — the way you'd open if you walked into his office.
Concrete and specific. Not "all systems healthy" generic. Something like:
"Quiet night across the lab — only thing flickering is Stark's memory at 93%, same as
yesterday." or "Heads up: AdGuard crashed at 3am and DNS was out for a couple hours
before it self-recovered." No emojis.>

===BRIEFING===
<2–3 paragraphs in a natural, spoken-aloud voice. Talk like a trusted operator who
has the whole picture. Fold specific numbers into sentences ("the 17 hosts are all
checking in", "net worth ticked up to $242k", "201 unread in Gmail — same backlog
as the last six days"). Connect dots between domains. Reference carried concerns
naturally with their age ("Stark's been running hot for 5 days now"). Do NOT re-flag
expected_events. Do NOT repeat the TL;DR verbatim. Use second person ("you") sparingly
and only when there's something Brian himself needs to do or know. Avoid corporate
jargon — write like you're actually talking. No bullet points, no headers, no emojis.>

===ACTIONS===
<0–3 specific things you recommend Brian do today. One per line. Imperative voice
("Bring Stark RAM under 90% — wireguard is the heaviest container right now").
Each line ≤ 130 chars. Each should explain WHY in the same line. Skip the section
entirely (nothing between markers) if today is genuinely clean.>

REGISTRY CONTEXT (project blockers, host inventory) — use to be specific in the briefing
and actions. When you reference a project by name, mention its named blockers if relevant.
{registry_context}

CONCERN LINEAGE (from the agent_concerns table — first_seen, days_active) — use to give
real ages instead of guessing. "Stark's been hot for 5 days" beats "Stark's been hot for a while".
{concerns_context}

CROSS-VALIDATION FLAGS — pre-computed sanity checks across the reports. If any
appear, address them directly in BRIEFING or ACTIONS (don't ignore them).
{validations_context}

Reports:
{report_payload}"""


def _parse_synthesis(raw: str) -> dict:
    """Split the AI synthesis output into {tldr, actions, briefing} sections.
    Tolerant of missing sections — returns empty strings for any not found."""
    out = {"tldr": "", "actions": [], "briefing": "", "raw": raw or ""}
    if not raw:
        return out
    parts = re.split(r"^===(\w+)===\s*$", raw, flags=re.MULTILINE)
    # parts is [pre, key1, body1, key2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        key = parts[i].strip().lower()
        body = parts[i + 1].strip()
        if key == "tldr":
            out["tldr"] = body
        elif key == "actions":
            out["actions"] = [ln.strip(" -•·*") for ln in body.splitlines() if ln.strip()]
        elif key == "briefing":
            out["briefing"] = body
    # Fallback: if the LLM ignored the format, treat the whole thing as briefing
    if not (out["tldr"] or out["actions"] or out["briefing"]):
        out["briefing"] = raw.strip()
    return out


def _build_registry_context() -> str:
    """Render a tight summary of registry state — active/blocked projects with their
    named blockers — so the synthesizer can reference specifics instead of generics.

    Returns a markdown-ish string. Empty string if registry unavailable."""
    try:
        from blunderbus_memory import (  # noqa: E402
            ProjectStatus, get_default_registry,
        )
        reg = get_default_registry()
        lines: list[str] = []
        active = reg.projects.list(status=ProjectStatus.ACTIVE)
        blocked = reg.projects.list(status=ProjectStatus.BLOCKED)
        if blocked:
            lines.append("Blocked projects (named blockers in priority order):")
            for p in blocked:
                lines.append(f"- {p.id} — {p.name}")
                for b in (p.blockers or [])[:5]:
                    lines.append(f"    · {b}")
        if active:
            lines.append("Active projects:")
            for p in active:
                bl = f" — blockers: {'; '.join(p.blockers[:2])}" if p.blockers else ""
                lines.append(f"- {p.id} — {p.name}{bl}")
        return "\n".join(lines) if lines else "(no project context)"
    except Exception as exc:
        return f"(registry context unavailable: {exc})"


# ── Cross-validation (ported from daily_report.py) ───────────────────────────
#
# Per-agent anomaly detection and Postgres concern lineage cover most "this is
# wrong" signals already. validate_reports() adds the cross-cutting sanity
# checks that don't fit cleanly inside one agent — they operate on the
# combined AgentReport set after fanout and produce findings the AI must
# address in the briefing.

def validate_reports(reports: list[AgentReport]) -> list[str]:
    """Cross-cutting validations across AgentReports. Returns a flat list of
    short findings prefixed with the kind (FLAG / VALIDATED / STALE)."""
    findings: list[str] = []
    by_agent = {r.agent: r for r in reports}

    # ── Finance: spending pace + FIRE sanity ────────────────────────────────
    fin = by_agent.get("finance")
    if fin and fin.status in ("ok", "degraded"):
        m = fin.metrics or {}
        pct = m.get("pace_pct_of_avg_adjusted")
        cur = m.get("current_spend_adjusted")
        avg = m.get("avg_spend") or 0
        elapsed = m.get("days_elapsed", 0)
        if pct is not None and avg > 0:
            if pct > 150:
                findings.append(
                    f"FLAG: current month discretionary spending ${cur:,.0f} is "
                    f"{pct/100:.1f}× the monthly average ${avg:,.0f} — investigate"
                )
            elif pct < 30 and elapsed > 10:
                findings.append(
                    f"FLAG: spending unusually low (${cur:,.0f} vs avg ${avg:,.0f}) — "
                    f"possible Monarch sync delay; verify ingest freshness"
                )
        sr = m.get("savings_rate_pct")
        if sr is not None and sr < 0:
            findings.append(
                f"FLAG: negative savings rate ({sr:.1f}%) — spending exceeds "
                f"income this period"
            )
        yrs = m.get("years_to_fire")
        if yrs and yrs > 50:
            findings.append(
                f"FLAG: FIRE timeline {yrs:.0f}y is unrealistic — likely a "
                f"low-sample-size month skewing the calculation"
            )

    # ── Workspace: too many carried tasks ───────────────────────────────────
    ws = by_agent.get("workspace")
    if ws:
        m = ws.metrics or {}
        obs_open = m.get("obsidian_tasks_open") or 0
        if obs_open >= 10:
            findings.append(
                f"FLAG: {obs_open} carried tasks in TASKS.md — past the point "
                f"where carrying them forward beats trimming"
            )

    return findings


def _build_validations_context(findings: list[str]) -> str:
    if not findings:
        return "(no cross-validation flags this run)"
    return "Cross-validation findings (must be addressed in briefing or actions):\n" + \
           "\n".join(f"- {f}" for f in findings)


# ── Idempotency check (ported from daily_report.py) ──────────────────────────

PENDING_MARKERS = (
    "BlunderBus is preparing today's briefing",
    "*pending - BlunderBus will populate",
)


def _ensure_note_shell(today: date, reports: list[AgentReport]) -> None:
    """Create today's note from the template if it doesn't already exist,
    AND keep the Schedule + Tasks sections in sync with their live sources
    on every run (TASKS.md changes between 6 AM and 8 AM should reflect).

    Schedule comes from the workspace agent's calendar fetch (already in
    raw_data); Tasks come from TASKS.md `## Active` and `## Ops — Needs
    Attention` sections — the single source of truth (no more daily-note
    carry-forward).
    """
    from note_store import resolve_note_store, upsert_section
    from note_template import (
        build_note_shell,
        read_active_tasks,
        render_schedule_block,
        render_tasks_block,
    )

    store = resolve_note_store()
    workspace = next((r for r in reports if r.agent == "workspace"), None)
    events = (workspace.raw_data.get("events") if workspace and workspace.raw_data else None) or []
    active_tasks = read_active_tasks()

    if not store.daily_exists(today):
        shell = build_note_shell(today, events, active_tasks)
        try:
            store.write_daily(today, shell)
            print(f"  ✓ Created daily note shell for {today} ({len(events)} events, {len(active_tasks)} active tasks)")
        except Exception as exc:
            print(f"  ⚠ Could not create daily note shell: {exc}", file=sys.stderr)
        return

    # Note already exists — refresh Schedule + Tasks sections in place. We
    # preserve everything else (Briefing, Today's Focus, Notes & Captures,
    # Projects & Lab, Evening Review) so manual edits aren't clobbered.
    try:
        note = store.read_daily(today)
        note = upsert_section(note, "## Schedule", render_schedule_block(events) + "\n")
        note = upsert_section(note, "## Tasks", render_tasks_block(active_tasks) + "\n")
        store.write_daily(today, note)
        print(f"  ✓ Refreshed Schedule + Tasks sections ({len(events)} events, {len(active_tasks)} active tasks)")
    except Exception as exc:
        print(f"  ⚠ Could not refresh Schedule/Tasks sections: {exc}", file=sys.stderr)


def briefing_already_populated(today: date) -> bool:
    """True if today's note has a Briefing section with real content (not the
    pending placeholder). Used so re-runs don't clobber a synthesised brief
    that's already in place."""
    try:
        from note_store import resolve_note_store
        note = resolve_note_store().read_daily(today)
    except Exception:
        return False
    m = re.search(r"^## Briefing\b(.*?)(?=^## |\Z)", note, re.MULTILINE | re.DOTALL)
    if not m:
        return False
    body = m.group(1).strip()
    if not body:
        return False
    return not any(marker in body for marker in PENDING_MARKERS)


def _build_concerns_context() -> str:
    """Render concern lineage from Postgres — id, severity, age in days, agent.
    Lets the synthesizer give real ages instead of vague hand-waves."""
    try:
        if not os.environ.get("BLUNDERBUS_DB_PASSWORD") and os.environ.get("BW_MASTER_PASS"):
            from vault import load_secrets  # type: ignore
            load_secrets()
        from blunderbus_memory.concerns import PostgresConcerns
        with PostgresConcerns() as store:
            active = store.list_active()
        if not active:
            return "(no active concerns in agent_concerns table)"
        lines = ["Active concerns from agent_concerns (truth source for age):"]
        for c in active:
            age = c.days_seen
            age_str = f"{age}d" if age >= 1 else "today"
            target = f" target={c.target}" if c.target else ""
            lines.append(
                f"- [{c.agent}] [{c.severity.value}] {c.summary[:90]} "
                f"(first_seen={c.first_seen.date().isoformat() if c.first_seen else '?'}, "
                f"age={age_str}{target})"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"(concerns context unavailable: {exc})"


def ai_synthesize(reports: list[AgentReport], validations: list[str] | None = None) -> str:
    """Single AI call across all agent reports. Uses local `claude` CLI."""
    import subprocess
    import os
    import json

    payload_parts = []
    for r in reports:
        d = r.to_dict()
        # Trim raw_data — synthesizer doesn't need full dumps, just the structured concerns
        d.pop("raw_data", None)
        payload_parts.append(f"--- {r.agent} ---\n{json.dumps(d, indent=2, default=str)}")
    payload = "\n\n".join(payload_parts)

    registry_context = _build_registry_context()
    concerns_context = _build_concerns_context()
    validations_context = _build_validations_context(validations or [])
    prompt = SYNTHESIS_PROMPT.format(
        report_payload=payload,
        registry_context=registry_context,
        concerns_context=concerns_context,
        validations_context=validations_context,
    )
    from runtime import resolve_claude_command

    claude_cmd = resolve_claude_command()
    if not claude_cmd:
        msg = "(AI synthesis unavailable: claude CLI not found on PATH or via CLAUDE_BIN/CLAUDE_CMD)"
        logging.warning(msg)
        print(f"  ⚠ {msg}")
        return msg

    try:
        result = subprocess.run(
            [claude_cmd, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True, text=True, timeout=90,
            encoding="utf-8",
            cwd=os.path.expanduser("~"),
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or (result.stdout or "").strip() or f"exit {result.returncode}"
            msg = f"(AI synthesis failed: {err[:300]})"
            logging.warning(msg)
            print(f"  ⚠ {msg}")
            return msg
        return result.stdout.strip()
    except Exception as exc:
        msg = f"(AI synthesis error: {exc})"
        logging.warning(msg)
        print(f"  ⚠ {msg}")
        return msg


# ── Composition ──────────────────────────────────────────────────────────────


def _emoji_for_severity(sev: str) -> str:
    return {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢", "info": "🔵"}.get(sev, "⚪")


def _fmt_date(d: date) -> str:
    if sys.platform == "win32":
        return d.strftime("%A, %B {0}, %Y").format(d.day)
    return d.strftime("%A, %B %-d, %Y")


def _persist_note(c) -> str:
    """Render a non-noisy persistence label. '' if fresh today, '· 3d' if older."""
    if c.days_seen >= 7:
        wks = c.days_seen // 7
        return f" · {wks}w" if wks >= 2 else f" · {c.days_seen}d"
    if c.days_seen >= 2:
        return f" · {c.days_seen}d"
    return ""


def _severity_rank(sev: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(sev, 5)


def _all_real_concerns(reports: list[AgentReport]) -> list[tuple[str, Concern]]:
    return sorted(
        [(r.agent, c) for r in reports for c in r.real_concerns],
        key=lambda x: (_severity_rank(x[1].severity), -getattr(x[1], "days_seen", 1)),
    )


def _all_carried_concerns(reports: list[AgentReport]) -> list[tuple[str, Concern]]:
    return sorted(
        [(r.agent, c) for r in reports for c in r.carried_concerns],
        key=lambda x: (-getattr(x[1], "days_seen", 1), _severity_rank(x[1].severity)),
    )


def _workspace_events(reports: list[AgentReport]) -> list[dict]:
    ws = next((r for r in reports if r.agent == "workspace"), None)
    events = (ws.raw_data.get("events") if ws and ws.raw_data else None) or []
    if not isinstance(events, list):
        return []
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        when, title = _event_time_and_title(event)
        key = (when, title.lower().strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _event_time_and_title(event: dict) -> tuple[str, str]:
    title = str(event.get("summary") or event.get("title") or event.get("name") or "Untitled")
    start = event.get("start") or event.get("start_time") or event.get("when") or ""
    if isinstance(start, dict):
        start = start.get("dateTime") or start.get("date") or ""
    start = str(start)
    m = re.search(r"T(\d{2}:\d{2})", start) or re.search(r"\b(\d{1,2}:\d{2})\b", start)
    when = m.group(1) if m else (start[:10] if start else "all day")
    return when, title


def _first_event_label(reports: list[AgentReport]) -> str:
    events = _workspace_events(reports)
    if not events:
        return "not available"
    when, title = _event_time_and_title(events[0])
    return f"{when} — {title}"


def _memory_health() -> dict:
    import json
    import urllib.request
    out = {"registry": "unknown", "concerns": "unknown", "active": None, "resolved": None, "error": ""}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out["registry"] = "fresh" if data.get("ok") else "failed"
    except Exception as exc:
        out["registry"] = "failed"
        out["error"] = f"registry health unavailable: {exc}"
    try:
        # First DB-backed concerns request after a fresh boot can spend several
        # seconds hydrating Vault/establishing Postgres. Keep this above the
        # observed cold-start (~6s) so the daily does not falsely claim the
        # concerns API is failed when it is merely cold.
        with urllib.request.urlopen("http://127.0.0.1:8000/api/concerns/stats", timeout=10) as resp:
            stats = json.loads(resp.read().decode("utf-8"))
        out["concerns"] = "fresh"
        # stats() omits zero-count statuses; normalize for clean daily copy
        # (avoid "None active" in Discord/Hermes previews).
        out["active"] = int(stats.get("active") or 0)
        out["resolved"] = int(stats.get("resolved") or 0)
    except Exception as exc:
        out["concerns"] = "failed"
        existing_error = str(out.get("error") or "")
        out["error"] = (existing_error + "; " if existing_error else "") + f"concerns unavailable: {exc}"
    return out


def _status_word(reports: list[AgentReport], memory: dict | None = None) -> str:
    if any(r.status == "failed" for r in reports) or any(c.severity == "critical" for _, c in _all_real_concerns(reports)):
        return "❌ Action needed"
    if any(r.status == "degraded" for r in reports) or _all_real_concerns(reports) or (memory and memory.get("concerns") == "failed"):
        return "⚠️ Attention needed"
    return "✅ Normal"


def _read_first_items(reports: list[AgentReport], memory: dict, parsed: dict, validations: list[str] | None = None) -> list[str]:
    items: list[str] = []
    if parsed.get("tldr"):
        items.append(parsed["tldr"].strip())
    if memory.get("concerns") == "failed":
        items.append("Memory concerns API is unhealthy, so recurring-issue tracking is degraded.")
    real = _all_real_concerns(reports)
    fresh = [(a, c) for a, c in real if getattr(c, "days_seen", 1) < 2]
    persistent = [(a, c) for a, c in real if getattr(c, "days_seen", 1) >= 2]
    if fresh:
        a, c = fresh[0]
        items.append(f"{c.summary} is the top fresh {a} signal.")
    if persistent:
        a, c = persistent[0]
        items.append(f"{c.summary} is still open on day {c.days_seen}.")
    ws = next((r for r in reports if r.agent == "workspace"), None)
    if ws:
        unread = ((ws.metrics or {}).get("unread_email")
                  or (ws.metrics or {}).get("email_unread")
                  or (ws.metrics or {}).get("unread"))
        events = ((ws.metrics or {}).get("events_today")
                  or (ws.metrics or {}).get("calendar_events_today")
                  or len(_workspace_events(reports)))
        if events:
            items.append(f"Calendar has {events} event(s) today; first is {_first_event_label(reports)}.")
        if unread and unread >= 100:
            items.append(f"Inbox backlog is high at {unread} unread; triage likely-important items, not newsletters.")
    for f in (validations or [])[:2]:
        items.append(f)
    if not items:
        items.append("No major overnight changes surfaced; core domains look stable.")
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key not in seen:
            out.append(item)
            seen.add(key)
        if len(out) == 5:
            break
    return out


def _action_items(reports: list[AgentReport], parsed: dict) -> tuple[list[str], list[str], list[str]]:
    do_today: list[str] = []
    can_wait: list[str] = []
    no_action: list[str] = []
    for action in (parsed.get("actions") or [])[:3]:
        if action:
            do_today.append(action)
    for agent, c in _all_real_concerns(reports):
        action = c.suggested_action or c.summary
        if c.severity in ("critical", "high") or getattr(c, "days_seen", 1) < 2:
            do_today.append(action)
        else:
            can_wait.append(f"{action} — still open on day {c.days_seen}")
    for agent, c in _all_carried_concerns(reports)[:4]:
        can_wait.append(f"{c.summary} — background concern, day {c.days_seen}")
    healthy = [r.agent.title() for r in reports if r.status == "ok" and not r.real_concerns]
    if healthy:
        no_action.append(f"{', '.join(healthy)} baseline normal.")
    if not do_today and not can_wait:
        no_action.append("No active operator action required from this run.")

    def dedupe(xs: list[str], cap: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for x in xs:
            x = x.strip().rstrip(".")
            if not x:
                continue
            k = x.lower()
            if k in seen:
                continue
            out.append(x)
            seen.add(k)
            if len(out) >= cap:
                break
        return out
    return dedupe(do_today, 5), dedupe(can_wait, 5), dedupe(no_action, 4)


def _render_memory_and_freshness(reports: list[AgentReport], memory: dict) -> list[str]:
    lines = ["## 🧠 Memory & Freshness", ""]
    reg = "✅" if memory.get("registry") == "fresh" else "❌"
    con = "✅" if memory.get("concerns") == "fresh" else "❌"
    concern_note = ""
    if memory.get("active") is not None:
        concern_note = f" · {memory.get('active')} active / {memory.get('resolved')} resolved"
    lines += [
        f"- {reg} Registry memory: {memory.get('registry', 'unknown')}",
        f"- {con} Concerns lifecycle: {memory.get('concerns', 'unknown')}{concern_note}",
    ]
    if memory.get("error"):
        lines.append(f"- ⚠️ Memory note: {memory['error']}")
    for r in reports:
        age = int((datetime.now() - r.as_of).total_seconds() // 60) if r.as_of else 0
        status = "✅ fresh" if age < 180 else "🔍 stale"
        lines.append(f"- {status}: {r.agent} as of {r.as_of.strftime('%H:%M') if r.as_of else '?'} · {r.headline}")
    lines.append("")
    return lines


def compose_obsidian(today: date, reports: list[AgentReport], synthesis: str) -> str:
    """Morning Command Brief. Deterministic and useful even without AI synthesis."""
    parsed = _parse_synthesis(synthesis) if synthesis and not synthesis.startswith("(") else {"tldr": "", "actions": [], "briefing": ""}
    memory = _memory_health()
    validations = validate_reports(reports)
    read_first = _read_first_items(reports, memory, parsed, validations)
    do_today, can_wait, no_action = _action_items(reports, parsed)
    real_by_sev = _all_real_concerns(reports)
    carried = _all_carried_concerns(reports)
    expected = [(r.agent, e) for r in reports for e in r.expected_events]
    questions = [(r.agent, q) for r in reports for q in (r.questions or [])[:3]]

    L: list[str] = []
    L += [f"### 🌅 Morning Command Brief — {_fmt_date(today)}", ""]
    L += [f"**Overall:** {_status_word(reports, memory)}"]
    L += [f"**Next:** {_first_event_label(reports)}", ""]

    if parsed.get("briefing"):
        L += ["> [!summary]+ Assistant take"]
        for line in parsed["briefing"].splitlines():
            L.append(f"> {line}" if line.strip() else ">")
        L += [""]

    L += ["## 🔥 Read This First", ""]
    for i, item in enumerate(read_first, 1):
        L.append(f"{i}. {item}")
    L.append("")

    L += ["## ✅ Action Queue", "", "### Do Today"]
    L += [f"- [ ] {x}" for x in do_today] if do_today else ["- [ ] No urgent operator action from this run."]
    L += ["", "### Can Wait"]
    L += [f"- [ ] {x}" for x in can_wait] if can_wait else ["- None"]
    L += ["", "### No Action"]
    L += [f"- {x}" for x in (no_action or ["No clean domains to call out."])]
    L.append("")

    L += _render_memory_and_freshness(reports, memory)

    events = _workspace_events(reports)
    if events:
        L += ["## 📅 Today", "", "| Time | Item |", "|---:|---|"]
        for event in events[:8]:
            when, title = _event_time_and_title(event)
            L.append(f"| {when} | {title} |")
        L.append("")

    L += ["## 🏠 HodgeSpot Ops", "", "| Domain | Status | Read |", "|---|---:|---|"]
    for r in reports:
        L.append(f"| {r.agent.title()} | {r.status_emoji} {r.status} | {r.headline} |")
    L.append("")

    if real_by_sev or carried:
        L += ["### Signals", ""]
        fresh = [(a, c) for a, c in real_by_sev if getattr(c, "days_seen", 1) < 2]
        persistent = [(a, c) for a, c in real_by_sev if getattr(c, "days_seen", 1) >= 2]
        if fresh:
            L.append("**Fresh / changed**")
            for agent, c in fresh[:8]:
                L.append(f"- {_emoji_for_severity(c.severity)} {c.summary} *({agent})*")
            L.append("")
        if persistent:
            L.append("**Still open**")
            for agent, c in persistent[:8]:
                L.append(f"- {_emoji_for_severity(c.severity)} {c.summary} — day {c.days_seen} *({agent})*")
            L.append("")
        if carried:
            L.append("**Background context**")
            for agent, c in carried[:6]:
                L.append(f"- {c.summary}{_persist_note(c)} *({agent})*")
            L.append("")

    ws = next((r for r in reports if r.agent == "workspace"), None)
    if ws:
        unread = ((ws.metrics or {}).get("unread_email")
                  or (ws.metrics or {}).get("email_unread")
                  or (ws.metrics or {}).get("unread"))
        tasks = (ws.metrics or {}).get("obsidian_tasks_open")
        L += ["## 📬 Workspace", ""]
        calendar_count = ((ws.metrics or {}).get('events_today')
                          or (ws.metrics or {}).get('calendar_events_today')
                          or len(events)
                          or 'unknown')
        L.append(f"- Calendar: {calendar_count} event(s) today")
        if unread is not None:
            L.append(f"- Email: {unread} unread")
        google_tasks = ((ws.metrics or {}).get("google_tasks_open") or 0)
        task_total = (tasks or 0) + google_tasks if tasks is not None else google_tasks
        if task_total:
            detail = []
            if tasks:
                detail.append(f"{tasks} TASKS.md")
            if google_tasks:
                detail.append(f"{google_tasks} Google")
            suffix = f" ({', '.join(detail)})" if detail else ""
            L.append(f"- Tasks: {task_total} open{suffix}")
            task_lines: list[str] = []
            for t in ((ws.raw_data or {}).get("obsidian_tasks_sample") or [])[:8]:
                task_lines.append(str(t))
            for t in ((ws.raw_data or {}).get("google_tasks") or [])[:8]:
                title = t.get("title") if isinstance(t, dict) else str(t)
                due = t.get("due") if isinstance(t, dict) else ""
                task_lines.append(f"{title} (due {due[:10]})" if due else str(title))
            if task_lines:
                L.append("  - Open items:")
                for task in task_lines[:10]:
                    L.append(f"    - {task}")
        L.append("")

    fin = next((r for r in reports if r.agent == "finance"), None)
    if fin:
        L += ["## 💸 Finance", "", f"- {fin.headline}"]
        nw = (fin.metrics or {}).get("net_worth")
        if nw is not None:
            L.append(f"- Net worth: ${nw:,.0f}")
        L.append("")

    if questions:
        L += ["## ❓ Questions for Brian", ""]
        for agent, q in questions[:8]:
            L.append(f"- {q} *(— {agent})*")
        L.append("")

    if expected:
        L += ["> [!abstract]- Expected / suppressed — no action", ">"]
        for agent, e in expected[:8]:
            L.append(f"> - {e.summary[:100]} — *{e.reason[:100]}* *({agent})*")
        L.append("")

    total_ms = sum(r.duration_ms for r in reports)
    L += ["---", f"<sub>{datetime.now().strftime('%H:%M')} · {total_ms}ms across {len(reports)} agents · memory {memory.get('concerns')}</sub>"]
    return "\n".join(L)


def _discord_sanitize(s: str) -> str:
    """Keep notification text Discord-friendly without pinging roles/users."""
    return s.replace("@", "@\u200b")


def compose_discord_notification(today: date, reports: list[AgentReport], synthesis: str | None) -> str:
    """Compact Discord surface for the Morning Command Brief."""
    parsed = _parse_synthesis(synthesis) if synthesis and not synthesis.startswith("(") else {"tldr": "", "actions": [], "briefing": ""}
    memory = _memory_health()
    read_first = _read_first_items(reports, memory, parsed, validate_reports(reports))
    do_today, can_wait, _ = _action_items(reports, parsed)

    L: list[str] = []
    L.append(f"☕ *Morning, Brian* — {today.strftime('%a %b')} {today.day}")
    L.append(f"Overall: {_status_word(reports, memory)} · Next: {_first_event_label(reports)}")
    L.append("")

    L.append("*Read first:*")
    for i, item in enumerate(read_first[:3], 1):
        L.append(f"{i}. {_discord_sanitize(item[:220])}")
    L.append("")

    actions = do_today[:3]
    if actions:
        L.append("*Action queue:*")
        for i, a in enumerate(actions, 1):
            L.append(f"{i}. {_discord_sanitize(a[:180])}")
        L.append("")
    elif can_wait:
        L.append(f"_No urgent action; {len(can_wait)} item(s) can wait._")
        L.append("")

    if memory.get("concerns") == "fresh":
        L.append(f"_Memory: {memory.get('active')} active / {memory.get('resolved')} resolved concerns. Full brief in Obsidian/Ops._")
    else:
        L.append("_Memory: concerns unavailable. Full brief in Obsidian/Ops._")

    msg = "\n".join(L)
    if len(msg) > 1900:
        msg = msg[:1850].rstrip() + "\n…"
    return msg


# ── Delivery ─────────────────────────────────────────────────────────────────


def write_obsidian(today: date, content: str, dry_run: bool) -> None:
    """Inject the composed brief into today's Obsidian note via REST API or
    filesystem write. Falls back to filesystem if OBSIDIAN_TOKEN absent."""
    if dry_run:
        print("\n=== DRY RUN — would write to Obsidian note ===\n")
        print(content)
        return

    from note_store import resolve_note_store, upsert_section, FileNoteStore

    def _write(store, label):
        rel = store.daily_path(today)
        try:
            note_text = store.read_text(rel)
        except Exception:
            note_text = (f"---\ndate: {today.isoformat()}\ntype: daily\n"
                         f"tags: [daily]\n---\n\n# {today.strftime('%A, %B {0}, %Y').format(today.day)}\n\n")
        new_text = upsert_section(note_text, "## Briefing", content)
        store.write_text(rel, new_text)
        print(f"  ✓ Obsidian note updated for {today} via {label}")

    try:
        # Primary: whatever resolve_note_store picks (typically REST when token set).
        store = resolve_note_store()
        try:
            _write(store, store.backend_name)
            return
        except Exception as exc:
            primary_err = exc
            print(f"  ⚠️ Primary write ({store.backend_name}) failed: {exc}")
            # Fallback: filesystem write to repo Daily/ dir. Always works locally.
            from pathlib import Path as _Path
            fs_store = FileNoteStore(vault_root=_Path(__file__).resolve().parent.parent)
            try:
                _write(fs_store, "filesystem-fallback")
                print(f"     (REST API was down — re-enable Local REST API plugin in Obsidian; tomorrow will use REST again)")
                return
            except Exception as exc2:
                print(f"  ✗ Fallback write also failed: {exc2}")
                raise primary_err
    except Exception as exc:
        print(f"  ✗ Obsidian write failed: {exc}")


def push_brief_to_ops(today: date, briefing_block: str, dry_run: bool) -> None:
    """POST today's rendered brief to the ops UI API so the dashboard at
    https://ops.hodgespot.com sees it. Tolerant of failures — never blocks
    the rest of the pipeline.

    Reads the just-written daily note from disk so we send the full markdown
    (briefing + the rest of the day's note) along with the extracted briefing
    section."""
    if dry_run:
        print("  [dry-run] would POST brief to ops UI")
        return
    import json
    import urllib.request

    api_base = os.environ.get("BBM_OPS_API_BASE", "https://ops.hodgespot.com")
    api_key  = os.environ.get("BBM_API_KEY", "")

    # Read full daily note from filesystem (vault is on this host)
    repo_root = Path(__file__).resolve().parent.parent
    note_path = repo_root / "Daily" / f"{today.isoformat()}.md"
    md = note_path.read_text(encoding="utf-8") if note_path.exists() else briefing_block

    # The briefing block we just rendered IS the ## Briefing content
    # (without the section header itself).
    payload = {
        "date": today.isoformat(),
        "markdown": md,
        "briefing": briefing_block,
    }
    try:
        req = urllib.request.Request(
            f"{api_base}/api/brief",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"X-API-Key": api_key} if api_key else {}),
            },
            method="POST",
        )
        # Skip cert verify for self-signed-on-LAN setups
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                print(f"  ✓ pushed brief to ops UI")
            else:
                print(f"  ✗ ops UI push HTTP {resp.status}")
    except Exception as exc:
        print(f"  ✗ ops UI push failed (non-blocking): {exc}")


def _chunk_discord_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > limit:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def send_discord(msg: str, dry_run: bool) -> None:
    if dry_run:
        print("\n=== DRY RUN — Discord/Hermes preview ===\n")
        print(msg)
        return
    import json
    import urllib.request
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channel_id = (
        os.environ.get("DISCORD_BRIEF_CHANNEL_ID")
        or os.environ.get("DISCORD_CHANNEL_ID")
        or os.environ.get("HERMES_DISCORD_CHANNEL_ID")
        # Brian's Hermes Discord #general channel, used as the assistant home surface.
        or "1477768383645749271"
    )
    if not token or not channel_id:
        print("  ✗ Discord skipped: DISCORD_BOT_TOKEN/channel id not set")
        return
    try:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        for chunk in _chunk_discord_message(msg):
            req = urllib.request.Request(
                url,
                data=json.dumps({"content": chunk, "allowed_mentions": {"parse": []}}).encode("utf-8"),
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "BlunderBus-DailyBrief/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 201):
                    print(f"  ✗ Discord HTTP {resp.status}")
                    return
        print("  ✓ Discord/Hermes brief sent")
    except Exception as exc:
        print(f"  ✗ Discord/Hermes send failed: {exc}")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-ai", action="store_true", help="skip AI synthesis")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    p.add_argument("--agents", default=",".join(AGENT_REGISTRY.keys()),
                   help=f"comma-separated subset of {list(AGENT_REGISTRY.keys())}")
    p.add_argument("--no-parallel", action="store_true", help="serial agent invocation (debugging)")
    p.add_argument("--force", action="store_true",
                   help="re-run even if today's Briefing section is already populated")
    args = p.parse_args()

    today = args.date or date.today()
    requested = [a.strip() for a in args.agents.split(",") if a.strip() in AGENT_REGISTRY]
    if not requested:
        print(f"ERROR: no valid agents in --agents '{args.agents}'", file=sys.stderr)
        return 2

    if not args.force and not args.dry_run and briefing_already_populated(today):
        print(f"  Briefing for {today} is already populated — skipping (use --force to override)")
        return 0

    print(f"=== BlunderBus DailyBrief · {today} · agents={requested} ===")
    t0 = time.time()
    reports = run_agents(requested, today, parallel=not args.no_parallel)
    fanout_ms = int((time.time() - t0) * 1000)
    print(f"Fanout complete in {fanout_ms}ms ({'parallel' if not args.no_parallel else 'serial'})")
    for r in reports:
        print(f"  {r.status_emoji} {r.agent:10s} {r.status:10s} {r.duration_ms:6d}ms · {r.headline[:90]}")

    # Create today's note shell if missing (replaces the old morning_prep.py 06:00 cron).
    # Sources Schedule from workspace agent's calendar fetch + Tasks from TASKS.md `## Active`.
    if not args.dry_run:
        _ensure_note_shell(today, reports)

    validations = validate_reports(reports)
    if validations:
        print(f"Cross-validation: {len(validations)} finding(s)")
        for f in validations:
            print(f"  ▸ {f}")

    synthesis = ""
    if not args.no_ai:
        print("Running AI synthesis...")
        t1 = time.time()
        synthesis = ai_synthesize(reports, validations)
        print(f"  synthesis {int((time.time()-t1)*1000)}ms")

    obsidian_block = compose_obsidian(today, reports, synthesis)
    discord_msg = compose_discord_notification(today, reports, synthesis)

    write_obsidian(today, obsidian_block, args.dry_run)
    send_discord(discord_msg, args.dry_run)
    push_brief_to_ops(today, obsidian_block, args.dry_run)

    total = int((time.time() - t0) * 1000)
    print(f"\n=== Done · total {total}ms ===")
    return 0 if all(r.status != "failed" for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
