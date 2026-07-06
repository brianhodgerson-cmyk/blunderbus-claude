---
name: learnings-consolidate
description: Reflective pass that scans recent Obsidian daily notes, detects persistent issues, recurring anomalies, and emerging patterns, then updates memory/learnings.md so BlunderBus carries observations forward week over week instead of re-discovering the same problems daily. Trigger when the user asks "what have we been seeing", "consolidate learnings", "what's the pattern", "update learnings", or weekly Sunday evening as part of a maintenance ritual. Also call after a stretch of unattended daily runs to re-baseline what's known.
---

# Skill: learnings-consolidate

Turn the stream of daily notes into a persistent, decaying memory of what's actually happening across the cluster, finances, and personal workspace.

## When to use

- **Weekly review** (Sunday evening) — full 7-day pass
- **After unattended runs** — caught up after a few days away
- **Investigating a pattern** — "is Stark always at 95% or just lately?"
- **Pre-decision** — before triaging an issue, see what we already know

## What it does

1. **Reads** the last N daily notes from `Daily/` (default N=7)
2. **Extracts** structured signals: persistent flags, recurring warnings, repeat detections, infra alerts, finance anomalies
3. **Diffs** against `memory/learnings.md` — what's new, what's resolved, what's still open
4. **Updates** `memory/learnings.md` with:
   - Active concerns (ongoing issues with day count)
   - Resolved issues (with date)
   - Emerging patterns (new this week)
5. **Prints** a brief summary to the operator

## How to invoke

```bash
# Default — last 7 days, write to memory/learnings.md
py scripts/consolidate_learnings.py

# Custom window
py scripts/consolidate_learnings.py --days 14

# Dry run — print what would change without writing
py scripts/consolidate_learnings.py --dry-run

# Specific date range
py scripts/consolidate_learnings.py --from 2026-04-01 --to 2026-04-29
```

## Output format

`memory/learnings.md` is updated with these sections:

```markdown
# BlunderBus Learnings

_Last consolidated: 2026-04-29_

## Active concerns

- 🔴 **Stark RAM at 95%** — flagged 4 consecutive days (since 2026-04-26). No remediation yet.
- 🟡 **Monitoring stack offline** — Thor down for 4 days affecting Grafana/Prometheus visibility.
- 🟡 **SecOnion api_key empty** — daily warning since 2026-04-15. Either populate or remove.

## Resolved this week

- ✅ ClickHouse tunnel auth — fixed 2026-04-29 by repointing to current container IP

## Emerging patterns

- 📊 April spending pace 2.4-3x monthly average — likely Insurance/Pets billing concentration, not steady burn
- 📊 Frigate NVR has been unreachable on 3 of last 7 days — intermittent

## Baseline (stable, don't re-flag)

- Cortex memory consistently 22-26%
- Heimdall ZFS pool healthy
- All scheduled tasks completing
```

## Pattern detection rules

The script tags an observation as "persistent" if it appears in **3+ consecutive daily notes**.
"Resolved" = was active, hasn't appeared in last 2 days.
"New" = appeared this run, not in previous learnings.

## Integration with daily flow

- `daily_brief.py` reads `memory/learnings.md` (via parse_carried_from_learnings) so today's brief is decorated with "still open since X" callouts.
- `daily_brief.py`'s `validate_reports()` cross-validation step can elevate persistent issues to higher severity.

## Safety

- Never deletes daily notes
- Atomic write: builds new `learnings.md` in temp, moves into place
- Keeps `memory/learnings.md.bak` (last version) for one-step rollback
