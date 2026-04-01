#!/usr/bin/env python3
"""Shared note storage helpers for BlunderBus daily notes."""

from __future__ import annotations

import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from runtime import env_first, project_root


DEFAULT_DAILY_DIR = env_first("BLUNDERBUS_DAILY_DIR", "OBSIDIAN_DAILY_DIR", default="Daily") or "Daily"


class NoteStoreError(RuntimeError):
    pass


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def daily_note_rel_path(target_date: date, daily_dir: str = DEFAULT_DAILY_DIR) -> str:
    return f"{daily_dir}/{target_date.isoformat()}.md"


def upsert_section(note_text: str, header: str, new_body: str, anchor: str | None = None) -> str:
    pattern = re.compile(rf"^{re.escape(header)}\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    replacement = f"{header}\n{new_body.rstrip()}\n"
    if pattern.search(note_text):
        return pattern.sub(replacement, note_text)
    if anchor:
        idx = note_text.find(anchor)
        if idx >= 0:
            return note_text[:idx] + replacement + "\n" + note_text[idx:]
    return note_text.rstrip() + "\n\n" + replacement


@dataclass
class BaseNoteStore:
    daily_dir: str = DEFAULT_DAILY_DIR

    @property
    def backend_name(self) -> str:
        raise NotImplementedError

    def exists(self, rel_path: str) -> bool:
        raise NotImplementedError

    def read_text(self, rel_path: str) -> str:
        raise NotImplementedError

    def write_text(self, rel_path: str, content: str) -> None:
        raise NotImplementedError

    def daily_path(self, target_date: date) -> str:
        return daily_note_rel_path(target_date, self.daily_dir)

    def daily_exists(self, target_date: date) -> bool:
        return self.exists(self.daily_path(target_date))

    def read_daily(self, target_date: date) -> str:
        return self.read_text(self.daily_path(target_date))

    def write_daily(self, target_date: date, content: str) -> None:
        self.write_text(self.daily_path(target_date), content)


@dataclass
class FileNoteStore(BaseNoteStore):
    vault_root: Path = project_root()

    @property
    def backend_name(self) -> str:
        return "filesystem"

    def _resolve(self, rel_path: str) -> Path:
        rel = Path(*rel_path.replace("\\", "/").split("/"))
        return self.vault_root / rel

    def exists(self, rel_path: str) -> bool:
        return self._resolve(rel_path).exists()

    def read_text(self, rel_path: str) -> str:
        path = self._resolve(rel_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, rel_path: str, content: str) -> None:
        path = self._resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@dataclass
class ObsidianRestNoteStore(BaseNoteStore):
    base_url: str = env_first("OBSIDIAN_URL", default="https://127.0.0.1:27124") or "https://127.0.0.1:27124"
    token: str = os.environ.get("OBSIDIAN_TOKEN", "")

    @property
    def backend_name(self) -> str:
        return "obsidian-rest"

    def _vault_path(self, rel_path: str) -> str:
        quoted = urllib.parse.quote(rel_path.replace("\\", "/"), safe="/-_.()")
        return f"/vault/{quoted}"

    def _request(self, method: str, rel_path: str, content: str | None = None) -> tuple[int | None, str]:
        data = content.encode("utf-8") if content is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{self._vault_path(rel_path)}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "text/markdown",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return None, str(exc)

    def exists(self, rel_path: str) -> bool:
        code, _ = self._request("GET", rel_path)
        return code == 200

    def read_text(self, rel_path: str) -> str:
        code, body = self._request("GET", rel_path)
        if code != 200:
            raise NoteStoreError(f"Could not read {rel_path}: HTTP {code} {body[:160]}")
        return body

    def write_text(self, rel_path: str, content: str) -> None:
        code, body = self._request("PUT", rel_path, content=content)
        if code not in (200, 201, 204):
            raise NoteStoreError(f"Could not write {rel_path}: HTTP {code} {body[:160]}")


def resolve_note_store() -> BaseNoteStore:
    backend = (env_first("BLUNDERBUS_NOTE_BACKEND", "NOTE_BACKEND") or "").strip().lower()
    daily_dir = env_first("BLUNDERBUS_DAILY_DIR", "OBSIDIAN_DAILY_DIR", default=DEFAULT_DAILY_DIR) or DEFAULT_DAILY_DIR
    explicit_root = env_first("BLUNDERBUS_VAULT_ROOT", "OBSIDIAN_VAULT_ROOT")

    if backend in {"filesystem", "file", "local"}:
        root = Path(explicit_root) if explicit_root else project_root()
        return FileNoteStore(vault_root=root, daily_dir=daily_dir)

    if backend in {"obsidian", "obsidian-rest", "rest"}:
        return ObsidianRestNoteStore(daily_dir=daily_dir)

    if explicit_root:
        return FileNoteStore(vault_root=Path(explicit_root), daily_dir=daily_dir)

    repo_root = project_root()
    if (repo_root / daily_dir).exists():
        return FileNoteStore(vault_root=repo_root, daily_dir=daily_dir)

    if os.environ.get("OBSIDIAN_TOKEN"):
        return ObsidianRestNoteStore(daily_dir=daily_dir)

    return FileNoteStore(vault_root=repo_root, daily_dir=daily_dir)
