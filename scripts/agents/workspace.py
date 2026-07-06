"""
Workspace domain agent.

Wraps Google Workspace (Calendar, Gmail, Tasks) via the `gws` CLI and Obsidian
task carry-forward into a standardized AgentReport. Calendar/Gmail/Tasks fetched
in parallel — each is an independent network call.

Memory:
  - memory/workspace/people.md       (correspondent context, triage rules)
  - memory/workspace/learnings.md    (auto-consolidated patterns, future)

Run standalone:
    py scripts/agents/workspace.py
    py scripts/agents/workspace.py --json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "agents"))

from base import AgentReport, Concern, parse_carried_from_learnings  # noqa: E402

MEMORY_DIR = ROOT / "memory" / "workspace"
LEARNINGS    = MEMORY_DIR / "learnings.md"
PEOPLE       = MEMORY_DIR / "people.md"
PROJECTS     = MEMORY_DIR / "projects.md"
RECURRING    = MEMORY_DIR / "recurring.md"
DECISIONS    = MEMORY_DIR / "decisions.md"
COMMITMENTS  = MEMORY_DIR / "commitments.md"
DATA_CONV    = MEMORY_DIR / "data-conventions.md"
TASKS_FILE   = ROOT / "TASKS.md"


# ── gws CLI helpers ──────────────────────────────────────────────────────────


def _gws_path() -> str | None:
    """Locate the `gws` CLI — npm installs as a .cmd shim on Windows."""
    p = shutil.which("gws.cmd") or shutil.which("gws")
    if p:
        return p
    for c in (
        os.path.expandvars(r"%APPDATA%\npm\gws.cmd"),
        r"C:\Users\brian\AppData\Roaming\npm\gws.cmd",
    ):
        if os.path.exists(c):
            return c
    return None


def _gws_json(args: list[str], timeout: int = 12) -> dict | None:
    """Run gws and parse JSON. Returns None on any failure."""
    gws = _gws_path()
    if not gws:
        return None
    try:
        r = subprocess.run([gws] + args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8")
        if r.returncode != 0 or not r.stdout.strip():
            return None
        # gws emits a keyring banner on stderr; stdout is clean JSON
        # but sometimes a leading line — parse the last JSON-looking block
        text = r.stdout.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Find first '{' or '['
            for i, ch in enumerate(text):
                if ch in "{[":
                    return json.loads(text[i:])
            return None
    except (subprocess.TimeoutExpired, Exception):
        return None


# ── Collectors ───────────────────────────────────────────────────────────────


def collect_calendar(today: date) -> list[dict]:
    """Today's calendar events from primary calendar."""
    t0 = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    params = {
        "calendarId": "primary",
        "timeMin": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeMax": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    data = _gws_json(["calendar", "events", "list",
                      "--params", json.dumps(params),
                      "--format", "json"])
    if not data:
        return []
    items = data.get("items", []) if isinstance(data, dict) else []
    out = []
    for ev in items:
        start = (ev.get("start", {}).get("dateTime")
                 or ev.get("start", {}).get("date") or "")
        out.append({
            "summary": ev.get("summary", "(no title)"),
            "start": start,
            "location": ev.get("location", ""),
            "attendees": [a.get("email", "") for a in ev.get("attendees", [])],
        })
    return out


def collect_unread_email(max_results: int = 5) -> dict:
    """Unread count + top N senders/subjects.

    Filter is `is:unread in:inbox` (not just `is:unread`) — that matches what
    operators mean by "unread inbox" and ignores unread items already filtered
    to Promotions, Updates, Forums, etc.

    Gmail's `resultSizeEstimate` can be wildly stale when `maxResults` is tiny
    (seen: estimate=201 with only 7 actual unread inbox messages returned once
    maxResults>=10). Fetch a larger page and use the returned count when the
    result set is exhausted.
    """
    fetch_limit = max(50, max_results)
    list_params = {"userId": "me", "q": "is:unread in:inbox", "maxResults": fetch_limit}
    list_data = _gws_json(["gmail", "users", "messages", "list",
                           "--params", json.dumps(list_params),
                           "--format", "json"])
    if not list_data:
        return {"count": None, "top": []}

    msgs = list_data.get("messages", []) if isinstance(list_data, dict) else []
    if isinstance(list_data, dict) and not list_data.get("nextPageToken"):
        total = len(msgs)
    else:
        total = list_data.get("resultSizeEstimate", len(msgs)) if isinstance(list_data, dict) else len(msgs)
    top = []
    for m in msgs[:max_results]:
        detail = _gws_json(["gmail", "users", "messages", "get",
                            "--params", json.dumps({
                                "userId": "me", "id": m["id"],
                                "format": "metadata",
                                "metadataHeaders": ["From", "Subject", "Date"],
                            }),
                            "--format", "json"], timeout=8)
        if not detail:
            continue
        headers = {h["name"]: h["value"]
                   for h in detail.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "?").split("<")[0].strip().strip('"')
        top.append({
            "from": sender,
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
        })
    return {"count": total, "top": top}


