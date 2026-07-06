"""
BlunderBus MCP server for Anthropic Custom Connector.

Exposes BlunderBus memory layer (decisions journal, concerns table,
registry, conversation chunks) as MCP tools. Designed to be reached by
claude.ai mobile/web/desktop via a remote MCP connector.

v1: 8 tools, stdio transport. HTTP + OAuth follows.
"""
from __future__ import annotations

import os
import sys
import uuid
import datetime as dt
from pathlib import Path
from typing import Optional

# Set up imports from existing BlunderBus code.  ProfX historically ran from
# /opt/blunderbus-claude; AI-Workstation uses /home/brian/blunderbus-claude.
# Keep this env-driven so the same checkout can run on either host.
PROJECT_ROOT = Path(
    os.environ.get("BLUNDERBUS_PROJECT_ROOT", Path(__file__).resolve().parents[2])
).resolve()
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from runtime import read_env_file
for k, v in read_env_file().items():
    os.environ.setdefault(k, v)

from vault import load_secrets
load_secrets()

from blunderbus_memory.concerns import PostgresConcerns
from blunderbus_memory.models import Severity

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from mcp.server.auth.settings import ClientRegistrationOptions

from oauth_provider import SqliteOAuthProvider

# ── Configuration ────────────────────────────────────────────────────────────
DECISIONS_DIR = PROJECT_ROOT / "decisions"
REGISTRY_DIR = PROJECT_ROOT / "memory" / "registry"
CHUNKS_DIR = PROJECT_ROOT / "memory" / "chunks"
CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

REGISTRY_KINDS = ("people", "projects", "accounts", "inventory")

# Public-facing issuer URL (Cloudflare Tunnel → this server). Override via env
# for local testing. The OAuth metadata documents claim this base URL.
OAUTH_ISSUER_URL = os.environ.get("OAUTH_ISSUER_URL", "https://bb-mcp.hodgespot.com")

OAUTH_DB_PATH = Path(__file__).parent / "oauth.db"

# Scopes claude.ai (or any DCR client) may request. We hand-pick two:
#  - bb:read  → read tools (decisions, concerns, registry get/list)
#  - bb:write → write tools (log decision, file/resolve concern, save chunk)
SCOPES = ["bb:read", "bb:write"]


# ── OAuth provider ──────────────────────────────────────────────────────────
# OAuth is only meaningful for the remote HTTP connector (claude.ai). Under
# stdio transport the caller is already a trusted local process, so we skip it
# entirely — instantiating SqliteOAuthProvider would otherwise try to write
# oauth.db, which fails for non-owner local callers (e.g. the Hermes gateway).
_HTTP_MODE = "--http" in sys.argv
oauth = SqliteOAuthProvider(
    db_path=OAUTH_DB_PATH,
    base_url=OAUTH_ISSUER_URL,
    resource_base_url=OAUTH_ISSUER_URL,
    client_registration_options=ClientRegistrationOptions(
        enabled=True,
        valid_scopes=SCOPES,
        default_scopes=SCOPES,
    ),
    required_scopes=None,  # per-tool scopes enforced via require_scopes()
) if _HTTP_MODE else None


def _scopes(*names: str):
    """Per-tool scope guard — enforced only under the remote HTTP connector.

    Under stdio the local caller is trusted and there is no auth context, so
    require_scopes() would have nothing to check against; return None to skip.
    """
    return require_scopes(*names) if _HTTP_MODE else None


# ── MCP server ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="BlunderBus Memory",
    instructions=(
        "Memory layer for the HodgeSpot home infrastructure agent (BlunderBus). "
        "Read tools query the decisions journal, registry of facts (people, "
        "projects, accounts, inventory), and active concerns. Write tools "
        "append decisions, file concerns, and save conversation chunks. "
        "Use these tools to maintain continuity across surfaces (mobile, "
        "desktop, Claude Code)."
    ),
    auth=oauth,
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _today() -> str:
    return dt.date.today().isoformat()


def _decision_files(days: int) -> list[Path]:
    """Return decision files for the past N days, newest first."""
    cutoff = dt.date.today() - dt.timedelta(days=days)
    files = []
    for f in sorted(DECISIONS_DIR.glob("*.md"), reverse=True):
        try:
            d = dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if d >= cutoff:
            files.append(f)
    return files


