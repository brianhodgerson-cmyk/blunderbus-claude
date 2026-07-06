# Agent Memory Architecture

**Status:** BlunderBus v1 shipped 2026-05-01. TLS workspace v1 designed (planned execution on Mercury, currently paused at Phase −1 preflight).

This doc describes the memory system pattern used (and being ported) across BlunderBus's home-lab agents and Russ's TLS Geothermics workspace on Mercury. It is a single architecture pattern with two implementations chosen by operator scale and technical surface.

## Mental model

Most "agents have memory" systems try to give one giant memory layer. This pattern instead distinguishes **four kinds of memory-shaped things** and treats only one as actual memory:

| Layer | What it is | Read | Written by |
|---|---|---|---|
| 1. Static context | Instructions loaded each session | Every run | Humans (CLAUDE.md, rules) |
| 2. Reference data | Files agents can read but not write back to | Every run | Humans / sync pipelines |
| 3. Session memory | The current conversation | While alive | Conversation itself |
| **4. Persistent agent memory** | What agents have decided, observed, learned across time | Future runs read it | **Agents themselves** |

Layers 1–3 are common. Layer 4 is the missing piece in most setups and the reason agents feel like clever interns who forget on Monday. This architecture supplies Layer 4 in three concrete substrates: **registry**, **decisions journal**, and **concerns lifecycle**.

## The three substrates

### Registry — *what's true*

Stable facts about the working environment. One file per entity, structured frontmatter for agent reading, prose body for human authoring. Entities standardized in the BlunderBus implementation:

- `Person` — internal team, stakeholders, clients
- `Project` — active work (campaigns, field projects, initiatives)
- `Account` — financial accounts, ad accounts, services
- `Inventory` — hosts, services, infrastructure
- `BrandSystem` (TLS extension) — voice rules, palette, forbidden phrases

Every entity has `tenant_id`, `id` (durable), `created_at`, `created_by_agent`, plus a JSONB-equivalent `attributes` dict for extension without schema migration.

### Decisions journal — *what was decided*

Append-only log of agent decisions. Every meaningful approve / revise / reject / escalate writes one entry with: `target`, `decision`, `reasoning` (3-line cap), `light_pillars` or equivalent principle tags, `related_people` (by canonical id), `related_projects`, timestamp, and authoring `persona`.

Three months in, this becomes the most valuable substrate: agents can be asked *"why did we kill that"* with a SQL query (or Dataview query in the markdown variant). Drift becomes visible as a column. Taste accumulates.

### Concerns — *what's unsettled*

Live mutable state of things agents are worried about. Lifecycle: `active → resolved → stale`. Each agent run:

- Files new concerns it noticed
- **Reconciles its own previously-filed concerns** — if the issue cleared, it auto-marks resolved; if still real, it stays active

The reconcile step is the design's secret. Without it, concerns become a graveyard nobody trusts. With it, the list is always real.

## Two implementations

| Aspect | BlunderBus (home lab) | TLS workspace (Russ on Mercury) |
|---|---|---|
| Operator | Brian (you) | Russ (non-technical in code) |
| Backend | **Postgres** (`jarvis-postgres` on Cortex, db `blunderbus_memory`) | **Markdown only** (Dataview indexes) |
| Registry | Markdown YAML-frontmatter under `memory/registry/{people,projects,accounts,inventory}/` | Same shape, under `registry/{people,projects,brands}/` |
| Concerns | Postgres `agent_concerns` table | Existing `tickets/` queue (workspace-evolution scope only — see "Locked decisions" below) |
| Journal | `journal_entries` table (deferred — not yet wired) | `decisions/YYYY-MM-DD.md` daily files |
| Tenant model | Multi-tenant from day 1 (`tenant_id` everywhere; Mercury already tagged `tenant=tls`) | Single-tenant TLS (workspace heavily TLS-namespaced at systemd/service level — multi-tenant would mean renaming services) |
| Why this split | Infra agents have high write rate (Prometheus alerts, sync state); concurrent-write safety matters; markdown would diff-noise the repo | Russ writes ~5–20 decisions/day across personas; markdown is what he speaks; Obsidian + Dataview is his medium; Postgres adds an admin surface he can't troubleshoot |

