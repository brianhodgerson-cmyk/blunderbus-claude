# Path C — Question Threads (Discord ↔ Registry write-back)

> Design spec, drafted 2026-05-13. Replaces the manual "you tell me the answer, I edit the registry" loop with a structured per-thread workflow.

---

## Goal

**Every open question the brief surfaces becomes a Discord thread. Reply in the thread → registry updated → next brief stops asking.**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   Agent finds: owner=UNKNOWN on nfcu-everyday-checking                  │
│        │                                                                │
│        ▼                                                                │
│   Question persisted to Postgres agent_questions                        │
│        │                                                                │
│        ▼                                                                │
│   Bot polls → creates thread in #agent-questions:                       │
│      "Who owns EveryDay Checking (...0958)?"                            │
│        │                                                                │
│        ▼                                                                │
│   Brian replies in thread: "I am"                                       │
│        │                                                                │
│        ▼                                                                │
│   Bot AI-parses: { entity: nfcu-everyday-checking, field: owner,       │
│                    value: brian-hodgerson }                             │
│        │                                                                │
│        ▼                                                                │
│   Bot proposes:                                                         │
│      "Setting owner=brian-hodgerson. React 👍 to confirm, ❌ to cancel" │
│        │                                                                │
│   ┌────┴────┐                                                           │
│   │ 👍      │ → write to memory/registry/accounts/nfcu-everyday-...    │
│   │         │ → log decisions/2026-05-13.md                             │
│   │         │ → mark question status=applied                            │
│   │         │ → reply: "✅ Recorded — brief will stop asking tomorrow"  │
│   │ ❌      │ → mark status=abandoned, reply: "Got it — left as-is"    │
│   └─────────┘                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Postgres table

```sql
CREATE TABLE agent_questions (
    id              TEXT PRIMARY KEY,            -- e.g. "finance:owner:nfcu-everyday-checking"
    tenant_id       TEXT NOT NULL DEFAULT 'blunderbus',
    agent           TEXT NOT NULL,               -- "finance", "infra", "workspace"
    question_type   TEXT NOT NULL,               -- "owner-confirm", "status-clarify", "project-blocker"
    target_kind     TEXT NOT NULL,               -- "account", "person", "project", "inventory"
    target_id       TEXT NOT NULL,               -- "nfcu-everyday-checking"
    target_field    TEXT,                        -- "owner" (the field we want filled)
    prompt          TEXT NOT NULL,               -- "Who owns EveryDay Checking (...0958)?"
    suggested_format TEXT,                       -- "registry person id, e.g. brian-hodgerson"
    status          TEXT NOT NULL DEFAULT 'open',-- open|posted|proposed|applied|abandoned|stale
    discord_thread_id BIGINT,                    -- set when bot creates the thread
    proposed_value  TEXT,                        -- set when AI parses an answer
    applied_value   TEXT,                        -- final value written
    answered_by     TEXT,                        -- Discord user id
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at     TIMESTAMPTZ,
    applied_at      TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX idx_questions_status ON agent_questions (status) WHERE status IN ('open','posted','proposed');
CREATE INDEX idx_questions_thread ON agent_questions (discord_thread_id) WHERE discord_thread_id IS NOT NULL;
```

### Python model (`blunderbus_memory/questions.py`)

```python
class Question(BaseModel):
    id: str
    agent: str
    question_type: Literal["owner-confirm", "status-clarify", "project-blocker", ...]
    target_kind: Literal["account", "person", "project", "inventory"]
    target_id: str
    target_field: Optional[str]
    prompt: str
    suggested_format: Optional[str]
    status: QuestionStatus
    discord_thread_id: Optional[int]
    # ... timestamps
```

---

## State Machine

```
┌──────┐  agent emits   ┌─────────┐  bot creates  ┌──────────┐
│      │ ───────────────│  open   │───────────────│  posted  │
│ (n/a)│                └────┬────┘  thread       └─────┬────┘
└──────┘                     │                          │
                             │ agent no longer          │ Brian replies in thread
                             │ emits it (e.g. field     │
                             │ already filled by hand)  ▼
                             ▼                    ┌─────────────┐
                          ┌───────┐ bot AI-parses │  proposed   │
                          │ stale │               └──────┬──────┘
                          └───────┘                      │
                                                         │ 👍 reaction
                                                         ▼
                                                   ┌──────────┐
                                                   │ applied  │ ← registry written
                                                   └──────────┘
                                                         │
                                                         │ ❌ reaction
                                                         ▼
                                                   ┌──────────┐
                                                   │ abandoned│
                                                   └──────────┘
```

---

## Discord UX — what Brian sees

### When a question gets posted to `#agent-questions`

A new thread named `owner of EveryDay Checking (...0958)`:

```
🤖 jarvis · just now
> **❓ Finance agent question**
>
> **Who owns this account?**
> EveryDay Checking · NFCU · (...0958)
>
> Reply in this thread with the owner's name. Examples:
>   • "me" / "i'm the owner"
>   • "joint with jamie"
>   • "evangeline" (child custodial)
>
> Question id: `finance:owner:nfcu-everyday-checking`
> [📋 View on dashboard](https://ops.hodgespot.com/registry/accounts/nfcu-everyday-checking)
```

### When Brian replies "I am"

```
🤖 jarvis · just now (in reply to "I am")
> I'll set the owner to **brian-hodgerson** on `nfcu-everyday-checking`.
>
> React 👍 to confirm, ❌ to cancel.
>
> _Reasoning_: registry person id `brian-hodgerson` matches Brian Hodgerson (you).
```

### When Brian reacts 👍

