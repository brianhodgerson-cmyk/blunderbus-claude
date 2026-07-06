#!/usr/bin/env python3
"""
Infra learnings consolidator.

Scans the ## Infrastructure block of recent daily notes, identifies persistent
host-down / container-unhealthy / monitoring-offline patterns, and rewrites
memory/infra/learnings.md so the infra-agent carries multi-day signal forward
instead of re-discovering "Stark RAM at 95% for 4 days" every morning.

Counterpart of consolidate_finance_learnings.py — same atomic-write + .bak
pattern. Runs daily 5:52 AM (between FinanceLearnings 5:50 and the generic
LearningsConsolidate 5:55).

Usage:
    py scripts/consolidate_infra_learnings.py
    py scripts/consolidate_infra_learnings.py --days 14
    py scripts/consolidate_infra_learnings.py --dry-run
"""
from __future__ import annotations
import argparse
import io
import re
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "Daily"
MEMORY_DIR = ROOT / "memory" / "infra"
LEARNINGS_FILE = MEMORY_DIR / "learnings.md"

HOSTS = ["Cortex", "Stark", "Thor", "Banner", "Heimdall", "Vision", "Loki",
         "Multiverse", "Groot", "Hawkeye", "Ultron", "Fury", "Jarvis"]


# ── Signal extraction (infrastructure-flavored) ──────────────────────────────

# Each pattern → (regex, category, severity)
INFRA_PATTERNS: list[tuple[str, str, str]] = [
    # Host offline / unreachable
    (r"~~(" + "|".join(HOSTS) + r")~~\s*\|.*?(❌|offline|unreachable)", "host-down", "high"),
    (r"\b(" + "|".join(HOSTS) + r")\b[^\n]{0,40}\b(offline|unreachable|down|❌)", "host-down", "high"),

    # Resource pressure
    (r"\b(" + "|".join(HOSTS) + r")\b[^\n]*?\b(9[0-9]|100)%\s*🔴", "host-resource-red", "high"),
    (r"\b(" + "|".join(HOSTS) + r")\b[^\n]*?\b(8[5-9])%\s*🟡", "host-resource-yellow", "medium"),

    # Container unhealthy
    (r"(\d+)\s+unhealthy", "container-unhealthy", "medium"),
    (r"\b(Stark|Cortex)\b[^\n]*?(\d+)\s+(?:unhealthy|❌)", "container-unhealthy", "medium"),

    # Monitoring / observability gaps
    (r"monitoring offline|prometheus.*offline|grafana.*unreachable", "monitoring-gap", "medium"),
    (r"SecOnion\s+(?:unreachable|offline|api[_ ]?key empty)", "security-gap", "medium"),
    (r"Frigate\s+(?:NVR\s+)?(?:unreachable|offline)", "camera-gap", "low"),

    # Storage
    (r"ZFS\s+pool\s+\S+\s+(?:DEGRADED|FAULTED|OFFLINE|UNAVAIL)", "storage", "critical"),
    (r"\bnas-pool\b[^\n]*?(?:DEGRADED|FAULTED)", "storage", "critical"),

    # Synthesis / persistent flags from prior briefs
    (r"PERSISTENT:?\s+(.+)", "persistent", "high"),
    (r"🚩\s+([A-Z][^\n]{10,150}offline|🚩\s+[A-Z][^\n]{10,150}redlin)", "synthesis-flag", "medium"),
]

# Normalizers collapse "Stark at 95%" and "Stark at 96%" into one signature
NORMALIZERS = [
    (r"\d{2}:\d{2}(:\d{2})?", "<time>"),
    (r"\d+(\.\d+)?%", "<pct>"),
    (r"\d{4}-\d{2}-\d{2}", "<date>"),
    (r"`[█░]+`", "<bar>"),
    (r"\d{3,}\b", "<n>"),     # 3+ digit numbers (counts, ports, etc.)
    (r"~~|\*\*|`|\|", " "),    # strip markdown noise
]


def _normalize(text: str) -> str:
    s = text.strip()
    for pat, repl in NORMALIZERS:
        s = re.sub(pat, repl, s)
    return re.sub(r"\s+", " ", s.lower())[:140]


