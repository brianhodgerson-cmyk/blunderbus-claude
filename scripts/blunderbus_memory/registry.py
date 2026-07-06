"""
Registry: the canonical store of entities (people, projects, accounts, inventory).

Two backends, same interface:
- MarkdownRegistry: YAML-frontmatter markdown files in memory/registry/
  Used for the home lab. Editable in Obsidian, git-tracked, syncs to phone.
- PostgresRegistry (future): for TLS V1 on Mercury. Same API, swap the backend.

Agents call:
    reg = MarkdownRegistry(Path("memory/registry"))
    sheila = reg.people.get("sheila-streeter")
    for p in reg.projects.list(status="active"): ...
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Generic, Optional, TypeVar

import yaml
from pydantic import BaseModel

from .models import (
    Account, Inventory, Person, Project, ProjectStatus,
    HostStatus, RegistryStats, _Entity,
)


T = TypeVar("T", bound=_Entity)


# ── YAML-frontmatter parser ──────────────────────────────────────────────────


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_str). Empty dict if no frontmatter."""
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}")
    if not isinstance(fm, dict):
        raise ValueError(f"Frontmatter must be a YAML mapping, got {type(fm).__name__}")
    return fm, m.group(2)


def render_frontmatter(model: BaseModel, body: str = "") -> str:
    """Serialize a Pydantic model + body into YAML-frontmatter markdown."""
    data = model.model_dump(mode="json", exclude_none=True, exclude={"notes"})
    # Empty containers add noise
    data = {k: v for k, v in data.items()
            if not (isinstance(v, (list, dict)) and len(v) == 0)}
    fm = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = (body or "").rstrip() + "\n"
    return f"---\n{fm}---\n\n{body}"


# ── Generic per-type collection ──────────────────────────────────────────────


class MarkdownCollection(Generic[T]):
    """One folder = one entity type. Files are <id>.md."""

    def __init__(self, root: Path, model_cls: type[T]):
        self.root = root
        self.model_cls = model_cls
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, T] = {}
        self._lock = threading.RLock()
        self._loaded = False

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._cache.clear()
            for md_path in sorted(self.root.glob("*.md")):
                if md_path.name.startswith("_"):
                    continue
                try:
                    text = md_path.read_text(encoding="utf-8")
                    fm, body = parse_frontmatter(text)
                    fm["notes"] = body.strip()
                    obj = self.model_cls(**fm)
                    self._cache[obj.id] = obj
                except Exception as e:
                    print(f"  ⚠ skipping {md_path.name}: {e}")
            self._loaded = True

    def reload(self) -> None:
        with self._lock:
            self._loaded = False
            self._load()

    def get(self, entity_id: str) -> Optional[T]:
        self._load()
        return self._cache.get(entity_id)

    def list(self, **filters) -> list[T]:
        self._load()
        out = list(self._cache.values())
        for k, v in filters.items():
            out = [e for e in out if getattr(e, k, None) == v]
        return out

    def all(self) -> list[T]:
        self._load()
        return list(self._cache.values())

    def upsert(self, entity: T, *, agent: str = "operator") -> T:
        """Create or update. Writes to disk, updates cache."""
        self._load()
        now = datetime.now()
        existing = self._cache.get(entity.id)
        if existing:
            entity.created_at = existing.created_at or entity.created_at or now
            entity.created_by_agent = existing.created_by_agent or agent
        else:
            entity.created_at = entity.created_at or now
            entity.created_by_agent = entity.created_by_agent or agent
        entity.updated_at = now
        path = self.root / f"{entity.id}.md"
        path.write_text(render_frontmatter(entity, entity.notes), encoding="utf-8")
        with self._lock:
            self._cache[entity.id] = entity
        return entity

    def delete(self, entity_id: str) -> bool:
        path = self.root / f"{entity_id}.md"
        existed = path.exists()
        if existed:
            path.unlink()
        with self._lock:
            self._cache.pop(entity_id, None)
        return existed

    def __len__(self) -> int:
        self._load()
        return len(self._cache)

    def __contains__(self, entity_id: str) -> bool:
        self._load()
        return entity_id in self._cache


# ── Registry ──────────────────────────────────────────────────────────────────


class MarkdownRegistry:
    """The home-lab backend. Markdown files in memory/registry/."""

    backend_name = "markdown"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.people = MarkdownCollection[Person](self.root / "people", Person)
        self.projects = MarkdownCollection[Project](self.root / "projects", Project)
        self.accounts = MarkdownCollection[Account](self.root / "accounts", Account)
        self.inventory = MarkdownCollection[Inventory](self.root / "inventory", Inventory)

    def stats(self) -> RegistryStats:
        return RegistryStats(
            backend=self.backend_name,
            counts={
                "people": len(self.people),
                "projects": len(self.projects),
                "accounts": len(self.accounts),
                "inventory": len(self.inventory),
            },
            last_loaded=datetime.now(),
        )

    def reload_all(self) -> None:
        for c in (self.people, self.projects, self.accounts, self.inventory):
            c.reload()


# ── Convenience: default registry pointing at the repo's memory/registry ────


_DEFAULT: Optional[MarkdownRegistry] = None


def get_default_registry() -> MarkdownRegistry:
    """Lazy-init a singleton pointing at <repo>/memory/registry."""
    global _DEFAULT
    if _DEFAULT is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        _DEFAULT = MarkdownRegistry(repo_root / "memory" / "registry")
    return _DEFAULT
