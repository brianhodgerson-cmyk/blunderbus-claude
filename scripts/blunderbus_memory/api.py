"""
FastAPI service exposing the BlunderBus memory layer.

Mounted under /api/* so a frontend at the same origin can hit /api/registry/people etc.

Auth: simple X-API-Key header. Set BBM_API_KEY in env. Disable for dev with
BBM_API_KEY="dev" or unset BBM_API_KEY (the latter is dev-mode/no-auth).

Run locally:
    uvicorn blunderbus_memory.api:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    GET  /api/health                          → liveness + counts
    GET  /api/registry/{kind}                 → list (people | projects | accounts | inventory)
    GET  /api/registry/{kind}/{id}            → fetch one
    PUT  /api/registry/{kind}/{id}            → upsert
    DELETE /api/registry/{kind}/{id}          → delete
    GET  /api/concerns                        → list active concerns
    GET  /api/concerns/all                    → list all (active + resolved + stale)
    POST /api/concerns/{id}/resolve           → manually mark resolved
    GET  /api/brief/today                     → render today's daily note (markdown)
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Body, Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .models import (
    Account, Concern, ConcernStatus, HostStatus, Inventory,
    Person, Project, ProjectStatus, Severity,
)
from .registry import MarkdownRegistry, get_default_registry


# ── Auth ─────────────────────────────────────────────────────────────────────


def _check_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    expected = os.environ.get("BBM_API_KEY")
    if not expected:
        # No key configured = dev mode, allow all
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


# ── App ──────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="BlunderBus Ops",
    description="Registry + concerns API for the BlunderBus memory layer.",
    version="0.1.0",
)

# CORS — for local dev where Next.js may run on a different port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Lazy registry singleton (avoid loading on import)
_registry: Optional[MarkdownRegistry] = None


def _reg() -> MarkdownRegistry:
    global _registry
    if _registry is None:
        if path := os.environ.get("BBM_REGISTRY_ROOT"):
            _registry = MarkdownRegistry(Path(path))
        else:
            _registry = get_default_registry()
    return _registry


def _collection(kind: str):
    reg = _reg()
    if kind == "people":     return reg.people
    if kind == "projects":   return reg.projects
    if kind == "accounts":   return reg.accounts
    if kind == "inventory":  return reg.inventory
    raise HTTPException(status_code=404, detail=f"unknown collection: {kind}")


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    """Liveness probe + registry counts. Doesn't require API key."""
    try:
        stats = _reg().stats()
        return {
            "ok": True,
            "backend": stats.backend,
            "counts": stats.counts,
            "auth_required": bool(os.environ.get("BBM_API_KEY")),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Registry CRUD ────────────────────────────────────────────────────────────


@app.get("/api/registry/{kind}", dependencies=[Depends(_check_api_key)])
def list_entities(kind: str) -> list[dict]:
    coll = _collection(kind)
    return [e.model_dump(mode="json", exclude_none=True) for e in coll.all()]


@app.get("/api/registry/{kind}/{entity_id}", dependencies=[Depends(_check_api_key)])
def get_entity(kind: str, entity_id: str) -> dict:
    coll = _collection(kind)
    obj = coll.get(entity_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="not found")
    return obj.model_dump(mode="json", exclude_none=True)


@app.put("/api/registry/{kind}/{entity_id}", dependencies=[Depends(_check_api_key)])
def upsert_entity(kind: str, entity_id: str,
                  payload: dict = Body(...)) -> dict:
    """Upsert. The payload's `id` is overridden by the URL path id."""
    coll = _collection(kind)
    payload["id"] = entity_id
    model_map = {"people": Person, "projects": Project,
                 "accounts": Account, "inventory": Inventory}
    cls = model_map[kind]
    try:
        obj = cls(**payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"validation error: {exc}")
    saved = coll.upsert(obj, agent="ops-ui")
    return saved.model_dump(mode="json", exclude_none=True)


@app.delete("/api/registry/{kind}/{entity_id}", dependencies=[Depends(_check_api_key)])
def delete_entity(kind: str, entity_id: str) -> dict:
    coll = _collection(kind)
    deleted = coll.delete(entity_id)
    return {"deleted": deleted}


# ── Concerns ─────────────────────────────────────────────────────────────────


def _store():
    """Lazy PostgresConcerns instance. Reuses a connection across requests."""
    from .concerns import PostgresConcerns
    if not hasattr(_store, "_inst"):
        _store._inst = PostgresConcerns()
    return _store._inst


@app.get("/api/concerns", dependencies=[Depends(_check_api_key)])
def list_concerns(agent: Optional[str] = None) -> list[dict]:
    """Active concerns (optionally filtered by agent)."""
    items = _store().list_active(agent)
    return [c.model_dump(mode="json", exclude_none=True) for c in items]


@app.get("/api/concerns/all", dependencies=[Depends(_check_api_key)])
def list_concerns_all(limit: int = 100) -> list[dict]:
    """All concerns (active + resolved + stale), most recent first."""
    store = _store()
    with store._cur() as cur:
        cur.execute(
            "SELECT * FROM agent_concerns WHERE tenant_id = %s "
            "ORDER BY last_verified DESC LIMIT %s",
            (store.tenant_id, limit),
        )
        rows = cur.fetchall()
    from .concerns import _row_to_concern
    return [_row_to_concern(r).model_dump(mode="json", exclude_none=True) for r in rows]


@app.post("/api/concerns/{concern_id:path}/resolve",
          dependencies=[Depends(_check_api_key)])
def resolve_concern(concern_id: str) -> dict:
    ok = _store().resolve(concern_id)
    return {"resolved": ok, "id": concern_id}


@app.get("/api/concerns/stats", dependencies=[Depends(_check_api_key)])
def concerns_stats() -> dict:
    return _store().stats()


# ── Today's brief ────────────────────────────────────────────────────────────


def _briefs_dir() -> Path:
    """Where pushed briefs are persisted. Resolved from BBM_BRIEFS_DIR env or
    a sibling of the registry root."""
    if path := os.environ.get("BBM_BRIEFS_DIR"):
        return Path(path)
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "data" / "briefs"


@app.get("/api/brief/today", dependencies=[Depends(_check_api_key)])
def todays_brief() -> dict:
    """Return today's brief. Read from the briefs store first (populated by
    daily_brief.py POSTing on each run), falling back to a local Daily/ note
    on the same host if present (useful in dev)."""
    today = date.today()
    iso = today.isoformat()

    # Primary: stored push from daily_brief.py
    stored = _briefs_dir() / f"{iso}.json"
    if stored.exists():
        import json
        try:
            data = json.loads(stored.read_text(encoding="utf-8"))
            return {"date": iso, "exists": True,
                    "markdown": data.get("markdown", ""),
                    "briefing": data.get("briefing", "")}
        except Exception:
            pass

    # Fallback: local Daily/ note (dev convenience)
    repo_root = Path(__file__).resolve().parent.parent.parent
    note_path = repo_root / "Daily" / f"{iso}.md"
    if note_path.exists():
        md = note_path.read_text(encoding="utf-8")
        import re
        m = re.search(r"^##\s+Briefing\s*\n(.*?)(?=^##\s|\Z)", md,
                      flags=re.MULTILINE | re.DOTALL)
        briefing = m.group(1).strip() if m else ""
        return {"date": iso, "exists": True, "markdown": md, "briefing": briefing}

    return {"date": iso, "exists": False, "markdown": "", "briefing": ""}


class BriefPayload(BaseModel):
    date: str         # YYYY-MM-DD
    markdown: str     # full daily-note markdown
    briefing: str     # extracted ## Briefing section


@app.post("/api/brief", dependencies=[Depends(_check_api_key)])
def push_brief(payload: BriefPayload) -> dict:
    """Receive a generated brief and persist it. Called by daily_brief.py
    after writing to the local Obsidian vault."""
    import json
    out_dir = _briefs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{payload.date}.json"
    out.write_text(
        json.dumps({"markdown": payload.markdown, "briefing": payload.briefing}),
        encoding="utf-8",
    )
    return {"stored": True, "date": payload.date, "bytes": out.stat().st_size}


# ── Tasks (TASKS.md proxy) ───────────────────────────────────────────────────


def _tasks_file() -> Path:
    """Locate TASKS.md. Override via BBM_TASKS_FILE env."""
    if path := os.environ.get("BBM_TASKS_FILE"):
        return Path(path)
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "TASKS.md"


def _hash_id(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


@app.get("/api/tasks", dependencies=[Depends(_check_api_key)])
def list_tasks() -> dict:
    """Parse TASKS.md and return structured tasks grouped by section.

    Each task has a stable id (hash of text + section), text, done flag,
    section name, and the line number for write-back.
    """
    path = _tasks_file()
    if not path.exists():
        return {"path": str(path), "exists": False, "sections": []}
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    sections: list[dict] = []
    current: dict | None = None
    for i, raw in enumerate(lines):
        s = raw.rstrip()
        # Detect ## section headers (skip the H1 "# Tasks")
        if s.startswith("## "):
            current = {"name": s[3:].strip(), "tasks": []}
            sections.append(current)
            continue
        if current is None:
            continue
        # Top-level checklist item: "- [ ] text" or "- [x] text"
        # (must start at column 0 — sub-bullets are details, not separate tasks)
        import re as _re
        m = _re.match(r"^- \[( |x|X)\] (.+)$", s)
        if not m:
            continue
        done = m.group(1).lower() == "x"
        body = m.group(2).strip()
        current["tasks"].append({
            "id": _hash_id(f"{current['name']}::{body}"),
            "text": body,
            "done": done,
            "section": current["name"],
            "line": i,  # 0-indexed
        })
    return {"path": str(path), "exists": True, "sections": sections}


class TaskToggle(BaseModel):
    id: str
    done: bool


# ── External task snapshots (pushed by agents) ───────────────────────────────


def _external_tasks_path() -> Path:
    if path := os.environ.get("BBM_EXT_TASKS_FILE"):
        return Path(path)
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "data" / "external_tasks.json"


class ExternalTask(BaseModel):
    """One task observed by an agent. Stable id derived by the source agent."""
    id: str
    text: str
    source: str          # "obsidian-carried", "google-tasks", future others
    section: Optional[str] = None  # e.g. "Daily Notes", "Inbox"
    done: bool = False
    metadata: dict[str, Any] = {}


class ExternalTaskSnapshot(BaseModel):
    """Snapshot pushed by an agent. Replaces the previous snapshot for `agent`."""
    agent: str
    captured_at: str           # ISO timestamp
    tasks: list[ExternalTask]


@app.post("/api/tasks/external", dependencies=[Depends(_check_api_key)])
def push_external_tasks(payload: ExternalTaskSnapshot) -> dict:
    """Receive a task snapshot from an agent. Stored per-agent so multiple
    agents can contribute (workspace, finance, future custom)."""
    import json
    path = _external_tasks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store: dict = {}
    if path.exists():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            store = {}
    store[payload.agent] = payload.model_dump(mode="json")
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")
    return {"stored": True, "agent": payload.agent, "count": len(payload.tasks)}


@app.get("/api/tasks/external", dependencies=[Depends(_check_api_key)])
def list_external_tasks() -> dict:
    """Return the latest snapshot from each agent."""
    import json
    path = _external_tasks_path()
    if not path.exists():
        return {"agents": {}}
    try:
        return {"agents": json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return {"agents": {}}


@app.post("/api/tasks/toggle", dependencies=[Depends(_check_api_key)])
def toggle_task(payload: TaskToggle) -> dict:
    """Mark a task done or undone by its stable id. Rewrites TASKS.md in place."""
    path = _tasks_file()
    if not path.exists():
        raise HTTPException(404, "TASKS.md not found")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Walk the file the same way list_tasks does and find the target id
    import re as _re
    current_section: str | None = None
    target_line: int | None = None
    target_done: bool | None = None
    for i, raw in enumerate(lines):
        s = raw.rstrip()
        if s.startswith("## "):
            current_section = s[3:].strip()
            continue
        if current_section is None:
            continue
        m = _re.match(r"^(- \[)( |x|X)(\] )(.+)$", s)
        if not m:
            continue
        body = m.group(4).strip()
        tid = _hash_id(f"{current_section}::{body}")
        if tid == payload.id:
            target_line = i
            target_done = m.group(2).lower() == "x"
            new_box = "[x]" if payload.done else "[ ]"
            lines[i] = f"- {new_box} {body}" + raw[len(s):]  # preserve trailing whitespace
            break
    if target_line is None:
        raise HTTPException(404, f"task id {payload.id} not found")
    if target_done == payload.done:
        return {"id": payload.id, "done": payload.done, "changed": False}

    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                    encoding="utf-8")
    return {"id": payload.id, "done": payload.done, "changed": True, "line": target_line}
