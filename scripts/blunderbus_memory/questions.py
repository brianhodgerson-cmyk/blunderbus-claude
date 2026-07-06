"""
PostgresQuestions — mutable question store for the Path C Discord workflow.

Sibling of `PostgresConcerns`. Same DSN resolution, same connection pattern,
different state machine.

Lifecycle:
    open → posted → proposed → applied | abandoned | stale

Agents emit `Question` objects via `questions_sync.sync(agent, questions)` —
exact same pattern as concerns. The Discord bot's background loop polls
`list_open()`, creates threads, and calls `mark_posted(id, thread_id)`.

When a thread reply lands, the bot calls `mark_proposed(id, value, msg_id)`.
On 👍 reaction, the bot writes the registry (separate concern — see
`registry_writer`) and calls `mark_applied(id, value)`. On ❌, `mark_abandoned(id)`.

When the agent re-runs and the field is now filled (e.g. via dashboard, not
via the question thread), the agent omits the question from its emit list and
the next sync pass will `mark_stale()` it.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import psycopg
from psycopg.rows import dict_row

from .models import Question, QuestionStatus, QuestionTargetKind


PKG_DIR = Path(__file__).resolve().parent


def _resolve_dsn() -> str:
    """Same DSN resolution as PostgresConcerns. Lifted out so both classes share."""
    if dsn := os.environ.get("BLUNDERBUS_DB_URL"):
        return dsn
    pw = os.environ.get("BLUNDERBUS_DB_PASSWORD")
    if not pw:
        try:
            import sys as _sys
            _sys.path.insert(0, str(PKG_DIR.parent))
            from vault import load_secrets  # type: ignore
            load_secrets()
            pw = os.environ.get("BLUNDERBUS_DB_PASSWORD")
        except Exception as e:
            raise RuntimeError(
                f"No BLUNDERBUS_DB_URL/BLUNDERBUS_DB_PASSWORD; vault hydration failed: {e}"
            )
    if not pw:
        raise RuntimeError(
            "No DB password resolved. Set BLUNDERBUS_DB_URL or ensure "
            "'jarvis-postgres' vault item is reachable."
        )
    host = os.environ.get("BLUNDERBUS_DB_HOST", "192.168.50.106")
    port = os.environ.get("BLUNDERBUS_DB_PORT", "5432")
    user = os.environ.get("BLUNDERBUS_DB_USER", "jarvis")
    db   = os.environ.get("BLUNDERBUS_DB_NAME", "blunderbus_memory")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


class PostgresQuestions:
    """Questions backed by Postgres. Same connection-reuse pattern as PostgresConcerns."""

    def __init__(self, dsn: Optional[str] = None, tenant_id: str = "blunderbus"):
        self.dsn = dsn or _resolve_dsn()
        self.tenant_id = tenant_id
        self._conn: Optional[psycopg.Connection] = None

    def connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        return self._conn

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

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

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_question(r: dict) -> Question:
        return Question(
            id=r["id"],
            tenant_id=r["tenant_id"],
            agent=r["agent"],
            question_type=r["question_type"],
            target_kind=QuestionTargetKind(r["target_kind"]),
            target_id=r["target_id"],
            target_field=r["target_field"],
            prompt=r["prompt"],
            suggested_format=r["suggested_format"],
            status=QuestionStatus(r["status"]),
            discord_thread_id=r["discord_thread_id"],
            discord_propose_message_id=r["discord_propose_message_id"],
            proposed_value=r["proposed_value"],
            applied_value=r["applied_value"],
            answered_by=r["answered_by"],
            payload=r["payload"] or {},
            first_seen=r["first_seen"],
            last_seen=r["last_seen"],
            answered_at=r["answered_at"],
            applied_at=r["applied_at"],
        )

    # ── Core operations ────────────────────────────────────────────────────

    def upsert(self, q: Question) -> Question:
        """Insert or refresh `last_seen` on a question. Idempotent on `(tenant_id, id)`.

        Does NOT clobber operator-driven state changes — if a row is already
        `posted`, `proposed`, `applied`, or `abandoned`, we just bump
        `last_seen`. Only `open` and `stale` rows get their content rewritten,
        so an agent re-emitting an open question keeps it fresh without losing
        bot-side state.
        """
        with self._cur() as cur:
            cur.execute("""
                INSERT INTO agent_questions
                    (id, tenant_id, agent, question_type, target_kind, target_id,
                     target_field, prompt, suggested_format, status, payload,
                     first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (tenant_id, id) DO UPDATE SET
                    last_seen = now(),
                    -- only rewrite content fields when the row is still pre-thread
                    agent             = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.agent             ELSE agent_questions.agent             END,
                    question_type     = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.question_type     ELSE agent_questions.question_type     END,
                    target_kind       = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.target_kind       ELSE agent_questions.target_kind       END,
                    target_id         = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.target_id         ELSE agent_questions.target_id         END,
                    target_field      = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.target_field      ELSE agent_questions.target_field      END,
                    prompt            = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.prompt            ELSE agent_questions.prompt            END,
                    suggested_format  = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.suggested_format  ELSE agent_questions.suggested_format  END,
                    payload           = CASE WHEN agent_questions.status IN ('open','stale') THEN EXCLUDED.payload           ELSE agent_questions.payload           END,
                    -- a stale question rediscovered → flip back to open
                    status = CASE WHEN agent_questions.status = 'stale' THEN 'open'::question_status ELSE agent_questions.status END
                RETURNING *
            """, (
                q.id, self.tenant_id, q.agent, q.question_type,
                q.target_kind.value, q.target_id, q.target_field,
                q.prompt, q.suggested_format, q.status.value,
                psycopg.types.json.Jsonb(q.payload),
            ))
            row = cur.fetchone()
            return self._row_to_question(row)

    def get(self, qid: str) -> Optional[Question]:
        with self._cur() as cur:
            cur.execute(
                "SELECT * FROM agent_questions WHERE tenant_id=%s AND id=%s",
                (self.tenant_id, qid),
            )
            r = cur.fetchone()
            return self._row_to_question(r) if r else None

    def get_by_thread(self, thread_id: int) -> Optional[Question]:
        with self._cur() as cur:
            cur.execute(
                "SELECT * FROM agent_questions WHERE tenant_id=%s AND discord_thread_id=%s",
                (self.tenant_id, thread_id),
            )
            r = cur.fetchone()
            return self._row_to_question(r) if r else None

    def list_by_status(self, statuses: Iterable[QuestionStatus]) -> list[Question]:
        vals = [s.value for s in statuses]
        with self._cur() as cur:
            cur.execute(
                "SELECT * FROM agent_questions WHERE tenant_id=%s AND status = ANY(%s) ORDER BY first_seen",
                (self.tenant_id, vals),
            )
            return [self._row_to_question(r) for r in cur.fetchall()]

    def list_open(self) -> list[Question]:
        """Questions waiting for the bot to create a thread."""
        return self.list_by_status([QuestionStatus.OPEN])

    def list_pending(self) -> list[Question]:
        """All not-yet-resolved questions (open + posted + proposed). For monitoring."""
        return self.list_by_status([QuestionStatus.OPEN, QuestionStatus.POSTED, QuestionStatus.PROPOSED])

    # ── State transitions ─────────────────────────────────────────────────

    def mark_posted(self, qid: str, thread_id: int) -> None:
        """open → posted. Bot has created the Discord thread."""
        with self._cur() as cur:
            cur.execute("""
                UPDATE agent_questions
                   SET status='posted', discord_thread_id=%s, last_seen=now()
                 WHERE tenant_id=%s AND id=%s AND status='open'
            """, (thread_id, self.tenant_id, qid))

    def mark_proposed(self, qid: str, value: str, propose_msg_id: int, answered_by: str) -> None:
        """posted → proposed. AI parsed a value from the operator's reply."""
        with self._cur() as cur:
            cur.execute("""
                UPDATE agent_questions
                   SET status='proposed',
                       proposed_value=%s,
                       discord_propose_message_id=%s,
                       answered_by=%s,
                       answered_at=now(),
                       last_seen=now()
                 WHERE tenant_id=%s AND id=%s AND status IN ('posted','proposed')
            """, (value, propose_msg_id, answered_by, self.tenant_id, qid))

    def mark_applied(self, qid: str, applied_value: str) -> None:
        """proposed → applied. Operator reacted 👍; registry was written."""
        with self._cur() as cur:
            cur.execute("""
                UPDATE agent_questions
                   SET status='applied', applied_value=%s, applied_at=now(), last_seen=now()
                 WHERE tenant_id=%s AND id=%s AND status='proposed'
            """, (applied_value, self.tenant_id, qid))

    def mark_abandoned(self, qid: str) -> None:
        """proposed → abandoned. Operator reacted ❌."""
        with self._cur() as cur:
            cur.execute("""
                UPDATE agent_questions
                   SET status='abandoned', last_seen=now()
                 WHERE tenant_id=%s AND id=%s AND status='proposed'
            """, (self.tenant_id, qid))

    def mark_stale(self, qid: str) -> None:
        """X → stale. Agent no longer emits this question (field filled out-of-band)."""
        with self._cur() as cur:
            cur.execute("""
                UPDATE agent_questions
                   SET status='stale', last_seen=now()
                 WHERE tenant_id=%s AND id=%s AND status IN ('open','posted')
            """, (self.tenant_id, qid))

    def reconcile(self, agent: str, active_ids: Iterable[str]) -> list[tuple[str, str]]:
        """Stale-mark any 'open' or 'posted' question for `agent` whose id is NOT
        in `active_ids`. Mirrors the concerns reconciler. Returns (id, prompt)
        of what got marked stale so the caller can log/journal.
        """
        ids = list(active_ids)
        with self._cur() as cur:
            if ids:
                cur.execute("""
                    UPDATE agent_questions
                       SET status='stale', last_seen=now()
                     WHERE tenant_id=%s AND agent=%s
                       AND status IN ('open','posted')
                       AND id <> ALL(%s)
                  RETURNING id, prompt
                """, (self.tenant_id, agent, ids))
            else:
                cur.execute("""
                    UPDATE agent_questions
                       SET status='stale', last_seen=now()
                     WHERE tenant_id=%s AND agent=%s
                       AND status IN ('open','posted')
                  RETURNING id, prompt
                """, (self.tenant_id, agent))
            return [(r["id"], r["prompt"]) for r in cur.fetchall()]
