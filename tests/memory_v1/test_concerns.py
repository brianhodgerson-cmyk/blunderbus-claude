"""PostgresConcerns: lifecycle, reconcile, idempotent upsert.

Skipped when Postgres is unreachable (CI runs without secrets).
"""
from __future__ import annotations

import pytest

from blunderbus_memory import Concern, ConcernStatus, Severity


def _mk(id_: str, status=ConcernStatus.ACTIVE, **kwargs) -> Concern:
    return Concern(
        id=id_,
        agent=kwargs.get("agent", "infra"),
        type=kwargs.get("type", "host-down"),
        target=kwargs.get("target", id_.split(":")[-1]),
        severity=kwargs.get("severity", Severity.MEDIUM),
        status=status,
        summary=kwargs.get("summary", f"summary for {id_}"),
        verifier=kwargs.get("verifier", f"probe:{id_}"),
        payload=kwargs.get("payload", {}),
    )


class TestLifecycle:
    def test_upsert_then_get(self, pg_concerns):
        c = _mk("infra:host-down:test1")
        result = pg_concerns.upsert(c)
        assert result.id == "infra:host-down:test1"
        assert result.status == ConcernStatus.ACTIVE
        assert result.tenant_id == pg_concerns.tenant_id

        loaded = pg_concerns.get("infra:host-down:test1")
        assert loaded is not None
        assert loaded.summary == "summary for infra:host-down:test1"

    def test_upsert_is_idempotent(self, pg_concerns):
        c = _mk("infra:host-down:test1")
        first = pg_concerns.upsert(c)
        second = pg_concerns.upsert(c)
        # last_verified bumps but the row is same
        assert second.id == first.id
        assert second.first_seen == first.first_seen
        assert second.last_verified >= first.last_verified

    def test_list_active_filters(self, pg_concerns):
        pg_concerns.upsert(_mk("infra:host-down:a", agent="infra"))
        pg_concerns.upsert(_mk("infra:disk-high:b", agent="infra"))
        pg_concerns.upsert(_mk("workspace:inbox:c", agent="workspace"))

        infra_active = pg_concerns.list_active(agent="infra")
        assert {c.id for c in infra_active} == {
            "infra:host-down:a", "infra:disk-high:b"
        }
        all_active = pg_concerns.list_active()
        assert len(all_active) == 3


class TestReconcile:
    def test_auto_resolves_missing(self, pg_concerns):
        pg_concerns.upsert(_mk("infra:host-down:a"))
        pg_concerns.upsert(_mk("infra:host-down:b"))
        pg_concerns.upsert(_mk("infra:host-down:c"))

        # Only `a` and `b` are still firing
        resolved_count = pg_concerns.reconcile(
            "infra", ["infra:host-down:a", "infra:host-down:b"]
        )
        assert resolved_count == 1

        # `c` should be marked resolved
        c = pg_concerns.get("infra:host-down:c")
        assert c.status == ConcernStatus.RESOLVED
        assert c.resolved_at is not None

        # `a` and `b` still active
        a = pg_concerns.get("infra:host-down:a")
        assert a.status == ConcernStatus.ACTIVE

    def test_reconcile_empty_list_resolves_all(self, pg_concerns):
        pg_concerns.upsert(_mk("infra:host-down:a"))
        pg_concerns.upsert(_mk("infra:host-down:b"))
        resolved = pg_concerns.reconcile("infra", [])
        assert resolved == 2
        assert pg_concerns.list_active("infra") == []

    def test_reconcile_does_not_touch_other_agents(self, pg_concerns):
        pg_concerns.upsert(_mk("infra:host-down:a", agent="infra"))
        pg_concerns.upsert(_mk("workspace:inbox:b", agent="workspace"))
        pg_concerns.reconcile("infra", [])
        # workspace one is still active
        assert pg_concerns.get("workspace:inbox:b").status == ConcernStatus.ACTIVE


class TestReopen:
    def test_resolved_can_reopen_via_upsert(self, pg_concerns):
        c = _mk("infra:host-down:flaky")
        pg_concerns.upsert(c)
        pg_concerns.reconcile("infra", [])  # resolves it
        assert pg_concerns.get(c.id).status == ConcernStatus.RESOLVED

        # Re-upsert active → should reopen
        pg_concerns.upsert(_mk(c.id))
        reopened = pg_concerns.get(c.id)
        assert reopened.status == ConcernStatus.ACTIVE
        assert reopened.resolved_at is None


class TestExplicitResolve:
    def test_resolve_returns_true_when_changed(self, pg_concerns):
        pg_concerns.upsert(_mk("infra:host-down:a"))
        assert pg_concerns.resolve("infra:host-down:a") is True
        assert pg_concerns.resolve("infra:host-down:a") is False  # already resolved
        assert pg_concerns.resolve("infra:host-down:nonexistent") is False
