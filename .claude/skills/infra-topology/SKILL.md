---
name: infra-topology
description: Full HodgeSpot fleet topology — VM/LXC tables with IPs, SSH aliases, users, roles, per-host gotchas, Grafana/Prometheus API patterns on Banner, and service URLs. Use when you need a host's IP, VMID, SSH user, service port, or the Grafana API.
allowed-tools: Bash
---

# Fleet Topology — HodgeSpot

All VMs run on Proxmox node `Multiverse`.

## Virtual Machines (QEMU)

| VM | Host | IP | SSH Alias | SSH User | Status | Role |
|----|------|----|-----------|----------|--------|------|
| 101 | Thor | 192.168.50.136 | `thor` | `brian` | stopped (verified 2026-07-06) | Workstation / Ollama (`qwen3:14b`), RTX 4080 GPU |
| 100 | Heimdall | 192.168.50.50 | `heimdall` (`truenas` legacy) | `truenas_admin` | running | TrueNAS SCALE — NAS storage, ZFS pools, PCIe passthrough |
| 102 | Jarvis | 192.168.50.206 | `homeassistant` | `root` | running | Home Assistant (SSH via Terminal & SSH add-on) |
| 103 | Fury | 192.168.50.103 | `fury` | `brian` | stopped (verified 2026-07-06) | IDS/IPS (SecOnion) |
| 104 | Stark | 192.168.50.204 | `stark` | `blunderbus` | running | NPM, Open WebUI, Mosquitto MQTT, Portainer. QEMU guest agent (`qm guest exec 104`) |
| 105 | hawkeye | unknown | — | — | stopped | — |
| 106 | Cortex | 192.168.50.106 | `cortex` | `root` | running | Docker stack: postgres, redis, litellm, langfuse, minio, clickhouse, mcp-gateway, pixel-dashboard. **ProxyJump through Stark** |
| 109 | AI-Workstation | 192.168.50.208 | local / `ai-workstation` | `brian` | running | BlunderBus/Hermes runner, RTX 4080 passthrough, Stream Deck, local STT, desktop Hermes |

## Containers (LXC)

LXC containers are minimal Debian — **SSH user is always `root`**. No non-root users exist unless explicitly created.

| VM | Host | IP | SSH Alias | Status | Role |
|----|------|----|-----------|--------|------|
| 200 | Groot | 192.168.50.53 | `groot` | running | AdGuard Home DNS (`:80` web, `:53` DNS) |
| 202 | Banner | 192.168.50.202 | `banner` | running | Grafana (`:3000`), Prometheus (`:9090`), Alertmanager (`:9093` → dispatcher webhook), InfluxDB |
| 205 | Hawkeye | 192.168.50.205 | `hawkeye-nvr` | running | Frigate NVR (`:5000`) |
| 207 | Loki | 192.168.50.207 | `loki` | running | Loki log aggregation (`:3100`) |
| 108 | Mercury | 192.168.50.109 | `mercury` | running | Russ's TLS workspace (memory-architecture tenant); user `russ` exists |
| 210 | Vision | 192.168.50.210 | `vision` | running | BlunderBus Ops UI (Next.js `:3030`), MCP server (`:8788`), vision_server (`:8787`), Frigate MQTT bridge |
| 107 | ProfX | 192.168.50.57 | `profx` | decommissioned | Former BlunderBus brain. Cold archive only — never an incident. |

## Proxmox Host

| Host | IP | SSH Alias | SSH User | Role |
|------|----|-----------|----------|------|
| Multiverse | 192.168.50.100 | `proxmox` | `root` | Proxmox VE hypervisor |

Fallback access: `pct exec <VMID>` (LXC) and `qm guest exec <VMID>` (QEMU with guest agent — currently only Cortex/106) via `proxmox`.

## Grafana API (Banner)

Prefer SSH over browser — direct JSON, no auth token needed from localhost:

```bash
ssh banner 'curl -s http://localhost:3000/api/health'
ssh banner 'curl -s http://localhost:3000/api/datasources'
ssh banner 'curl -s "http://localhost:3000/api/dashboards/home"'
```

Authenticated endpoints (user/org management): add `-u admin:$GRAFANA_PASS`.

Prometheus lives on the same host: `ssh banner 'curl -s http://localhost:9090/api/v1/alerts'`. Alert rules: `/etc/prometheus/rules/host.yml`; Alertmanager config: `/etc/prometheus/alertmanager.yml` (receiver = BlunderBus dispatcher `:8790`).

## Other services

- pfSense: NOT installed — ignore all pfSense references
- Vaultwarden: `vaultwarden.hodgespot.com`
- AdGuard Home: `http://192.168.50.53:80` (Groot) — web UI and API both HTTP :80 (HTTPS/:3000 do not answer)
- WireGuard VPN: wg-easy on Stark, UI `http://wg.hodgespot.com` (:51821), UDP 51820 forwarded from router WAN
- Obsidian / daily notes: pipeline repo `/home/brian/blunderbus-claude` on AI-Workstation; Local REST API `https://127.0.0.1:27124`, token in Vaultwarden "Obsidian API" (field `token`)
- Google Workspace: `gws` CLI, authenticated as `bh@hodgespot.com`; skills `gws-mail`, `gws-tasks`, `workspace-brief`
- Homepage dashboard: `http://192.168.50.204:3000`
