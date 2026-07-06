---
name: dnc-log
description: >
  Log Do Not Call Registry violations to Obsidian with automated enrichment.
  Use when the user reports an unwanted sales call, spam call, robocall, or telemarketer,
  or wants to log a DNC violation. Also triggers from Discord (Hermes) messages starting with "DNC".
  Handles phone number lookup, area code identification, repeat offender tracking,
  complaint narrative drafting, and TCPA escalation flagging.
allowed-tools: WebSearch, mcp__obsidian__obsidian_read, mcp__obsidian__obsidian_append, mcp__obsidian__obsidian_write
---

# DNC Log — Do Not Call Registry Violation Tracker

Log unwanted calls with automated enrichment: area code origin, web reputation lookup,
repeat offender tracking, complaint narrative generation, and TCPA escalation alerts.

## Input Format

```
/dnc-log <phone-number> [time] <description>
```

The user may also just describe a spam call naturally — parse what you can from their message.

**Examples:**
- `/dnc-log 619-326-7349 warranty scam, robocall, told them to remove me`
- `/dnc-log 619-326-7349 11:31 AM warranty scam, live caller, hung up`
- `/dnc-log 800-555-1234 2:15 PM solar panels, robocall, didn't answer`
- Discord (Hermes): `DNC 619-326-7349 warranty scam robocall`

## Parsing Rules

Extract these fields from the user's input:

1. **Phone number** (required) — 10-digit US number, with or without dashes/parens. Normalize to `XXX-XXX-XXXX` for storage.
2. **Time** (optional) — Look for a time pattern like `11:31 AM` or `2:15 PM` after the number. If not provided, use the current time and note "(logged at time of report)".
3. **Description** — Everything remaining after number and optional time.
4. **Call type** — Scan description for keywords:
   - Robocall/autodialer/recorded → `Robocall`
   - Live caller/live person → `Live caller`
   - If not specified → `Unknown`
5. **User response** — Scan description for keywords:
   - "didn't answer" / "missed" → `Didn't answer`
   - "hung up" → `Hung up`
   - "asked to be removed" / "told them to remove me" / "told them to stop" → `Asked to be removed`
   - If not specified → `Unknown`

## Enrichment Workflow

Run these steps in order after parsing:

### Step 1: Area Code Lookup
Extract the 3-digit area code from the phone number and determine the geographic origin.
Use WebSearch if needed (e.g., query `"619 area code location"`).
Format as: `City, ST` (e.g., `San Diego, CA`).

### Step 2: Phone Number Reputation Search
Search the web for the full phone number to find spam reports and identify the company behind it.
Good query: `"619-326-7349" spam` or `"6193267349" complaints`.
Summarize findings in 1-2 sentences — company name (if identified), number of complaints found,
and the general nature of reports.

### Step 3: Read Existing Log
Read `Reference/Do Not Call Registry Log.md` from Obsidian using `obsidian_read`.
- Count how many times this phone number already appears in the log table.
- The new entry's repeat number = previous count + 1.

If the note doesn't exist (404), create it first with `obsidian_write` using this header:

```markdown
# Do Not Call Registry — Violation Log

Tracking unwanted sales/spam calls received while registered on the National Do Not Call Registry.

## Log

| Date | Time | Caller Number | Area Code Origin | Company/Type | Call Type | Response | Repeat # | Complaint Filed |
|------|------|---------------|------------------|--------------|-----------|----------|----------|-----------------|

## Entries

## Reference

- **FTC Complaint Portal:** [donotcall.gov](https://www.donotcall.gov)
- **FTC Phone:** 1-888-382-1222
- **TCPA:** Robocall/autodialer violations — $500–$1,500 per call via private action
- **State AG:** File with your state Attorney General's consumer protection division
```

### Step 4: Generate Complaint Narrative
Draft a 2-3 sentence summary suitable for pasting into an FTC complaint form. Include:
- Date and time of the call
- Caller's phone number
- Nature of the call (what they were selling/promoting)
- That no business relationship exists
- Registration on the Do Not Call Registry
- Whether the user asked to be removed on a prior call (if repeat)
- Repeat contact count if applicable

### Step 5: Append to Obsidian

**Append a table row** under the `Log` heading:

```
| YYYY-MM-DD | HH:MM AM/PM | XXX-XXX-XXXX | City, ST | Description | Call Type | Response | N | Yes/No |
```

Use `obsidian_append` with `heading="Log"` for the table row.

**Append a detailed entry** under the `Entries` heading:

```markdown

### YYYY-MM-DD — XXX-XXX-XXXX
- **Time:** HH:MM AM/PM
- **Area Code:** City, ST
- **Company/Type:** Description from user
- **Call Type:** Robocall / Live caller / Unknown
- **Response:** Asked to be removed / Hung up / Didn't answer / Unknown
- **Repeat Contact:** Nth call from this number
- **Web Findings:** Summary of search results
- **Complaint Narrative:** The drafted narrative text
```

Use `obsidian_append` with `heading="Entries"` for the detailed entry.

### Step 6: Escalation Check
If the repeat count (including this entry) is **3 or more**, add an escalation flag to the detailed entry:

```markdown
- **⚠️ ESCALATION:** N contacts from this number — candidate for TCPA private action ($500–$1,500/call). Consider consulting a consumer rights attorney.
```

## Output to User

After logging, respond with a concise confirmation:

```
✅ **Logged:** XXX-XXX-XXXX — Description (Area Code Origin)
- **Web findings:** 1-2 sentence summary
- **Repeat contact:** Nth from this number
- **Escalation:** ⚠️ TCPA candidate (if 3+) / None
- **Complaint narrative:** (the full narrative, ready to copy)
```

## Discord Integration

This skill can be triggered from Discord (via the Hermes gateway) with the format:
```
DNC 619-326-7349 warranty scam robocall
```

When a Discord message starts with `DNC` followed by a phone number, treat it identically
to `/dnc-log` — parse, enrich, log, and reply in the Discord channel with the confirmation.
