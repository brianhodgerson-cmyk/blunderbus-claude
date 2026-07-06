# Handoff — BlunderBus 2.0 roadmap COMPLETE through §4; remaining: hygiene tier + followups

Repo: `/home/brian/blunderbus-claude` on AI-Workstation (VM 109, 192.168.50.208). Committed/pushed through `d938363`. **Read CLAUDE.md first** — it documents everything shipped today.

## Done today (context, don't redo)
Drift sentinel (02:30 nightly, `agents/drift.py`) · OnFailure= failure agent on all units · Langfuse tracing on the litellm lane (worker added, web 3.205.1) · voice bridge + HA voice cleanup (BlunderBus pipeline sole+preferred) · event dispatcher (`blunderbus-dispatcher.service`, MQTT+`:8790` webhooks, rules in `config/dispatch-rules.yaml`) · SSH CA (`scripts/ssh_ca.py`, 9 hosts trust, weekly cert renew timer) · Telegram fully retired.

## Remaining jobs, suggested order

1. **Wire Alertmanager → dispatcher (small, do first).** The dispatcher's `/webhook/alertmanager` claude-triage rule is live but nothing feeds it. On Banner (LXC 202, `ssh banner`): check if Alertmanager runs; if not, install/enable it, point Prometheus `alerting:` at it, add starter alert rules (host down, disk >85%, systemd unit failed), receiver = `http://192.168.50.208:8790/webhook/alertmanager`. Test with a synthetic alert (amtool or curl) and confirm the Discord triage post.

2. **§6 hooks/skills hardening.**
   - Move must-hold safety rules (no `rm -rf`, confirm restarts/destructive ops) from prose in `.claude/rules/` into deterministic PreToolUse hooks.
   - Split CLAUDE.md bulk (topology tables, ClickHouse runbook, Monarch auth section) into on-demand skills to shrink always-loaded context.
   - Consider agent teams for parallel fleet audits.

3. **§7 memory hygiene.**
   - Generalize the finance learnings consolidator into a periodic consolidation agent: merge duplicate registry facts, expire stale ones, prune resolved concerns.
   - Optional: embedding index over registry + decisions + Obsidian vault in ClickHouse (already runs on Cortex) for "have we seen this failure before?" search.

4. **Journaled followups (see decisions/2026-07-06.md).**
   - Groot sshd stalls ~25s on cold connections (DNS/PAM, likely DoT-related — same family as the morning DNS incident). Diagnose; also reconcile the stale "Host groot is down" infra concern.
   - `agent_concerns` has "ZFS pool nas-pool status=ONLINE" filed **critical** since 2026-06-29 — almost certainly an inverted healthy-flag bug in the infra collector (`_build_nas_concerns`). Fix the collector, resolve the bogus concern.

5. **Camera alert tuning (Brian-led, in TASKS.md backlog).** `frigate-person` rule is commented out in `config/dispatch-rules.yaml`. When Brian wants it: quiet hours, Frigate zone filter, or armed-only gate via HA state. Don't re-enable without him.

## Parked — do NOT touch unless Brian asks
- **tool-agent ANTHROPIC_API_KEY** (litellm on Cortex): invalid key, silently falls back to `perplexity/sonar`. Brian explicitly abstained from API-key use.
- **russ@mercury password rotation** — on request only.
- **HA core update** 2026.3.2 → 2026.7.1 — requires restart, Brian's call.

## Rules that bit today (obey)
- Memory contract: journal decisions, file/reconcile concerns, fix stale registry first. `decisions/` is gitignored — journal locally, don't try to commit it.
- Always SSH aliases; creds only via `scripts/vault.py`; never read `.env` directly; confirm destructive ops.
- AI generation only via local `claude` CLI (`runtime.resolve_claude_command()`, `cwd=$HOME`, `encoding="utf-8"`). Never the Anthropic SDK.
- Registry files have mixed CRLF/LF line endings — check with `cat -A` before scripted edits.
- systemd unit changes: edit `deploy/ai-workstation/`, then install; the drift sentinel's EXPECTED lists in `agents/drift.py` must be updated when adding units.
