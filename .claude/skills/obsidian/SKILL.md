---
name: obsidian
description: >
  Read, write, search, and manage notes in the HodgeSpot Obsidian vault via the Local REST API.
  Use this skill whenever the user wants to capture a note, log something to their second brain,
  search their vault, create a daily note, add a task to Obsidian, read an existing note,
  append to a note, or file information into their knowledge base. Also triggers when BlunderBus
  needs to save a report, brief, or finding to the vault (e.g. after a security triage or morning
  brief). Trigger for: "add to obsidian", "save that to my vault", "note this", "what did I write
  about X", "create a project note", "log to obsidian", "append to my daily note", "check my notes
  on X", or any time knowledge should be persisted for future reference.
---

# Obsidian Skill

Manages the HodgeSpot Obsidian vault via the Local REST API plugin.

- **API base:** `https://127.0.0.1:27124`
- **Auth:** Bearer token — retrieve from vault as `Obsidian API` (field: `token`)
- **TLS:** self-signed cert — always use `-k` / `ssl.CERT_NONE`
- **Vault location:** `/mnt/truenas/proxmox-share/Blunderbus` (NAS-backed, mounted on AI-Workstation; `~/Documents/Obsidian Vault` symlink). Obsidian desktop app runs locally on AI-Workstation.

## Vault Structure

```
00 - Inbox/        ← default capture destination
10 - Projects/     ← active projects with defined outcomes
20 - Areas/        ← ongoing responsibilities
30 - Resources/    ← reference notes, research
40 - Archive/      ← completed/inactive
50 - Fleeting/     ← quick thoughts, process within 48h
Daily/             ← daily notes (YYYY-MM-DD.md)
Templates/         ← note templates
BlunderBus/        ← agent-generated reports, briefs, logs
```

**Default save locations:**
- Quick captures → `00 - Inbox/`
- Daily note append → `Daily/YYYY-MM-DD.md`
- Agent-generated content → `BlunderBus/`
- Infrastructure reports → `BlunderBus/Briefs/`

## Auth Setup

```bash
source /home/brian/blunderbus-claude/.env
bw config server https://vaultwarden.hodgespot.com --quiet 2>/dev/null
export BW_SESSION=$(bw unlock "$BW_MASTER_PASS" --raw 2>/dev/null)
OB_KEY=$(bw list items --session "$BW_SESSION" 2>/dev/null | python3 -c "
import sys, json
items = json.load(sys.stdin)
for i in items:
    if 'obsidian' in i.get('name','').lower():
        for f in i.get('fields', []):
            if f.get('name','').lower() == 'token':
                print(f.get('value',''))
" 2>/dev/null)
```

Always `unset OB_KEY` after use.

## Core Operations

### Read a note
```bash
curl -sk -H "Authorization: Bearer $OB_KEY" \
  "https://127.0.0.1:27124/vault/PATH/TO/NOTE.md"
```

### Write / create a note (overwrites)
```bash
curl -sk -X PUT "https://127.0.0.1:27124/vault/PATH/TO/NOTE.md" \
  -H "Authorization: Bearer $OB_KEY" \
  -H "Content-Type: text/markdown" \
  --data-raw "# Note Title

Content here."
```

### Append to an existing note
```bash
curl -sk -X POST "https://127.0.0.1:27124/vault/PATH/TO/NOTE.md" \
  -H "Authorization: Bearer $OB_KEY" \
  -H "Content-Type: text/markdown" \
  --data-raw "

## Appended Section
Content to append."
```

### Search vault (full-text)
```bash
curl -sk -G "https://127.0.0.1:27124/search/simple/" \
  -H "Authorization: Bearer $OB_KEY" \
  --data-urlencode "query=SEARCH TERM" \
  --data-urlencode "contextLength=100"
```

### List files in a folder
```bash
curl -sk -H "Authorization: Bearer $OB_KEY" \
  "https://127.0.0.1:27124/vault/FOLDER/" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for f in sorted(data.get('files', [])):
    print(f)
"
```

### List all vault files
```bash
curl -sk -H "Authorization: Bearer $OB_KEY" \
  "https://127.0.0.1:27124/vault/" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for f in sorted(data.get('files', [])):
    print(f)
"
```

### Delete a note
```bash
curl -sk -X DELETE "https://127.0.0.1:27124/vault/PATH/TO/NOTE.md" \
  -H "Authorization: Bearer $OB_KEY"
```

## Python Helper (preferred for complex operations)

Use Python for multi-file operations, Unicode content, or when paths contain spaces:

```python
import urllib.request, urllib.parse, ssl

OB_KEY = "..."  # from vault, never hardcode
BASE = "https://127.0.0.1:27124/vault/"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def ob_put(path, content):
    url = BASE + urllib.parse.quote(path)
    req = urllib.request.Request(url, data=content.encode('utf-8'), method='PUT')
    req.add_header('Authorization', f'Bearer {OB_KEY}')
    req.add_header('Content-Type', 'text/markdown')
    urllib.request.urlopen(req, context=ctx)

def ob_get(path):
    url = BASE + urllib.parse.quote(path)
    req = urllib.request.Request(url)
    req.add_header('Authorization', f'Bearer {OB_KEY}')
    resp = urllib.request.urlopen(req, context=ctx)
    return resp.read().decode('utf-8')

def ob_append(path, content):
    url = BASE + urllib.parse.quote(path)
    req = urllib.request.Request(url, data=content.encode('utf-8'), method='POST')
    req.add_header('Authorization', f'Bearer {OB_KEY}')
    req.add_header('Content-Type', 'text/markdown')
    urllib.request.urlopen(req, context=ctx)
```

## Daily Note Pattern

Today's daily note path: `Daily/YYYY-MM-DD.md` (use current date)

To append a log entry to today's daily note:
```bash
DATE=$(date +%Y-%m-%d)
curl -sk -X POST "https://127.0.0.1:27124/vault/Daily/${DATE}.md" \
  -H "Authorization: Bearer $OB_KEY" \
  -H "Content-Type: text/markdown" \
  --data-raw "

## BlunderBus Log — $(date +%H:%M)
Content here."
```

If the daily note doesn't exist yet, use PUT to create it from the template structure.

## BlunderBus Report Pattern

Save agent-generated content to `BlunderBus/` with date-stamped filenames:

```
BlunderBus/Briefs/2026-03-23-morning-brief.md
BlunderBus/Security/2026-03-23-alert-summary.md
BlunderBus/Infra/2026-03-23-health-check.md
```

## Frontmatter Convention

Always include frontmatter on new notes:
```yaml
---
date: YYYY-MM-DD
type: note|brief|project|meeting|resource
tags: []
source: blunderbus  # if agent-generated
---
```

## Notes on Path Encoding

- Spaces in paths must be URL-encoded as `%20` in curl, or use `urllib.parse.quote()` in Python
- Folder paths end with `/`
- File paths include `.md` extension
- Subfolders are created automatically when writing a file to a new path