The two systems share an *architecture pattern* (registry + concerns + journal) without sharing an *implementation*. Each fits its operator's scale and technical surface.

## Skill / agent memory contract

The behavioral integration is the same shape in both implementations. Each skill (or persona) gets these eight lines added to its existing system prompt — typically inserted into the *"Always Read First"* section that already exists:

```
At the start of every task:
  1. Read registry/brands/<active>.md (or equivalent) — current voice / posture
  2. Read registry/projects/<relevant>.md — framing rules
  3. Read registry/people/<id>.md — for any stakeholder mentioned
  4. Read open concerns filed by yourself or peers — flag if any block this work

At the end of every task:
  5. Append a journal entry: target, decision, reasoning (≤3 lines), principle tags
  6. For stakeholder observations, append to that person's "## Agent notes" section
     (NOT frontmatter or sync-managed sections)
  7. File a concern for any unresolved tension
  8. Reconcile your own concerns: anything you addressed → status: resolved
```

This contract is **content** in skill prompts, not framework code. It travels with each skill and is the single integration point.

## The CRM-as-people-registry projection pattern (Russ-specific)

Russ has a Google-Sheets-backed CRM (`assets/crm-app/src/data/stakeholders.json`). Rather than duplicate stakeholder data, the TLS implementation **projects** the CRM into the registry.

```
   Google Sheet  (Russ types here, mobile-friendly)
        │
        ▼
   sync:pull  (existing script)
        │
        ├──→ assets/crm-app/...     (the React CRM app — unchanged)
        │
        └──→ registry/people/{slug}.md  (NEW — generated, agent-readable)
                ↑
         Agents read this. Dataview indexes it.
         Agents *append* notes to a "## Agent notes" section (preserved across syncs).
```

Key properties:
- **CRM `id` stays canonical** (e.g. `c-869815cd`). Slug is a projection field for filename + human readability only. Agents key off `id`, not slug.
- **Idempotent merge:** projection sync replaces frontmatter + `## From CRM` body section; preserves `## Agent notes` verbatim. Lets agents annotate without polluting Russ's spreadsheet.
- **Deletion handling:** If an `id` disappears from the CRM JSON, file moves to `registry/people/_archive/` rather than being deleted.
- **Slug renames:** If a stable `id` gets a new slug, file is renamed and the change logged in `registry/people/_aliases.md`.

Same pattern works for Brand and Project entities in TLS, where the canonical source is documents Russ already maintains.

## Subagents — when slash command vs subagent

Most personas should be **slash commands** (in-session collaborators sharing context). Convert to **subagent** only when the role is a *gate* or *researcher* — heavy private memory, isolated context, returns one summary.

Heuristic in one sentence:
> *Slash command if it collaborates mid-conversation. Subagent if it returns a verdict, a research summary, or operates on a private body of memory.*

For TLS (Russ has six existing slash-command personas), memory v1 adds two subagents using names already in `docs/roadmap.md`:

- **`brand-reviewer`** — review gate. Reads `registry/brands/tls.md` + last 30 days of `decisions/` + draft target. Returns approve/revise/reject with LIGHT-pillar-tied reasoning. Writes own decision entry.
- **`stakeholder-analyst`** — dossier on demand. Inputs a stakeholder `id` or name; reads `registry/people/{slug}.md` + intel briefs mentioning them + decisions referencing that `id`. Returns structured dossier.

`design-critic` and `content-strategist` (also in the roadmap) stay deferred — not part of memory v1.

## Locked decisions for TLS implementation

These were arbitrated during design review and should not silently drift:

