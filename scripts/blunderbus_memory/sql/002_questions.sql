-- 002_questions.sql — agent_questions table for Path C (Discord question threads)
--
-- Each row is one open question an agent wants the operator to resolve. The
-- lifecycle is:
--
--   open      → agent emitted it, bot hasn't posted a thread yet
--   posted    → bot created a Discord thread, awaiting operator reply
--   proposed  → operator replied, bot AI-parsed a value, awaiting 👍 reaction
--   applied   → operator confirmed; registry was written, journal entry logged
--   abandoned → operator declined (❌ reaction); no registry change
--   stale     → underlying field has been filled by something else (out of band);
--               daily sweep noticed and closed the question silently
--
-- This mirrors the agent_concerns lifecycle (active→resolved) but with the
-- extra states needed for the human-in-the-loop write-back flow.

CREATE TYPE question_status AS ENUM (
    'open', 'posted', 'proposed', 'applied', 'abandoned', 'stale'
);

CREATE TABLE IF NOT EXISTS agent_questions (
    id                TEXT NOT NULL,                       -- e.g. "finance:owner:nfcu-share-savings"
    tenant_id         TEXT NOT NULL DEFAULT 'blunderbus',
    agent             TEXT NOT NULL,                       -- "finance", "infra", "workspace"
    question_type     TEXT NOT NULL,                       -- "owner-confirm", "project-blocker", ...
    target_kind       TEXT NOT NULL,                       -- "account", "person", "project", "inventory"
    target_id         TEXT NOT NULL,                       -- e.g. "nfcu-share-savings"
    target_field      TEXT,                                -- e.g. "owner" (which YAML field to fill)
    prompt            TEXT NOT NULL,                       -- the human-readable question
    suggested_format  TEXT,                                -- guidance for the answer shape
    status            question_status NOT NULL DEFAULT 'open',
    discord_thread_id BIGINT,                              -- set when bot creates the thread
    discord_propose_message_id BIGINT,                     -- set when bot posts the "react 👍" proposal
    proposed_value    TEXT,                                -- value AI parsed from operator's reply
    applied_value     TEXT,                                -- final value written to registry
    answered_by       TEXT,                                -- Discord user id of replier
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,  -- type-specific extras
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at       TIMESTAMPTZ,
    applied_at        TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, id)
);

-- Open + posted + proposed are the "hot" states the bot polls for.
CREATE INDEX IF NOT EXISTS idx_questions_status
    ON agent_questions (tenant_id, status)
    WHERE status IN ('open', 'posted', 'proposed');

-- Lookup by Discord thread (when a reply comes in, find the question).
CREATE INDEX IF NOT EXISTS idx_questions_thread
    ON agent_questions (discord_thread_id)
    WHERE discord_thread_id IS NOT NULL;

-- Target lookup for "is this entity covered by any question?" checks.
CREATE INDEX IF NOT EXISTS idx_questions_target
    ON agent_questions (tenant_id, target_kind, target_id);
