---
name: obsidian-tasks
description: Sync tasks between Obsidian daily notes and TASKS.md. Read open tasks from recent daily notes, update task status, carry forward incomplete tasks, and keep TASKS.md in sync.
allowed-tools: Bash, mcp__obsidian__obsidian_read, mcp__obsidian__obsidian_write, mcp__obsidian__obsidian_append, mcp__obsidian__obsidian_search
---

# Obsidian Tasks — Task Sync & Management

## What This Does
Bridges task tracking between Obsidian daily notes and the project-level `TASKS.md`. Reads tasks from daily notes, carries forward incomplete items, and keeps both systems in sync.

## Task Locations

**Obsidian daily notes** have tasks in two sections:
- `## Morning Intentions` — AI-suggested focus items for the day
- `## Tasks` — manual task entries, carried-forward items

**TASKS.md** (project root) has:
- `## Active` — current sprint / in-progress work
- `## Backlog` — queued items
- `## Completed` — done items

## Operations

### 1. View today's tasks
Read today's daily note and extract all task lines:
```
obsidian_read("Daily/YYYY-MM-DD.md")
```
Parse `- [ ]` (open) and `- [x]` (done) lines from Tasks and Morning Intentions sections.

### 2. Add a task to today's note
```
obsidian_append(
    path="Daily/YYYY-MM-DD.md",
    content="\n- [ ] <task description>",
    heading="Tasks"
)
```

### 3. Carry forward incomplete tasks
Read the last 7 days of daily notes. For each open task (`- [ ]`), check if it appears in a later note. If not, append it to today's note with a "carried from" annotation:
```
- [ ] Fix the sprinkler timer *(carried from 2026-03-30)*
```

### 4. Sync Obsidian tasks to TASKS.md
After reading Obsidian tasks, update `TASKS.md`:
- New tasks from Obsidian daily notes → add to `## Active`
- Completed tasks (`- [x]`) → move to `## Completed` with date
- Tasks already in TASKS.md that appear in Obsidian → no-op

### 5. Sync TASKS.md to Obsidian
When a task in `TASKS.md` is marked active, ensure it appears in today's daily note Tasks section.

### 6. Search for a task across notes
```
obsidian_search("task keyword")
```

## Conventions
- Task format: `- [ ] Description` or `- [x] Description`
- Carried tasks get annotation: `*(carried from YYYY-MM-DD)*`
- Completed tasks in TASKS.md get date: `- [x] Description (completed 2026-04-02)`
- Don't duplicate — if a task already exists in today's note, skip it
- Morning Intentions are ephemeral — don't carry them forward unless the user explicitly asks

## When To Use
- User says "what are my tasks", "show tasks", "add task", "carry forward"
- User asks to sync tasks between Obsidian and TASKS.md
- Part of the `/morning-brief` flow (carry forward check)
- User asks "what did I not finish this week"
