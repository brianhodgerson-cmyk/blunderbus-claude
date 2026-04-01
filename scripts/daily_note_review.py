#!/usr/bin/env python3
"""
Daily Note Review — BlunderBus
Reads today's Obsidian daily note, extracts open tasks, logs them to Clickhouse,
creates Google Tasks entries, and places a single consolidated calendar review block.

Env vars:
  BLUNDERBUS_NOTE_BACKEND  optional override for note backend selection
  BLUNDERBUS_VAULT_ROOT    filesystem backend root override
  OBSIDIAN_TOKEN           only required when using the obsidian-rest backend
  CLICKHOUSE_HOST       Clickhouse host (default: 192.168.50.106)
  CLICKHOUSE_PORT       Clickhouse port (default: 9000)
  GOOGLE_TASKS_TOKEN    Google Tasks OAuth token (optional — skips gtask creation if absent)

Usage:
  python daily_note_review.py [--dry-run] [--date YYYY-MM-DD]
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from blunderbus_data import log_life_event
from note_store import NoteStoreError, resolve_note_store

# ─── Config ──────────────────────────────────────────────────────────────────

OBSIDIAN_URL     = os.environ.get("OBSIDIAN_URL", "https://127.0.0.1:27124")
CLICKHOUSE_HOST  = os.environ.get("CLICKHOUSE_HOST", "192.168.50.106")
CLICKHOUSE_PORT  = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
LOOKBACK_DAYS    = 7        # days back to scan for unclosed tasks
WORKING_HOUR_START = 9      # earliest calendar block time
WORKING_HOUR_END   = 17     # latest calendar block end
CALENDAR_ID      = "brian.hodgerson@gmail.com"
BLUNDERBUS_TAG   = "Scheduled by BlunderBus"
NOTE_STORE       = resolve_note_store()

# ─── SSL (Obsidian self-signed cert) ─────────────────────────────────────────

def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ─── Obsidian API ─────────────────────────────────────────────────────────────

def obsidian_get(path, token):
    url = f"{OBSIDIAN_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl_ctx()) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


def get_note_for_date(token, target_date):
    del token
    try:
        return NOTE_STORE.read_daily(target_date)
    except (FileNotFoundError, NoteStoreError):
        return None


def extract_open_tasks(note_text):
    """Return list of unchecked task strings from a markdown note."""
    tasks = []
    for line in note_text.splitlines():
        m = re.match(r"^\s*-\s+\[ \]\s+(.+)$", line)
        if m:
            tasks.append(m.group(1).strip())
    return tasks


def extract_closed_tasks(note_text):
    """Return list of completed task strings from a markdown note."""
    tasks = []
    for line in note_text.splitlines():
        m = re.match(r"^\s*-\s+\[x\]\s+(.+)$", line, re.IGNORECASE)
        if m:
            tasks.append(m.group(1).strip())
    return tasks

# ─── Clickhouse ───────────────────────────────────────────────────────────────

def ch_query(sql):
    url = f"http://{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/"
    data = sql.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")
    except Exception as e:
        return None, str(e)


def task_id(task_text, source_date):
    """Stable ID: hash of task text + source date."""
    raw = f"{source_date.isoformat()}::{task_text.lower().strip()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def upsert_task(task_text, source_date, carried_forward_count=0, gtask_id=None, cal_event_id=None):
    tid = task_id(task_text, source_date)
    gtask_val = f"'{gtask_id}'" if gtask_id else "NULL"
    cal_val   = f"'{cal_event_id}'" if cal_event_id else "NULL"
    sql = f"""
INSERT INTO finance.tasks
  (task_id, task_text, source_note_date, created_at, status,
   carried_forward_count, gtask_id, calendar_event_id)
VALUES (
  '{tid}',
  '{task_text.replace("'", "\\'")}',
  '{source_date.isoformat()}',
  now(),
  'open',
  {carried_forward_count},
  {gtask_val},
  {cal_val}
)
"""
    return ch_query(sql)


def mark_task_complete(task_text, source_date):
    tid = task_id(task_text, source_date)
    sql = f"""
ALTER TABLE finance.tasks UPDATE
  status = 'complete',
  completed_at = now()
