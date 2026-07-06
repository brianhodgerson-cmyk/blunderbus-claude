# BlunderBus Repo Review — 2026-07-06

Full-repo audit: code quality, security, config drift, git hygiene, and new use-case ideas.
Three parallel review passes (Python codebase, `.claude/` config, repo hygiene), key findings independently verified.

---

## 0. Verified current state (live host, 2026-07-06) — supersedes doc claims

Operator corrections + direct verification on AI-Workstation:

| Fact | Verified |
|---|---|
| Runtime host | AI-Workstation (this host). ProfX decommissioned; Windows host off |
| Scheduling | **User systemd timers**, not cron: `blunderbus-daily-brief.timer` → `run_pipeline.sh daily_brief.py` (enabled). **No cron anywhere** — CLAUDE.md's `/etc/cron.d/blunderbus` story is fully obsolete |
| ⚠️ Brief timer fires at **07:45 UTC = 02:45 AM CDT** | Last run Mon 02:45 CDT. CLAUDE.md says 7:30 AM pipeline — either intentional (pre-wake) or a UTC/local bug. Decide + document |
| ⚠️ `blunderbus-monarch-ingest.timer` is **disabled** ("until Monarch auth/cutover") | Finance ingest not running → ClickHouse finance data is stale; finance agent synthesizes from old snapshots. Standing gap — file a concern or re-enable |
| Messaging | **Discord, via two paths**: (1) `daily_brief.py` posts direct to Discord API (`send_discord`, ~L918); (2) interactive = **Hermes gateway** (`hermes-gateway.service`) → #general (JARVIS). **Telegram fully retired** |
| `mcp-servers/anthropic-bridge/` | **LIVE** — runs as `bb-mcp.service` (BlunderBus MCP server). Earlier "abandoned?" note is wrong |
| Also running | `bbm-api.service` (Memory FastAPI), `blunderbus-couchdb-sync.service` |
| Hermes | `~/.hermes` — SOUL.md is the generic persona; the operational facts live in `~/.hermes/memories/MEMORY.md` (bb-memory MCP tools, memory contract, Vaultwarden CLI pin `bw-vaultwarden-2024`, vault at `/mnt/truenas/proxmox-share/Blunderbus`) |
| Live systemd units exist **only** in `~/.config/systemd/user/` | The repo's units (`scripts/*.service`, `deploy/profx/`) are all wrong/ProfX-era. **The real deployment config is not in git** |

**Impact on recommendations below:** delete Telegram assets (bot, services, `.claude/telegram-history/`) rather than fix the auth bug — but do NOT commit `telegram_bot.py` as-is if keeping for reference. `deploy/profx/` → replace with `deploy/ai-workstation/` mirroring the actual `~/.config/systemd/user/` units. CLAUDE.md Automation Pipeline + Obsidian sections need a rewrite against this table, and should absorb the current facts from Hermes `MEMORY.md`.

---

## 1. Security findings

### CRITICAL — Telegram bot fails open on auth
`scripts/telegram_bot.py:216-218, 331` — `_allowed_ids()` returns an empty set if `TELEGRAM_ALLOWED_USER_IDS` is unset or malformed, and the gate is `if allowed and user.id not in allowed: return`. Empty allowlist = **every Telegram user on Earth accepted**, with their text piped into `claude --print --continue` running with full BlunderBus context (SSH aliases, infra access).
**Fix:** hard-fail at startup if the allowlist is empty; add one unit test.

### CRITICAL — Plaintext credential in repo root, unignored
- `command.md` — SSH tunnel command for `russ@192.168.50.109` (Kasm VNC :6901)
- `Untitled.md` — bare 16-char string, almost certainly that account's password

Both untracked but **not gitignored** — one `git add -A` pushes a working login to GitHub. They've sat there since ~May.
**Fix:** rotate the russ password, delete both files, add `/Untitled*` and `command.md` patterns to `.gitignore`.

