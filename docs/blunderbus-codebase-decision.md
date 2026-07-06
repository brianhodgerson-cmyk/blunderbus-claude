# BlunderBus — Two Codebases, One Decision

> Snapshot prepared 2026-05-12 for the question: *"I had Discord working with local models — should I revive v3 or build Discord into blunderbus-claude?"*

---

## TL;DR

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   You have TWO BlunderBus codebases running in parallel.                 │
│                                                                          │
│   blunderbus-claude (ProfX)  →  Works. Daily brief, memory, dashboard.   │
│   blunderbus-v3/v4 (Cortex)  →  Scaffolded. Discord alive but idle.      │
│                                                                          │
│   The local-model routing you remember was aspirational, not deployed.   │
│                                                                          │
│   Recommendation: stay on claude, port Discord IN — don't migrate OUT.   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Status Snapshot

|  | **blunderbus-claude** (ProfX) | **blunderbus-v3 / "V4"** (Cortex) |
|---|---|---|
| **Last code touch** | 🟢 hours ago (active) | 🟡 **2026-03-09** — quiet for 2 months |
| **Production behavior** | 🟢 Daily brief 07:30, real signal, telegram + dashboard | 🟡 Discord gateway resuming, no agent invocations in logs |
| **Stack footprint** | 🟢 1 ProfX VM, 2 cron entries, 1 venv | 🔴 10 Docker containers, ~3.2 GB RAM, Cortex VM |
| **Git** | 🟢 Tracked + commits + history | 🔴 Not a git repo on disk; pure file mount |
| **AI auth** | 🟢 `claude` CLI subprocess (free, OAuth) | 🟡 Anthropic API key (metered, billed) |
| **What's "working"** | Memory contract, daily brief, ops dashboard, finance/infra/workspace agents | Discord bot lifecycle, HA entity polling, Postgres + Langfuse + LiteLLM stack |
| **What's not** | Discord, multi-agent routing, MCP gateway | Evals (scaffolded, never ran), local-model routing (aspirational), agent invocations |

---

## What Each One Actually Does

### blunderbus-claude — what's live today

```
┌─ 07:00 ─────────────────────────────────────────────┐
│  monarch_ingest.py  →  21 accounts + Tx to ClickHouse│
└─────────────────────────────────────────────────────┘
┌─ 07:30 ─────────────────────────────────────────────┐
│  daily_brief.py                                     │
│   ├─ finance agent   (NW, anomalies, FIRE)          │
│   ├─ infra agent     (8 hosts, 19 containers)       │
│   ├─ workspace agent (gmail, cal, tasks via gws)    │
│   ├─ cross_validate  (savings rate, spending pace)  │
│   ├─ AI synthesis    (claude CLI)                   │
│   ├─ Postgres concerns + decisions journal          │
│   └─ → Obsidian note + Telegram + ops dashboard     │
└─────────────────────────────────────────────────────┘
```

### blunderbus-v3 / "V4" — what was designed

```
                     Discord channels (#infra, #home, #life, etc.)
                                     │
                                     ▼
                ┌───────────────────────────────────────┐
                │ JARVIS orchestrator (LangGraph)       │
                │ classify channel → delegate           │
                └───────────────────────────────────────┘
                  │      │      │      │      │
                  ▼      ▼      ▼      ▼      ▼
              Infra  Home  Health  Life  Project
              (5 specialist agents with their own skills)
                            │
                            ▼
                MCP Gateway :8811 (15 servers, deferred load)
                  ├─ blunderbus core tools
                  ├─ think (reasoning gate)
                  ├─ safety (confirm=True enforcement)
                  ├─ artifact storage (MinIO)
                  ├─ tool_search
                  ├─ batch_tools
                  └─ ...
                            │
                            ▼
                  LiteLLM proxy → Anthropic / OpenAI / (Perplexity)
                  Langfuse observability
```

What's *actually* live in v3 right now:
- ✅ Discord bot stays connected and resumes sessions
- ✅ Home Assistant entity cache refresh every 5 min
- ✅ Postgres + Redis + ClickHouse + MinIO + Langfuse + LiteLLM all healthy
- ❌ No agent invocations in 2 months of logs
- ❌ Evals directory exists but `/results/` is empty — never ran
- ❌ LiteLLM `config.yaml` only routes to OpenAI; **no qwen3:14b / Ollama target wired**

---

## "I had it working with local models" — let's check that memory

LiteLLM config on Cortex right now:

```yaml
model_list:
  - model_name: gpt-3.5-turbo, gpt-4
    litellm_params:
      model: openai/gpt-4.1-mini
      api_key: os.environ/OPENAI_API_KEY
  # ... (more OpenAI rows)
  # No ollama/, no qwen, no local target.
```