WHERE task_id = '{tid}'
"""
    return ch_query(sql)


def get_open_tasks_from_db():
    """Fetch all tasks currently marked open in Clickhouse."""
    sql = "SELECT task_id, task_text, source_note_date, carried_forward_count FROM finance.tasks FINAL WHERE status = 'open' ORDER BY source_note_date"
    code, body = ch_query(sql)
    if code != 200:
        return []
    rows = []
    for line in body.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            rows.append({
                "task_id": parts[0],
                "task_text": parts[1],
                "source_note_date": parts[2],
                "carried_forward_count": int(parts[3]),
            })
    return rows

# ─── Calendar (Google Calendar REST API via token) ────────────────────────────

def gcal_list_tomorrow_events(token, tomorrow):
    """Check if a BlunderBus review block already exists for tomorrow."""
    time_min = f"{tomorrow.isoformat()}T00:00:00Z"
    time_max = f"{tomorrow.isoformat()}T23:59:59Z"
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events"
        f"?timeMin={time_min}&timeMax={time_max}&q=BlunderBus&singleEvents=true"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return [e for e in data.get("items", []) if BLUNDERBUS_TAG in e.get("description", "")]
    except Exception:
        return []


def gcal_create_review_block(token, tomorrow, tasks_with_ages):
    """Create a single consolidated Task Review block on tomorrow's calendar."""
    task_lines = []
    for t in tasks_with_ages:
        age_tag = f" ⚠️ ({t['days_old']}d)" if t["days_old"] >= 2 else ""
        task_lines.append(f"• {t['text']}{age_tag}")

    task_count = len(tasks_with_ages)
    duration_min = max(15, min(60, task_count * 15))
    start_hour = WORKING_HOUR_START
    start_dt = f"{tomorrow.isoformat()}T{start_hour:02d}:00:00"
    end_dt   = f"{tomorrow.isoformat()}T{start_hour:02d}:{duration_min:02d}:00" if duration_min < 60 \
               else f"{tomorrow.isoformat()}T{start_hour + 1:02d}:00:00"

    body = json.dumps({
        "summary": f"📋 Task Review — {task_count} open item{'s' if task_count != 1 else ''}",
        "description": "Open tasks from your Obsidian daily notes:\n\n"
                       + "\n".join(task_lines)
                       + f"\n\n---\n{BLUNDERBUS_TAG}",
        "start": {"dateTime": start_dt, "timeZone": "America/Chicago"},
        "end":   {"dateTime": end_dt,   "timeZone": "America/Chicago"},
        "colorId": "5",
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 10},
                {"method": "email", "minutes": 30},
            ],
        },
    }).encode("utf-8")

    url = f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("id")
    except Exception as e:
        print(f"  Calendar error: {e}")
        return None

# ─── Google Tasks API ─────────────────────────────────────────────────────────

GTASK_LIST_NAME = "BlunderBus"

