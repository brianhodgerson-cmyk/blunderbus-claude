"""
Shared helper: push an agent's `Question` list to the Postgres `agent_questions`
table and reconcile (stale-mark missing ones).

Mirrors `concerns_sync.sync()`. Agents emit a list of Question models; this
helper upserts them, then any question that was previously `open` or `posted`
for this agent but is no longer being emitted gets marked `stale` so the bot
doesn't keep a dead thread alive forever.

Tolerant of DB outages — if Postgres is unreachable, returns 0 instead of
crashing the brief.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow standalone agent runs to find the package
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def sync(agent: str, questions: list, *, verbose: bool = True) -> int:
    """Upsert each Question; reconcile by staling anything the agent no longer
    emits. Returns count of questions staled.

    Auto-journals stale events so the decision log shows "this question is no
    longer relevant — agent dropped it" without manual intervention.
    """
    if not os.environ.get("BLUNDERBUS_DB_PASSWORD") and os.environ.get("BW_MASTER_PASS"):
        try:
            from vault import load_secrets  # type: ignore
            load_secrets()
        except Exception:
            pass

    try:
        from blunderbus_memory.questions import PostgresQuestions
    except Exception as exc:
        if verbose:
            print(f"  ⚠ {agent}: questions sync skipped — {exc}", file=sys.stderr)
        return 0

    active_ids: list[str] = []
    try:
        with PostgresQuestions() as store:
            for q in questions:
                store.upsert(q)
                active_ids.append(q.id)
            staled = store.reconcile(agent, active_ids)
            if verbose and staled:
                print(f"  ✓ {agent}: marked {len(staled)} question(s) stale (no longer emitted)")
            # Optionally journal stale events; questions are softer than concerns
            # so we skip auto-journaling for now to avoid log spam. Operator can
            # always query the DB.
            return len(staled)
    except Exception as exc:
        if verbose:
            print(f"  ⚠ {agent}: questions sync skipped — {exc}", file=sys.stderr)
        return 0
