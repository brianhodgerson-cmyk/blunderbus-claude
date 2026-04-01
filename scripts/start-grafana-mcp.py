#!/usr/bin/env python3
"""Load .env and exec the installed Grafana MCP binary with inherited stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from runtime import read_env_file


EXTENSION_ID = "ant.dir.gh.grafana.grafana-mcp"


def _merge_env() -> dict[str, str]:
    env = os.environ.copy()
    for key, value in read_env_file().items():
        env.setdefault(key, value)
    return env


def _candidate_roots(env: dict[str, str]) -> list[Path]:
    roots: list[Path] = []

    appdata = env.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Claude" / "Claude Extensions" / EXTENSION_ID / "server")

    home = Path.home()
    roots.extend(
        [
            home / ".config" / "Claude" / "Claude Extensions" / EXTENSION_ID / "server",
            home / ".local" / "share" / "Claude" / "Claude Extensions" / EXTENSION_ID / "server",
        ]
    )

    return roots


def _direct_candidates(root: Path) -> list[Path]:
    return [
        root / "win32-x64" / "mcp-grafana.exe",
        root / "linux-x64" / "mcp-grafana",
        root / "linux-arm64" / "mcp-grafana",
        root / "darwin-arm64" / "mcp-grafana",
        root / "darwin-x64" / "mcp-grafana",
    ]


def _resolve_binary(env: dict[str, str]) -> Path:
    override = env.get("GRAFANA_MCP_BIN")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"GRAFANA_MCP_BIN points to a missing file: {candidate}")

    for root in _candidate_roots(env):
        for candidate in _direct_candidates(root):
            if candidate.is_file():
                return candidate

    for root in _candidate_roots(env):
        if not root.exists():
            continue
        for candidate in sorted(root.rglob("mcp-grafana*")):
            if candidate.is_file():
                return candidate

    searched = "\n".join(str(path) for path in _candidate_roots(env))
    raise FileNotFoundError(
        "Grafana MCP binary not found. Set GRAFANA_MCP_BIN or install the Grafana extension.\n"
        f"Searched:\n{searched}"
    )


def main() -> None:
    env = _merge_env()
    binary = _resolve_binary(env)
    os.execve(str(binary), [str(binary)], env)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
