#!/usr/bin/env python3
"""
BlunderBus Morning Prep.

Creates today's daily note if it does not exist, carries forward unfinished
tasks, injects today's calendar, drafts morning intentions, and optionally
schedules a task-review block.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

from blunderbus_data import log_life_event
from note_store import NoteStoreError, resolve_note_store
from runtime import configure_utf8_stdio, resolve_claude_command

configure_utf8_stdio()


CALENDAR_ID = "brian.hodgerson@gmail.com"
LOOKBACK_DAYS = 7
NOTE_STORE = resolve_note_store()
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

GCAL_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", ".gcal_token.json")
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def read_note(target_date: date) -> str | None:
    try:
        return NOTE_STORE.read_daily(target_date)
    except (FileNotFoundError, NoteStoreError):
        return None


def extract_open_tasks(text: str) -> list[str]:
    tasks = []
    for line in text.splitlines():
        match = re.match(r"^\s*-\s+\[ \]\s+(.+)$", line)
        if not match:
            continue
        task = re.sub(r"\s*\*\(carried[^)]*\)\*", "", match.group(1)).strip()
        if task:
            tasks.append(task)
    return tasks


def extract_closed_tasks(text: str) -> set[str]:
    tasks = set()
    for line in text.splitlines():
        match = re.match(r"^\s*-\s+\[x\]\s+(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        task = re.sub(r"\s*\*\(carried[^)]*\)\*", "", match.group(1))
        task = re.sub(r"\s*✅\s*\S+", "", task).strip()
        if task:
            tasks.add(task.lower())
    return tasks


def _gcal_creds():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None

    if not os.path.exists(GCAL_TOKEN_FILE):
        return None

    creds = Credentials.from_authorized_user_file(GCAL_TOKEN_FILE, GCAL_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(GCAL_TOKEN_FILE, "w", encoding="utf-8") as handle:
                handle.write(creds.to_json())
        except Exception as exc:
            print(f"  Calendar token refresh failed: {exc}")
            return None
    return creds if creds and creds.valid else None


def get_today_events(target_date: date) -> list[str]:
    creds = _gcal_creds()
    if not creds:
        print("  Calendar: no credentials - run `python scripts/gcal_auth.py` once to connect")
        return []

    try:
        from googleapiclient.discovery import build

        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        time_min = f"{target_date.isoformat()}T00:00:00-06:00"
        time_max = f"{target_date.isoformat()}T23:59:59-06:00"
        result = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = []
        for event in result.get("items", []):
            summary = event.get("summary", "(no title)")
            start = event.get("start", {})
            if "dateTime" in start:
                dt = datetime.fromisoformat(start["dateTime"])
                time_str = dt.strftime("%I:%M %p").lstrip("0")
                events.append(f"- {time_str} - {summary}")
            else:
                events.append(f"- All day - {summary}")
        return events
    except Exception as exc:
        print(f"  Calendar fetch failed: {exc}")
        return []


def schedule_task_review(carried_tasks, target_date: date) -> None:
    if not carried_tasks:
        return

    creds = _gcal_creds()
    if not creds:
        print("  Task Review: no calendar credentials - skipping")
        return

    try:
        from googleapiclient.discovery import build

        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        tz = "-06:00"
        day = target_date.isoformat()
        window_start = datetime.fromisoformat(f"{day}T08:30:00{tz}")
        window_end = datetime.fromisoformat(f"{day}T18:00:00{tz}")
        review_len = timedelta(minutes=30)

        existing = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=f"{day}T00:00:00{tz}",
            timeMax=f"{day}T23:59:59{tz}",
            q="Task Review",
            singleEvents=True,
        ).execute()
        if existing.get("items"):
            print("  Task Review already on calendar - skipping")
            return

        freebusy = svc.freebusy().query(
            body={
                "timeMin": window_start.isoformat(),
                "timeMax": window_end.isoformat(),
                "items": [{"id": CALENDAR_ID}],
            }
        ).execute()
        busy = []
        for block in freebusy["calendars"][CALENDAR_ID]["busy"]:
            busy.append((datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"])))
        busy.sort()

        slot_start = window_start
        for busy_start, busy_end in busy:
            if slot_start + review_len <= busy_start:
                break
            if busy_end > slot_start:
                slot_start = busy_end
        else:
            if slot_start + review_len > window_end:
                print("  No free 30-minute slot found today for Task Review")
                return

        slot_end = slot_start + review_len
        task_list = "\n".join(f"- {task}" for task, _, _ in carried_tasks)
        event = {
            "summary": "Task Review",
            "description": f"Carried tasks to clear:\n{task_list}",
            "start": {"dateTime": slot_start.isoformat(), "timeZone": "America/Chicago"},
            "end": {"dateTime": slot_end.isoformat(), "timeZone": "America/Chicago"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 5}]},
        }
        svc.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"  Task Review scheduled at {slot_start.strftime('%I:%M %p').lstrip('0')}")
    except Exception as exc:
        print(f"  Task Review scheduling failed: {exc}")


def draft_intentions(carried_tasks, schedule_lines, today: date) -> list[str]:
    claude_bin = resolve_claude_command()
    if not claude_bin:
        return []

    task_text = "\n".join(f"- {task}" for task, _, _ in carried_tasks) if carried_tasks else "- (no open tasks)"
    cal_text = "\n".join(schedule_lines) if schedule_lines else "- No events scheduled"
    if sys.platform == "win32":
        day_name = today.strftime("%A, %B %d").replace(" 0", " ")
    else:
        day_name = today.strftime("%A, %B %-d")

    prompt = f"""Today is {day_name}.