# ── Read tools ───────────────────────────────────────────────────────────────
@mcp.tool(auth=_scopes("bb:read"))
def bb_recent_decisions(days: int = 7, agent: Optional[str] = None) -> str:
    """
    Read recent decisions from the BlunderBus decisions journal.

    Args:
        days: How many days back to look (default 7, max 90).
        agent: Optional agent filter (e.g. 'finance', 'infra', 'workspace').

    Returns:
        Markdown-formatted concatenation of decision entries newest-first.
    """
    days = max(1, min(days, 90))
    files = _decision_files(days)
    if not files:
        return f"No decision files found in the past {days} days."

    out = []
    for f in files:
        content = f.read_text()
        if agent:
            # Only include entries mentioning this agent prefix
            sections = content.split("\n## ")
            kept = [sections[0]] + [s for s in sections[1:] if s.lower().startswith(agent.lower())]
            if len(kept) == 1:
                continue
            content = "\n## ".join(kept)
        out.append(f"# {f.stem}\n\n{content}")
    return "\n\n---\n\n".join(out) or f"No decisions matching agent='{agent}'."


@mcp.tool(auth=_scopes("bb:read"))
def bb_active_concerns(agent: Optional[str] = None, severity: Optional[str] = None) -> str:
    """
    List active concerns from the agent_concerns table.

    Args:
        agent: Optional filter ('finance', 'infra', 'workspace').
        severity: Optional filter ('critical', 'high', 'medium', 'low', 'info').

    Returns:
        Tabular list of active concerns, newest first.
    """
    with PostgresConcerns() as s:
        all_active = s.list_active()
    rows = []
    for c in all_active:
        if agent and c.agent.lower() != agent.lower():
            continue
        if severity and str(c.severity).split(".")[-1].lower() != severity.lower():
            continue
        rows.append(c)
    if not rows:
        return f"No active concerns matching filters."
    lines = [f"## Active concerns ({len(rows)})"]
    for c in rows:
        sev = str(c.severity).split(".")[-1]
        lines.append(
            f"- `{c.id[:8]}` [{c.agent}/{sev}] **{c.summary}**"
            + (f"\n  target: {c.target}" if c.target else "")
            + (f"\n  suggested: {c.suggested_action}" if c.suggested_action else "")
        )
    return "\n".join(lines)


@mcp.tool(auth=_scopes("bb:read"))
def bb_registry_list(kind: str, query: Optional[str] = None) -> str:
    """
    List entries from a registry directory.

    Args:
        kind: One of 'people', 'projects', 'accounts', 'inventory'.
        query: Optional case-insensitive substring filter on filename.

    Returns:
        Bulleted list of slug + first heading from each entry.
    """
    if kind not in REGISTRY_KINDS:
        return f"Invalid kind '{kind}'. Use one of: {', '.join(REGISTRY_KINDS)}"
    dir = REGISTRY_DIR / kind
    if not dir.exists():
        return f"No registry directory for kind '{kind}'."
    items = []
    for f in sorted(dir.glob("*.md")):
        slug = f.stem
        if query and query.lower() not in slug.lower():
            continue
        first = next((ln for ln in f.read_text().splitlines() if ln.strip()), "")
        items.append(f"- **{slug}** — {first.lstrip('#').strip()}")
    if not items:
        return f"No {kind} entries{(' matching ' + query) if query else ''}."
    return f"## {kind} ({len(items)})\n\n" + "\n".join(items)


@mcp.tool(auth=_scopes("bb:read"))
def bb_registry_get(kind: str, slug: str) -> str:
    """
    Read the full content of a specific registry entry.

    Args:
        kind: One of 'people', 'projects', 'accounts', 'inventory'.
        slug: The filename without .md extension.

    Returns:
        Full markdown content of the registry entry, or an error message.
    """
    if kind not in REGISTRY_KINDS:
        return f"Invalid kind '{kind}'. Use one of: {', '.join(REGISTRY_KINDS)}"
    f = REGISTRY_DIR / kind / f"{slug}.md"
    if not f.exists():
        return f"No {kind} entry '{slug}' found."
    return f.read_text()


# ── Write tools ──────────────────────────────────────────────────────────────
@mcp.tool(auth=_scopes("bb:write"))
def bb_log_decision(
    agent: str,
    target: str,
    decision: str,
    reasoning: str,
    related: Optional[list[str]] = None,
) -> str:
    """
    Append an entry to today's decisions journal.

    Args:
        agent: The agent making the decision ('finance', 'infra', 'workspace', or a skill name).
        target: What the decision is about (e.g. 'amex-platinum-fee', 'q3-launch-positioning').
        decision: One-line verb-led decision (e.g. 'applied', 'rolled-back', 'approved').
        reasoning: 1-3 lines explaining why.
        related: Optional list of registry IDs or file references this decision touches.

    Returns:
        Confirmation string with the file path.
    """
    DECISIONS_DIR.mkdir(exist_ok=True)
    f = DECISIONS_DIR / f"{_today()}.md"
    header = f"# Decisions — {_today()}\n\n" if not f.exists() else ""
    now = dt.datetime.now().strftime("%H:%M:%S")
    related_line = f"- **Related**: {', '.join(related)}\n" if related else ""
    entry = (
        f"\n## {agent} · {decision} · {target}\n\n"
        f"- **At**: {now}\n"
        f"- **Decision**: {decision}\n"
        f"- **Reasoning**: {reasoning}\n"
        f"{related_line}"
    )
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(header + entry)
    return f"Logged decision to {f}: {agent} · {decision} · {target}"


