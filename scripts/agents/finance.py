"""
Finance domain agent.

Wraps existing finance_intel.py functions to produce a standardized AgentReport
for the DailyBrief orchestrator. All domain logic (queries, anomaly detection,
suppression) stays in finance_intel.py — this module is the contract surface.

Reads memory:
  - memory/finance/learnings.md   (carried concerns)
  - memory/finance/recurring.md   (read indirectly via finance_intel suppression)
  - memory/finance/decisions.md   (referenced in concern source field)

Run standalone for testing:
    py scripts/agents/finance.py
    py scripts/agents/finance.py --json
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, date
from pathlib import Path

# Make scripts/ importable so we can pull in finance_intel and base
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "agents"))

from base import AgentReport, Concern, Event, parse_carried_from_learnings  # noqa: E402

MEMORY_DIR = ROOT / "memory" / "finance"
LEARNINGS = MEMORY_DIR / "learnings.md"
RECURRING = MEMORY_DIR / "recurring.md"
DECISIONS = MEMORY_DIR / "decisions.md"


def _severity_for_anomaly(a: dict) -> str:
    """Map finance_intel severity (HIGH/MEDIUM) + magnitude to AgentReport severity."""
    sev = a.get("severity", "MEDIUM").lower()
    z = a.get("z_score") or 0
    pct = a.get("pct_over") or 0
    if sev == "high" and (z >= 5 or pct >= 500):
        return "critical"
    if sev == "high":
        return "high"
    return "medium"


def _build_real_concerns(anomalies: list[dict]) -> list[Concern]:
    """Convert non-suppressed finance_intel anomalies into Concerns."""
    out: list[Concern] = []
    for a in anomalies:
        if a.get("suppressed"):
            continue
        cat = a.get("category", "?")
        cur = a.get("current", 0)
        avg = a.get("avg", 0) or 1
        ratio = cur / avg
        if a.get("anomaly_type") == "exceeded":
            summary = f"{cat} ${cur:,.0f} (already {ratio:.1f}x monthly avg ${avg:,.0f})"
        else:
            proj = a.get("projected", cur)
            projected_ratio = proj / avg if avg else 0
            summary = (
                f"{cat} ${cur:,.0f} ({ratio:.1f}x avg ${avg:,.0f}) "
                f"→ projected ${proj:,.0f} ({projected_ratio:.1f}x avg)"
            )
        out.append(Concern(
            severity=_severity_for_anomaly(a),
            summary=summary,
            category="spending",
            metric={
                "category": cat,
                "current": cur,
                "avg": avg,
                "projected": a.get("projected"),
                "z_score": a.get("z_score"),
                "ratio": round(ratio, 2),
            },
            source="finance_intel.detect_anomalies + memory/finance/baselines.md",
        ))
    return out


def _build_expected_events(anomalies: list[dict]) -> list[Event]:
    """Convert suppressed anomalies into transparency-level Events.

    Some acknowledged one-time items are intentionally quiet: suppress them
    from concerns and from the expected/no-action section so they do not keep
    reappearing after the operator has accepted them.
    """
    out: list[Event] = []
    for a in anomalies:
        if not a.get("suppressed"):
            continue
        reason = str(a.get("suppression_reason", ""))
        if "quiet:" in reason.lower() or "do not surface" in reason.lower():
            continue
        out.append(Event(
            summary=f"{a.get('category','?')} ${a.get('current',0):,.0f}",
            category="spending",
            amount=float(a.get("current", 0)),
            reason=reason,
            source="recurring.md",
        ))
    return out


def _build_metrics(balances: dict, fire: dict, pace: dict, anomalies: list[dict]) -> dict:
    """Headline numbers for the brief tables."""
    bal = balances or {}
    raw_spend = (pace or {}).get("current_spend") or 0
    avg_spend = (pace or {}).get("avg_spend") or 0
    suppressed_total = sum(a.get("current", 0) for a in anomalies if a.get("suppressed"))
    adj_spend = max(0, raw_spend - suppressed_total)
    adj_pace = round((adj_spend / avg_spend) * 100, 1) if avg_spend > 0 else None

    t30 = (fire or {}).get("trailing_30") or {}
    return {
        "net_worth": bal.get("net_worth"),
        "investments": bal.get("investments"),
        "cash": bal.get("cash"),
        "mortgage": bal.get("mortgage"),
        "credit": bal.get("credit"),
        "fire_progress_pct": (fire or {}).get("progress_pct"),
        "fire_year": (fire or {}).get("fire_year"),
        "savings_rate_pct": (fire or {}).get("savings_rate_pct"),
        "years_to_fire": (fire or {}).get("years_to_fire"),
        "pace_pct_of_avg_raw": (pace or {}).get("pct_of_avg"),
        "pace_pct_of_avg_adjusted": adj_pace,
        "current_spend_raw": raw_spend,
        "current_spend_adjusted": adj_spend,
        "suppressed_total": suppressed_total,
        "avg_spend": avg_spend,
        # T30 lumpy fields (2026-05-14): both raw + excl-lumpy SR for transparency
        "trailing_30_income": t30.get("income"),
        "trailing_30_spending": t30.get("spending"),
        "trailing_30_spending_excl_lumpy": t30.get("spending_excl_lumpy"),
        "trailing_30_sr_pct": t30.get("savings_rate_pct"),
        "trailing_30_sr_pct_excl_lumpy": t30.get("savings_rate_pct_excl_lumpy"),
        "trailing_30_lumpy_total": t30.get("lumpy_total"),
        "trailing_30_lumpy_count": len(t30.get("lumpy_excluded") or []),
    }


def _build_headline(metrics: dict, real: list[Concern], expected: list[Event]) -> str:
    nw = metrics.get("net_worth")
    nw_str = f"NW ${nw:,.0f}" if isinstance(nw, (int, float)) else "NW unknown"

    if not real:
        if expected:
            sup_total = sum(e.amount or 0 for e in expected)
            return f"{nw_str} · normal after ${sup_total:,.0f} of expected events suppressed"
        return f"{nw_str} · all baselines normal"

    n_real = len(real)
    worst = next((c for c in real if c.severity in ("critical", "high")), real[0])
    return f"{nw_str} · {n_real} concern(s), worst: {worst.summary[:80]}"


def _emit_structured_questions():
    """Build structured Question objects for the Path C Discord workflow.

    Returns list[blunderbus_memory.Question]. Each one corresponds to an
    actionable registry gap (unknown account owner, account status flag).
    These get persisted to Postgres so the Discord bot can post them as
    threads and apply the operator's reply to the right registry file.

    Quietly returns [] if the memory package can't be imported (e.g. running
    against a fresh checkout with no DB connection).
    """
    try:
        from blunderbus_memory import (
            Question, QuestionStatus, QuestionTargetKind, get_default_registry,
        )
    except Exception:
        return []

    out = []
    try:
        reg = get_default_registry()
        for a in reg.accounts.all():
            owner = (a.owner or "").upper()
            if owner == "UNKNOWN" or not a.owner:
                last = f" (...{a.last_four})" if a.last_four else ""
                out.append(Question(
                    id=f"finance:owner:{a.id}",
                    agent="finance",
                    question_type="owner-confirm",
                    target_kind=QuestionTargetKind.ACCOUNT,
                    target_id=a.id,
                    target_field="owner",
                    prompt=f"Who owns **{a.name}{last}**? — {a.institution or 'unknown institution'}, {a.account_type or 'account'}",
                    suggested_format=(
                        "registry person id (e.g. `brian-hodgerson`) "
                        "OR `joint with <name>` "
                        "OR a child's first name for custodial accounts"
                    ),
                    status=QuestionStatus.OPEN,
                    payload={"institution": a.institution or "", "account_type": a.account_type or ""},
                ))
            status_note = (a.attributes or {}).get("status_question")
            if status_note:
                out.append(Question(
                    id=f"finance:status:{a.id}",
                    agent="finance",
                    question_type="status-clarify",
                    target_kind=QuestionTargetKind.ACCOUNT,
                    target_id=a.id,
                    target_field="status",
                    prompt=f"`{a.name}` — {status_note}",
                    suggested_format="open / closed / pre-funding 2026 / etc.",
                    status=QuestionStatus.OPEN,
                ))
    except Exception as exc:
        print(f"  ⚠ structured question emit failed: {exc}", file=sys.stderr)
    return out


def _carry_questions_from_memory() -> list[str]:
    """Derive operator questions for the finance domain.

    Primary source: blunderbus_memory registry (accounts with unknown owners,
    accounts with status questions). Each question carries the entity id so
    filling the field auto-resolves the question.

    Fallback: legacy ## Open questions sections in finance memory files for
    anything not yet modeled in the registry.
    """
    import re
    qs: list[str] = []

    # ── Primary: registry-derived questions ──────────────────────────────────
    try:
        from blunderbus_memory import get_default_registry  # noqa: E402
        reg = get_default_registry()
        for a in reg.accounts.all():
            owner = (a.owner or "").upper()
            if owner == "UNKNOWN" or not a.owner:
                last = f" (...{a.last_four})" if a.last_four else ""
                qs.append(f"[{a.id}] Confirm owner of `{a.name}{last}`")
            # Surface explicit status notes flagged in `notes_short` or attributes
            status_note = (a.attributes or {}).get("status_question")
            if status_note:
                qs.append(f"[{a.id}] {status_note}")
    except Exception as exc:
        print(f"  ⚠ registry-backed finance questions failed: {exc}", file=sys.stderr)

    # ── Fallback: legacy markdown ────────────────────────────────────────────
    for f in (MEMORY_DIR / "accounts.md", MEMORY_DIR / "tax-positions.md",
              MEMORY_DIR / "goals.md"):
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"^## Open questions.*?$(.*?)(?=^## |\Z)", text,
                      flags=re.MULTILINE | re.DOTALL)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("- [ ]"):
                continue
            qs.append(f"[{f.stem}] {line[5:].strip()}")

    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in qs:
        key = q.lower().strip()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out[:8]


def run(today: date | None = None) -> AgentReport:
    """Produce the finance domain's AgentReport for the given day."""
    started = datetime.now()
    today = today or date.today()

    try:
        # Pull existing finance_intel logic — single source of truth for queries
        # and suppression. We do NOT duplicate that logic here.
        import finance_intel as fi   # noqa: E402

        balances = fi.get_balances()
        monthly = fi.get_monthly_summary(months=3)
        current = fi.get_current_month()
        # detect_anomalies already honors recurring.md suppression and the
        # 2026-05-14 short-history merchant guard (drops drifting low-amount
        # (merchant, category) pairs like OpenAI/Internet-and-Cable).
        anomalies = fi.detect_anomalies()
        trailing_30 = fi.get_trailing_30() if hasattr(fi, "get_trailing_30") else None
        fire = fi.fire_calc(balances, monthly, current, trailing_30=trailing_30)
        pace = fi.budget_pace_alert(monthly, current)

        real = _build_real_concerns(anomalies)
        expected = _build_expected_events(anomalies)
        carried = parse_carried_from_learnings(LEARNINGS)
        metrics = _build_metrics(balances, fire, pace, anomalies)
        questions = _carry_questions_from_memory()

        # Push to Postgres agent_concerns for persistence + auto-resolution
        try:
            from concerns_sync import sync as _sync_concerns  # noqa: E402
            _sync_concerns("finance", real)
        except Exception as exc:
            print(f"  ⚠ finance concerns sync skipped: {exc}", file=sys.stderr)

        # Path C: push structured Questions to agent_questions for the Discord
        # bot to surface as threads. Failure here MUST NOT break the brief.
        try:
            from questions_sync import sync as _sync_questions  # noqa: E402
            structured_qs = _emit_structured_questions()
            _sync_questions("finance", structured_qs)
        except Exception as exc:
            print(f"  ⚠ finance questions sync skipped: {exc}", file=sys.stderr)

        # Status: degraded for high/critical unsuppressed concerns or suspect data signals.
        status = "degraded" if any(c.severity in ("critical", "high") for c in real) else "ok"
        sr = metrics.get("savings_rate_pct")
        cash = metrics.get("cash")
        if sr is not None and abs(sr) < 0.5 and cash and cash > 50_000:
            status = "degraded"
            real.append(Concern(
                severity="medium",
                summary=f"Savings rate reads {sr}% with ${cash:,.0f} cash — implausible, likely data quality issue",
                category="data-quality",
                detail="See memory/finance/data-conventions.md: transfer noise pollutes income side. Verify Monarch income categorization before treating savings rate as truth.",
                source="data-conventions.md",
            ))

        headline = _build_headline(metrics, real, expected)

        memory_consulted = []
        if LEARNINGS.exists():
            memory_consulted.append("learnings.md")
        if RECURRING.exists():
            memory_consulted.append("recurring.md")
        memory_consulted.append("baselines.md")
        if DECISIONS.exists():
            memory_consulted.append("decisions.md")

        elapsed = int((datetime.now() - started).total_seconds() * 1000)
        return AgentReport(
            agent="finance",
            status=status,
            as_of=datetime.now(),
            headline=headline,
            real_concerns=real,
            carried_concerns=carried,
            expected_events=expected,
            metrics=metrics,
            questions=questions,
            raw_data={
                "anomalies_count": len(anomalies),
                "monthly_summaries": monthly,
                "top_categories": fi.get_top_categories(days=30) if hasattr(fi, "get_top_categories") else [],
            },
            memory_consulted=memory_consulted,
            duration_ms=elapsed,
        )
    except Exception as exc:
        return AgentReport.failed("finance", str(exc), started)


