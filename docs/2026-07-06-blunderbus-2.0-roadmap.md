# BlunderBus 2.0 — Modern Agent Techniques Roadmap

*Written 2026-07-06, following the full repo review/cleanup. Grounded in the current state of the agent ecosystem (mid-2026) and mapped onto what BlunderBus already has.*

## Where BlunderBus stands

The repo is ~a year old, but several of its core bets aged well:

- **Markdown memory (registry + decisions journal)** — 2026 consensus is that plain files the human and agent co-author beat vector DBs for exactly this use case. Keep it.
- **MCP servers (bb-mcp, vision :8788)** — MCP won; it's now under Linux Foundation governance with native support in every major client. You were early.
- **litellm + langfuse on Cortex** — the observability stack most people are just now adopting. It's deployed but *underused* (see #5).
- **Subagents with private memory (finance-agent)** — matches the current "context isolation per specialist" pattern.

The architecture's one dated assumption: **everything is either a timer or a chat message.** The field moved to *ambient/event-driven agents* — agents subscribed to event streams that act when something happens, not when a clock fires or a human types.

## The seven upgrades, in priority order

### 1. Event-driven agent dispatch (the big one)

Today: 05:15 ingest, 06:00 brief, Discord chat. Everything else in the house *generates events nobody consumes*: Mosquitto MQTT (Stark), Frigate detections (Hawkeye), Prometheus alerts (Banner), Loki log streams, HA state changes, systemd unit failures.

Build one small dispatcher on AI-Workstation: subscribe to MQTT topics + an HTTP webhook endpoint, map event → agent invocation (`claude -p` headless with the right skill), rate-limit, log to the decisions journal. Examples:

- `frigate/events` person-detected while alarm armed → vision agent summarizes clip, pushes Discord
- Alertmanager webhook (disk, service down) → infra agent triages *before* you look: gathers `journalctl`, correlates Loki, files a concern with suggested fix
- `OnFailure=` on every blunderbus systemd unit → agent reads the failed unit's log and posts a diagnosis (today a failed 06:00 brief is silent until you notice)
- HA automation webhooks → "the garage has been open 20 min and everyone left" class of reasoning HA's rules engine can't do

This converts the fleet's existing telemetry into agent triggers with ~one new component.

### 2. Voice: bridge the two existing stacks (desk + house)

*(Revised 2026-07-06 after auditing the live desk setup.)* There are already two voice systems, and they serve different rooms:

- **Desk (AI-Workstation)**: `canary-stt.service` — warm NVIDIA Canary-Qwen STT on `:8765` (GPU, better than faster-whisper) — plus `jarvis-streamdeck` push-to-talk: DICT (type into focused app), ASK DISCORD (voice → Hermes → #general shared session), ASK VOICE (spoken reply via edge-tts). udev-managed since 2026-07-06: the service starts on deck plug, retries every 30 s instead of crash-looping. Units + udev rule are canonical in `deploy/ai-workstation/`.
- **House (HA Assist)**: wake-word, room satellites, device control — not yet LLM-wired.

Don't pick one; converge the plumbing:

1. **Share the STT** — wrap the warm Canary server in a Wyoming-protocol shim so HA's pipeline uses it instead of running its own whisper. One GPU model serves desk + house.
2. **Share the brain** — point HA Assist's conversation agent at litellm (tool-calling model; Qwen3 8B is the 2026 community pick, skip reasoning models for voice) and converge on the same Hermes/BlunderBus context so desk and house are one JARVIS.
3. **Swap edge-tts → Piper** on the desk lane — the only remaining cloud dependency; HA needs Piper anyway, share one instance. (Trade-off: edge-tts voices sound better; test Piper's `en_US-*-high` voices before committing.)

### 3. Drift sentinel (proved necessary *today*)

This session alone found: Ultron deleted but documented as running, Mercury running but undocumented, Thor/Fury stopped but documented running, the workstation SSH key missing from the entire fleet, guest-agent availability wrong. A nightly agent run that compares registry/CLAUDE.md against reality (`qm list`/`pct list`, ssh-alias reachability, DNS records, systemd timer states, docker ps per host) and files concerns for diffs. The memory contract already has the substrate (`agent_concerns`); this is a ~1-day skill.

### 4. SSH certificate authority instead of key-copying

Today's fleet fix was appending one pubkey to 10 hosts by hand (via side-channels, because the key wasn't there). The modern pattern: a small SSH CA (key in Vaultwarden), hosts trust the CA once (`TrustedUserCAKeys`), workstations get short-lived signed certs. New machine = sign a cert, not touch 10 `authorized_keys`. Also fixes the "ProfX-era keys still trusted everywhere" problem — old keys expire instead of accumulating.

### 5. Agent evals + tracing through the stack you already run

Langfuse sits on Cortex mostly idle. Route the daily brief's `claude -p` calls and agent runs through litellm with langfuse callbacks: every agent run gets a trace, cost, latency, and a place to score outputs. Then add a tiny eval set (10–20 canned infra/finance questions with expected behaviors) run weekly — catches prompt/model regressions the way unit tests catch code regressions. This is the difference between "the brief seems worse lately" and knowing.

### 6. Progressive skills + hooks hardening

Current Claude Code layering advice: skills + MCP for 80% of workflows, hooks for deterministic guardrails, subagents for heavy context, agent teams for parallel work. Concretely for this repo: (a) move the safety rules that *must* hold (no rm -rf, confirm restarts) from prose into PreToolUse hooks — deterministic, not vibes; (b) split CLAUDE.md's bulk (topology tables, ClickHouse runbook) into on-demand skills so the always-loaded context shrinks; (c) use agent teams for big jobs like "audit all 10 hosts in parallel."

### 7. Memory: consolidation pass + optional semantic index

Keep markdown. Add two things: (a) a periodic consolidation agent (like the finance learnings consolidator, generalized) that merges duplicate registry facts, expires stale ones, and prunes resolved concerns — temporal-validity discipline without a graph DB; (b) *optionally*, an embedding index over registry + decisions + Obsidian vault (ClickHouse can store vectors; you already run it) so agents can semantically search "have we seen this failure before?" across a year of journal entries.

## What NOT to do

- **Don't adopt a multi-agent framework** (LangGraph/CrewAI/AG2). Your orchestration is `daily_brief.py` + Claude Code subagents — simpler and debuggable. Frameworks earn their complexity at team scale, not homelab scale.
- **Don't replace markdown memory with a vector DB or knowledge graph.** The research favors your current design for human-in-the-loop systems; graphs (Zep/Graphiti) win benchmarks but cost real operational overhead.
- **Don't install third-party MCP servers/skills casually.** Snyk found malicious payloads in ~2% of published agent skills; the registry verification story is immature. Keep writing your own; vet anything external.

## Suggested sequence

1. Drift sentinel (#3) — smallest, immediately useful, exercises the concerns table
2. systemd `OnFailure=` agent hook (#1-lite) — one unit file change, kills silent failures
3. Langfuse tracing (#5) — config, not code
4. MQTT/webhook dispatcher (#1 full)
5. Voice pipeline (#2)
6. SSH CA (#4)
7. Consolidation + semantic index (#7), hooks/skills refactor (#6) as ongoing hygiene
