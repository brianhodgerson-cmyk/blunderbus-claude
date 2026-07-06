"""
BlunderBus agent contract — the standardized return shape every domain agent
must produce. The DailyBrief orchestrator never touches domain logic; it asks
each agent for an AgentReport and composes the brief from the structured output.

This is the single source of truth for what an "agent report" means in BlunderBus.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Literal


Severity = Literal["critical", "high", "medium", "low", "info"]
Status = Literal["ok", "degraded", "failed"]


@dataclass
class Concern:
    """Something the operator should see. Real anomalies AND carried persistent
    issues both use this shape — the difference is which list they live in on
    AgentReport."""
    severity: Severity
    summary: str                     # one-liner, e.g. "Personal $630 (21x baseline)"
    detail: str = ""                 # longer explanation, optional
    category: str = ""               # domain-specific tag: "spending", "host", "task", "alert"
    days_seen: int = 1               # for carried concerns; 1 = new today
    first_seen: str | None = None    # ISO date
    last_seen: str | None = None     # ISO date
    metric: dict[str, Any] = field(default_factory=dict)   # numeric backing data
    suggested_action: str | None = None
    source: str = ""                 # which memory file or query produced this

    def is_carried(self) -> bool:
        return self.days_seen >= 3


@dataclass
class Event:
    """Something that happened and is explained — suppressed from concerns,
    surfaced in expected_events for transparency. The operator sees that
    BlunderBus knows about it but isn't worried."""
    summary: str
    category: str = ""
    amount: float | None = None
    reason: str = ""                 # citation: "annual auto+home renewal" / "mortgage payment"
    source: str = ""                 # "recurring.md" / "decisions.md"


@dataclass
class AgentReport:
    """Standardized return shape for every domain agent."""
    agent: str                                              # "finance" / "infra" / "workspace"
    status: Status
    as_of: datetime
    headline: str                                           # one-line bottom-line summary
    real_concerns: list[Concern] = field(default_factory=list)
    carried_concerns: list[Concern] = field(default_factory=list)
    expected_events: list[Event] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)   # for tables in the brief
    questions: list[str] = field(default_factory=list)      # unresolved questions for operator
    raw_data: dict[str, Any] = field(default_factory=dict)  # full dump for AI synthesis
    memory_consulted: list[str] = field(default_factory=list)   # which memory files
    error: str | None = None
    duration_ms: int = 0

    # ── Convenience constructors ─────────────────────────────────────────────

    @classmethod
    def failed(cls, agent: str, error: str, started: datetime | None = None) -> AgentReport:
        now = datetime.now()
        elapsed = int((now - started).total_seconds() * 1000) if started else 0
        return cls(
            agent=agent,
            status="failed",
            as_of=now,
            headline=f"{agent}-agent failed: {error[:80]}",
            error=error,
            duration_ms=elapsed,
        )

    # ── Severity helpers ─────────────────────────────────────────────────────

    @property
    def worst_severity(self) -> Severity:
        order: list[Severity] = ["critical", "high", "medium", "low", "info"]
        for sev in order:
            if any(c.severity == sev for c in self.real_concerns + self.carried_concerns):
                return sev
        return "info"

    @property
    def status_emoji(self) -> str:
        return {
            "failed": "❌",
            "degraded": "🟡",
            "ok": {"critical": "🔴", "high": "🔴", "medium": "🟡",
                   "low": "🟢", "info": "🟢"}[self.worst_severity],
        }[self.status]

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ── Helpers shared across agents ─────────────────────────────────────────────


def parse_carried_from_learnings(learnings_path) -> list[Concern]:
    """Parse `## Active concerns` / persistent block from a learnings.md.
    Used by every agent that has a daily learnings consolidator. Returns
    empty list if file missing or no Active concerns section."""
    import re
    from pathlib import Path
    p = Path(learnings_path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    # Ignore examples/templates in fenced code blocks. Placeholder learnings files
    # can contain sample "## Active concerns" sections that should not become
    # real carried concerns in the morning brief.
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    out: list[Concern] = []

    # Match "## Active concerns" or "## Persistent flags" sections.
    # Each entry in the new (consolidator) format spans two lines:
    #   - 🔴 **host-down** (high) · <sample text>
    #     _seen 7× · first 2026-04-24 · last 2026-04-30_
    for header in ("## Active concerns", "## Persistent flags"):
        m = re.search(rf"^{re.escape(header)}.*?$(.*?)(?=^## |\Z)", text,
                      flags=re.MULTILINE | re.DOTALL)
        if not m:
            continue
        block_lines = m.group(1).splitlines()
        i = 0
        while i < len(block_lines):
            line = block_lines[i].strip()
            i += 1
            if not line.startswith(("- ", "* ")):
                continue
            # Look ahead: the metadata "_seen N× · first ... · last ..._" line
            meta = ""
            if i < len(block_lines):
                next_line = block_lines[i].strip()
                if next_line.startswith("_") or "seen" in next_line.lower():
                    meta = next_line
                    i += 1

            # Parse N from "seen N×" or "n days" — check both lines
            ndays = 1
            for source_line in (meta, line):
                ndays_m = re.search(r"seen\s+(\d+)\s*[×x]", source_line, re.IGNORECASE)
                if not ndays_m:
                    ndays_m = re.search(r"\b(\d+)\s+days?\b", source_line)
                if ndays_m:
                    ndays = int(ndays_m.group(1))
                    break

            # First/last dates
            first_seen = last_seen = None
            for source_line in (meta, line):
                d2_m = re.search(r"first\s+(\d{4}-\d{2}-\d{2}).*?last\s+(\d{4}-\d{2}-\d{2})",
                                 source_line, re.IGNORECASE)
                if d2_m:
                    first_seen, last_seen = d2_m.group(1), d2_m.group(2)
                    break
                d_range = re.search(r"\((\d{4}-\d{2}-\d{2})\s*[→-]\s*(\d{4}-\d{2}-\d{2})\)", source_line)
                if d_range:
                    first_seen, last_seen = d_range.group(1), d_range.group(2)
                    break

            # Build a clean summary: strip markdown, table residue, bullets
            summary = re.sub(r"^[-*]\s+", "", line)
            summary = re.sub(r"^[🔴🟡🟢🆕✅]+\s*", "", summary)
            # Strip the leading **category** (high)  and the trailing severity tags
            summary = re.sub(r"^\*\*[^*]+\*\*\s*\([^)]+\)\s*[·:\-]?\s*", "", summary)
            summary = re.sub(r"^\*\*[^*]+\*\*\s*[·:\-]?\s*", "", summary)
            # Kill table-row residue: "Thor | Ollama · GPU | ❌"
            summary = re.sub(r"\s*\|\s*[^|]*\|\s*[^|]*$", "", summary)
            summary = summary.replace("|", " ").replace("**", "").replace("`", "")
            summary = re.sub(r"\s+", " ", summary).strip(" ·-_:")
            summary = summary[:140]
            if not summary:
                continue

            sev: Severity = "high" if ndays >= 5 else "medium" if ndays >= 3 else "low"
            out.append(Concern(
                severity=sev,
                summary=summary,
                category="carried",
                days_seen=ndays,
                first_seen=first_seen,
                last_seen=last_seen,
                source=p.name,
            ))

    # De-duplicate: identical summary should appear once with the highest day count
    deduped: dict[str, Concern] = {}
    for c in out:
        key = c.summary.lower()[:80]
        if key in deduped:
            existing = deduped[key]
            if c.days_seen > existing.days_seen:
                deduped[key] = c
        else:
            deduped[key] = c
    return list(deduped.values())