def collect_tasks() -> list[dict]:
    """Pending Google Tasks. Auto-discovers default task list."""
    # First, list the user's task lists to find the default
    lists = _gws_json(["tasks", "tasklists", "list", "--format", "json"])
    if not lists:
        return []
    list_id = None
    items = lists.get("items", []) if isinstance(lists, dict) else []
    if items:
        list_id = items[0].get("id")
    if not list_id:
        return []

    data = _gws_json(["tasks", "tasks", "list",
                      "--params", json.dumps({"tasklist": list_id, "showCompleted": False}),
                      "--format", "json"])
    if not data:
        return []
    out = []
    for t in (data.get("items", []) if isinstance(data, dict) else []):
        out.append({
            "title": t.get("title", "(untitled)"),
            "due": t.get("due", ""),
            "notes": t.get("notes", "")[:120],
        })
    return out


def _push_external_tasks(gtasks: list[dict]) -> None:
    """Push the agent's external-task picture to the ops UI.
    Tolerant of failures — never blocks the agent run.
    """
    import hashlib
    import json
    import ssl
    import urllib.request
    from datetime import datetime as _dt

    api_base = os.environ.get("BBM_OPS_API_BASE", "https://ops.hodgespot.com")
    api_key  = os.environ.get("BBM_API_KEY", "")

    items: list[dict] = []
    for t in (gtasks or []):
        title = t.get("title", "(untitled)")
        due = t.get("due", "") or ""
        notes = t.get("notes", "") or ""
        # Stable id so repeated runs upsert the same row visually
        sid = hashlib.sha1(f"google-tasks::{title}::{due}".encode()).hexdigest()[:10]
        items.append({
            "id": sid,
            "text": title,
            "source": "google-tasks",
            "section": "Google Tasks",
            "done": False,  # we filtered out completed at fetch time
            "metadata": {"due": due, "notes": notes[:120]},
        })

    payload = {
        "agent": "workspace",
        "captured_at": _dt.now().astimezone().isoformat(timespec="seconds"),
        "tasks": items,
    }
    try:
        req = urllib.request.Request(
            f"{api_base}/api/tasks/external",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"X-API-Key": api_key} if api_key else {}),
            },
            method="POST",
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                print(f"  ✓ pushed {len(items)} external task(s) to ops UI")
            else:
                print(f"  ✗ external-tasks push HTTP {resp.status}")
    except Exception as exc:
        print(f"  ✗ external-tasks push failed (non-blocking): {exc}")


def collect_obsidian_carried_tasks() -> list[str]:
    """Read TASKS.md for tasks carried from prior daily notes (filesystem only —
    fast, no API)."""
    if not TASKS_FILE.exists():
        return []
    try:
        text = TASKS_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- [ ]"):
            out.append(s[5:].strip())
    return out


# ── Concern building ─────────────────────────────────────────────────────────


def _classify_email_severity(sender: str, subject: str) -> str:
    """Triage rules from memory/workspace/people.md applied as code."""
    s_lower = (sender or "").lower()
    sub_lower = (subject or "").lower()
    if any(k in sub_lower for k in ("k-1", "1040", "roth", "tax amendment")):
        return "high"
    if any(k in s_lower for k in ("noreply", "no-reply", "notifications", "newsletter")):
        return "low"
    if any(name in s_lower for name in ("sheila", "chris", "vanessa", "rusty",
                                         "mike hess", "jamie")):
        return "medium"
    return "low"


