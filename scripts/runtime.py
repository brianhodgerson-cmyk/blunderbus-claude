#!/usr/bin/env python3
"""Shared runtime helpers for BlunderBus scripts."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent


def configure_utf8_stdio() -> None:
    """Force UTF-8 stdio on Windows so Unicode prompts/logs survive subprocess use."""
    if sys.platform != "win32":
        return

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or getattr(stream, "_blunderbus_utf8", False):
            continue
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
            setattr(stream, "_blunderbus_utf8", True)
            continue
        if not hasattr(stream, "buffer"):
            continue

        import io

        wrapped = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
        setattr(wrapped, "_blunderbus_utf8", True)
        setattr(sys, name, wrapped)


def project_root() -> Path:
    return PROJECT_DIR


def env_first(*names: str, default: str | None = None) -> str | None:
    """Return the first non-empty environment value from the provided names."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def read_env_file(path: str | Path | None = None) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file without requiring python-dotenv."""
    env_path = Path(path) if path else PROJECT_DIR / ".env"
    if not env_path.exists():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def resolve_claude_command() -> str | None:
    """
    Resolve a runnable Claude CLI entrypoint across Windows and Unix-like hosts.

    Resolution order:
    1. CLAUDE_BIN / CLAUDE_CMD override
    2. Known npm global install locations
    3. PATH lookups
    """
    override = env_first("CLAUDE_BIN", "CLAUDE_CMD")
    if override:
        candidate = Path(override)
        if candidate.exists():
            return str(candidate)
        return override

    candidates: list[Path] = []

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "claude.cmd")
        for executable in ("claude.cmd", "claude"):
            resolved = shutil.which(executable)
            if resolved and Path(resolved).suffix.lower() != ".ps1":
                candidates.append(Path(resolved))
    else:
        for executable in ("claude", "claude.cmd"):
            resolved = shutil.which(executable)
            if resolved:
                candidates.append(Path(resolved))

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Claude Desktop ships versioned binaries with no stable PATH entry
    # (~/.config/Claude/claude-code/<version>/claude). Pick the newest so
    # systemd jobs survive app updates without CLAUDE_BIN churn.
    desktop_dir = Path.home() / ".config" / "Claude" / "claude-code"
    if desktop_dir.is_dir():
        def _ver_key(p: Path) -> tuple:
            try:
                return tuple(int(x) for x in p.parent.name.split("."))
            except ValueError:
                return (0,)
        versioned = sorted(desktop_dir.glob("*/claude"), key=_ver_key)
        for candidate in reversed(versioned):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

    return None
