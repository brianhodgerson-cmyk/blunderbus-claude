# Memory Contract

Every meaningful action you take in this project participates in the agent memory system. The full architecture is in [docs/agent-memory-architecture.md](../../docs/agent-memory-architecture.md). This rule is the operational contract that turns the architecture into behavior.

## Three substrates you read and write

- **Registry** (`memory/registry/{people,projects,accounts,inventory}/*.md`) — *what is true*. Stable facts. Read-only from your perspective unless explicitly editing a registry entry.
- **Decisions journal** (`decisions/YYYY-MM-DD.md`) — *what was decided*. Append-only.
- **Concerns** (Postgres `agent_concerns` table on `jarvis-postgres`, db `blunderbus_memory`) — *what is unsettled*. Filed by agents, reconciled by their next run.

## When you start a substantive task

Before acting, especially on infrastructure, finance, or stakeholder work:

1. **Read registry entries that are relevant.** If a host, person, project, or account is named or implied, read its registry file first. Don't guess facts that the registry knows.
2. **Skim open concerns** for the relevant agent if you're operating in its domain (`infra`, `finance`, `workspace`). A concern that's already active means someone else (a scheduled agent) noticed and may be acting — don't double-file. Use `psql` against `jarvis-postgres` `agent_concerns` table, filtered by `tenant_id='blunderbus' AND status='active'`.

## When you finish a substantive task

3. **Write a decision entry** if the task involved an approve / revise / reject / deploy / disable / rollback or any judgment call worth remembering. Append to `decisions/{today}.md` with: `target` (what), `decision` (verb), `reasoning` (≤3 lines, why), `related` (registry ids of hosts/people/projects touched). One paragraph max.
4. **For stakeholder observations,** append to that person's `memory/registry/people/{slug}.md` `## Agent notes` section. NEVER edit frontmatter or the `## From CRM` section — those are sync-managed.
5. **File a concern** if you observed something actionable that you couldn't resolve or that needs human judgment later. Use `agent_concerns` schema (`agent`, `type`, `target`, `severity`, `summary`, `suggested_action`, `payload`).
6. **Reconcile your own previously-filed concerns** before exiting, when feasible. If the issue is now resolved, mark `status='resolved'` and set `resolved_at=now()`. The reconcile pass is what keeps the concerns table real instead of a graveyard.

## What does NOT need a decision entry

- Pure information lookups that didn't change anything
- Read-only diagnostics
- One-off troubleshooting that didn't lead to a config change
- Anything trivial (running `ls`, checking status)

The bar is "would future-me want to know I did this and why?"

## When the registry is wrong

If you find a registry file that's stale or contradicts current reality (e.g., an inventory entry pointing at an old IP), **fix the registry file first**, then proceed. The registry is canonical for facts; if it lies, every agent gets misled.

## When you're invoked from a scheduled agent run

The scheduled agents (`scripts/agents/{infra,finance,workspace}.py`, orchestrated by `scripts/daily_brief.py`) already implement this contract programmatically — they file/reconcile concerns and write to the journal. When invoked from there, the framework handles 5–6 for you. Your job in that context is just to produce a clean `AgentReport`.

## Identity

When you write decisions or file concerns, identify yourself by skill name (e.g., `infra-check`, `dnc-log`) or agent name (`infra`, `finance`, `workspace`). Single-tenant for now: `tenant_id='blunderbus'`. The architecture supports multi-tenant; we just don't use it here.

## What this is not

- Not a logging system. Don't write a journal entry for every command. Only write for *decisions* — moments where you chose A over B for a reason.
- Not a replacement for git history. Code changes belong in commits. Decisions belong in the journal. The two reference each other but live in different layers.
- Not optional for skills that touch infrastructure or finance. For purely interactive tools (e.g. `vault-get`, `gws-setup`), the contract effectively reduces to "read registry when relevant" — no concern-filing or journal-writing needed.
