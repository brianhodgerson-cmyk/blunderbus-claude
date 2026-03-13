---
name: gws-setup
description: Google Workspace CLI setup and management — Gmail, Calendar, Drive, and Docs integration for Claude Code.
allowed-tools: Bash
---

# GWS Setup — Google Workspace Integration

## What This Does
Guides setup and usage of Google Workspace tools within Claude Code — email, calendar, drive, and docs.

## Prerequisites
Google Workspace CLI must be configured in Claude Code. Run these in the Claude Code terminal:

### Enable Google Workspace tools
```bash
# Check if GWS is already configured
claude mcp list | grep -i google

# If not, add via OAuth (interactive)
claude mcp add --transport http google-workspace https://accounts.google.com/mcp
```

## Common Operations

### Gmail — Read recent emails
Use Claude's built-in Gmail tool after GWS setup:
- "Check my recent emails"
- "Search emails from <sender>"
- "Read the email about <subject>"

### Calendar — View schedule
- "What's on my calendar today?"
- "Show my meetings this week"
- "When am I free tomorrow?"

### Drive — File operations
- "Find files named <query> in Drive"
- "Read the contents of <document>"

## Notes
- GWS integration uses OAuth — the operator must authenticate interactively on first use.
- Token is stored locally and persists across sessions.
- Account: bh@hodgespot.com
