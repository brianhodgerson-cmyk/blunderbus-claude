"""
Decisions journal — append-only record of judgments worth remembering.

Per the memory contract (.claude/rules/memory-contract.md), agents call
write_decision() at the end of any substantive action — auto-resolution,
suppression, status flip — so future-me can grep `decisions/*.md` and
recover the WHY behind state changes that the concerns table can only show
as timestamps.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DECISIONS_DIR = REPO_ROOT / "decisions"


def _path_for(d: date) -> Path:
    return DECISIONS_DIR / f"{d.isoformat()}.md"


def write_decision(
    *,
    agent: str,
    target: str,
    decision: str,
    reasoning: str,
    related: Iterable[str] = (),
    when: Optional[datetime] = None,
) -> Path:
    """Append a decision entry to today's journal file (creates the file with
    a header if missing). Returns the path written.

    agent     who made the decision (e.g. 'infra', 'finance', 'workspace',
              'concerns_sync', or a skill name)
    target    what the decision is about (host, concern id, project slug, etc.)
    decision  one-word verb: resolved, suppressed, deferred, escalated, applied,
              reverted, observed
    reasoning ≤3 sentences explaining the WHY
    related   identifiers to cross-link (concern ids, host slugs, paths)
    when      timestamp (default: now)
    """
    when = when or datetime.now()
    today = when.date()
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(today)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write(f"# Decisions — {today.isoformat()}\n\n")
        f.write(f"\n## {agent} · {decision} · {target}\n\n")
        f.write(f"- **At**: {when.strftime('%H:%M:%S')}\n")
        f.write(f"- **Decision**: {decision}\n")
        f.write(f"- **Reasoning**: {reasoning}\n")
        if related:
            f.write(f"- **Related**: {', '.join(related)}\n")
    return path