The CLAUDE.md doc *describes* local-first routing (`qwen3:14b on Thor → Perplexity fallback`), but the actual config never landed. Best guess: you scaffolded the docs and the routing concept, but the implementation stopped before the LiteLLM yaml was wired up. That's why you can't find where you stopped — there's no there.

---

## Decision Matrix

|  | A. Build Discord INTO claude | B. Migrate TO v3, port claude features IN | C. Bridge (claude daily + v3 Discord) |
|---|---|---|---|
| **Time to value** | 🟢 half day | 🔴 1-2 weeks | 🟡 1-2 days |
| **Risk of regression** | 🟢 low — additive to working system | 🔴 high — replacing what works | 🟡 medium — two systems to sync |
| **Maintenance surface** | 🟢 1 codebase | 🟡 1 codebase, much bigger | 🔴 2 codebases forever |
| **Capability ceiling** | 🟡 capped at single-agent-per-channel | 🟢 full multi-agent LangGraph | 🟢 high but split |
| **AI cost ($/mo)** | 🟢 ~$0 (claude CLI) | 🔴 metered API | 🟡 mixed |
| **Local model fit** | 🟡 needs LiteLLM bolt-on | 🟢 LiteLLM already in stack (just needs config) | 🟢 use Cortex stack |
| **Memory contract** | 🟢 already there | 🔴 needs port | 🟢 stays in claude |
| **Approval/safety gates** | 🟡 needs build (~1 day) | 🟢 already in v3 (approval.py) | 🟢 v3 owns it |
| **Confidence level** | 🟢 high | 🔴 unknown — never validated end-to-end | 🟡 medium |

**Weighted score** (your goals: working signal, low ops cost, room to grow):

| Path | Weight × Fit |
|---|---|
| **A. Build Discord into claude** | **★★★★☆** |
| **B. Migrate to v3** | **★★☆☆☆** |
| **C. Bridge both** | **★★★☆☆** |

---

## Recommendation: Path A

**Port Discord IN to `blunderbus-claude`. Steal the useful patterns from v3 without taking the stack.**

What to copy from v3:
1. **`bot.py` channel-routing pattern** — `CHANNEL_ROUTING` dict mapping channel name → agent + model. ~150 lines.
2. **`approval.py` confirmation flow** — "are you sure?" before destructive actions. ~80 lines.
3. **`validation.py` sanitize_discord_input** — anti-injection. ~30 lines.

What to leave on Cortex:
- LangGraph multi-agent orchestration (overkill for the question-feedback use case)
- MCP Gateway with 15 servers (we don't have tools that need it yet)
- LiteLLM (we use claude CLI, not metered API)
- Langfuse (we don't have enough volume to need observability)

What this gets you in half a day:
- `#brief` channel posts the morning brief
- `#questions` channel posts agent questions (`[brian-ira-nfs] Is the IRA closed?...`)
- You reply in-thread → bot writes the answer back into `memory/registry/...`
- `#infra-alerts` and `#finance-flags` get live concern push as they fire
- Slash commands: `/resolve <concern-id>`, `/snooze <target> <duration>`

---

## What to do with the running v3 stack on Cortex

Three options for the existing `jarvis-bot` container + its supporting stack:

| Option | What happens | When |
|---|---|---|
| **Leave it running** | 10 containers, ~3 GB RAM, no functional value. Discord gateway holds a session. | Now — no action |
| **Shut it down, archive code** | `docker compose down`, tar `/opt/blunderbus-v3/`, push to TrueNAS backup. Recover Cortex resources. | When path A starts working — week or two from now |
| **Mine it for parts and delete** | Copy bot.py, approval.py, validation.py into blunderbus-claude. Then nuke. | After path A is shipped + you confirm you don't miss it |

My read: option 2 — leave it for now (it's cheap), shut down once Discord lands in claude. Don't delete the source until you've stolen the useful patterns.

---

## If you go Path A — concrete first step

```bash
# On ProfX, in the blunderbus-claude repo:
mkdir -p scripts/discord
# Pull just the patterns we want from v3:
scp cortex:/opt/blunderbus-v3/blunderbus/bot.py        scripts/discord/_v3_reference_bot.py
scp cortex:/opt/blunderbus-v3/blunderbus/approval.py   scripts/discord/_v3_reference_approval.py
scp cortex:/opt/blunderbus-v3/blunderbus/validation.py scripts/discord/_v3_reference_validation.py
# These are reference; rewrite into our memory-contract idiom, not copy-paste.
```

Then you'd build:
- `scripts/discord_bot.py` — daemon, channel routing
- `scripts/blunderbus_memory/questions.py` — new `agent_questions` table in Postgres
- Agent code change: when an agent files a Question, also enqueue to `agent_questions` for Discord pickup
- `blunderbus-discord.service` systemd unit on ProfX (mirrors blunderbus-telegram.service)