```
🤖 jarvis · just now
> ✅ Recorded — `owner=brian-hodgerson` on `nfcu-everyday-checking`
>
> Tomorrow's brief will not re-ask this. Logged to `decisions/2026-05-13.md`.
>
> _Thread will auto-archive in 24 hrs._
```

---

## Components to build

| File | What it does | Effort |
|---|---|---|
| `scripts/blunderbus_memory/sql/002_questions.sql` | Postgres migration | 5 min |
| `scripts/blunderbus_memory/questions.py` | Pydantic model + `PostgresQuestions` CRUD class | 1 hr |
| `scripts/agents/questions_sync.py` | Sibling of `concerns_sync.py` — agents push their questions, syncer upserts | 30 min |
| `scripts/agents/finance.py` (edit) | Emit `Question` objects for owner/status gaps (currently builds prompt strings) | 30 min |
| `scripts/agents/workspace.py` (edit) | Same — emit `Question` for project blockers | 30 min |
| `scripts/discord_questions.py` | Background loop in bot: poll `agent_questions WHERE status='open'`, create thread, update DB | 1 hr |
| `scripts/discord_bot.py` (extend on_message) | Detect thread replies → AI-parse answer → propose | 1.5 hr |
| `scripts/discord_bot.py` (on_reaction_add) | Detect 👍/❌ → apply or abandon | 30 min |
| `scripts/blunderbus_memory/registry_writer.py` | Type-specific writers: `set_account_owner`, `resolve_project_blocker`, etc. | 1 hr |
| `scripts/blunderbus_memory/journal.py` (reuse) | Already exists — log applied decisions | 0 (reuse) |

**Total estimate: ~6 hrs of focused work.** Probably one good evening session.

---

## Phased Build Plan

### Phase C-1: Read-side (posts questions, no write-back yet) — 2 hrs
- Migration + `Question` model + `PostgresQuestions`
- `questions_sync.sync()` helper (mirrors `concerns_sync.sync()`)
- Wire finance agent's `_carry_questions_from_memory()` to also emit `Question` objects
- Bot background loop that picks up `status=open` questions, creates threads, marks `posted`
- **Brian can see the threads in Discord. Replying does nothing yet.**

### Phase C-2: Write-side (parse, propose, apply) — 3 hrs
- Extend `on_message` — if the channel parent is a question thread, treat it as an answer
- AI parsing pass (claude CLI): given the question spec + the reply, extract `{field, value}` JSON
- `registry_writer.set_account_owner(target_id, value)` writes the YAML, preserves frontmatter
- Bot posts "react 👍 to confirm" message, stores `proposed_value`
- `on_reaction_add` → apply or abandon

### Phase C-3: Polish — 1 hr
- Suggested values in the question post (drop-down style: known person ids)
- Slash command `/question-status <id>` to manually move a question along
- `/question-snooze <id> <days>` for "ask me later"
- Auto-archive threads after `applied` or `abandoned`

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **AI parses answer wrong** (e.g. "I am" → wrong person) | Always require 👍 confirmation before applying. Show the proposed value back. |
| **Brian replies in wrong thread** | Each thread is bound to one question id via `discord_thread_id`. Wrong-thread replies just propose wrong values, easy to ❌. |
| **Stale questions linger** (e.g. user already fixed via dashboard) | Daily sweep: agent re-evaluates, if the underlying field is now filled, mark `status=stale`. Bot deletes/archives the thread. |
| **Bot down during peak triage** | Questions remain `status=open` in Postgres. Bot catches up on next start. |
| **Multiple bots, dupe threads** | DB constraint: only one thread per `(tenant_id, id)`. Bot checks before creating. |
| **AI write-back fails silently** | All writes go through `registry_writer`, which raises on any failure. Bot replies "❌ couldn't write — check journal" instead of silent ack. |
| **Brian wants to undo** | Add `/question-undo <id>` that pulls the YAML edit from git history. Decision-journal entry already records what changed. |

---

## What this DOESN'T do (out of scope for Path C)

- ❌ Free-form chat → write-back. The bot still won't infer writes from random conversation; only structured question threads.
- ❌ Multi-step plans ("first set the owner, then close the question, then update the project"). Each question is a single-field write.
- ❌ Tool calls in general. This is a single-purpose write surface, not a Claude Agent SDK port.

If you want any of those, that's Path B (full agent with tools).

---

## Ready-to-execute first task (Phase C-1, step 1)

```bash
cat > scripts/blunderbus_memory/sql/002_questions.sql <<'EOF'
CREATE TYPE question_status AS ENUM ('open','posted','proposed','applied','abandoned','stale');

CREATE TABLE IF NOT EXISTS agent_questions (
    id                TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'blunderbus',
    agent             TEXT NOT NULL,
    question_type     TEXT NOT NULL,
    target_kind       TEXT NOT NULL,
    target_id         TEXT NOT NULL,
    target_field      TEXT,
    prompt            TEXT NOT NULL,
    suggested_format  TEXT,
    status            question_status NOT NULL DEFAULT 'open',
    discord_thread_id BIGINT,
    proposed_value    TEXT,
    applied_value     TEXT,
    answered_by       TEXT,
    payload           JSONB NOT NULL DEFAULT '{}',
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at       TIMESTAMPTZ,
    applied_at        TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_questions_status
    ON agent_questions (status)
    WHERE status IN ('open','posted','proposed');

CREATE INDEX IF NOT EXISTS idx_questions_thread
    ON agent_questions (discord_thread_id)
    WHERE discord_thread_id IS NOT NULL;
EOF

ssh cortex "docker exec -i jarvis-postgres psql -U jarvis -d blunderbus_memory" < scripts/blunderbus_memory/sql/002_questions.sql
```