### HIGH — Hardcoded ClickHouse credentials in dead Windows scripts
`scripts/run_finance_intel.ps1:114,128`, `scripts/run_daily_brief.ps1:125,144` — user/password literals committed to git. LAN-only DB, but they're in history.
**Fix:** delete all `.ps1`/`.bat` scripts (Windows scheduler retired 2026-05-12); rotate the ClickHouse password since it's in git history.

### HIGH — Safety hook is stale and bypassable
`.claude/hooks/safety-check.sh:34-43` still enforces Fury/192.168.50.103 as read-only (retired 2026-05-01), and matches on raw IP substrings — so `ssh fury 'systemctl restart ...'` (the mandated alias style) never triggers it. The destructive-pattern list is naive substring matching.
**Fix:** rewrite to match on aliases + IPs, drop Fury read-only, or delete and rely on rules + confirmation.

### MEDIUM — settings.local.json allowlist graveyard
~15 retired `powershell` entries with `C:\blunderbus-claude` paths, two entries explicitly allowing `echo $env:GITHUB_TOKEN` (contradicts `rules/credentials.md`), and a broad `Read(//root/**)`.
**Fix:** prune to Linux-only entries.

### MEDIUM — Sensitive data unignored in working tree
`scripts/.config/Bitwarden CLI/data.json` (BW session state path), `.claude/telegram-history/` (personal chat logs). Also: `decisions/2026-05-14.md` (tracked, in the 5 **unpushed** commits) contains detailed personal finances — decide whether `decisions/` and `memory/finance/` should ever reach GitHub before pushing.

**Good news:** git history is clean — no tokens/keys/cookies ever committed. No `shell=True` anywhere; all subprocess calls use list args.

---

## 2. ProfX drift — docs contradict reality

CLAUDE.md declares ProfX decommissioned, then treats it as live in five places:

| Location | Stale claim |
|---|---|
| CLAUDE.md L129, L137 | "SSH config on ProfX is hand-maintained" |
| CLAUDE.md L141-148 | Automation Pipeline "runs on ProfX via /etc/cron.d/blunderbus" |
| CLAUDE.md L165 | "`claude` is at /usr/bin/claude on ProfX" |
| CLAUDE.md L217 | Obsidian relaunch "via Kasm GUI on ProfX" |
| CLAUDE.md | Groot AdGuard web `:80` in table vs `:3000` in Other Services |

Deployment artifacts all point at nonexistent ProfX paths:

| File | Problem |
|---|---|
| `scripts/blunderbus-telegram.service` | `ExecStart=/opt/blunderbus-claude/...`, `ReadWritePaths=/mnt/nas/profx` |
| `deploy/profx/blunderbus-telegram.service` | Third divergent layout (`/opt/blunderbus/venv`, `User=blunderbus`) |
| `scripts/blunderbus-couchdb-sync.service:13` | `/opt/blunderbus-claude/.venv/...` |
| `deploy/profx/blunderbus.crontab` | `/opt/blunderbus-claude/scripts/run_pipeline.sh` |
| `scripts/install-cron.sh:20-32` | Installs **retired** pipeline incl. `morning_prep.py` (file deleted) — running it creates a cron job that fails every 6 AM |
| `scripts/finance_intel.sh:17`, `monarch_ingest.sh:16` | Source `/root/.env` (ProfX-root era) |

Other stale config:

- `.claude/agents/security-investigator.md` — "SecOnion READ-ONLY", "check pfSense logs"
- `.claude/skills/firewall-check/` — entire skill is pfSense; dead
- `.claude/skills/security-triage/SKILL.md:12` — SecOnion read-only claim
- `.claude/skills/adguard-dns/` — uses `http://`; CLAUDE.md mandates `https://` + `-k`
- `.claude/skills/obsidian/SKILL.md` — "on Windows host"
- `consolidate_infra_learnings.py:40`, `consolidate_learnings.py:43` — still treat ProfX as alertable host
- `rules/ssh-safety.md` — says keys live in SSH agent (CLAUDE.md: local key file, no agent); alias list omits `groot`, `vision`, `ultron`, `heimdall`, `proxmox`, `hawkeye-nvr`
- Top-level `claude/` dir — stale untracked snapshot of `.claude/` with old pfSense-era skills; an agent globbing `**/SKILL.md` ingests the wrong versions. Delete.
- `.mcp.json` — skills depend on `mcp__blunderbus__*` tools not declared anywhere in-repo (user-scope config; document it)

