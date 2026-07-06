---
name: workspace-brief
description: Daily personal workspace summary combining Google Calendar, Gmail, and Tasks into one clean overview. Use this skill when the user asks what's on their plate today, wants a daily summary, morning personal brief, workspace overview, what they have going on, what's in their inbox, or any combination of email + calendar + tasks. Also triggered from morning-brief to add personal workspace data alongside infrastructure health. Works for bh@hodgespot.com via the gws CLI.
allowed-tools: Bash
---

# Workspace Brief — Personal Daily Overview

Account: `bh@hodgespot.com`
CLI: `gws` (authenticated, no login needed)
JSON parsing: `/c/Python314/python.exe`

Run all three sections in parallel, then compile the report.

---

## 1. Today's Calendar Events

```bash
TODAY=$(date -u +%Y-%m-%dT00:00:00Z)
TOMORROW=$(date -u -d '+1 day' +%Y-%m-%dT00:00:00Z 2>/dev/null || \
  /c/Python314/python.exe -c "from datetime import datetime, timedelta; print((datetime.utcnow().replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z'))")

gws calendar events list \
  --params "{\"calendarId\":\"primary\",\"timeMin\":\"$TODAY\",\"timeMax\":\"$TOMORROW\",\"singleEvents\":true,\"orderBy\":\"startTime\"}" \
  --format json 2>/dev/null
```

Parse events — show time, title, location if present.

---

## 2. Unread Email Summary

```bash
/c/Python314/python.exe - << 'EOF'
import subprocess, json

# Get count
count_result = subprocess.run(
    ['gws', 'gmail', 'users', 'messages', 'list',
     '--params', '{"userId":"me","q":"is:unread","maxResults":1}',
     '--format', 'json'],
    capture_output=True, text=True
)
count_data = json.loads(count_result.stdout)
total = count_data.get('resultSizeEstimate', 0)
print(f"Unread: {total}")

# Get top 5 subjects
list_result = subprocess.run(
    ['gws', 'gmail', 'users', 'messages', 'list',
     '--params', '{"userId":"me","q":"is:unread","maxResults":5}',
     '--format', 'json'],
    capture_output=True, text=True
)
msgs = json.loads(list_result.stdout).get('messages', [])

for m in msgs:
    detail = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'get',
         '--params', f'{{"userId":"me","id":"{m["id"]}","format":"metadata","metadataHeaders":["From","Subject","Date"]}}',
         '--format', 'json'],
        capture_output=True, text=True
    )
    data = json.loads(detail.stdout)
    headers = {h['name']: h['value'] for h in data.get('payload', {}).get('headers', [])}
    sender = headers.get('From', '?').split('<')[0].strip().strip('"')
    print(f"  • {sender}: {headers.get('Subject', '(no subject)')}")
EOF
```

---

## 3. Pending Tasks

```bash
gws tasks tasks list \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","showCompleted":false}' \
  --format json 2>/dev/null
```

Show title and due date for each pending task.

---

## Report Format

```
## 📅 Today — <DAY, MONTH DATE>

### Calendar
• 9:00 AM — Team standup (Google Meet)
• 2:00 PM — Doctor appointment
(or: No events scheduled today)

### 📬 Email — 12 unread
• Amazon: Your order has shipped
• Brian Smith: Re: Project proposal
• GitHub: [blunderbus-claude] PR #14 opened
• Comcast: Your bill is ready
• Notion: Weekly digest

### ✅ Tasks (3 pending)
• Fix WireGuard VPN (due today)
• Review Grafana dashboards
• Call electrician
(or: No pending tasks — all clear ✅)
```

Keep it scannable. No filler. Flag anything time-sensitive (meeting starting soon, overdue tasks, emails from known contacts that look important).

---

## When called from morning-brief

Append the workspace section at the end of the infrastructure brief under a `## Personal` heading, using the same compact format above.