def gtask_get_or_create_list(token):
    """Get the BlunderBus task list ID, creating it if needed."""
    url = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            for lst in data.get("items", []):
                if lst.get("title") == GTASK_LIST_NAME:
                    return lst["id"]
    except Exception:
        pass

    # Create it
    body = json.dumps({"title": GTASK_LIST_NAME}).encode("utf-8")
    req = urllib.request.Request(
        "https://tasks.googleapis.com/tasks/v1/users/@me/lists",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("id")
    except Exception as e:
        print(f"  Google Tasks list create error: {e}")
        return None


def gtask_create(token, list_id, task_text, due_date):
    body = json.dumps({
        "title": task_text,
        "due": f"{due_date.isoformat()}T00:00:00.000Z",
        "notes": f"Tracked by BlunderBus — source: daily note",
    }).encode("utf-8")
    url = f"https://tasks.googleapis.com/tasks/v1/lists/{list_id}/tasks"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("id")
    except Exception as e:
        print(f"  Google Tasks create error: {e}")
        return None

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen, don't write")
    parser.add_argument("--date", default=None, help="Override today's date YYYY-MM-DD")
    args = parser.parse_args()

    today    = date.fromisoformat(args.date) if args.date else date.today()
    tomorrow = today + timedelta(days=1)

    gcal_token  = os.environ.get("GOOGLE_CALENDAR_TOKEN")
    gtask_token = os.environ.get("GOOGLE_TASKS_TOKEN")

    print(f"\n📋 BlunderBus Daily Note Review — {today.isoformat()}")
    print("=" * 52)

    # ── Step 1: Scan today + last N days for open/closed tasks ───────────────
    all_open_tasks   = {}   # task_text → {source_date, days_old}
    all_closed_texts = set()

    for i in range(LOOKBACK_DAYS):
        scan_date = today - timedelta(days=i)
        note = get_note_for_date(None, scan_date)
        if not note:
            continue

        closed = extract_closed_tasks(note)
        all_closed_texts.update(t.lower() for t in closed)

        open_tasks = extract_open_tasks(note)
        for t in open_tasks:
            key = t.lower()
            if key not in all_open_tasks:
                all_open_tasks[key] = {
                    "text": t,
                    "source_date": scan_date,
                    "days_old": i,
                }

    # Remove anything that was checked off at any point
    final_tasks = [
        v for k, v in all_open_tasks.items()
        if k not in all_closed_texts
    ]
    final_tasks.sort(key=lambda x: x["days_old"], reverse=True)  # oldest first

    print(f"\nFound {len(final_tasks)} open task(s):")
    for t in final_tasks:
        age = f" ({t['days_old']}d old)" if t["days_old"] > 0 else " (today)"
        print(f"  • {t['text']}{age}")

    if not final_tasks:
        print("\n✅ All caught up — no open tasks found.")
        return

    if args.dry_run:
        print("\n[dry-run] Would log to Clickhouse, create Google Tasks, and create calendar block.")
        return

    # ── Step 2: Log to Clickhouse ─────────────────────────────────────────────
    print("\n→ Logging to Clickhouse...")
    existing_db = {r["task_text"].lower(): r for r in get_open_tasks_from_db()}

    for t in final_tasks:
        key = t["text"].lower()
        carried = existing_db.get(key, {}).get("carried_forward_count", 0)
        if t["days_old"] > 0:
            carried += 1
        code, _ = upsert_task(t["text"], t["source_date"], carried_forward_count=carried)
        status = "✓" if code == 200 else f"✗ HTTP {code}"
        print(f"  {status} {t['text'][:60]}")

    # Mark any tasks in DB that are now checked off
    for t_lower, row in existing_db.items():
        if t_lower in all_closed_texts:
            mark_task_complete(row["task_text"], date.fromisoformat(row["source_note_date"]))
            print(f"  ✓ Marked complete: {row['task_text'][:60]}")

    # ── Step 3: Google Tasks ──────────────────────────────────────────────────
    gtask_ids = {}
    if gtask_token:
        print("\n→ Creating Google Tasks...")
        list_id = gtask_get_or_create_list(gtask_token)
        if list_id:
            for t in final_tasks:
                gid = gtask_create(gtask_token, list_id, t["text"], tomorrow)
                if gid:
                    gtask_ids[t["text"]] = gid
                    print(f"  ✓ {t['text'][:60]}")
                else:
                    print(f"  ✗ Failed: {t['text'][:60]}")
    else:
        print("\n⚠️  GOOGLE_TASKS_TOKEN not set — skipping Google Tasks creation")

    # ── Step 4: Calendar block ────────────────────────────────────────────────
    if gcal_token:
        print(f"\n→ Checking tomorrow's calendar ({tomorrow.isoformat()})...")
        existing = gcal_list_tomorrow_events(gcal_token, tomorrow)
        if existing:
            print(f"  ⏭  BlunderBus review block already exists — skipping")
        else:
            print("  Creating consolidated review block...")
            event_id = gcal_create_review_block(gcal_token, tomorrow, final_tasks)
            if event_id:
                print(f"  ✓ Calendar block created ({event_id})")
                # Update Clickhouse with calendar event ID
                for t in final_tasks:
                    ch_query(f"""
                        ALTER TABLE finance.tasks UPDATE calendar_event_id = '{event_id}'
                        WHERE task_id = '{task_id(t["text"], t["source_date"])}'
                    """)
            else:
                print("  ✗ Calendar block creation failed")
    else:
        print("\n⚠️  GOOGLE_CALENDAR_TOKEN not set — skipping calendar block")

    log_life_event(
        domain="projects",
        event_type="daily_review",
        source="daily_note_review",
        summary=f"Daily task review processed {len(final_tasks)} open tasks",
        detail={"backend": NOTE_STORE.backend_name, "task_count": len(final_tasks)},
        tags=["tasks", "daily-note"],
    )

    print(f"\n✅ Done — {len(final_tasks)} task(s) logged")


if __name__ == "__main__":
    main()
