# BlunderBus - Claude Code Infrastructure Agent

BlunderBus is a Claude Code project for operating the HodgeSpot home lab directly with `ssh`, `curl`, and native CLI tools. There is no wrapper service; Claude is the operator interface. It runs on **AI-Workstation** (Ubuntu, Proxmox VM 109, `192.168.50.208`).

## Architecture

- `CLAUDE.md` — identity, topology, routing
- `.claude/rules/` — always-loaded guardrails
- `.claude/skills/` — on-demand slash commands
- `.claude/agents/` — specialist subagents (`finance-agent`, `security-investigator`, `deploy-validator`)
- `.claude/hooks/` — Bash safety checks before execution
- `scripts/` — pipeline and agent code (`daily_brief.py`, `monarch_ingest.py`, `agents/`)
- `memory/` + `decisions/` — agent memory system (registry, decisions journal, `agent_concerns` in Postgres); see `docs/agent-memory-architecture.md`
- `mcp-servers/` — BlunderBus MCP server (`bb-mcp.service`)
- `ops-ui/` — Ops dashboard (served from Vision, `ops.hodgespot.com`)
- `deploy/ai-workstation/` — canonical systemd user units + `install.sh`

### Daily pipeline (systemd user timers, America/Chicago)

| Time | Unit | What it does |
|------|------|--------------|
| 05:15 | `blunderbus-monarch-ingest.timer` | Monarch Money → ClickHouse (currently disabled pending cookie refresh) |
| 06:00 | `blunderbus-daily-brief.timer` | Agent fan-out (finance/infra/workspace) → AI synthesis → Obsidian daily note + Discord + ops.hodgespot.com |

Long-running services: `bb-mcp.service` (MCP server), `bbm-api.service` (Memory FastAPI), `blunderbus-couchdb-sync.service`. Interactive chat is the Hermes gateway (`hermes-gateway.service`) → Discord `#general`.

## Quick Start (AI-Workstation / Linux)

1. Clone the repo.
   ```bash
   git clone git@github.com:brianhodgerson-cmyk/blunderbus-claude.git
   cd blunderbus-claude
   ```

2. Create the venv and install dependencies.
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. Configure secrets.
   ```bash
   cp .env.example .env   # set BW_MASTER_PASS; everything else comes from Vaultwarden
   ```
   Runtime secrets load via `scripts/vault.py --export` (Vaultwarden through the Bitwarden CLI — use `~/.local/bin/bw-vaultwarden-2024` on this host).

4. Configure SSH. A local key file (`~/.ssh/id_ed25519`) is deployed to all hosts; install host aliases into `~/.ssh/config` using `.ssh-config.example` as the canonical template. Test with `ssh cortex echo ok`.

5. Install the systemd user units.
   ```bash
   ./deploy/ai-workstation/install.sh
   ```

6. Validate and start.
   ```bash
   ./setup.sh
   claude
   ```

7. Use skills: `/system-status`, `/morning-brief`, `/patrol`, `/security-triage`.

## Skill Catalog

| Skill | Description | Target |
|-------|-------------|--------|
| `/system-status` | Full topology sweep | All VMs |
| `/security-triage` | IDS alerts and firewall events | SecOnion, ASUS router |
| `/stack-deploy` | Docker Compose management | Cortex |
| `/home-control` | Smart home device control | Home Assistant |
| `/infra-check` | VM health | All VMs |
| `/log-query` | Centralized log search | Loki |
| `/ioc-enrich` | Threat intel lookups | VT, AbuseIPDB, Shodan |
| `/health-summary` | Metrics dashboard | Grafana, Prometheus |
| `/nas-status` | Storage pool health | TrueNAS |
| `/camera-events` | NVR detection events | Frigate |
| `/mqtt-bridge` | IoT message pub/sub | Mosquitto |
| `/obsidian` | Vault notes via Local REST API | Obsidian |
| `/vault-status` | Password vault health | Vaultwarden |
| `/adguard-dns` | DNS filtering management | AdGuard Home |
| `/ollama-status` | Local LLM and GPU status | Thor, Open WebUI |
| `/portainer-ops` | Container management | Stark |
| `/proxy-check` | Reverse proxy and SSL certs | NPM |
| `/data-query` | Analytics and model proxy | ClickHouse, LiteLLM |
| `/morning-brief` `/patrol` `/project-ops` `/gws-*` | Daily ops, monitoring, git, Google Workspace | Various |

## Safety

- A PreToolUse hook (`.claude/hooks/safety-check.sh`) blocks destructive Bash patterns before execution.
- Rules enforce credential handling, SSH aliases, and confirmation for destructive operations.
- Fury (SecOnion) is a normal operational target; destructive/service-affecting changes still require operator confirmation.

## Network

See the topology tables in `CLAUDE.md` — Proxmox node `Multiverse` hosts all VMs/LXCs (Cortex, Stark, Thor, Heimdall, Fury, Banner, Groot, Loki, Ultron, Vision, Hawkeye, Jarvis, AI-Workstation).
