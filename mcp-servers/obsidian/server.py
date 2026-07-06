#!/usr/bin/env python3
"""Obsidian MCP Server — exposes vault search and read as MCP tools.

Wraps the Obsidian Local REST API (https://127.0.0.1:27124) so Claude Code
and enterprise-search can treat Obsidian as a first-class knowledge base.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Obsidian REST API helpers
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("OBSIDIAN_URL", "https://127.0.0.1:27124")
TOKEN = os.environ.get("OBSIDIAN_TOKEN", "")


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_request(
    method: str,
    path: str,
    *,
    body: str | bytes | None = None,
    content_type: str = "application/json",
    accept: str = "application/json",
    params: dict[str, str] | None = None,
) -> tuple[int, str]:
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else body

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": content_type,
            "Accept": accept,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "obsidian",
    instructions=(
        "Obsidian knowledge base — search and read markdown notes from the user's "
        "Obsidian vault. Use obsidian_search for full-text queries, obsidian_read "
        "to fetch a specific note's content."
    ),
)


@mcp.tool()
def obsidian_search(query: str, context_length: int = 150) -> str:
    """Search the Obsidian vault for notes matching a query.

    Full-text fuzzy search across all markdown files in the vault.
    Returns matching filenames with context snippets showing where
    the query matched.

    Args:
        query: Search terms (fuzzy matched against filenames and content).
        context_length: Characters of surrounding context per match (default 150).
    """
    code, body = _api_request(
        "POST",
        "/search/simple/",
        params={"query": query, "contextLength": str(context_length)},
    )
    if code != 200:
        return f"Search failed (HTTP {code}): {body[:300]}"

    try:
        results = json.loads(body)
    except json.JSONDecodeError:
        return f"Invalid response: {body[:300]}"

    if not results:
        return f'No results found for "{query}".'

    lines: list[str] = [f'Found {len(results)} result(s) for "{query}":\n']
    for item in results[:20]:  # cap at 20 results
        filename = item.get("filename", "?")
        score = item.get("score", "")
        score_str = f" (score: {score:.2f})" if isinstance(score, (int, float)) else ""
        lines.append(f"### {filename}{score_str}")

        for match in item.get("matches", [])[:3]:  # cap snippets per file
            ctx = match.get("context", "").strip()
            source = match.get("match", {}).get("source", "content")
            if ctx and source == "content":
                lines.append(f"> ...{ctx}...")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def obsidian_read(path: str) -> str:
    """Read a specific note from the Obsidian vault.

    Args:
        path: Vault-relative path to the note (e.g. 'Daily/2026-04-01.md'
              or '10 - Projects/BlunderBus.md').
    """
    quoted = urllib.parse.quote(path.replace("\\", "/"), safe="/-_.()")
    code, body = _api_request(
        "GET",
        f"/vault/{quoted}",
        accept="text/markdown",
    )
    if code == 404:
        return f"Note not found: {path}"
    if code != 200:
        return f"Failed to read {path} (HTTP {code}): {body[:300]}"
    return body


@mcp.tool()
def obsidian_list(folder: str = "/") -> str:
    """List files and folders in the Obsidian vault.

    Args:
        folder: Vault-relative folder path (default '/' for root).
              Example: 'Daily', '10 - Projects'.
    """
    clean = folder.strip("/").replace("\\", "/")
    quoted = urllib.parse.quote(clean, safe="/-_.()")
    vault_path = f"/vault/{quoted}/" if clean else "/vault/"
    code, body = _api_request("GET", vault_path, accept="application/json")
    if code != 200:
        return f"Failed to list {folder} (HTTP {code}): {body[:300]}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return f"Invalid response: {body[:300]}"

    files = data.get("files", data) if isinstance(data, dict) else data
    if isinstance(files, list):
        return "\n".join(str(f) for f in files[:100])
    return body[:2000]


@mcp.tool()
def obsidian_write(path: str, content: str) -> str:
    """Create or overwrite a note in the Obsidian vault.

    If the note already exists, its content is fully replaced.
    Parent folders are created automatically by the API.

    Args:
        path: Vault-relative path (e.g. 'Daily/2026-04-02.md').
        content: Full markdown content for the note.
    """
    quoted = urllib.parse.quote(path.replace("\\", "/"), safe="/-_.()")
    code, body = _api_request(
        "PUT",
        f"/vault/{quoted}",
        body=content,
        content_type="text/markdown",
    )
    if code in (200, 204):
        return f"Wrote {path} successfully."
    return f"Failed to write {path} (HTTP {code}): {body[:300]}"


@mcp.tool()
def obsidian_append(path: str, content: str, heading: str = "") -> str:
    """Append content to an existing note in the Obsidian vault.

    Adds content to the end of the note, or beneath a specific heading
    if provided. The note must already exist.

    Args:
        path: Vault-relative path (e.g. 'Daily/2026-04-02.md').
        content: Markdown content to append.
        heading: Optional heading to append under (e.g. 'Infrastructure').
                 If empty, appends to the end of the note.
    """
    quoted = urllib.parse.quote(path.replace("\\", "/"), safe="/-_.()")
    if heading:
        endpoint = f"/vault/{quoted}"
        params = {"heading": heading}
        code, body = _api_request(
            "POST",
            endpoint,
            body=content,
            content_type="text/markdown",
            params=params,
        )
    else:
        code, body = _api_request(
            "POST",
            f"/vault/{quoted}",
            body=content,
            content_type="text/markdown",
        )
    if code in (200, 204):
        target = f" under '{heading}'" if heading else ""
        return f"Appended to {path}{target} successfully."
    if code == 404:
        return f"Note not found: {path} — create it first with obsidian_write."
    return f"Failed to append to {path} (HTTP {code}): {body[:300]}"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not TOKEN:
        print("WARNING: OBSIDIAN_TOKEN not set. API calls will fail.", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
