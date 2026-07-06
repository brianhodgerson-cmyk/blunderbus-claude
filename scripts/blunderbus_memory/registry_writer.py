"""
Registry writers — apply Path C question-thread answers to the right YAML file.

Each function here corresponds to one (target_kind, target_field) combination.
They:
  1. Read the existing markdown + frontmatter
  2. Update the specific YAML field, preserving everything else
  3. Append a one-line note to `## Agent notes` with who/when/why
  4. Atomic write (temp file → rename)

The Discord bot calls these from its `on_reaction_add` (👍) handler. The
write is paired with a `journal.write_decision()` call upstream so the
decisions log has the audit trail.

Safety:
- NEVER edits frontmatter blindly. We use parse_frontmatter / render_frontmatter
  from the registry module so the YAML stays well-formed.
- If the file doesn't exist, raises RegistryWriteError. Doesn't auto-create.
- Returns a short, human-readable diff summary the bot can echo back.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .registry import parse_frontmatter


ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_ROOT = ROOT / "memory" / "registry"


def _dump_frontmatter(fm: dict, body: str) -> str:
    """Serialize a frontmatter dict + body back to YAML-frontmatter markdown.

    We bypass `registry.render_frontmatter` because that one expects a
    Pydantic model. Path C writers operate on the raw dict so they don't
    drop unknown fields (e.g. fields the Pydantic schema doesn't know about
    but the operator hand-added).
    """
    yaml_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    body = (body or "").rstrip("\n") + "\n"
    return f"---\n{yaml_text}---\n\n{body}"


class RegistryWriteError(RuntimeError):
    pass


def _entity_path(kind: str, entity_id: str) -> Path:
    """Resolve the markdown file for an entity. kind ∈ accounts|people|projects|inventory."""
    folder = {"account": "accounts", "person": "people",
              "project": "projects", "inventory": "inventory"}.get(kind)
    if not folder:
        raise RegistryWriteError(f"unknown target_kind: {kind}")
    p = REGISTRY_ROOT / folder / f"{entity_id}.md"
    if not p.exists():
        raise RegistryWriteError(f"no registry file at {p}")
    return p


def _append_agent_note(body: str, note: str) -> str:
    """Append a bullet under `## Agent notes`. Creates the section if missing."""
    line = f"- **{datetime.now().strftime('%Y-%m-%d %H:%M')} — discord question-thread**: {note}"
    if "## Agent notes" in body:
        # Insert at end of the Agent notes section, before any subsequent ## section
        parts = body.split("## Agent notes", 1)
        head, tail = parts[0], parts[1]
        # Find next ## or end of file
        next_header_pos = tail.find("\n## ")
        if next_header_pos < 0:
            return head + "## Agent notes" + tail.rstrip() + "\n" + line + "\n"
        return head + "## Agent notes" + tail[:next_header_pos].rstrip() + "\n" + line + "\n" + tail[next_header_pos:]
    # Section doesn't exist yet
    return body.rstrip() + "\n\n## Agent notes\n\n" + line + "\n"


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    shutil.move(str(tmp), str(path))


def set_field(kind: str, entity_id: str, field: str, value: Any, *,
              note: str = "") -> dict[str, str]:
    """Generic frontmatter-field setter. Returns dict with `before`, `after`, `path`.

    For top-level Person/Project/Account YAML fields. Updates `updated_at`
    automatically. If the field doesn't exist, adds it.
    """
    path = _entity_path(kind, entity_id)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    before = frontmatter.get(field, "<unset>")
    frontmatter[field] = value
    frontmatter["updated_at"] = datetime.now().isoformat(timespec="seconds")
    rendered = _dump_frontmatter(frontmatter, body)
    if note:
        rendered = _append_agent_note(rendered, note)
    _atomic_write(path, rendered)
    return {"path": str(path.relative_to(ROOT)),
            "field": field,
            "before": str(before),
            "after": str(value)}


def set_attribute(kind: str, entity_id: str, attr: str, value: Any, *,
                  note: str = "") -> dict[str, str]:
    """Set a key under `attributes:` rather than at top level. Use this for
    fields the Person/Project model treats as free-form (status_question,
    email, phone, credential, etc.)."""
    path = _entity_path(kind, entity_id)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    attrs = frontmatter.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    before = attrs.get(attr, "<unset>")
    attrs[attr] = value
    frontmatter["attributes"] = attrs
    frontmatter["updated_at"] = datetime.now().isoformat(timespec="seconds")
    rendered = _dump_frontmatter(frontmatter, body)
    if note:
        rendered = _append_agent_note(rendered, note)
    _atomic_write(path, rendered)
    return {"path": str(path.relative_to(ROOT)),
            "field": f"attributes.{attr}",
            "before": str(before),
            "after": str(value)}


def resolve_project_blocker(project_id: str, blocker_text: str, resolution: str, *,
                             note: str = "") -> dict[str, str]:
    """Move a blocker from `blockers:` to `resolved_blockers:` on a project
    file, attaching the resolution text. Idempotent if the blocker isn't found
    (returns a no-op diff). The `## Agent notes` section gets the resolution
    spelled out for human-readable context."""
    path = _entity_path("project", project_id)
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    blockers = fm.get("blockers") or []
    resolved = fm.get("resolved_blockers") or []
    if not isinstance(blockers, list):
        blockers = []
    if not isinstance(resolved, list):
        resolved = []

    # Find the blocker (case-insensitive substring tolerance — blocker text in
    # the DB may have minor whitespace drift from the YAML).
    target_idx = None
    for i, b in enumerate(blockers):
        if isinstance(b, str) and (b.strip() == blocker_text.strip()
                                   or blocker_text.strip().lower() in b.strip().lower()):
            target_idx = i
            break

    before_count = len(blockers)
    if target_idx is not None:
        removed = blockers.pop(target_idx)
        resolved.append({"blocker": removed, "resolution": resolution,
                          "resolved_at": datetime.now().isoformat(timespec="seconds")})
        fm["blockers"] = blockers
        fm["resolved_blockers"] = resolved
    fm["updated_at"] = datetime.now().isoformat(timespec="seconds")
    rendered = _dump_frontmatter(fm, body)
    if note:
        rendered = _append_agent_note(rendered,
            f"{note} — resolution: {resolution!r}")
    _atomic_write(path, rendered)

    return {"path": str(path.relative_to(ROOT)),
            "field": "blockers → resolved_blockers",
            "before": f"{before_count} blocker(s) open",
            "after": f"{len(blockers)} blocker(s) open ({'RESOLVED: ' + resolution if target_idx is not None else 'NOT FOUND'})"}


def apply_question(question, value: str, *, answered_by: str = "discord-thread") -> dict[str, str]:
    """Top-level dispatcher: given a Question + parsed value, write the right
    field on the right entity. Returns the diff summary."""
    kind = question.target_kind.value if hasattr(question.target_kind, "value") else question.target_kind
    field = question.target_field or ""
    note = (f"answered_by={answered_by}, question_id={question.id}, "
            f"applied_value={value!r}")

    # Map question_type → writer. Extend as new question types land.
    if question.question_type in ("owner-confirm",):
        return set_field(kind, question.target_id, "owner", value, note=note)
    if question.question_type in ("status-clarify",):
        return set_attribute(kind, question.target_id, "status_question_resolved",
                              value, note=note)
    if question.question_type in ("project-blocker",):
        blocker_text = (question.payload or {}).get("blocker_text", "")
        return resolve_project_blocker(
            question.target_id, blocker_text, resolution=value, note=note,
        )
    # Default: write the named field at top level
    if field:
        return set_field(kind, question.target_id, field, value, note=note)
    # No mapping → just append the note so the answer isn't lost
    path = _entity_path(kind, question.target_id)
    text = path.read_text(encoding="utf-8")
    new_text = _append_agent_note(text, note + f"  (no field mapping for question_type={question.question_type})")
    _atomic_write(path, new_text)
    return {"path": str(path.relative_to(ROOT)),
            "field": "(agent notes only)",
            "before": "(no field mapping)",
            "after": value}