def _build_concerns(events: list[dict], email: dict, gtasks: list[dict],
                    carried_tasks: list[str], today: date) -> list[Concern]:
    out: list[Concern] = []

    # Calendar: meetings with no agenda or starting in next 30 min are highest priority
    now = datetime.now(timezone.utc)
    for ev in events:
        start_str = ev.get("start", "")
        if not start_str:
            continue
        # Parse start time
        try:
            if "T" in start_str:
                ev_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            else:
                continue   # all-day event
        except ValueError:
            continue
        delta_min = (ev_dt - now).total_seconds() / 60
        if 0 <= delta_min <= 30:
            out.append(Concern(
                severity="high",
                summary=f"Meeting in {int(delta_min)}m: {ev['summary']}",
                category="calendar-imminent",
                metric={"start": start_str, "title": ev["summary"]},
                source="gws calendar",
            ))

    # Email: high-severity unread (tax, family, key contacts)
    for m in email.get("top", []):
        sev = _classify_email_severity(m.get("from", ""), m.get("subject", ""))
        if sev in ("high",):
            out.append(Concern(
                severity=sev,
                summary=f"Unread {sev}: {m.get('from','?')[:30]} — {m.get('subject','?')[:60]}",
                category="email-priority",
                source="gws gmail + people.md triage",
            ))

    # Inbox volume signal
    cnt = email.get("count")
    if cnt and cnt > 50:
        out.append(Concern(
            severity="medium",
            summary=f"{cnt} unread emails — inbox triage needed",
            category="email-volume",
            metric={"unread_count": cnt},
            suggested_action="Run inbox triage: archive newsletters, respond to priorities.",
            source="gws gmail",
        ))

    # Carried Obsidian tasks not progressing
    if len(carried_tasks) >= 5:
        out.append(Concern(
            severity="medium",
            summary=f"{len(carried_tasks)} tasks open in TASKS.md — review and triage",
            category="task-backlog",
            metric={"open_count": len(carried_tasks)},
            source="TASKS.md",
        ))

    # Slipping commitments (past their date)
    from datetime import datetime as _dt, date as _date
    for c in _load_commitments():
        by = c.get("by", "").strip()
        if not by or by.lower() in ("ongoing", "tbd"):
            continue
        # Try parsing common date formats
        target = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d %Y", "%B %d %Y"):
            try:
                target = _dt.strptime(by, fmt).date()
                break
            except ValueError:
                continue
        if target and target < today:
            days_late = (today - target).days
            sev = "critical" if days_late > 14 else "high" if days_late > 3 else "medium"
            out.append(Concern(
                severity=sev,
                summary=f"Commitment slipping: \"{c['promise']}\" to {c['to']} (was due {by}, {days_late}d late)",
                category="commitment-slip",
                metric={"promise": c["promise"], "to": c["to"], "due": by, "days_late": days_late},
                source="commitments.md",
            ))

    return out


def _build_metrics(events, email, gtasks, carried) -> dict:
    return {
        "calendar_events_today": len(events),
        "email_unread": email.get("count"),
        "email_priority": sum(1 for m in email.get("top", [])
                              if _classify_email_severity(m.get("from"), m.get("subject")) == "high"),
        "google_tasks_open": len(gtasks),
        "obsidian_tasks_open": len(carried),
    }


def _headline(metrics: dict, real: list[Concern]) -> str:
    parts = []
    ev = metrics.get("calendar_events_today")
    if ev is not None:
        parts.append(f"{ev} event(s) today")
    em = metrics.get("email_unread")
    if em is not None:
        parts.append(f"{em} unread")
    tk = (metrics.get("google_tasks_open") or 0) + (metrics.get("obsidian_tasks_open") or 0)
    if tk:
        parts.append(f"{tk} task(s) open")
    base = " · ".join(parts) if parts else "no data"
    if real:
        return f"{base} · {len(real)} concern(s)"
    return f"{base} · no priorities flagged"


def _emit_structured_questions():
    """Build structured Question objects for Path C (Discord question threads).

    Mirrors the finance agent's pattern. Each open project blocker becomes one
    Question row in `agent_questions`, keyed by `workspace:blocker:<project>:<slug>`.

    Stable ID is critical: re-emitting on subsequent runs upserts the same row
    (no duplicates), and reconciliation marks a question `stale` if the agent
    stops emitting it (blocker resolved out-of-band).
    """
    try:
        from blunderbus_memory import (
            ProjectStatus, Question, QuestionStatus, QuestionTargetKind,
            get_default_registry,
        )
    except Exception:
        return []

    import re
    out = []
    try:
        reg = get_default_registry()
        for p in reg.projects.list():
            if p.status not in (ProjectStatus.ACTIVE, ProjectStatus.BLOCKED):
                continue
            for idx, blocker in enumerate(p.blockers[:5]):
                # Stable slug derived from the blocker text — first 5 keyword-y words
                slug = re.sub(r"[^a-z0-9]+", "-", blocker.lower())[:48].strip("-") or f"b{idx}"
                qid = f"workspace:blocker:{p.id}:{slug}"
                out.append(Question(
                    id=qid,
                    agent="workspace",
                    question_type="project-blocker",
                    target_kind=QuestionTargetKind.PROJECT,
                    target_id=p.id,
                    target_field="blockers",  # writer will move it from blockers → resolved_blockers
                    prompt=f"**{p.name}** blocker:\n\n  > {blocker}",
                    suggested_format=(
                        "free text — describe how the blocker is resolved "
                        "(e.g. \"concurrent filing\", \"Joe Smith EA is preparing\"), "
                        "or reply `skip` / `still blocked` to leave it"
                    ),
                    status=QuestionStatus.OPEN,
                    payload={"blocker_text": blocker, "blocker_index": idx,
                             "project_name": p.name},
                ))
    except Exception as exc:
        print(f"  ⚠ workspace structured question emit failed: {exc}", file=sys.stderr)
    return out


