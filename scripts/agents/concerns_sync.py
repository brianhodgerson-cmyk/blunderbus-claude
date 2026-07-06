"""
Shared helper: push an agent's `base.Concern` list to the Postgres
`agent_concerns` table and reconcile (auto-resolve missing).

Tolerant of failures — if Postgres is unreachable we log and continue,
so a DB outage never breaks the daily brief.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow standalone agent runs to find the package
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def sync(agent: str, real_concerns: list, *, verbose: bool = True) -> int:
    """Push concerns to Postgres + reconcile. Returns count of concerns
    auto-resolved (those that were active in DB but not in this run).

    Concerns are identified by a stable id derived from
    `(agent, category, alert/metric, host)`. Re-firing the same concern
    on a later run updates the existing row; a missing one gets resolved.
    """
    from blunderbus_memory import (
        Concern as PMConcern, ConcernStatus, Severity,
    )
    from blunderbus_memory.concerns import PostgresConcerns

    # Hydrate vault if not already done
    if not os.environ.get("BLUNDERBUS_DB_PASSWORD") and os.environ.get("BW_MASTER_PASS"):
        try:
            from vault import load_secrets  # type: ignore
            load_secrets()
        except Exception:
            pass

    sev_map = {
        "critical": Severity.CRITICAL, "high": Severity.HIGH,
        "medium": Severity.MEDIUM, "low": Severity.LOW, "info": Severity.INFO,
    }

    active_ids: list[str] = []
    try:
        with PostgresConcerns() as store:
            for c in real_concerns:
                metric = (c.metric or {}) if isinstance(c.metric, dict) else {}
                discriminator = (
                    metric.get("alert")
                    or metric.get("kind")
                    or _slug(c.summary[:60])
                )
                target = metric.get("host") or metric.get("target") or "global"
                cid = f"{agent}:{c.category or 'concern'}:{discriminator}:{target}".lower()
                store.upsert(PMConcern(
                    id=cid,
                    agent=agent,
                    type=c.category or "concern",
                    target=metric.get("host") or metric.get("target"),
                    severity=sev_map.get(getattr(c, "severity", "medium"), Severity.MEDIUM),
                    status=ConcernStatus.ACTIVE,
                    summary=c.summary[:500],
                    suggested_action=getattr(c, "suggested_action", None),
                    verifier=getattr(c, "source", None),
                    payload={"metric": metric, "days_seen": getattr(c, "days_seen", 0)},
                ))
                active_ids.append(cid)
            resolved_rows = store.reconcile(agent, active_ids)
            if verbose and resolved_rows:
                print(f"  ✓ {agent}: auto-resolved {len(resolved_rows)} concern(s) no longer active")
            # Journal auto-resolutions per the memory contract — keep entries
            # terse, one per resolved concern. Failures here are non-fatal.
            if resolved_rows:
                try:
                    from blunderbus_memory.journal import write_decision
                    for cid, summary in resolved_rows:
                        write_decision(
                            agent=agent,
                            target=cid,
                            decision="resolved",
                            reasoning=f'Concern "{summary[:200]}" no longer active per latest probe.',
                            related=[f"agent_concerns:{cid}"],
                        )
                except Exception as exc:
                    if verbose:
                        print(f"  ⚠ {agent}: journal write skipped — {exc}", file=sys.stderr)
            return len(resolved_rows)
    except Exception as exc:
        if verbose:
            print(f"  ⚠ {agent}: concerns sync skipped — {exc}", file=sys.stderr)
        return 0


def _slug(text: str) -> str:
    """Stable kebab-cased slug for concern IDs derived from free-form summaries."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "unknown"
