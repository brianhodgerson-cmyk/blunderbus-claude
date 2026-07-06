"""
PostgresConcerns — mutable concern store with auto-resolution lifecycle.

This is the ONE place we use a database in v1, because concerns are the only
state that genuinely needs transactional updates and lifecycle tracking. Everything
else (registry of stable facts, journal of observations) stays in markdown.

Concerns flow:
    1. Agent probes (or Prometheus alert fires) → upsert(active)
    2. Probe stops returning → mark resolved (auto)
    3. Probe never returns over TTL → mark stale (auto)

A concern's `id` is stable per (agent, type, target) so re-firing updates the
same row. `verifier` records HOW we know the concern is active, so any future
run can re-check it without human curation.

Connection details come from the `jarvis-postgres` Vaultwarden item or the
`BLUNDERBUS_DB_URL` env var (handy for tests).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from .models import Concern, ConcernStatus, Severity


PKG_DIR = Path(__file__).resolve().parent
SQL_INIT = PKG_DIR / "sql" / "001_init.sql"


# ── Connection ───────────────────────────────────────────────────────────────


def _resolve_dsn() -> str:
    """Resolve the Postgres DSN from env (preferred) or vault.

    Order:
      1. BLUNDERBUS_DB_URL env var (full DSN)
      2. BLUNDERBUS_DB_PASSWORD env var (combined with default host/db)
      3. Vault item 'jarvis-postgres' via scripts/vault.py
    """
    if dsn := os.environ.get("BLUNDERBUS_DB_URL"):
        return dsn
    pw = os.environ.get("BLUNDERBUS_DB_PASSWORD")
    if not pw:
        # Lazy vault hydration; tolerate vault unavailability
        try:
            import sys as _sys
            _sys.path.insert(0, str(PKG_DIR.parent))
            from vault import load_secrets  # type: ignore
            load_secrets()
            pw = os.environ.get("BLUNDERBUS_DB_PASSWORD")
        except Exception as e:
            raise RuntimeError(
                "No BLUNDERBUS_DB_URL/BLUNDERBUS_DB_PASSWORD set and vault "
                f"hydration failed: {e}"
            )
    if not pw:
        raise RuntimeError(
            "No DB password resolved. Set BLUNDERBUS_DB_URL or ensure the "
            "'jarvis-postgres' vault item is reachable."
        )
    host = os.environ.get("BLUNDERBUS_DB_HOST", "192.168.50.106")
    port = os.environ.get("BLUNDERBUS_DB_PORT", "5432")
    user = os.environ.get("BLUNDERBUS_DB_USER", "jarvis")
    db   = os.environ.get("BLUNDERBUS_DB_NAME", "blunderbus_memory")
    return (
        "postgresql://"
        + quote(user, safe="")
        + ":"
        + quote(pw, safe="")
        + f"@{host}:{port}/{db}"
    )


# ── Concerns store ───────────────────────────────────────────────────────────


class PostgresConcerns:
    """Concerns backed by Postgres. One instance per process; reuses a connection.

    Methods are intentionally explicit — no ORM ceremony. SQL stays readable
    and the migration to a different backend (or a managed service) is trivial.
    """

    def __init__(self, dsn: Optional[str] = None, tenant_id: str = "blunderbus"):
        self.dsn = dsn or _resolve_dsn()
        self.tenant_id = tenant_id
        self._conn: Optional[psycopg.Connection] = None

    def connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, autocommit=True,
                                         connect_timeout=5)
        return self._conn

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    # Context manager for callers who want explicit lifetime
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _cur(self):
        conn = self.connect()
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur

    # ── Core operations ──

    def upsert(self, concern: Concern) -> Concern:
        """Insert or update a concern. Refreshes last_verified on every call.

        If the existing row is `resolved` and we're re-asserting `active`,
        we clear resolved_at and reopen.
        """
        now = datetime.now(timezone.utc)
        with self._cur() as cur:
            # The store's tenant_id is authoritative on writes; ignore whatever
            # the model carries. This keeps tenant-isolation airtight.
            cur.execute("""
                INSERT INTO agent_concerns
                    (id, tenant_id, agent, type, target, severity, status,
                     summary, suggested_action, verifier, first_seen,
                     last_verified, resolved_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, id) DO UPDATE SET
                    agent = EXCLUDED.agent,
                    type = EXCLUDED.type,
                    target = EXCLUDED.target,
                    severity = EXCLUDED.severity,
                    status = CASE
                        WHEN agent_concerns.status = 'resolved'
                             AND EXCLUDED.status = 'active' THEN 'active'::concern_status
                        ELSE EXCLUDED.status
                    END,
                    summary = EXCLUDED.summary,
                    suggested_action = EXCLUDED.suggested_action,
                    verifier = EXCLUDED.verifier,
                    last_verified = EXCLUDED.last_verified,
                    resolved_at = CASE
                        WHEN EXCLUDED.status = 'active' THEN NULL
                        ELSE EXCLUDED.resolved_at
                    END,
                    payload = agent_concerns.payload || EXCLUDED.payload
                RETURNING *
            """, (
                concern.id, self.tenant_id, concern.agent,
                concern.type, concern.target, concern.severity.value,
                concern.status.value, concern.summary, concern.suggested_action,
                concern.verifier, concern.first_seen or now, now,
                concern.resolved_at,
                psycopg.types.json.Jsonb(concern.payload or {}),
            ))
            row = cur.fetchone()
        return _row_to_concern(row)

    def resolve(self, concern_id: str, *, agent: Optional[str] = None) -> bool:
        """Mark a concern resolved. Returns True if a row was updated."""
        now = datetime.now(timezone.utc)
        sql = """
            UPDATE agent_concerns
               SET status = 'resolved', resolved_at = %s, last_verified = %s
             WHERE tenant_id = %s AND id = %s AND status = 'active'
        """
        params = [now, now, self.tenant_id, concern_id]
        if agent:
            sql += " AND agent = %s"
            params.append(agent)
        with self._cur() as cur:
            cur.execute(sql, params)
            return cur.rowcount > 0

    def reconcile(self, agent: str, active_ids: Iterable[str]) -> list[tuple[str, str]]:
        """Auto-resolve stale concerns: anything ACTIVE for `agent` whose id
        is not in `active_ids` gets marked resolved. Returns list of
        (id, summary) tuples for what got resolved.

        This is the load-bearing self-correction step — call it at the end of
        each agent run with the IDs the probe confirmed active right now.
        """
        ids = list(active_ids)
        with self._cur() as cur:
            if ids:
                cur.execute("""
                    UPDATE agent_concerns
                       SET status = 'resolved', resolved_at = now(),
                           last_verified = now()
                     WHERE tenant_id = %s AND agent = %s
                       AND status = 'active'
                       AND id <> ALL(%s)
                  RETURNING id, summary
                """, (self.tenant_id, agent, ids))
            else:
                cur.execute("""
                    UPDATE agent_concerns
                       SET status = 'resolved', resolved_at = now(),
                           last_verified = now()
                     WHERE tenant_id = %s AND agent = %s
                       AND status = 'active'
                  RETURNING id, summary
                """, (self.tenant_id, agent))
            return [(r["id"], r["summary"]) for r in cur.fetchall()]

    def list_active(self, agent: Optional[str] = None) -> list[Concern]:
        sql = "SELECT * FROM agent_concerns WHERE tenant_id = %s AND status = 'active'"
        params = [self.tenant_id]
        if agent:
            sql += " AND agent = %s"
            params.append(agent)
        sql += " ORDER BY severity, first_seen"
        with self._cur() as cur:
            cur.execute(sql, params)
            return [_row_to_concern(r) for r in cur.fetchall()]

    def get(self, concern_id: str) -> Optional[Concern]:
        with self._cur() as cur:
            cur.execute(
                "SELECT * FROM agent_concerns WHERE tenant_id = %s AND id = %s",
                (self.tenant_id, concern_id),
            )
            row = cur.fetchone()
        return _row_to_concern(row) if row else None

    def stats(self) -> dict[str, int]:
        with self._cur() as cur:
            cur.execute("""
                SELECT status::text AS status, count(*) AS n
                  FROM agent_concerns WHERE tenant_id = %s
                 GROUP BY status
            """, (self.tenant_id,))
            return {r["status"]: r["n"] for r in cur.fetchall()}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _row_to_concern(row: dict) -> Concern:
    return Concern(
        id=row["id"],
        tenant_id=row["tenant_id"],
        agent=row["agent"],
        type=row["type"],
        target=row.get("target"),
        severity=Severity(row["severity"]),
        status=ConcernStatus(row["status"]),
        summary=row["summary"],
        suggested_action=row.get("suggested_action"),
        verifier=row.get("verifier"),
        first_seen=row.get("first_seen"),
        last_verified=row.get("last_verified"),
        resolved_at=row.get("resolved_at"),
        payload=row.get("payload") or {},
    )
