"""
Daily-note shell builder.

Replaces the old morning_prep.py — produces the note skeleton that daily_brief.py
fills in. Sources:
  - Schedule: events list from workspace agent (gws calendar)
  - Tasks: open items from `## Active` and `## Ops — Needs Attention` sections of TASKS.md
    (no more daily-note carry-forward — TASKS.md is the single source of truth)
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = REPO_ROOT / "TASKS.md"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Sections of TASKS.md that surface in the daily note Tasks block.
# Everything else (Backlog, Completed, Other) stays hidden by default.
ACTIVE_SECTIONS = ("Active", "Ops — Needs Attention")


def format_day_header(target_date: date) -> str:
    return f"# {DAYS[target_date.weekday()]}, {MONTHS[target_date.month - 1]} {target_date.day}, {target_date.year}"


def read_active_tasks(tasks_path: Path = TASKS_FILE) -> list[str]:
    """Return open task texts from the configured active sections of TASKS.md.
    Returns an empty list if TASKS.md is missing or has no open items."""
    if not tasks_path.exists():
        return []
    text = tasks_path.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        s = line.rstrip()
        if s.startswith("## "):
            current = s[3:].strip()
            continue
        if current not in ACTIVE_SECTIONS:
            continue
        m = re.match(r"^- \[ \] (.+)$", s)
        if m:
            out.append(m.group(1).strip())
    return out


def render_schedule_block(events: Iterable[dict]) -> str:
    """events: list of dicts with `summary`, `start`, `location`. Empty → 'no events' callout."""
    deduped = []
    seen: set[tuple[str, str, str]] = set()
    for ev in list(events or []):
        title = (ev.get("summary") or "(no title)").strip()
        start = ev.get("start") or ""
        loc = ev.get("location") or ""
        time_str = start[11:16] if "T" in start else start
        key = (time_str, title.lower(), loc.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((time_str, title, loc))
    if not deduped:
        return "> [!note]- Calendar\n> *No events scheduled*"
    lines = []
    for time_str, title, loc in deduped:
        loc_str = f" — {loc}" if loc else ""
        time_prefix = f"**{time_str}** · " if time_str else ""
        lines.append(f"{time_prefix}{title}{loc_str}")
    body = "\n> ".join(lines)
    return f"> [!info]+ Today's Calendar\n> {body}"


def render_tasks_block(active_tasks: Iterable[str]) -> str:
    """Render open-task lines pulled from TASKS.md. Includes an empty checkbox at end for ad-hoc capture."""
    lines = [f"- [ ] {t}" for t in active_tasks]
    lines.append("- [ ] ")
    return "\n".join(lines)


def build_note_shell(today: date, events: Iterable[dict], active_tasks: Iterable[str]) -> str:
    """Render the full daily-note shell. daily_brief.py fills the `## Briefing` and
    `## Today's Focus` sections in a second pass."""
    return f"""---
date: {today.isoformat()}
type: daily
tags: [daily]
---

{format_day_header(today)}

## Briefing

*BlunderBus is preparing today's briefing — it'll land here around 07:30.*

## Today's Focus

> [!tip]+ What I'm prioritizing today
> - [ ]
> - [ ]
> - [ ]

## Schedule

{render_schedule_block(events)}

## Tasks

{render_tasks_block(active_tasks)}

## Notes & Captures



## Projects & Lab



## Evening Review

> [!question]- How did the day land?
> **What I shipped:**
>
> **What's carrying over:**
>
> **One thing I learned:**
"""
