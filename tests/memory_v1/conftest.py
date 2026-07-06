"""Common fixtures for blunderbus_memory tests."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Make scripts/ importable
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from blunderbus_memory import MarkdownRegistry  # noqa: E402


@pytest.fixture
def tmp_registry(tmp_path: Path) -> MarkdownRegistry:
    """Fresh markdown registry rooted in a tempdir."""
    return MarkdownRegistry(tmp_path / "registry")


@pytest.fixture
def pg_concerns():
    """Yield a PostgresConcerns scoped to a unique tenant_id so tests can run
    against the live db without colliding. Skips if DB unreachable."""
    try:
        # hydrate vault if we have BW_MASTER_PASS, otherwise fall through to env
        if os.environ.get("BW_MASTER_PASS") and not os.environ.get("BLUNDERBUS_DB_PASSWORD"):
            from vault import load_secrets  # type: ignore
            load_secrets()
        from blunderbus_memory.concerns import PostgresConcerns
        store = PostgresConcerns(tenant_id=f"test-{uuid.uuid4().hex[:8]}")
        store.connect()
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")

    yield store

    # Cleanup all rows for this tenant
    try:
        with store._cur() as cur:
            cur.execute("DELETE FROM agent_concerns WHERE tenant_id = %s",
                        (store.tenant_id,))
    finally:
        store.close()