---

## 3. Git hygiene

State: last commit **2026-05-14**, 5 commits unpushed, 80 modified files (~31 are pure CRLF→LF noise), plus the entire memory system, agents, tests, docs, and ops-ui untracked. Two months of work exists only on this disk.

**Commit plan (in order):**

1. `.gitattributes` + line-ending normalization commit (isolates the 31 no-op rewrites)
2. Memory system: `scripts/blunderbus_memory/`, `scripts/agents/`, `consolidate_*.py`, `rules/memory-contract.md`, `tests/`, memory docs
3. Daily-brief pipeline: `daily_brief.py`, `note_template.py`, `run_pipeline.sh`, cron changes
4. Vault tooling: `vault_*.py`, `bw-vaultwarden.sh`, `vault-get` skill
5. New skills/agents: `gws-*`, `workspace-brief`, `dnc-log`, `learnings-consolidate`, `obsidian`, `finance-agent.md`
6. Bots/services: `couchdb_sync.py`, systemd units, `telegram_bot.py` (after the auth fix), `mcp-servers/anthropic-bridge/`
7. `ops-ui/` (one commit; its own .gitignore is sane)
8. Docs + CLAUDE.md + README + `.gitignore`
9. `decisions/` — only after the privacy call

**Branches:** all 4 local `claude/*` branches point at `365d679` (= origin/main, ancestor of main) — fully merged, safe to delete. Then push.

**.gitignore additions:** `.venv/` (**314 MB unignored** — worst offender), `OS/`, `_scratch/`, `.trash/`, `.tmp/`, `data/`, `dashboard.html`, `mcp-obsidian-stderr.txt`, `command.md`, `/Untitled*`, `scripts/.config/`, `.claude/telegram-history/`, `.claude/couchdb-sync-seq.txt`, `.claude/scheduled_tasks.lock`, `.claude/launch.json`, `*.bak`

**Delete:** `command.md` + `Untitled.md` (after rotation), `Untitled{,1,2,3}.base`, `Untitled.canvas`, `OS/` (accidental second vault), `dashboard.html`, `mcp-obsidian-stderr.txt`, `scripts/requirements.txt` (byte-identical to root `requirements.txt`), all Windows `.ps1`/`.bat`/`.psd1` scripts, `.claude/skills/home-control-workspace/`, top-level `claude/`

**Move out of repo (to vault/NAS):** `K1.pdf`, `Household Budget.xlsx`, `2026-03-26.pdf`, stray `2026-*.md`, `Libbey app...md`

**README.md:** stale clone URL (`brianhodgerson-cmyk` vs origin `brian-hodgespot`), Windows-centric Quick Start, ProfX deploy section, no mention of memory system / daily_brief / agents / ops-ui. Rewrite.

---

## 4. Code quality

| Severity | Finding |
|---|---|
| MEDIUM | Dead code cluster: `monarch_login.py` (legacy rate-limited flow, still advertised at `monarch_ingest.py:291`), `finance_intel.py` (1,513 lines, superseded by `daily_brief.py`), `discord_bot.py` (773 lines, no service/cron references) — ~2,400 lines. Delete or move to an `attic/` |
| MEDIUM | Triplicated consolidators: `consolidate{,_finance,_infra}_learnings.py` share atomic-write, note-scanning, hostname logic (943 lines total); host lists already drifting. Merge into one script + per-agent config |
| MEDIUM | Silent exception swallowing: `couchdb_sync.py:178` (`except Exception: pass` hides sync corruption), broad excepts in `agents/infra.py:186,260,296`, `vault.py:98,223` |
| LOW | `morning_brief_push.py:82-96` embeds a hardcoded HOSTS probe table — registry should be canonical (already flagged in `agent-memory-architecture.md:168`) |
| LOW | `telegram_bot.py` calls claude with `cwd=PROJECT_DIR`, contradicting CLAUDE.md's "must be `~`" rule (possibly intentional — document it) |
| LOW | Tests: 4 files / 421 lines. Zero coverage of vault loading, Monarch upserts, Telegram auth gate (one test would have caught the CRITICAL), note_template |

