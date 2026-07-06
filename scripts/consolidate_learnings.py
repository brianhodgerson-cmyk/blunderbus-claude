#!/usr/bin/env python3
"""
BlunderBus learnings consolidator.

Scans recent Daily notes, detects persistent issues / recurring patterns / new
flags, and rewrites memory/learnings.md so the agent carries forward what it
already knows instead of re-discovering the same problems daily.

Usage:
    py scripts/consolidate_learnings.py                    # last 7 days
    py scripts/consolidate_learnings.py --days 14
    py scripts/consolidate_learnings.py --dry-run
    py scripts/consolidate_learnings.py --from 2026-04-01 --to 2026-04-29

Output: memory/learnings.md  (atomic write; previous version saved as .bak)
"""
from __future__ import annotations
import argparse
import io
import re
import shutil
import sys

# Force UTF-8 stdout (Windows defaults to cp1252 and chokes on → emojis 🔴 etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "Daily"
MEMORY_DIR = ROOT / "memory"
LEARNINGS_FILE = MEMORY_DIR / "learnings.md"

# ── signal extraction ────────────────────────────────────────────────────────

# Patterns we look for in daily notes. Each maps a regex → category tag.
# Matched lines become "observation" records keyed by a normalized signature.
SIGNAL_PATTERNS = [
    # Infra warnings
    (r"\b(Stark|Thor|Cortex|Heimdall|Banner|Vision|Loki|Groot|Ultron|Hawkeye|Fury)\b.{0,80}\b(95%|9[6-9]%|100%|offline|unreachable|down|critical|red|🔴)",
     "infra"),
    # Persistent/multi-day callouts
    (r"flagged\s+(\d+)\s+consecutive\s+day", "persistent"),
    (r"PERSISTENT:?\s+(.+)", "persistent"),
    # Finance anomalies
    (r"(Spending pace|spending pace).{0,40}(\d+%|\d+x)", "finance"),
    (r"Net worth\s+\$[\d,]+", "finance-baseline"),
    (r"Savings rate.{0,40}(0\.0%|negative)", "finance"),
    # Security
    (r"SecOnion.{0,40}(empty|unreachable|failed|offline)", "security"),
    (r"Frigate.{0,40}(unreachable|offline|down)", "security"),
    # AI/Synthesis flags
    (r"🚩\s+(.+)", "synthesis-flag"),
    # Generic warnings/errors that survived to the note
    (r"WARN:?\s+(.+)", "warn"),
    (r"ERROR:?\s+(.+)", "error"),
]

# Substring fingerprints used to collapse similar lines into one signature.
# (We don't want "Stark at 95%" and "Stark at 96%" as two separate flags.)
SIGNATURE_NORMALIZERS = [
    (r"\d{2}:\d{2}(:\d{2})?", "<time>"),
    (r"\$[\d,]+(\.\d+)?", "$<amt>"),
    (r"\d+(\.\d+)?%", "<pct>"),
    (r"\d+\.\d+x", "<mult>"),
    (r"\d{4}-\d{2}-\d{2}", "<date>"),
    (r"\b\d{2,}\b", "<n>"),
]

def _normalize(text: str) -> str:
    s = text.strip()
    for pat, repl in SIGNATURE_NORMALIZERS:
        s = re.sub(pat, repl, s)
    return s.lower()[:120]


def parse_daily(note_path: Path) -> list[dict]:
    """Extract observation records from a single daily note."""
    obs = []
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  WARN: could not read {note_path.name}: {e}", file=sys.stderr)
        return obs

    note_date = note_path.stem  # "2026-04-29"
    for pat, category in SIGNAL_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            raw = m.group(0)[:200].strip()
            obs.append({
                "date": note_date,
                "category": category,
                "raw": raw,
                "signature": _normalize(raw),
            })
    return obs