def _extract_infra_block(note_path: Path) -> str:
    """Return just the ## Infrastructure section of a daily note (or empty)."""
    try:
        text = note_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # Match "## Infrastructure" until the next "## " or EOF
    m = re.search(r"^## Infrastructure.*?$(.*?)(?=^## |\Z)", text,
                  flags=re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def parse_daily(note_path: Path) -> list[dict]:
    obs = []
    block = _extract_infra_block(note_path)
    if not block:
        return obs
    note_date = note_path.stem
    for pat, category, severity in INFRA_PATTERNS:
        for m in re.finditer(pat, block, flags=re.IGNORECASE):
            raw = m.group(0)[:200].strip()
            sig = _normalize(raw)
            obs.append({
                "date": note_date,
                "category": category,
                "severity": severity,
                "raw": raw,
                "signature": sig,
            })
    return obs


def load_notes(days: int) -> list[Path]:
    if not DAILY_DIR.exists():
        return []
    return sorted(DAILY_DIR.glob("????-??-??.md"), reverse=True)[:days]


# ── Consolidation ────────────────────────────────────────────────────────────


def consolidate(notes: list[Path]) -> dict:
    by_sig: dict[str, dict] = defaultdict(lambda: {
        "dates": set(), "category": "", "severity": "", "samples": [],
    })
    for note in notes:
        for o in parse_daily(note):
            entry = by_sig[o["signature"]]
            entry["dates"].add(o["date"])
            entry["category"] = o["category"]
            # keep the worst severity seen for this signature
            sev_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if sev_order.get(o["severity"], 0) > sev_order.get(entry["severity"], 0):
                entry["severity"] = o["severity"]
            if len(entry["samples"]) < 3:
                entry["samples"].append(o["raw"])

    today = date.today()
    consolidated = []
    for sig, data in by_sig.items():
        date_objs = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in data["dates"])
        if not date_objs:
            continue
        first_seen, last_seen = date_objs[0], date_objs[-1]
        days_seen = len(date_objs)
        days_since_last = (today - last_seen).days

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
            "severity": data["severity"] or "medium",
            "days_seen": days_seen,
            "first_seen": first_seen.isoformat(),
            "last_seen": last_seen.isoformat(),
            "days_since_last": days_since_last,
            "status": status,
            "sample": data["samples"][0] if data["samples"] else "",
        })

    # Sort: critical first, then by days_seen desc
    sev_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    consolidated.sort(key=lambda x: (-sev_order.get(x["severity"], 0), -x["days_seen"]))

    return {
        "as_of": today.isoformat(),
        "notes_scanned": len(notes),
        "window_start": notes[-1].stem if notes else None,
        "window_end": notes[0].stem if notes else None,
        "observations": consolidated,
    }


# ── Render ───────────────────────────────────────────────────────────────────


def _clean_for_display(sample: str) -> str:
    """Tighten the raw signature into something readable in the brief."""
    s = sample
    s = re.sub(r"~~([A-Z][a-zA-Z]+)~~", r"\1", s)        # Strip strikethrough markdown
    s = re.sub(r"\|.*?\|.*?\|.*?\|", "", s)              # Strip table rows
    s = re.sub(r"\*\*", "", s)                            # Bold markers
    s = re.sub(r"`+", "", s)                              # Code ticks
    s = re.sub(r"\s+", " ", s).strip()
    return s[:140]


def render_markdown(result: dict) -> str:
    lines = [
        "# Infra Learnings (auto-consolidated)",
        "",
        f"_Last consolidated: {result['as_of']} · scanned {result['notes_scanned']} daily notes · window {result['window_start']} → {result['window_end']}_",
        "",
    ]

    by_status: dict[str, list[dict]] = defaultdict(list)
    for o in result["observations"]:
        by_status[o["status"]].append(o)

    sev_emoji = {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢"}

    def block(title: str, status: str, max_items: int = 30):
        items = by_status.get(status, [])
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")
        for o in items[:max_items]:
            emoji = sev_emoji.get(o["severity"], "🟡")
            sample = _clean_for_display(o["sample"])
            lines.append(
                f"- {emoji} **{o['category']}** ({o['severity']}) · {sample}  \n"
                f"  _seen {o['days_seen']}× · first {o['first_seen']} · last {o['last_seen']}_"
            )
        if len(items) > max_items:
            lines.append(f"- _… {len(items) - max_items} more truncated_")
        lines.append("")

    block("Active concerns (3+ consecutive days, still showing)", "persistent")
    block("New this run", "new")
    block("Resolved (was active, gone for 2+ days)", "resolved")
    block("Intermittent (sporadic, watch)", "intermittent")

    if not any(by_status.values()):
        lines.append("_No infrastructure signals detected in the scanned window._")
        lines.append("")

    return "\n".join(lines)


# ── Atomic write ─────────────────────────────────────────────────────────────


def atomic_write(path: Path, content: str):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(content, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    print(f"Infra learnings consolidation @ {datetime.now().isoformat(timespec='seconds')}")
    notes = load_notes(args.days)
    if not notes:
        print("  No daily notes found", file=sys.stderr)
        return 1
    print(f"  Scanning {len(notes)} note(s): {notes[-1].stem} → {notes[0].stem}")

    result = consolidate(notes)
    md = render_markdown(result)

    by_status: dict[str, int] = defaultdict(int)
    for o in result["observations"]:
        by_status[o["status"]] += 1
    print(f"  Observations: {len(result['observations'])}")
    for s in ("persistent", "new", "resolved", "intermittent"):
        if by_status[s]:
            print(f"    {s:14s} {by_status[s]}")

    if args.dry_run:
        print("\n─── DRY RUN ───\n")
        print(md)
        return 0

    atomic_write(LEARNINGS_FILE, md)
    print(f"  ✓ wrote {LEARNINGS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