def _carry_questions_from_memory() -> list[str]:
    """Derive open questions for the operator.

    Source of truth, in priority order:
      1. blunderbus_memory registry — projects with blockers, people with
         unknown/missing fields, accounts without owners.
      2. legacy markdown ## Open questions sections (fallback during transition).

    The registry path means a question disappears the moment the underlying
    field gets filled — no manual question-pruning needed.
    """
    import re
    qs: list[str] = []

    # ── Primary: derive from registry ────────────────────────────────────────
    try:
        from blunderbus_memory import (  # noqa: E402
            ProjectStatus, get_default_registry,
        )
        reg = get_default_registry()

        # Projects with explicit blockers
        for p in reg.projects.list():
            if p.status not in (ProjectStatus.ACTIVE, ProjectStatus.BLOCKED):
                continue
            for blocker in p.blockers[:3]:
                qs.append(f"[{p.id}] {blocker}")

        # Accounts without confirmed ownership
        for a in reg.accounts.all():
            if (a.owner or "").upper() == "UNKNOWN":
                qs.append(f"[accounts] Confirm owner of `{a.name} (...{a.last_four})`")

        # People with placeholder/unknown last name
        for person in reg.people.all():
            if "(last name unknown)" in (person.full_name or "").lower():
                qs.append(f"[people] {person.full_name.split('(')[0].strip()} — full name?")
    except Exception as exc:
        print(f"  ⚠ registry-backed questions failed: {exc}", file=sys.stderr)

    # ── Fallback: legacy markdown (only fields not in registry yet) ──────────
    legacy_sources = [(PEOPLE, "people"), (DECISIONS, "decisions"),
                      (COMMITMENTS, "commitments"), (DATA_CONV, "data-conventions")]
    for path, tag in legacy_sources:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"^## Open questions.*?$(.*?)(?=^## |\Z)", text,
                      flags=re.MULTILINE | re.DOTALL)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            if line.startswith("- [ ]"):
                qs.append(f"[{tag}] {line[5:].strip()}")

    # De-dupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for q in qs:
        key = q.lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(q)
    return deduped[:8]


def _load_active_projects() -> list[dict]:
    """Pull project markers from projects.md so the agent can route signals.
    Returns list of {name, state, markers: [str]}."""
    import re
    if not PROJECTS.exists():
        return []
    try:
        text = PROJECTS.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    # Each project is a "### `name` — title" block under a state header
    for m in re.finditer(r"^###\s+`([\w-]+)`\s+—\s+(.+?)$", text, flags=re.MULTILINE):
        name = m.group(1)
        title = m.group(2).strip()
        # Markers line within ~12 lines of header
        block_start = m.end()
        block = text[block_start:block_start + 1500]
        markers_m = re.search(r"\*\*Email/calendar markers:\*\*\s*(.+?)$",
                              block, flags=re.MULTILINE)
        markers_line = markers_m.group(1).strip() if markers_m else ""
        markers = [t.strip().strip('"`') for t in re.findall(r'"([^"]+)"', markers_line)]
        out.append({"name": name, "title": title, "markers": markers})
    return out


def _load_commitments() -> list[dict]:
    """Pull open commitments from commitments.md. Returns list of {text, by_date}."""
    import re
    from datetime import date as _date
    if not COMMITMENTS.exists():
        return []
    try:
        text = COMMITMENTS.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out = []
    # Format: - [ ] **promise** · to person · by date · _logged YYYY-MM-DD_
    for m in re.finditer(r"^-\s+\[\s*\]\s+\*\*([^*]+)\*\*\s*(?:·\s+to\s+([^·]+))?(?:·\s+by\s+([^·]+))?",
                         text, flags=re.MULTILINE):
        out.append({
            "promise": m.group(1).strip(),
            "to": (m.group(2) or "").strip(),
            "by": (m.group(3) or "").strip(),
        })
    return out