@mcp.tool(auth=_scopes("bb:write"))
def bb_set_concern(
    agent: str,
    type: str,
    target: str,
    severity: str,
    summary: str,
    suggested_action: Optional[str] = None,
) -> str:
    """
    File a concern in the agent_concerns table.

    Args:
        agent: The agent filing this concern ('finance', 'infra', 'workspace').
        type: Concern type category (e.g. 'host-down', 'spending-anomaly', 'unread-emails').
        target: What this concern is about (e.g. hostname, category, person slug).
        severity: One of 'critical', 'high', 'medium', 'low', 'info'.
        summary: Human-readable one-line summary.
        suggested_action: Optional suggested next step.

    Returns:
        Confirmation with the concern's id.
    """
    sev_map = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
        "info": Severity.INFO,
    }
    sev = sev_map.get(severity.lower())
    if not sev:
        return f"Invalid severity '{severity}'. Use one of: {', '.join(sev_map.keys())}"
    with PostgresConcerns() as s:
        cid = s.upsert(
            agent=agent, type=type, target=target,
            severity=sev, summary=summary,
            suggested_action=suggested_action, verifier=None, payload={},
        )
    return f"Concern filed: id={cid[:8]} [{agent}/{severity}] {summary}"


@mcp.tool(auth=_scopes("bb:write"))
def bb_resolve_concern(concern_id: str, note: Optional[str] = None) -> str:
    """
    Mark a concern as resolved.

    Args:
        concern_id: The concern's id (full uuid or 8-char prefix).
        note: Optional resolution note (not stored in v1; future enhancement).

    Returns:
        Confirmation message.
    """
    with PostgresConcerns() as s:
        active = s.list_active()
        match = [c for c in active if c.id == concern_id or c.id.startswith(concern_id)]
        if not match:
            return f"No active concern matching id '{concern_id}'."
        if len(match) > 1:
            return f"Ambiguous prefix '{concern_id}' — matches {len(match)} concerns."
        c = match[0]
        ok = s.resolve(c.id)
    return f"{'Resolved' if ok else 'No-op'}: {c.id[:8]} [{c.agent}] {c.summary[:80]}"


@mcp.tool(auth=_scopes("bb:write"))
def bb_save_chunk(
    topic: str,
    summary: str,
    decisions: Optional[list[str]] = None,
    follow_ups: Optional[list[str]] = None,
    raw_excerpt: Optional[str] = None,
) -> str:
    """
    Persist a conversation chunk to BlunderBus memory.

    Use this when discussing a meaningful topic, decision, deadline, or
    commitment. The chunk becomes searchable in future sessions across
    any surface (mobile Claude, desktop Claude, Claude Code).

    Args:
        topic: Short slug-ish topic (e.g. 'q3-launch-pricing', 'mom-birthday-plan').
        summary: 2-4 sentence summary of what was discussed.
        decisions: Optional list of specific decisions made.
        follow_ups: Optional list of follow-up actions or open questions.
        raw_excerpt: Optional raw quote from the conversation (1-2 sentences).

    Returns:
        Confirmation with the chunk's file path.
    """
    ts = dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    chunk_id = f"{ts}-{topic.replace(' ', '-')[:40]}"
    f = CHUNKS_DIR / f"{chunk_id}.md"
    content = [
        f"# Chunk: {topic}",
        f"",
        f"- **Saved**: {dt.datetime.now().isoformat()}",
        f"- **Topic**: {topic}",
        "",
        "## Summary",
        summary,
    ]
    if decisions:
        content.append("\n## Decisions")
        content.extend(f"- {d}" for d in decisions)
    if follow_ups:
        content.append("\n## Follow-ups")
        content.extend(f"- {u}" for u in follow_ups)
    if raw_excerpt:
        content.append("\n## Excerpt")
        content.append(f"> {raw_excerpt}")
    f.write_text("\n".join(content))
    return f"Chunk saved: {f.name}"


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Default: stdio transport (for local testing)
    # Use --http to switch to HTTP transport (for remote/claude.ai)
    if "--http" in sys.argv:
        port = int(os.environ.get("MCP_PORT", "8789"))
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run()
