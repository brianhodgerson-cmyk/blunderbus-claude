-- BlunderBus memory v1 — initial schema.
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS schema_version (
    version int PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'concern_status') THEN
        CREATE TYPE concern_status AS ENUM ('active', 'resolved', 'stale');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'severity') THEN
        CREATE TYPE severity AS ENUM ('critical', 'high', 'medium', 'low', 'info');
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS agent_concerns (
    id              text         NOT NULL,
    tenant_id       text         NOT NULL DEFAULT 'blunderbus',
    agent           text         NOT NULL,
    type            text         NOT NULL,
    target          text,
    severity        severity     NOT NULL DEFAULT 'medium',
    status          concern_status NOT NULL DEFAULT 'active',
    summary         text         NOT NULL,
    suggested_action text,
    verifier        text,
    first_seen      timestamptz  NOT NULL DEFAULT now(),
    last_verified   timestamptz  NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    payload         jsonb        NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS idx_concerns_tenant_status ON agent_concerns(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_concerns_agent_status ON agent_concerns(agent, status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_concerns_target ON agent_concerns(target) WHERE target IS NOT NULL;

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