def run(today: date | None = None) -> AgentReport:
    started = datetime.now()
    today = today or date.today()

    try:
        if not _gws_path():
            return AgentReport(
                agent="workspace",
                status="degraded",
                as_of=datetime.now(),
                headline="gws CLI not found — workspace data unavailable",
                error="gws.cmd missing from PATH and %APPDATA%\\npm",
                duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            )

        # Three independent network calls — fan out
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_cal   = pool.submit(collect_calendar, today)
            f_email = pool.submit(collect_unread_email, 5)
            f_tasks = pool.submit(collect_tasks)
            f_obs   = pool.submit(collect_obsidian_carried_tasks)
            events  = f_cal.result()
            email   = f_email.result()
            gtasks  = f_tasks.result()
            carried = f_obs.result()

        real = _build_concerns(events, email, gtasks, carried, today)
        carried_concerns = parse_carried_from_learnings(LEARNINGS) if LEARNINGS.exists() else []
        metrics = _build_metrics(events, email, gtasks, carried)
        questions = _carry_questions_from_memory()

        # Push to Postgres agent_concerns for persistence + auto-resolution
        try:
            from concerns_sync import sync as _sync_concerns  # noqa: E402
            _sync_concerns("workspace", real)
        except Exception as exc:
            print(f"  ⚠ workspace concerns sync skipped: {exc}", file=sys.stderr)

        # Path C: push structured Questions for project blockers to agent_questions
        # so the Discord bot can surface them as threads. Non-fatal on failure.
        try:
            from questions_sync import sync as _sync_questions  # noqa: E402
            structured_qs = _emit_structured_questions()
            _sync_questions("workspace", structured_qs)
        except Exception as exc:
            print(f"  ⚠ workspace questions sync skipped: {exc}", file=sys.stderr)

        # Push the broader task picture (Google Tasks, etc.) to the ops UI.
        # TASKS.md is read live by the UI itself, so we only push sources
        # that aren't otherwise visible to the dashboard.
        try:
            _push_external_tasks(gtasks)
        except Exception as exc:
            print(f"  ⚠ workspace external-tasks push skipped: {exc}", file=sys.stderr)

        # Status: degraded when the workspace collector itself is unhealthy or
        # when it emits high/critical operator-facing concerns. Avoid confusing
        # report rows like "🔴 ok".
        gws_ok = email.get("count") is not None or events or gtasks
        if not gws_ok:
            status = "degraded"
        elif real:
            status = "degraded"
        else:
            status = "ok"

        memory_consulted = []
        for f in (LEARNINGS, PEOPLE, PROJECTS, RECURRING,
                  DECISIONS, COMMITMENTS, DATA_CONV):
            if f.exists():
                memory_consulted.append(str(f.relative_to(ROOT)).replace("\\", "/"))
        if TASKS_FILE.exists():
            memory_consulted.append("TASKS.md")

        elapsed = int((datetime.now() - started).total_seconds() * 1000)
        return AgentReport(
            agent="workspace",
            status=status,
            as_of=datetime.now(),
            headline=_headline(metrics, real),
            real_concerns=real,
            carried_concerns=carried_concerns,
            expected_events=[],
            metrics=metrics,
            questions=questions,
            raw_data={
                "events": events[:10],
                "email_top": email.get("top", []),
                "google_tasks": gtasks[:10],
                "obsidian_tasks_sample": carried[:10],
            },
            memory_consulted=memory_consulted,
            duration_ms=elapsed,
        )
    except Exception as exc:
        return AgentReport.failed("workspace", str(exc), started)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _print_human(r: AgentReport) -> None:
    print(f"\n=== workspace-agent · {r.status_emoji} {r.status.upper()} · {r.duration_ms}ms ===")
    print(f"Headline: {r.headline}")
    if r.error:
        print(f"ERROR: {r.error}")
        return
    if r.real_concerns:
        print(f"\nReal concerns ({len(r.real_concerns)}):")
        for c in r.real_concerns:
            print(f"  [{c.severity:8s}] {c.summary}")
    if r.questions:
        print(f"\nOpen questions:")
        for q in r.questions:
            print(f"  ? {q}")
    print(f"\nMetrics:")
    for k, v in r.metrics.items():
        print(f"  {k:24s} {v}")
    print(f"\nMemory consulted: {', '.join(r.memory_consulted) or '(none)'}")
    if r.raw_data.get("events"):
        print(f"\nToday's events ({len(r.raw_data['events'])}):")
        for ev in r.raw_data["events"][:5]:
            print(f"  · {ev.get('start','?')[:16]} — {ev.get('summary','?')}")


if __name__ == "__main__":
    import argparse, io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    args = p.parse_args()
    report = run(args.date)
    if args.json:
        print(report.to_json())
    else:
        _print_human(report)
    sys.exit(0 if report.status != "failed" else 1)
