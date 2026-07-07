"""
Memory hygiene agent — roadmap §7.

Weekly sweep that keeps the memory system trustworthy:

  1. concern-prune      — DELETE resolved agent_concerns older than 30 days
  2. concern-stale      — active concerns with no verification in 14 days → 'stale'
                          (upsert reopens them if an agent re-asserts)
  3. registry-dedupe    — deterministic: remove exact-duplicate bullet lines in
                          registry prose bodies (first occurrence wins)
  4. registry-review    — AI-assisted (local `claude` CLI, degrades gracefully):
                          per-file conservative review returning strict JSON of
                          redundant-duplicate line deletions + suspected-stale
                          facts. Stale facts are NEVER auto-deleted — they roll
                          up into ONE low-severity concern for operator judgment.

Guards (enforced by the applier regardless of AI output):
  - YAML frontmatter and "## From CRM" sections are never touched (sync-managed)
  - per-file deletion cap: min(15 lines, 20% of body) — else skip + concern
  - registry/ is gitignored, so a .bak sibling is written before any edit
  - line endings preserved per-line (registry files are mixed CRLF/LF)

Per the memory contract: journals via write_decision() when it changes anything;
a clean pass journals nothing. Reconciles its own concerns at the end of each run.

Run standalone:
    ./scripts/run_pipeline.sh agents/memory_hygiene.py [--dry-run] [--no-ai]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import psycopg  # noqa: E402
from blunderbus_memory.concerns import PostgresConcerns, _resolve_dsn  # noqa: E402
from blunderbus_memory.journal import write_decision  # noqa: E402
from blunderbus_memory.models import (  # noqa: E402
    Concern as PMConcern, ConcernStatus, Severity,
)

AGENT = "memory-hygiene"
TENANT = "blunderbus"
REGISTRY_DIR = ROOT / "memory" / "registry"

RESOLVED_TTL_DAYS = 30      # resolved concerns older than this are deleted
STALE_AFTER_DAYS = 14       # active concerns unverified this long → stale
MAX_AI_DELETES = 15         # absolute per-file cap on AI-suggested deletions
MAX_AI_DELETE_FRACTION = 0.20
MIN_LINES_FOR_AI = 15       # skip the AI pass on tiny files
CLAUDE_TIMEOUT = 120


# ── Concern table maintenance (direct SQL — lifecycle ops the store has no
#    method for; same DSN resolution as PostgresConcerns) ────────────────────


def prune_resolved(conn: psycopg.Connection, dry_run: bool) -> list[str]:
    with conn.cursor() as cur:
        if dry_run:
            cur.execute("""
                SELECT id FROM agent_concerns
                 WHERE tenant_id = %s AND status = 'resolved'
                   AND resolved_at < now() - %s * interval '1 day'
            """, (TENANT, RESOLVED_TTL_DAYS))
        else:
            cur.execute("""
                DELETE FROM agent_concerns
                 WHERE tenant_id = %s AND status = 'resolved'
                   AND resolved_at < now() - %s * interval '1 day'
             RETURNING id
            """, (TENANT, RESOLVED_TTL_DAYS))
        return [r[0] for r in cur.fetchall()]


def mark_stale(conn: psycopg.Connection, dry_run: bool) -> list[str]:
    with conn.cursor() as cur:
        if dry_run:
            cur.execute("""
                SELECT id FROM agent_concerns
                 WHERE tenant_id = %s AND status = 'active'
                   AND last_verified < now() - %s * interval '1 day'
            """, (TENANT, STALE_AFTER_DAYS))
        else:
            cur.execute("""
                UPDATE agent_concerns SET status = 'stale'
                 WHERE tenant_id = %s AND status = 'active'
                   AND last_verified < now() - %s * interval '1 day'
             RETURNING id
            """, (TENANT, STALE_AFTER_DAYS))
        return [r[0] for r in cur.fetchall()]


# ── Registry file structure helpers ──────────────────────────────────────────


def body_start_index(lines: list[str]) -> int:
    """First line index after YAML frontmatter (0 if no frontmatter)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def protected_indexes(lines: list[str]) -> set[int]:
    """Line indexes inside any '## From CRM' section (sync-managed — hands off)."""
    protected: set[int] = set()
    in_crm = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.lower().startswith("## from crm"):
            in_crm = True
        elif in_crm and s.startswith("## "):
            in_crm = False
        if in_crm:
            protected.add(i)
    return protected


def write_with_backup(path: Path, original: str, lines: list[str],
                      drop: set[int]) -> None:
    """Drop line indexes and rewrite. lines carry their own endings
    (splitlines(keepends=True)), so mixed CRLF/LF files survive intact.
    registry/ is gitignored → .bak sibling is the only rollback."""
    path.with_name(path.name + ".bak").write_text(original, encoding="utf-8",
                                                  newline="")
    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(kept))


