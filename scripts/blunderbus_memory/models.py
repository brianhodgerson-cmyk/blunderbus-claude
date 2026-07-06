"""
Pydantic models for BlunderBus memory.

These are the load-bearing schemas. Storage backends (markdown today, Postgres
later for TLS) serialize/deserialize these. Agents work with these objects.

Design principles:
- Stable identifiers (slugs) — never the display name. Slug is the primary key.
- `attributes` JSONB-equivalent dict for extension without schema migrations.
- `tenant_id` from day 1 so the home → TLS port is a config change.
- `created_by_agent` and timestamps so we can replay history if event-sourcing
  ever becomes useful at scale.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


# ── Enums ────────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ConcernStatus(str, Enum):
    ACTIVE = "active"        # currently true
    RESOLVED = "resolved"    # explicitly cleared (by probe or operator)
    STALE = "stale"          # TTL exceeded with no recent verification


class QuestionStatus(str, Enum):
    """Lifecycle of an agent_questions row — see sql/002_questions.sql."""
    OPEN = "open"             # emitted by agent, bot hasn't posted thread yet
    POSTED = "posted"         # bot created thread, awaiting operator reply
    PROPOSED = "proposed"     # operator replied, AI parsed a value, awaiting reaction
    APPLIED = "applied"       # operator confirmed, registry written
    ABANDONED = "abandoned"   # operator declined the proposed value
    STALE = "stale"           # underlying field filled out-of-band; closed silently


class QuestionTargetKind(str, Enum):
    ACCOUNT = "account"
    PERSON = "person"
    PROJECT = "project"
    INVENTORY = "inventory"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    DONE = "done"
    ARCHIVED = "archived"


class HostStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNREACHABLE = "unreachable"


# ── Base ─────────────────────────────────────────────────────────────────────


class _Entity(BaseModel):
    """Common fields every registry entity carries."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable slug identifier — primary key.")
    tenant_id: str = "blunderbus"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""    # free-form markdown body; not parsed
    attributes: dict[str, Any] = Field(default_factory=dict, description="Schemaless extension.")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by_agent: Optional[str] = None


# ── Registry entities ────────────────────────────────────────────────────────


class Person(_Entity):
    full_name: str
    role: Optional[str] = None       # "financial planner", "client", "spouse"
    title: Optional[str] = None      # job title
    firm: Optional[str] = None       # employer/affiliation
    relationships: list[str] = Field(default_factory=list, description="project ids this person is involved in")
    triage: Optional[str] = None     # "high" / "medium" / "low" — inbox routing


class Project(_Entity):
    name: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    summary: Optional[str] = None
    people: list[str] = Field(default_factory=list, description="person ids on this project")
    blockers: list[str] = Field(default_factory=list, description="open questions blocking progress")
    resolved_blockers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="archived blockers with resolution + resolved_at — appended by registry_writer "
                    "when a project-blocker question is applied via Discord thread"
    )
    last_activity: Optional[datetime] = None


class Account(_Entity):
    name: str                        # display name
    institution: str                 # NFCU, Fidelity, Roundpoint
    account_type: str                # checking, savings, IRA, brokerage, mortgage
    last_four: Optional[str] = None
    account_number: Optional[str] = None  # full account # (sensitive — only stored when operator chooses)
    owner: Optional[str] = None      # person id (or "joint")
    balance: Optional[float] = None
    notes_short: Optional[str] = None


class Inventory(_Entity):
    """Hosts, VMs, LXC containers, services."""
    hostname: str
    ip: Optional[str] = None
    vmid: Optional[int] = None       # Proxmox VMID
    kind: str = "vm"                 # vm | lxc | service | host
    role: Optional[str] = None       # "monitoring", "ids", "storage"
    ssh_alias: Optional[str] = None
    status: HostStatus = HostStatus.RUNNING
    monitored: bool = False          # is this in Prometheus?


# ── Mutable state ────────────────────────────────────────────────────────────


class Concern(BaseModel):
    """Mutable concern with auto-resolution lifecycle.

    Lives in Postgres (`agent_concerns` table). Sourced from Prometheus alerts
    for infra, from agent probes for finance/workspace.
    """
    model_config = ConfigDict(extra="forbid")

    id: str                               # stable: "infra:host-down:thor"
    tenant_id: str = "blunderbus"
    agent: str                            # which agent owns this concern
    type: str                             # "host-down", "disk-high", "stalled-project"
    target: Optional[str] = None          # entity id this concerns (host id, project id)
    severity: Severity = Severity.MEDIUM
    status: ConcernStatus = ConcernStatus.ACTIVE
    summary: str
    suggested_action: Optional[str] = None
    verifier: Optional[str] = None        # "prometheus:HostDown" or "probe:host_reachable"
    first_seen: Optional[datetime] = None
    last_verified: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def days_seen(self) -> int:
        if not self.first_seen:
            return 0
        ref = self.last_verified or datetime.now(self.first_seen.tzinfo)
        return max(0, (ref - self.first_seen).days)


class Question(BaseModel):
    """Mutable question with multi-state lifecycle. Lives in Postgres
    `agent_questions` table. Backs the Path C Discord-thread write-back flow.

    Agents emit Questions when they discover a field in the registry they need
    the operator to fill (e.g. `owner=UNKNOWN` on an account). The Discord bot
    picks them up, creates a thread, listens for replies, proposes a value, and
    on operator confirmation writes the registry + journals the decision.
    """
    model_config = ConfigDict(extra="forbid")

    id: str                                    # stable: "finance:owner:nfcu-share-savings"
    tenant_id: str = "blunderbus"
    agent: str                                 # "finance", "infra", "workspace"
    question_type: str                         # "owner-confirm", "project-blocker", ...
    target_kind: QuestionTargetKind
    target_id: str                             # entity slug, e.g. "nfcu-share-savings"
    target_field: Optional[str] = None         # which YAML field to fill on apply
    prompt: str                                # the human-facing question text
    suggested_format: Optional[str] = None     # answer-shape hint
    status: QuestionStatus = QuestionStatus.OPEN
    discord_thread_id: Optional[int] = None
    discord_propose_message_id: Optional[int] = None
    proposed_value: Optional[str] = None       # set when AI parses a reply
    applied_value: Optional[str] = None        # final value written
    answered_by: Optional[str] = None          # Discord user id
    payload: dict[str, Any] = Field(default_factory=dict)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None


class JournalEntry(BaseModel):
    """Append-only observation from an agent. Never mutated.

    Stored as markdown in `memory/agents/<agent>/journal.md` for now —
    one entry per dated heading. Could move to Postgres for TLS.
    """
    model_config = ConfigDict(extra="forbid")

    id: str                               # uuid or timestamp-based
    tenant_id: str = "blunderbus"
    agent: str
    at: datetime
    kind: str                             # "observation" | "decision" | "pattern"
    summary: str                          # one-line headline
    body: str = ""                        # full markdown body, optional
    refs: list[str] = Field(default_factory=list, description="entity ids this references")


# ── Result of registry lookups ──────────────────────────────────────────────


class RegistryStats(BaseModel):
    """Returned by Registry.stats() for diagnostics."""
    backend: str
    counts: dict[str, int]
    last_loaded: datetime