| # | Decision | Reasoning |
|---|---|---|
| 1 | Markdown-only backend | Russ's scale (~5–20 decisions/day), non-technical operator, Obsidian/Dataview is his medium |
| 2 | Single-tenant locked (not deferred) | Workspace heavily TLS-namespaced at systemd/service level; multi-tenant means renaming services, out of scope |
| 3 | Google Sheet stays canonical for stakeholders | `stakeholders.json` is authoritative; registry/people is a projection |
| 4 | Person canonical ID = CRM `id`, NOT slug | Existing schema declares `id` as PK; `normalize.py` preserves stable IDs across syncs |
| 5 | CLAUDE.md stays monolithic | Specifics move into `registry/`; CLAUDE.md becomes pointer-style |
| 6 | Decisions log retention | Daily file, append-only, never deleted (cheap; queryable) |
| 7 | Subagent names follow existing `docs/roadmap.md` | `brand-reviewer` + `stakeholder-analyst` (not `marketing-director` / `stakeholder-researcher`) |
| 8 | Sync mechanism = systemd user timer | Modeled on existing `tls-daily-brief.timer`, NOT cron |
| 9 | Behavioral contract location = `.claude/skills/*/SKILL.md` | Commands are thin wrappers calling skills; contracts go on the actual behavior layer |
| 10 | Workshop tickets untouched | `tickets/` stays scoped to workspace evolution per existing `.claude/rules/tickets.md`; persona observations go to `decisions/` exclusively |

## File locations

### BlunderBus (already deployed)
- Code: `scripts/blunderbus_memory/{models,registry,concerns,migrate}.py`
- Schema: `scripts/blunderbus_memory/sql/001_init.sql` (loaded via `docker cp + psql -f`)
- Markdown registry: `memory/registry/{people,projects,accounts,inventory}/*.md`
- Postgres: `jarvis-postgres` on Cortex, db `blunderbus_memory`, table `agent_concerns`
- Secrets: `BLUNDERBUS_DB_PASSWORD` from Vaultwarden (`jarvis-postgres` item, `login.password`)
- Tests: `tests/memory_v1/` — registry roundtrip + concerns lifecycle (19 tests, all passing)

### TLS (designed, execution paused at preflight)
- Workspace root: `/home/russ/tls-ai-workspace/` on Mercury (LXC 108)
- Planned: `registry/{people,projects,brands,_meta}/`
- Planned: `decisions/YYYY-MM-DD.md`
- Planned: `_templates/Decision.md` (Templater scaffold)
- Planned new subagents under: `.claude/agents/{brand-reviewer,stakeholder-analyst}.md`
- Planned per-agent runbooks under: `memory/{brand-reviewer,stakeholder-analyst}/{runbook,learnings}.md`
- Generated: `registry/_meta/sync-status.md` (markdown projection of CRM sync state — Home dashboard reads this so it works in Obsidian on phone with no SQL/systemd dependency)

## Out of scope (intentional)

- FastAPI service in front of Postgres (only when porting to scenarios that need multi-machine access — TLS isn't that today)
- `PostgresRegistry` backend for TLS (deferred until/unless multi-tenant happens for Rusty)
- Vector embeddings, event sourcing (over-engineered at this scale; door open via `created_by_agent` columns)
- Meetings entity projection (`meetings.json` is sibling to stakeholders in CRM; skip for v1)
- Multi-tenant for TLS (locked single-tenant)
- `design-critic` and `content-strategist` subagents (stay in roadmap, not in v1)
- Full inventory port — agents still use hardcoded HOSTS lists in `morning_brief_push.py` for SSH probes; registry inventory is a separate truth used by `infra.py` to know "what should exist"

## When you (a future Claude session) read this

If a user asks about "the memory system we architected" or "agent memory" or "how do agents remember things across runs", this doc is the source of truth. Specifically:

- **For BlunderBus** — the Postgres-backed implementation is live as of 2026-05-01. Look at `memory/registry/` and the `agent_concerns` table on `jarvis-postgres`.
- **For TLS / Russ / Mercury** — the markdown-only implementation is *designed* but not yet built. The plan is gated at "Phase −1 preflight" (Proxmox snapshot + tarball backup of Mercury workspace) and was paused mid-conversation. Russ's involvement and explicit go-ahead are needed before execution.
- **The architecture pattern** — registry + decisions journal + concerns lifecycle + skill memory contract — applies to any new agent-system in the lab. New tenants follow this pattern; pick markdown or Postgres backend by scale.

If a user asks for the plan to ship TLS memory v1, the canonical reference is the Mercury-fit v2 plan we landed on (Phases −1 through 7). It is reproducible from the locked decisions table above. The single most fragile piece is the CRM projection script's idempotent-merge logic — three test cases gate it: fresh write, overwrite-without-notes, merge-with-notes preserved.
