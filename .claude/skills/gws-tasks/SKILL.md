---
name: gws-tasks
description: Google Tasks management via Google Workspace CLI. Use this skill whenever the user mentions tasks, to-do list, todo, task list, add a task, check off a task, complete a task, or wants to see what they need to do. Trigger for casual requests too — "add that to my list", "what do I still need to do", "mark that done", "remind me to...". Manages task lists and individual tasks for bh@hodgespot.com.
allowed-tools: Bash
---

# Google Tasks Management

Account: `bh@hodgespot.com`
CLI: `gws` (authenticated)
JSON parsing: `/c/Python314/python.exe`

Default task list: **My Tasks**
Default task list ID: `MDE3NDMzNjc2MDExNTI1NTExNDY6MDow`

---

## List Task Lists

```bash
gws tasks tasklists list --format json 2>/dev/null
```

Returns all task lists with their IDs and titles. Use the ID in subsequent task commands.

---

## List Tasks

```bash
gws tasks tasks list \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","showCompleted":false,"showHidden":false}' \
  --format json 2>/dev/null
```

To include completed tasks, set `"showCompleted":true`.

**Parse and display with Python:**
```bash
/c/Python314/python.exe - << 'EOF'
import subprocess, json

result = subprocess.run(
    ['gws', 'tasks', 'tasks', 'list',
     '--params', '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","showCompleted":false}',
     '--format', 'json'],
    capture_output=True, text=True
)
data = json.loads(result.stdout)
tasks = data.get('items', [])

if not tasks:
    print("No pending tasks.")
else:
    for t in tasks:
        due = t.get('due', '')[:10] if t.get('due') else 'no due date'
        notes = f" — {t['notes']}" if t.get('notes') else ''
        print(f"• [{t['id'][:8]}...] {t['title']} (due: {due}){notes}")
EOF
```

---

## Add a Task

```bash
gws tasks tasks insert \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow"}' \
  --json '{"title":"TASK_TITLE","notes":"OPTIONAL_NOTES","due":"2026-03-24T00:00:00.000Z"}' \
  --format json 2>/dev/null
```

- `due` is optional — omit the field if no due date
- `notes` is optional — omit if not needed
- Due date must be in RFC 3339 format (midnight UTC works fine for date-only)

---

## Complete a Task

```bash
gws tasks tasks patch \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","task":"TASK_ID"}' \
  --json '{"status":"completed"}' \
  --format json 2>/dev/null
```

Get the task ID from `list` output (`id` field).

---

## Delete a Task

```bash
gws tasks tasks delete \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","task":"TASK_ID"}' \
  2>/dev/null
```

---

## Update a Task Title or Notes

```bash
gws tasks tasks patch \
  --params '{"tasklist":"MDE3NDMzNjc2MDExNTI1NTExNDY6MDow","task":"TASK_ID"}' \
  --json '{"title":"NEW_TITLE"}' \
  --format json 2>/dev/null
```

---

## Output Format

**Listing tasks:**
```
📋 My Tasks (3 pending)
• Fix WireGuard VPN (due: 2026-03-24)
• Review Grafana dashboards
• Call electrician — check availability Thursday
```

**After adding:** ✅ Task added: "[title]"
**After completing:** ✅ Marked complete: "[title]"
**Empty list:** ✅ No pending tasks — all clear.