---

## 5. Structural recommendation

The root cause of most hygiene issues is **vault/repo conflation** — the Obsidian vault and the codebase share one directory, so half of `.gitignore` fights the vault and personal files (tax docs, budgets) live in a git worktree. Medium-term: split into `~/blunderbus` (code, clean checkout) and `~/vault` (Obsidian data), with the pipeline writing notes via path config. This also makes the "agent reads its own repo" story cleaner.

---

## 6. New use-case ideas

1. **Registry-driven topology, autogenerated docs.** `topology-sweep.sh` + Proxmox API → regenerate the CLAUDE.md network table and `memory/registry/inventory/` nightly. Kills the entire class of ProfX/pfSense drift found in this review — docs can't lie if they're generated.
2. **Nightly repo-janitor agent.** Commits `decisions/` and `memory/` deltas, flags unignored sensitive files (would have caught `Untitled.md`), reports uncommitted-work age in the morning brief. The 2-months-uncommitted problem never recurs.
3. **Drift sentinel.** Weekly agent diffs *documented* state (registry, CLAUDE.md, skills) against *observed* state (running containers, listening ports, cron entries) and files `agent_concerns` for mismatches. Turns this one-off review into a standing capability.
4. **Concern escalation tiers.** `agent_concerns` severity≥high → immediate Telegram push instead of waiting for the 7:30 brief; auto-resolve stale concerns after N days with a "went quiet" note.
5. **Doorstep digest.** Frigate events → vision model on Thor (qwen-VL or LLaVA on the 4080) → "who came by yesterday" section in the morning brief; person-vs-courier classification via MQTT bridge already on Vision.
6. **Pool chemistry vision skill.** Photo of test strip via Telegram → vision model → append to `Pool Chemical Log.md` + dosing recommendation. You already keep the log manually.
7. **Backup verification agent.** Weekly: Heimdall ZFS snapshot/replication audit + Proxmox vzdump status → concern if any dataset unprotected >7 days. Backups are the one thing nobody checks until it's too late.
8. **Meeting-prep agent.** gws calendar lookahead → match attendees to `memory/registry/people/` → auto-generate prep notes (the Whitney 1on1 note shows you're doing this by hand).
9. **Utility/energy ingest.** HA energy sensors → ClickHouse alongside finance → the finance agent can correlate "electric bill up 22%" with actual kWh data.
10. **Voice ops via Stream Deck.** Local STT on AI-Workstation → `claude --print` with skill routing → push-to-talk BlunderBus for quick infra checks without a keyboard.

---

## Suggested execution order (revised after operator corrections)

1. **Today:** rotate russ password + ClickHouse password; delete `command.md`/`Untitled.md`; decide on Monarch ingest (re-enable or file concern) and the 02:45 AM brief timer
2. **This week:** `.gitignore` fixes; delete Telegram assets, dead Windows scripts, `deploy/profx/`, stale branches; add `deploy/ai-workstation/` with the real systemd user units; commit plan steps 1-8; push
3. **Next:** CLAUDE.md/README/skills "doc truth" pass — systemd timers + Discord/Hermes, no cron/Telegram/pfSense/ProfX-active; rewrite or delete safety hook
4. **Then:** pick 1-2 use cases (repo-janitor + drift sentinel compound best — this review found doc drift within weeks of each migration; the sentinel makes that impossible to miss)
