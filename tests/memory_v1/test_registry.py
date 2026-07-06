"""Markdown registry: roundtrip, frontmatter parsing, list/filter, persistence."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from blunderbus_memory import (
    Account, HostStatus, Inventory, MarkdownRegistry,
    Person, Project, ProjectStatus,
    parse_frontmatter, render_frontmatter,
)


# ── Frontmatter parser ───────────────────────────────────────────────────────


class TestFrontmatter:
    def test_parses_valid_frontmatter(self):
        text = textwrap.dedent("""\
            ---
            id: foo
            tags:
              - one
              - two
            ---

            Body content here.
            """)
        fm, body = parse_frontmatter(text)
        assert fm["id"] == "foo"
        assert fm["tags"] == ["one", "two"]
        assert "Body content here." in body

    def test_no_frontmatter_returns_empty_dict(self):
        text = "Just markdown, no frontmatter.\n"
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_invalid_yaml_raises(self):
        bad = "---\nid: foo: bar: baz\n  - invalid\n---\n"
        with pytest.raises(ValueError):
            parse_frontmatter(bad)

    def test_non_mapping_frontmatter_raises(self):
        # YAML list at top level isn't a mapping
        bad = "---\n- one\n- two\n---\n"
        with pytest.raises(ValueError):
            parse_frontmatter(bad)


# ── Person CRUD roundtrip ────────────────────────────────────────────────────


class TestPersonRoundtrip:
    def test_upsert_then_get(self, tmp_registry: MarkdownRegistry):
        p = Person(
            id="sheila-streeter",
            full_name="Sheila Streeter",
            role="financial planner",
            tags=["professional/advisor"],
            relationships=["tax-amendment-2025"],
            triage="high",
            attributes={"is_tax_cpa": False},
            notes="Handles Roth IRA.",
        )
        tmp_registry.people.upsert(p)

        # Reload from disk
        tmp_registry.people.reload()
        loaded = tmp_registry.people.get("sheila-streeter")
        assert loaded is not None
        assert loaded.full_name == "Sheila Streeter"
        assert loaded.role == "financial planner"
        assert loaded.relationships == ["tax-amendment-2025"]
        assert loaded.attributes["is_tax_cpa"] is False
        assert "Handles Roth IRA." in loaded.notes
        assert loaded.created_at is not None
        assert loaded.created_by_agent == "operator"

    def test_upsert_preserves_created_at(self, tmp_registry: MarkdownRegistry):
        p = Person(id="x", full_name="X")
        tmp_registry.people.upsert(p)
        first_created = tmp_registry.people.get("x").created_at

        # Update the same id with a different agent
        p2 = Person(id="x", full_name="X Updated")
        tmp_registry.people.upsert(p2, agent="finance-agent")

        loaded = tmp_registry.people.get("x")
        assert loaded.full_name == "X Updated"
        assert loaded.created_at == first_created  # never changes
        assert loaded.updated_at >= first_created
        # First writer wins for created_by_agent
        assert loaded.created_by_agent == "operator"

    def test_delete(self, tmp_registry: MarkdownRegistry):
        tmp_registry.people.upsert(Person(id="x", full_name="X"))
        assert "x" in tmp_registry.people
        assert tmp_registry.people.delete("x") is True
        assert tmp_registry.people.get("x") is None
        assert tmp_registry.people.delete("x") is False  # idempotent


# ── Filtering ────────────────────────────────────────────────────────────────


class TestFiltering:
    def test_list_by_status(self, tmp_registry: MarkdownRegistry):
        tmp_registry.projects.upsert(Project(id="a", name="A", status=ProjectStatus.ACTIVE))
        tmp_registry.projects.upsert(Project(id="b", name="B", status=ProjectStatus.BLOCKED))
        tmp_registry.projects.upsert(Project(id="c", name="C", status=ProjectStatus.DONE))

        active = tmp_registry.projects.list(status=ProjectStatus.ACTIVE)
        assert {p.id for p in active} == {"a"}

        blocked = tmp_registry.projects.list(status=ProjectStatus.BLOCKED)
        assert {p.id for p in blocked} == {"b"}

    def test_inventory_by_role(self, tmp_registry: MarkdownRegistry):
        tmp_registry.inventory.upsert(Inventory(id="banner", hostname="banner",
                                                role="monitoring", monitored=True))
        tmp_registry.inventory.upsert(Inventory(id="loki", hostname="loki",
                                                role="logs", monitored=True))
        tmp_registry.inventory.upsert(Inventory(id="thor", hostname="thor",
                                                role="workstation", monitored=False))

        monitored = tmp_registry.inventory.list(monitored=True)
        assert {h.id for h in monitored} == {"banner", "loki"}


# ── render_frontmatter excludes empties ──────────────────────────────────────


class TestRender:
    def test_empties_omitted(self):
        p = Person(id="x", full_name="X")  # no tags, relationships, etc.
        out = render_frontmatter(p, "")
        # Empty lists shouldn't appear
        assert "tags:" not in out
        assert "relationships:" not in out
        # But required fields do
        assert "id: x" in out
        assert "full_name: X" in out


# ── Stats ────────────────────────────────────────────────────────────────────


class TestStats:
    def test_counts(self, tmp_registry: MarkdownRegistry):
        tmp_registry.people.upsert(Person(id="p1", full_name="P1"))
        tmp_registry.people.upsert(Person(id="p2", full_name="P2"))
        tmp_registry.accounts.upsert(Account(id="a1", name="A1",
                                             institution="X", account_type="checking"))

        stats = tmp_registry.stats()
        assert stats.backend == "markdown"
        assert stats.counts["people"] == 2
        assert stats.counts["accounts"] == 1
        assert stats.counts["projects"] == 0
