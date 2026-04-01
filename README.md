# BlunderBus - Claude Code Infrastructure Agent

BlunderBus is a Claude Code project for operating the HodgeSpot home lab directly with `ssh`, `curl`, and native CLI tools. There is no wrapper service here; Claude is the operator interface.

## Architecture

The project is built around:

- `CLAUDE.md` for identity, topology, and routing
- `.claude/rules/` for always-loaded guardrails
- `.claude/skills/` for on-demand slash commands
- `.claude/agents/` for specialist subagents
- `.claude/hooks/` for safety checks before Bash execution

## Quick Start

1. Clone the repo.
   ```bash
   git clone https://github.com/brianhodgerson-cmyk/blunderbus-claude.git
   cd blunderbus-claude
   ```

2. Configure API credentials.
   ```bash
   cp .env.example .env
   ```
   Fill in the real values in `.env`.
   If your secrets already live in Vaultwarden, you can also populate `.env` from Bitwarden CLI:
   ```powershell
   .\scripts\fill-env-from-bitwarden.ps1 -Force
   ```
   The script never prints secret values. If your vault item names differ from the defaults, copy `.\\scripts\\bitwarden-env-map.example.psd1` to `.\\scripts\\bitwarden-env-map.local.psd1` and pin the item names there.
   Security Onion uses an API Client from `SOC Administration -> API Clients`; the SOC web UI username/password is not valid for the Connect API helpers in this repo.

3. Install Python dependencies.
   ```bash
   python -m pip install -r requirements.txt
   ```

4. Configure SSH access.
   Store the SSH private key in Vaultwarden and expose it through Bitwarden Desktop's SSH Agent on this machine. Then install the host aliases:
   ```powershell
   .\scripts\install-ssh-config.ps1
   ```
   The script uses the current HodgeSpot usernames by default: `root` on `cortex`, `blunderbus` on `stark`, and `brian` on the remaining hosts. Override them with `-CortexUser`, `-StarkUser`, or `-DefaultUser` if needed.
   On Windows, Bitwarden's SSH agent expects the OpenSSH Authentication Agent service to be disabled.

5. Validate the setup.
   ```bash
   ./setup.sh
   ```

6. Open Claude Code in this repo.
   ```bash
   claude
   ```

7. Start using skills.
   ```
   /system-status
   /morning-brief
   /patrol
   /security-triage
   ```

## ProfX Deploy Artifacts

Linux deploy examples for the ProfX target now live under `deploy/profx/`:

- `bootstrap.sh` installs the venv, requirements, `systemd` unit, and cron file
- `blunderbus-telegram.service` runs the Telegram bot under `systemd`
- `blunderbus.crontab` installs the repo's scheduled pipelines

## Vaultwarden-backed SSH

Vaultwarden is the key store, not the SSH transport. The intended flow is:

1. Sign Bitwarden Desktop into your Vaultwarden server.
2. Enable Bitwarden Desktop's SSH Agent and unlock the vault.
3. Install the repo's SSH host aliases into `%USERPROFILE%\.ssh\config`.
4. Confirm `ssh-add -L` shows the Bitwarden-managed key.
5. Test with `ssh cortex echo ok` before using the SSH-based skills.

The repo never stores SSH private keys. Claude should only use the local SSH agent plus host aliases such as `cortex`, `stark`, and `thor`.

## Skill Catalog

| Skill | Description | Target |
|-------|-------------|--------|
| `/system-status` | Full topology sweep | All VMs |
| `/security-triage` | IDS alerts and firewall events | SecOnion, pfSense |
| `/stack-deploy` | Docker Compose management | Cortex |
| `/home-control` | Smart home device control | Home Assistant |
| `/infra-check` | VM health | All VMs |
| `/log-query` | Centralized log search | Loki |
| `/ioc-enrich` | Threat intel lookups | VT, AbuseIPDB, Shodan |
| `/health-summary` | Metrics dashboard | Grafana, Prometheus |
| `/firewall-check` | Firewall rules and states | pfSense |
| `/nas-status` | Storage pool health | TrueNAS |
| `/camera-events` | NVR detection events | Frigate |
| `/mqtt-bridge` | IoT message pub/sub | Mosquitto |
| `/project-ops` | Git and repo management | Local |
| `/gws-setup` | Google Workspace setup | GWS |
| `/patrol` | Continuous monitoring loop | All |
| `/morning-brief` | Daily infrastructure summary | All |
| `/vault-status` | Password vault health | Vaultwarden |
| `/adguard-dns` | DNS filtering management | AdGuard Home |
| `/ollama-status` | Local LLM and GPU status | Thor, Open WebUI |
| `/portainer-ops` | Container management | Stark |
| `/proxy-check` | Reverse proxy and SSL certs | NPM |
| `/data-query` | Analytics and model proxy | Clickhouse, LiteLLM |

## Subagents

| Agent | Purpose | Model |
|-------|---------|-------|
| `security-investigator` | Deep-dive threat analysis in isolated context | Sonnet |
| `deploy-validator` | Pre and post deployment validation checks | Haiku |

## Network Topology

| VM | Host | IP | Services |
|----|------|----|----------|
| 106 | Cortex | 192.168.50.106 | Postgres, Redis, LiteLLM, Langfuse, MinIO, Clickhouse |
| 104 | Stark | 192.168.50.204 | NPM, Open WebUI, Mosquitto, Portainer |
| 101 | Thor | 192.168.50.136 | Ollama (`qwen3:14b`), RTX 4080 |
| 202 | Banner | 192.168.50.202 | Grafana, Prometheus |
| 100 | TrueNAS | 192.168.50.50 | ZFS NAS storage |
| 102 | HomeAssistant | 192.168.50.206 | Home Assistant |
| 103 | Fury | 192.168.50.103 | Security Onion IDS/IPS (read only) |
| - | Frigate | 192.168.50.205 | NVR camera system |
| - | Loki | 192.168.50.207 | Log aggregation |
| - | pfSense | `pfsense.hodgespot.com` | Edge firewall |
| - | Vaultwarden | `vaultwarden.hodgespot.com` | Password manager |

## Safety

- Hooks block destructive Bash patterns before execution.
- Rules enforce credential handling, SSH usage, and read-only system boundaries.
- SecOnion is read only. Hooks block write-like operations against VM 103.

## Project Structure

```text
blunderbus-claude/
|-- CLAUDE.md
|-- .mcp.json
|-- requirements.txt
|-- .ssh-config.example
|-- .claude/
|   |-- settings.json
|   |-- settings.local.json
|   |-- rules/
|   |-- hooks/
|   |-- skills/
|   `-- agents/
|-- scripts/
|-- setup.sh
|-- .env.example
`-- .gitignore
```
