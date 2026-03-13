# BlunderBus — Claude Code Infrastructure Agent

A purpose-built Claude Code project for managing the HodgeSpot home lab infrastructure. No Python runtime, no Docker wrappers, no abstraction layers — Claude IS the agent.

## Architecture

BlunderBus turns Claude Code into a full infrastructure operations agent by loading context-on-demand through skills (slash commands). Claude uses `ssh`, `curl`, and native CLI tools directly against your services.

```
┌─────────────────────────────┐
│     Claude Code (You)       │
│   ┌─────────────────────┐   │
│   │    CLAUDE.md         │   │  Identity + topology + routing
│   │    .claude/rules/    │   │  Always-loaded guardrails
│   │    .claude/skills/   │   │  On-demand /slash commands
│   │    .claude/agents/   │   │  Forked-context specialists
│   │    .claude/hooks/    │   │  Safety enforcement
│   └─────────────────────┘   │
│            │                 │
│     ssh / curl / psql        │
│            │                 │
└────────────┼─────────────────┘
             │
    ┌────────┴────────┐
    │  HodgeSpot LAN  │
    │  192.168.50.x    │
    └─────────────────┘
```

## Quick Start

1. **Clone the repo**
   ```bash
   git clone https://github.com/brianhodgerson-cmyk/blunderbus-claude.git
   cd blunderbus-claude
   ```

2. **Configure credentials**
   ```bash
   cp .env.example .env
   # Edit .env with your actual API keys, passwords, and tokens
   ```

3. **Validate setup**
   ```bash
   ./setup.sh
   ```

4. **Open in Claude Code**
   ```bash
   claude          # Terminal
   # Or open the folder in Claude Code Desktop
   ```

5. **Start using skills**
   ```
   /system-status          # Full topology sweep
   /morning-brief          # Daily infrastructure summary
   /patrol                 # Continuous monitoring loop
   /security-triage        # Check for threats
   ```

## Skill Catalog

| Skill | Description | Target |
|-------|-------------|--------|
| `/system-status` | Full topology sweep | All VMs |
| `/security-triage` | IDS alerts + firewall events | SecOnion, pfSense |
| `/stack-deploy` | Docker Compose management | Cortex |
| `/home-control` | Smart home device control | Home Assistant |
| `/infra-check` | VM health (CPU/mem/disk) | All VMs |
| `/log-query` | Centralized log search | Loki |
| `/ioc-enrich` | Threat intel lookups | VT, AbuseIPDB, Shodan |
| `/health-summary` | Metrics dashboard | Grafana, Prometheus |
| `/firewall-check` | Firewall rules and states | pfSense |
| `/nas-status` | Storage pool health | TrueNAS |
| `/camera-events` | NVR detection events | Frigate |
| `/mqtt-bridge` | IoT message pub/sub | Mosquitto |
| `/project-ops` | Git and repo management | Local |
| `/gws-setup` | Google Workspace setup | GWS |
| `/patrol` | Continuous /loop monitoring | All |
| `/morning-brief` | Daily scheduled briefing | All |
| `/vault-status` | Password vault health | Vaultwarden |
| `/adguard-dns` | DNS filtering management | AdGuard Home |
| `/ollama-status` | Local LLM + GPU status | Thor, Open WebUI |
| `/portainer-ops` | Container management | Stark (Portainer) |
| `/proxy-check` | Reverse proxy + SSL certs | NPM |
| `/data-query` | Analytics + model proxy | Clickhouse, LiteLLM |

## Subagents

| Agent | Purpose | Model |
|-------|---------|-------|
| `security-investigator` | Deep-dive threat analysis in isolated context | Sonnet |
| `deploy-validator` | Pre/post deployment validation checks | Haiku |

## Network Topology

| VM | Host | IP | Services |
|----|------|----|----------|
| 106 | Cortex | 192.168.50.106 | Postgres, Redis, LiteLLM, Langfuse, MinIO, Clickhouse |
| 104 | Stark | 192.168.50.204 | NPM, Open WebUI, Mosquitto, Portainer |
| 101 | Thor | 192.168.50.136 | Ollama (qwen3:14b), RTX 4080 |
| 202 | Banner | 192.168.50.202 | Grafana, Prometheus |
| 100 | TrueNAS | 192.168.50.50 | ZFS NAS storage |
| 102 | HomeAssistant | 192.168.50.206 | Home Assistant |
| 103 | Fury | 192.168.50.103 | Security Onion IDS/IPS (READ-ONLY) |
| — | Frigate | 192.168.50.205 | NVR camera system |
| — | Loki | 192.168.50.207 | Log aggregation |
| — | pfSense | pfsense.hodgespot.com | Edge firewall |
| — | Vaultwarden | vaultwarden.hodgespot.com | Password manager |

## Safety

- **Hooks**: PreToolUse hook blocks destructive bash patterns before execution
- **Rules**: Always-loaded guardrails for credentials, SSH, and read-only systems
- **Permissions**: Explicit allow/deny lists in `settings.json`
- **SecOnion**: Hardcoded read-only — hooks block any write attempt to 192.168.50.103

## Project Structure

```
blunderbus-claude/
├── CLAUDE.md                     # Agent identity + topology (≤200 lines)
├── .mcp.json                     # MCP servers (add HA later)
├── .claude/
│   ├── settings.json             # Permissions + hooks
│   ├── settings.local.json       # Personal overrides (gitignored)
│   ├── rules/                    # Always-loaded guardrails
│   ├── hooks/                    # Safety enforcement scripts
│   ├── skills/                   # 22 on-demand slash commands
│   └── agents/                   # 2 specialist subagents
├── scripts/                      # Helper bash scripts
├── setup.sh                      # First-run validation
├── .env.example                  # Credential template
└── .gitignore
```

## Future

- **MCP**: Add Home Assistant MCP server for native tool access
- **Portable layer**: Ollama/Open WebUI compatibility for local inference
- **Additional skills**: Expand as infrastructure grows