# ── Pass 3: deterministic dedupe ─────────────────────────────────────────────


def find_duplicate_bullets(lines: list[str]) -> list[int]:
    start = body_start_index(lines)
    protected = protected_indexes(lines)
    seen: set[str] = set()
    dupes: list[int] = []
    for i in range(start, len(lines)):
        if i in protected:
            continue
        s = lines[i].strip()
        if not (s.startswith("- ") or s.startswith("* ")):
            continue
        if s in seen:
            dupes.append(i)
        else:
            seen.add(s)
    return dupes


# ── Pass 4: AI-assisted review ───────────────────────────────────────────────

AI_PROMPT = """You are a memory-hygiene reviewer for a personal infrastructure/household knowledge registry. Below is the file "{name}" with 1-based line numbers.

Identify, very conservatively:
1. "delete_lines": line numbers whose content is a redundant duplicate of another line in this file (the same fact restated). When in ANY doubt, do not include the line. Never include YAML frontmatter (between --- markers) or anything under a "## From CRM" heading.
2. "stale_facts": short verbatim quotes of facts that look outdated or superseded by a later line in the same file. Do NOT put these in delete_lines — they are for human review only.

Respond with ONLY strict JSON, no prose, no code fences:
{{"delete_lines": [], "stale_facts": []}}

FILE:
{numbered}
"""