Open tasks:
{task_text}

Calendar today:
{cal_text}

Draft exactly 3 short, specific morning focus items for this person. Each should be one line, actionable, and reflect the actual tasks and events above. Format as a plain list, one item per line, no bullets or numbers, no preamble."""

    try:
        result = subprocess.run(
            [claude_bin, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            cwd=os.path.expanduser("~"),
        )
        if result.returncode == 0 and result.stdout.strip():
            return [line.strip() for line in result.stdout.splitlines() if line.strip()][:3]
    except Exception as exc:
        print(f"  AI intentions failed: {exc}")
    return []


def format_day_header(target_date: date) -> str:
    return f"# {DAYS[target_date.weekday()]}, {MONTHS[target_date.month - 1]} {target_date.day}, {target_date.year}"


def build_note(today: date, carried_tasks, schedule_lines, intentions=None) -> str:
    task_block = ""
    for task_text, source_date, days_old in carried_tasks:
        day_short = source_date.strftime("%b %d")
        carry_note = "*(carried from yesterday)*" if days_old == 1 else f"*(carried from {day_short})*"
        task_block += f"- [ ] {task_text} {carry_note}\n"
    task_block += "- [ ] "

    if schedule_lines:
        event_lines = "\n> ".join(schedule_lines)
        schedule_block = f"> [!info]+ Today's Calendar\n> {event_lines}"
    else:
        schedule_block = "> [!note]- Calendar\n> *No events scheduled*"

    if intentions:
        intention_lines = "\n> ".join(f"- [ ] {item}" for item in intentions)
        intentions_block = f"> [!tip]+ Today's Focus - AI Suggested\n> {intention_lines}"
    else:
        intentions_block = "> [!tip] Today's Focus\n> - [ ]\n> - [ ]\n> - [ ]"

    return f"""---
date: {today.isoformat()}
type: daily
tags: [daily]
---

{format_day_header(today)}

## Health
*pending - BlunderBus will populate*

## Infrastructure
*pending - BlunderBus will populate at 06:30*

## Morning Intentions

{intentions_block}

## Schedule

{schedule_block}

## Notes & Captures



## Tasks

{task_block}
## Projects & Lab



## Finance
*pending - BlunderBus will populate at 07:30*

## Evening Review

> [!question]- Reflection
> **Done today:**
>
> **Carrying forward:**
>
> **One insight:**
"""


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Override today's date YYYY-MM-DD")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    print(f"\nBlunderBus Morning Prep - {today.isoformat()}")
    print("=" * 48)

    if NOTE_STORE.daily_exists(today):
        print(f"Note already exists for {today.isoformat()} - skipping creation")
        return

    all_closed = set()
    open_by_key = {}

    for offset in range(1, LOOKBACK_DAYS + 1):
        scan_date = today - timedelta(days=offset)
        note_text = read_note(scan_date)
        if not note_text:
            continue

        all_closed.update(extract_closed_tasks(note_text))
        for task in extract_open_tasks(note_text):
            key = task.lower()
            if key not in open_by_key:
                open_by_key[key] = (task, scan_date, offset)

    carried = [value for key, value in open_by_key.items() if key not in all_closed]
    carried.sort(key=lambda item: item[2], reverse=True)

    if carried:
        print(f"\n  Carrying forward {len(carried)} open task(s):")
        for task_text, _, days_old in carried:
            print(f"  - [{days_old}d] {task_text}")
    else:
        print("\n  No open tasks to carry forward")

    print("\n  Fetching today's calendar...")
    schedule_lines = get_today_events(today)
    print(f"  Calendar entries: {len(schedule_lines)}")

    print("\n  Drafting morning intentions...")
    intentions = draft_intentions(carried, schedule_lines, today)
    print(f"  Intentions drafted: {len(intentions)}")

    note_content = build_note(today, carried, schedule_lines, intentions)
    try:
        NOTE_STORE.write_daily(today, note_content)
    except NoteStoreError as exc:
        print(f"\nFailed to create note: {exc}")
        raise SystemExit(1) from exc

    print(f"\nCreated note: {NOTE_STORE.daily_path(today)} via {NOTE_STORE.backend_name}")
    log_life_event(
        domain="projects",
        event_type="daily_note_created",
        source="morning_prep",
        summary=f"Created daily note for {today.isoformat()}",
        detail={
            "backend": NOTE_STORE.backend_name,
            "carried_task_count": len(carried),
            "calendar_event_count": len(schedule_lines),
            "intentions_count": len(intentions),
        },
        tags=["daily-note", "projects"],
    )

    if carried:
        print("\n  Scheduling Task Review block...")
        schedule_task_review(carried, today)


if __name__ == "__main__":
    main()