def load_notes(days: int | None, since: date | None, until: date | None) -> list[Path]:
    """Pick the daily notes within the requested window."""
    if not DAILY_DIR.exists():
        return []
    all_notes = sorted(DAILY_DIR.glob("????-??-??.md"), reverse=True)
    if since or until:
        kept = []
        for p in all_notes:
            try:
                d = datetime.strptime(p.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if since and d < since:
                continue
            if until and d > until:
                continue
            kept.append(p)
        return sorted(kept, reverse=True)
    return all_notes[:days] if days else all_notes


# ── consolidation ────────────────────────────────────────────────────────────

def consolidate(notes: list[Path]) -> dict:
    """Group observations by signature, count appearances, classify."""
    by_sig: dict[str, dict] = defaultdict(lambda: {
        "dates": set(),
        "category": "",
        "samples": [],
    })
    for note in notes:
        for o in parse_daily(note):
            entry = by_sig[o["signature"]]
            entry["dates"].add(o["date"])
            entry["category"] = o["category"]
            if len(entry["samples"]) < 3:
                entry["samples"].append(o["raw"])

    today = date.today()
    consolidated = []
    for sig, data in by_sig.items():
        date_objs = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in data["dates"])
        if not date_objs:
            continue
        first_seen = date_objs[0]
        last_seen = date_objs[-1]
        days_seen = len(date_objs)
        days_since_last = (today - last_seen).days

        # Classify
        if days_seen >= 3 and days_since_last <= 1:
            status = "persistent"
        elif days_seen >= 2 and days_since_last >= 2:
            status = "resolved"
        elif days_seen == 1 and days_since_last == 0:
            status = "new"
        else:
            status = "intermittent"

        consolidated.append({
            "signature": sig,
            "category": data["category"],
            "days_seen": days_seen,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
            "days_since_last": days_since_last,
            "status": status,
            "sample": data["samples"][0] if data["samples"] else "",
        })

    return {
        "as_of": today.isoformat(),
        "notes_scanned": len(notes),
        "window_start": notes[-1].stem if notes else None,
        "window_end": notes[0].stem if notes else None,
        "observations": sorted(consolidated, key=lambda x: (-x["days_seen"], x["category"])),
    }


# ── render ───────────────────────────────────────────────────────────────────

def render_markdown(result: dict) -> str:
    lines = [
        "# BlunderBus Learnings",
        "",
        f"_Last consolidated: {result['as_of']} · scanned {result['notes_scanned']} daily notes · window {result['window_start']} → {result['window_end']}_",
        "",
    ]

    by_status: dict[str, list[dict]] = defaultdict(list)
    for o in result["observations"]:
        by_status[o["status"]].append(o)

    def block(title: str, status: str, emoji: str, max_items: int = 20):
        items = by_status.get(status, [])
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")
        for o in items[:max_items]:
            lines.append(
                f"- {emoji} **{o['category']}** — {o['sample']}  \n"
                f"  _seen {o['days_seen']}× · first {o['first_seen']} · last {o['last_seen']}_"
            )
        if len(items) > max_items:
            lines.append(f"- _… {len(items) - max_items} more truncated_")
        lines.append("")

    block("Active concerns (3+ consecutive days, still showing)", "persistent", "🔴")
    block("New this run", "new", "🆕")
    block("Resolved (was active, gone for 2+ days)", "resolved", "✅")
    block("Intermittent (sporadic, watch)", "intermittent", "🟡")

    if not any(by_status.values()):
        lines.append("_No structured signals detected in the scanned window._")
        lines.append("")

    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    p.add_argument("--from", dest="since", type=date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--to", dest="until", type=date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="print to stdout, don't write")
    args = p.parse_args()

    days = None if (args.since or args.until) else args.days
    notes = load_notes(days, args.since, args.until)
    if not notes:
        print("No daily notes found in window.", file=sys.stderr)
        return 1

    print(f"Scanning {len(notes)} daily note(s): {notes[-1].stem} → {notes[0].stem}")
    result = consolidate(notes)
    md = render_markdown(result)

    print()
    print(f"Observations: {len(result['observations'])}")
    by_status: dict[str, int] = defaultdict(int)
    for o in result["observations"]:
        by_status[o["status"]] += 1
    for s in ("persistent", "new", "resolved", "intermittent"):
        if by_status[s]:
            print(f"  {s:14s} {by_status[s]}")

    if args.dry_run:
        print()
        print("─── DRY RUN — would write ───")
        print(md)
        return 0

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if LEARNINGS_FILE.exists():
        shutil.copy2(LEARNINGS_FILE, LEARNINGS_FILE.with_suffix(".md.bak"))
    LEARNINGS_FILE.write_text(md, encoding="utf-8")
    print(f"Wrote {LEARNINGS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