def _cli_healthy(claude_cmd: str) -> bool:
    """One cheap probe so a broken CLI (e.g. expired OAuth) doesn't cost a
    failing subprocess per registry file."""
    try:
        r = subprocess.run(
            [claude_cmd, "--print", "--output-format", "text"],
            input="Reply with exactly: OK", capture_output=True, text=True,
            encoding="utf-8", timeout=60, cwd=os.path.expanduser("~"),
        )
        if r.returncode == 0 and r.stdout.strip():
            return True
        print(f"claude CLI unhealthy (rc={r.returncode}): "
              f"{(r.stdout or r.stderr or '')[:120]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"claude CLI probe failed: {exc}", file=sys.stderr)
    return False


def ai_review(path: Path, lines: list[str], claude_cmd: str
              ) -> tuple[list[int], list[str]] | None:
    numbered = "".join(f"{i + 1}: {ln}" if ln.endswith("\n") else f"{i + 1}: {ln}\n"
                       for i, ln in enumerate(lines))
    prompt = AI_PROMPT.format(name=path.name, numbered=numbered)
    try:
        r = subprocess.run(
            [claude_cmd, "--print", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=CLAUDE_TIMEOUT, cwd=os.path.expanduser("~"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {path.name}: claude CLI failed: {exc}", file=sys.stderr)
        return None
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  ! {path.name}: claude rc={r.returncode}", file=sys.stderr)
        return None
    text = r.stdout.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        deletes = [int(n) - 1 for n in data.get("delete_lines", [])]  # → 0-based
        stale = [str(s)[:200] for s in data.get("stale_facts", [])][:10]
        return deletes, stale
    except (ValueError, TypeError):
        print(f"  ! {path.name}: unparseable AI response", file=sys.stderr)
        return None


def guard_ai_deletes(lines: list[str], deletes: list[int]) -> tuple[set[int], str | None]:
    """Enforce structural guards on AI-suggested deletions.
    Returns (safe_indexes, skip_reason). skip_reason set ⇒ apply nothing."""
    start = body_start_index(lines)
    protected = protected_indexes(lines)
    safe = {i for i in deletes
            if start <= i < len(lines) and i not in protected}
    body_lines = max(1, len(lines) - start)
    cap = min(MAX_AI_DELETES, max(1, int(body_lines * MAX_AI_DELETE_FRACTION)))
    if len(safe) > cap:
        return set(), f"AI suggested {len(safe)} deletions (cap {cap}) — skipped"
    return safe, None


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report changes without writing files or the DB")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the claude-CLI review pass")
    ap.add_argument("--no-telegram", action="store_true",
                    help=argparse.SUPPRESS)  # run_pipeline.sh compat, unused
    args = ap.parse_args()
    dry = args.dry_run
    tag = "[dry-run] " if dry else ""
    changes: list[str] = []          # journal-worthy actions
    stale_facts_all: list[str] = []  # rolled into one concern
    skipped: list[str] = []          # files where guards refused AI edits

    # 1+2 — concern lifecycle
    pruned, staled = [], []
    try:
        with psycopg.connect(_resolve_dsn(), autocommit=True,
                             connect_timeout=5) as conn:
            pruned = prune_resolved(conn, dry)
            staled = mark_stale(conn, dry)
    except Exception as exc:  # noqa: BLE001
        print(f"! concern maintenance failed: {exc}", file=sys.stderr)
    if pruned:
        print(f"{tag}pruned {len(pruned)} resolved concern(s) "
              f">{RESOLVED_TTL_DAYS}d: {', '.join(pruned[:5])}"
              f"{'…' if len(pruned) > 5 else ''}")
        changes.append(f"pruned {len(pruned)} resolved concerns older than "
                       f"{RESOLVED_TTL_DAYS}d")
    if staled:
        print(f"{tag}marked {len(staled)} concern(s) stale "
              f"(unverified >{STALE_AFTER_DAYS}d): {', '.join(staled[:5])}"
              f"{'…' if len(staled) > 5 else ''}")
        changes.append(f"marked {len(staled)} unverified concerns stale: "
                       + ", ".join(staled[:8]))

    # 3+4 — registry files
    claude_cmd = None
    if not args.no_ai:
        try:
            from runtime import resolve_claude_command
            claude_cmd = resolve_claude_command()
        except Exception:  # noqa: BLE001
            pass
        if claude_cmd and not _cli_healthy(claude_cmd):
            claude_cmd = None
        if not claude_cmd:
            print("claude CLI unavailable — deterministic pass only")

    reg_files = sorted(REGISTRY_DIR.glob("*/*.md"))
    for path in reg_files:
        rel = path.relative_to(ROOT)
        original = path.read_bytes().decode("utf-8")
        lines = original.splitlines(keepends=True)

        drop = set(find_duplicate_bullets(lines))
        via = {i: "dupe" for i in drop}

        if claude_cmd and len(lines) >= MIN_LINES_FOR_AI:
            result = ai_review(path, lines, claude_cmd)
            if result:
                ai_deletes, stale = result
                stale_facts_all += [f"{path.name}: {s}" for s in stale]
                safe, skip_reason = guard_ai_deletes(lines, ai_deletes)
                if skip_reason:
                    skipped.append(f"{rel}: {skip_reason}")
                    print(f"  ! {rel}: {skip_reason}")
                else:
                    for i in safe - drop:
                        via[i] = "ai"
                    drop |= safe

        if not drop:
            continue
        detail = ", ".join(f"L{i + 1}({via[i]})" for i in sorted(drop))
        print(f"{tag}{rel}: removing {len(drop)} line(s): {detail}")
        if not dry:
            write_with_backup(path, original, lines, drop)
        changes.append(f"{rel}: removed {len(drop)} duplicate line(s) [{detail}]")

    # File/refresh concerns, reconcile our own set
    active_ids: list[str] = []
    try:
        with PostgresConcerns() as store:
            if stale_facts_all and not dry:
                cid = f"{AGENT}:registry:stale-facts:global"
                store.upsert(PMConcern(
                    id=cid, agent=AGENT, type="registry", target="registry",
                    severity=Severity.LOW, status=ConcernStatus.ACTIVE,
                    summary=(f"{len(stale_facts_all)} possibly-stale registry "
                             "fact(s) flagged for review — not auto-deleted"),
                    suggested_action="Review and prune manually; see payload.",
                    verifier="scripts/agents/memory_hygiene.py",
                    payload={"facts": stale_facts_all[:40]},
                ))
                active_ids.append(cid)
            if skipped and not dry:
                cid = f"{AGENT}:registry:guard-skip:global"
                store.upsert(PMConcern(
                    id=cid, agent=AGENT, type="registry", target="registry",
                    severity=Severity.LOW, status=ConcernStatus.ACTIVE,
                    summary=f"{len(skipped)} registry file(s) skipped — AI edit "
                            "exceeded safety caps",
                    suggested_action="Inspect files manually; see payload.",
                    verifier="scripts/agents/memory_hygiene.py",
                    payload={"skipped": skipped},
                ))
                active_ids.append(cid)
            if not dry:
                for rid, _ in store.reconcile(AGENT, active_ids):
                    print(f"auto-resolved own concern: {rid}")
    except Exception as exc:  # noqa: BLE001
        print(f"! concern sync failed: {exc}", file=sys.stderr)

    if stale_facts_all:
        filed = "would file concern" if dry else "concern filed"
        print(f"{tag}{len(stale_facts_all)} possibly-stale fact(s) flagged "
              f"({filed}, nothing deleted):")
        for s in stale_facts_all[:10]:
            print(f"    - {s}")

    # Journal — only when something actually changed (clean pass = silence)
    if changes and not dry:
        write_decision(
            agent=AGENT, target="memory",
            decision="applied",
            reasoning="Weekly hygiene sweep: " + "; ".join(changes[:12]),
            related=active_ids,
        )
        print(f"journaled {len(changes)} action(s)")

    print(f"{tag}done — {len(changes)} change(s), "
          f"{len(stale_facts_all)} stale-fact flag(s), {len(skipped)} skip(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
