#!/usr/bin/env python3
"""
CouchDB Livesync → Filesystem sync for ProfX.

Pulls Obsidian vault notes from CouchDB (written by Livesync plugin) and
writes them to the local filesystem so Claude CLI can read them.

Supports two modes:
  --once      Single pull of all changed docs since last sync (for cron)
  --watch     Continuous _changes feed listener (for systemd daemon)

Livesync storage format:
  - Note docs: _id = lowercase path, type = "plain", children = ["h:xxx", ...]
  - Chunk docs: _id = "h:xxx", type = "leaf", data = <text content>
  - Full note content = concatenation of all children chunks in order
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
import ssl

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
VAULT_DIR = PROJECT_DIR  # vault IS the repo
SEQ_FILE = os.path.join(PROJECT_DIR, ".claude", "couchdb-sync-seq.txt")

COUCHDB_URL = os.environ.get("COUCHDB_URL", "http://localhost:5984")
COUCHDB_DB = os.environ.get("COUCHDB_DB", "obsidian-livesync")
COUCHDB_USER = os.environ.get("COUCHDB_USER", "admin")
COUCHDB_PASS = os.environ.get("COUCHDB_PASS", "")

# File extensions to sync (skip binaries/images we can't use)
SYNC_EXTENSIONS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".toml"}

# Path substrings that disqualify a doc regardless of extension. These directories
# get pushed in by Obsidian Livesync from clients that don't filter them, and
# replaying every README.md from node_modules pegs the host.
JUNK_SUBSTRINGS = (
    "node_modules/",
    ".venv/",
    "venv/",
    ".git/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    "dist/",
    "build/",
    ".next/",
    ".cache/",
)

# Paths to never overwrite (git-tracked code)
PROTECTED_PREFIXES = [
    "scripts/",
    ".claude/",
    ".github/",
    ".obsidian/",
    "mcp-servers/",
    "requirements.txt",
    "CLAUDE.md",
    "TASKS.md",
]

# ── HTTP helpers ─────────────────────────────────────────────────────────────

# Allow self-signed certs
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _auth_header() -> str:
    import base64
    creds = base64.b64encode(f"{COUCHDB_USER}:{COUCHDB_PASS}".encode()).decode()
    return f"Basic {creds}"


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{COUCHDB_URL}/{COUCHDB_DB}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=30) as resp:
        return json.loads(resp.read())


def _get_raw(path: str, params: dict | None = None) -> bytes:
    url = f"{COUCHDB_URL}/{COUCHDB_DB}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=30) as resp:
        return resp.read()


# ── Livesync document assembly ───────────────────────────────────────────────

def fetch_note_content(doc: dict) -> str | None:
    """Reassemble a Livesync note from its chunk children."""
    children = doc.get("children", [])
    if not children:
        # Some small docs store data inline
        return doc.get("data", "")

    parts = []
    for chunk_id in children:
        try:
            chunk = _get(urllib.parse.quote(chunk_id, safe=""))
            parts.append(chunk.get("data", ""))
        except Exception as exc:
            logger.warning("Failed to fetch chunk %s: %s", chunk_id, exc)
            return None
    return "".join(parts)


def should_sync(doc_id: str) -> bool:
    """Determine if a document should be synced to filesystem."""
    # Skip chunk docs
    if doc_id.startswith("h:"):
        return False
    # Skip CouchDB design docs
    if doc_id.startswith("_"):
        return False
    # Skip non-text files
    _, ext = os.path.splitext(doc_id)
    if ext.lower() not in SYNC_EXTENSIONS:
        return False
    # Skip dependency / build / cache trees pushed in by Livesync clients
    lowered = doc_id.lower()
    for junk in JUNK_SUBSTRINGS:
        if junk in lowered:
            return False
    # Skip protected code paths
    for prefix in PROTECTED_PREFIXES:
        if doc_id.lower().startswith(prefix.lower()):
            return False
    return True


def doc_id_to_path(doc_id: str) -> str:
    """Convert CouchDB doc ID back to filesystem path.

    Livesync lowercases IDs but preserves the original path in the doc.
    We use the `path` field when available, falling back to the ID.
    """
    return doc_id


def write_note(doc_id: str, content: str, doc: dict) -> bool:
    """Write note content to filesystem. Returns True if file was updated."""
    # Use the path field if available (preserves original case)
    rel_path = doc.get("path", doc_id).lstrip("/")
    abs_path = os.path.join(VAULT_DIR, rel_path)

    # Check if content actually changed
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                existing = f.read()
            if existing == content:
                return False
        except Exception:
            pass

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def delete_note(doc_id: str) -> bool:
    """Remove a deleted note from filesystem."""
    rel_path = doc_id.lstrip("/")
    abs_path = os.path.join(VAULT_DIR, rel_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
        logger.info("Deleted: %s", rel_path)
        return True
    return False


# ── Sequence tracking ────────────────────────────────────────────────────────

def load_seq() -> str:
    if os.path.exists(SEQ_FILE):
        with open(SEQ_FILE) as f:
            return f.read().strip()
    return "0"


def save_seq(seq: str) -> None:
    os.makedirs(os.path.dirname(SEQ_FILE), exist_ok=True)
    with open(SEQ_FILE, "w") as f:
        f.write(seq)


# ── Sync modes ───────────────────────────────────────────────────────────────

def full_sync() -> int:
    """Pull all notes from CouchDB. Used for initial sync or reset."""
    logger.info("Starting full sync...")
    all_docs = _get("_all_docs", {"limit": "10000"})
    rows = all_docs.get("rows", [])
    synced = 0
    skipped = 0

    for row in rows:
        doc_id = row["id"]
        if not should_sync(doc_id):
            continue

        try:
            doc = _get(urllib.parse.quote(doc_id, safe=""))
            content = fetch_note_content(doc)
            if content is None:
                logger.warning("Skipping %s — failed to assemble", doc_id)
                continue
            if write_note(doc_id, content, doc):
                synced += 1
                logger.info("Synced: %s", doc.get("path", doc_id))
            else:
                skipped += 1
        except Exception as exc:
            logger.warning("Error syncing %s: %s", doc_id, exc)

    logger.info("Full sync complete: %d updated, %d unchanged", synced, skipped)
    # Save current update_seq
    db_info = _get("")
    save_seq(str(db_info.get("update_seq", "0")))
    return synced


def incremental_sync() -> int:
    """Pull only changes since last sync."""
    since = load_seq()
    logger.info("Incremental sync since seq: %s", since[:40])

    changes = _get("_changes", {
        "since": since,
        "include_docs": "true",
        "limit": "500",
    })

    synced = 0
    for result in changes.get("results", []):
        doc_id = result["id"]
        if not should_sync(doc_id):
            continue

        # Handle deletions
        if result.get("deleted"):
            delete_note(doc_id)
            continue

        doc = result.get("doc", {})
        if doc.get("type") != "plain":
            continue

        try:
            content = fetch_note_content(doc)
            if content is None:
                continue
            if write_note(doc_id, content, doc):
                synced += 1
                logger.info("Updated: %s", doc.get("path", doc_id))
        except Exception as exc:
            logger.warning("Error syncing %s: %s", doc_id, exc)

    new_seq = changes.get("last_seq", since)
    save_seq(str(new_seq))
    logger.info("Incremental sync: %d notes updated", synced)
    return synced


def watch_continuous() -> None:
    """Listen to CouchDB _changes feed continuously."""
    since = load_seq()
    logger.info("Starting continuous watch from seq: %s", since[:40])

    while True:
        try:
            # Long-poll with 60s timeout — CouchDB holds connection open
            # until a change arrives or timeout expires
            url = (
                f"{COUCHDB_URL}/{COUCHDB_DB}/_changes?"
                + urllib.parse.urlencode({
                    "since": since,
                    "include_docs": "true",
                    "feed": "longpoll",
                    "timeout": "60000",
                    "limit": "100",
                })
            )
            req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
            with urllib.request.urlopen(req, context=_ssl_ctx, timeout=90) as resp:
                changes = json.loads(resp.read())

            synced = 0
            for result in changes.get("results", []):
                doc_id = result["id"]
                if not should_sync(doc_id):
                    continue

                if result.get("deleted"):
                    delete_note(doc_id)
                    continue

                doc = result.get("doc", {})
                if doc.get("type") != "plain":
                    continue

                try:
                    content = fetch_note_content(doc)
                    if content is None:
                        continue
                    if write_note(doc_id, content, doc):
                        synced += 1
                        logger.info("Live update: %s", doc.get("path", doc_id))
                except Exception as exc:
                    logger.warning("Error syncing %s: %s", doc_id, exc)

            new_seq = changes.get("last_seq", since)
            if new_seq != since:
                since = str(new_seq)
                save_seq(since)
                if synced:
                    logger.info("Processed %d updates", synced)

        except KeyboardInterrupt:
            logger.info("Watch stopped by user")
            break
        except Exception as exc:
            logger.warning("Changes feed error: %s — retrying in 10s", exc)
            time.sleep(10)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sync Obsidian vault from CouchDB Livesync")
    parser.add_argument("--once", action="store_true", help="Single incremental sync then exit")
    parser.add_argument("--full", action="store_true", help="Full resync of all notes")
    parser.add_argument("--watch", action="store_true", help="Continuous sync via _changes feed")
    args = parser.parse_args()

    if not COUCHDB_PASS:
        logger.error("COUCHDB_PASS not set. Export it or add to .env")
        sys.exit(1)

    # Test connectivity
    try:
        db_info = _get("")
        logger.info("Connected to CouchDB: %s (%d docs)",
                     db_info.get("db_name"), db_info.get("doc_count", 0))
    except Exception as exc:
        logger.error("Cannot connect to CouchDB: %s", exc)
        sys.exit(1)

    if args.full:
        full_sync()
    elif args.watch:
        # Do an initial incremental catch-up, then watch
        incremental_sync()
        watch_continuous()
    elif args.once:
        seq = load_seq()
        if seq == "0":
            logger.info("No prior sync — doing full pull first")
            full_sync()
        else:
            incremental_sync()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