# ── CLI for parallel-run validation ──────────────────────────────────────────


def _print_human(r: AgentReport) -> None:
    print(f"\n=== finance-agent · {r.status_emoji} {r.status.upper()} · {r.duration_ms}ms ===")
    print(f"Headline: {r.headline}")
    if r.error:
        print(f"ERROR: {r.error}")
        return
    if r.real_concerns:
        print(f"\nReal concerns ({len(r.real_concerns)}):")
        for c in r.real_concerns:
            print(f"  [{c.severity:8s}] {c.summary}")
    if r.carried_concerns:
        print(f"\nCarried concerns ({len(r.carried_concerns)}):")
        for c in r.carried_concerns:
            print(f"  [{c.severity:8s}] {c.summary}  (seen {c.days_seen}×)")
    if r.expected_events:
        print(f"\nExpected events ({len(r.expected_events)} suppressed):")
        for e in r.expected_events:
            amt = f"${e.amount:,.0f}" if e.amount is not None else "—"
            print(f"  · {e.summary[:60]:60s} {amt}  ← {e.reason[:60]}")
    if r.questions:
        print(f"\nOpen questions ({len(r.questions)}):")
        for q in r.questions[:5]:
            print(f"  ? {q}")
    print(f"\nMetrics:")
    for k, v in r.metrics.items():
        if v is None:
            continue
        if isinstance(v, float):
            print(f"  {k:22s} {v:,.2f}")
        else:
            print(f"  {k:22s} {v}")
    print(f"\nMemory consulted: {', '.join(r.memory_consulted)}")


if __name__ == "__main__":
    import argparse
    import io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="emit JSON instead of human format")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    args = p.parse_args()

    report = run(args.date)
    if args.json:
        print(report.to_json())
    else:
        _print_human(report)
    sys.exit(0 if report.status != "failed" else 1)
