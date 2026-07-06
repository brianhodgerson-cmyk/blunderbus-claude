# BlunderBus Memory — One-Page View

> Quick visual reference for how the memory system actually works.
> Deeper architecture and rationale: [agent-memory-architecture.md](agent-memory-architecture.md).
> Operator entry point: [../memory/MEMORY.md](../memory/MEMORY.md).
> Daily contract for skills/agents: [../.claude/rules/memory-contract.md](../.claude/rules/memory-contract.md).

---

## What the system is

Most AI agents forget overnight. This one builds memory across three substrates with different lifespans and write semantics, so the morning brief can say "Stark's been hot for 6 days" with real evidence — not a guess.

## The 4-Layer Model

| Layer | Lifespan | Read | Written by | Example |
|---|---|---|---|---|
| **1. Static context** | Forever | Every run | Humans (you) | `CLAUDE.md`, `.claude/rules/` |
| **2. Reference data** | Stable | Every run | Humans / sync | `memory/registry/people/sheila-streeter.md` |
| **3. Session memory** | This chat | While alive | The conversation | What I see right now |
| **4. Persistent agent memory** | Cross-run | Future runs | **Agents themselves** | The three substrates below ⬇ |

## The 3 Substrates of Layer 4

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│    REGISTRY     │    │    CONCERNS     │    │   DECISIONS     │
│  what is TRUE   │    │ what is OPEN    │    │ what was DECIDED│
├─────────────────┤    ├─────────────────┤    ├─────────────────┤
│ memory/registry │    │ Postgres        │    │ decisions/      │
│   /people       │    │ jarvis-postgres │    │   YYYY-MM-DD.md │
│   /projects     │    │ db: blunderbus_ │    │                 │
│   /accounts     │    │ memory          │    │ Append-only     │
│   /inventory    │    │ table:          │    │ Markdown        │
│                 │    │ agent_concerns  │    │                 │
│ Stable facts.   │    │ Live state with │    │ Why an agent    │
│ Read every run, │    │ first_seen,     │    │ took an action  │
│ updated rarely. │    │ days_seen,      │    │ (resolved,      │
│                 │    │ resolved_at,    │    │  suppressed,    │
│                 │    │ auto-reconciled │    │  escalated)     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        ↑                      ↑                      ↑
        └────── READ + WRITE BY 3 AGENTS ──────┬──────┘
                                              │
        ┌──────────┐    ┌──────────┐    ┌─────┴────┐
        │ infra    │    │ finance  │    │workspace │
        │ agent    │    │ agent    │    │ agent    │
        └─────┬────┘    └────┬─────┘    └─────┬────┘
              └──────────────┼──────────────────┘
                             ▼
                  ┌──────────────────────┐
                  │  daily_brief.py      │
                  │  (orchestrator)      │
                  │                      │
                  │  fanout → validate   │
                  │  → AI synthesize     │
                  └────────┬─────────────┘
                           ▼
              Obsidian note + Telegram + ops UI
```

## Daily Flow (what happens at 7:30 AM)

```
                ┌─ reads ──┬─ files new ────────────┐
                │          │                        │
agents/infra.py ├─ reads ──┤                        ▼
agents/finance ├─ reads ──┤  agent_concerns        ┌──────────────────┐
agents/work…   ├─ reads ──┤  (Postgres)            │  reconcile()     │
                │          │                        │  resolves stale  │
                │          └─ reads ────────────── ─┤  rows; emits     │
memory/registry │                                   │  (id, summary)   │
memory/<agent>  │                                   │  tuples          │
                │                                   └────────┬─────────┘
                │                                            │
                │                                            ▼
                │                            ┌───────────────────────────┐
                │                            │ write_decision() per      │
                │                            │ resolved row →            │
                │                            │ decisions/YYYY-MM-DD.md   │
                │                            └───────────────────────────┘
                │
daily_brief.py ─┴─→ ai_synthesize(reports, validations)
                              │
                              ▼
                    SYNTHESIS_PROMPT now includes:
                    - Registry context (people, projects, hosts)
                    - Concern lineage (id, severity, days_active)
                    - Cross-validation flags (e.g. negative savings rate)
                    - Agent reports (status, headlines, metrics)
                              │
                              ▼
                    "Morning, Brian. Spending is outpacing
                     income this period with a -16.4% savings
                     rate, and fury went offline overnight…"
```

## Why It's Better — Concrete Comparisons

| Without this system (naive markdown logs) | With this system |
|---|---|
| "Did I flag this yesterday?" → grep yesterday's note, hope you find the right phrasing | `SELECT days_seen FROM agent_concerns WHERE id=…` returns `6` |
| AI re-discovers the same issues every morning, gives broadly similar summaries | AI reads structured `days_seen` + `first_seen` → can say "10 days running, no movement" |
| Stale concerns linger forever — someone has to manually prune | `reconcile()` runs every agent invocation, drops what's cleared |
| Three agents notice the same host issue → user sees it three times | Stable concern ID, single row, deduplicated automatically |
| AI prompt grows with every day of notes added → cost + context explosion | Reads small structured tables; full vault never enters the prompt |
| "Why did the agent silence that alert?" → no answer | `decisions/<date>.md` has the reasoning |
| Cross-domain insights live only in operator's head | Registry shared by all three agents + AI synthesis |

## Use Cases Where It Earns Its Keep

| Scenario | What memory does for you |
|---|---|
| **Recurring 3 AM CPU spike on Cortex** | `memory/infra/recurring.md` marks it as a known Docker watchtower pull → agent suppresses it from "new concerns" automatically. No more daily "CPU high" noise. |
| **You start a tax amendment in March, ignore it for 2 weeks** | Workspace agent's concern `task-backlog tax-amendment-2025` has `days_seen=14` → AI surfaces "this has been stalled 14 days, blockers haven't changed" without you remembering. |
| **A new host appears in the inventory** | First agent run files a fresh concern. Subsequent runs see the registry entry, change framing from "anomaly" to "known host". |
| **You restart Fury at 2 PM (operator action)** | Next agent run sees Fury reachable, reconciler resolves the `HostDown` concern, journal entry logged: `infra · resolved · …Fury…` — future you can grep when it happened. |
| **AI says something dumb** | All upstream evidence is in the registry + concerns table — you can audit exactly what the AI saw. Not a black box. |
| **You hire someone / open a new account / spin up a VM** | Edit one registry file. All three agents pick it up next run. No code changes. |
| **You're debugging "why did the brief say X yesterday?"** | `decisions/<yesterday>.md` + the concern row's history both answer it. |

## What's Special About the Design

Three things that aren't obvious from the diagram:

1. **Self-correcting** — every agent run is a fresh probe of reality. If a concern was true yesterday and isn't today, the reconciler clears it without operator action. Concerns can't ossify.

2. **Append-only journal, structured concerns** — two complementary write modes. The journal is human-readable narrative ("why did we do X"); the concerns table is queryable state ("what's currently broken"). Neither tries to do the other's job.

3. **Memory is the *agent's*, not the *AI's*** — the LLM is stateless and disposable. The agent framework + Postgres + markdown is what persists. You could swap Claude for Gemini tomorrow and the memory wouldn't move.
