# Tasks

## Active
- [x] Run ProfX pipelines for 2 successful mornings before disabling Thor Task Scheduler — pipelines running but Telegram has network errors *(as of 2026-04-06)*
- [x] Fix ProfX Telegram delivery — `urlopen error [Errno 101] Network is unreachable` in finance_intel *(flagged 2026-04-06)*
- [x] Build custom Google Tasks MCP connector *(carried since 2026-04-01)*
  - *Superseded by gws CLI working as root + gws-mail / gws-tasks / workspace-brief skills (2026-05-12)*

## Ops — Needs Attention
- [x] Stark memory critical — 1.9Gi/1.9Gi used, 69Mi free. Top: open-webui 643M, n8n 242M, uptime-kuma 128M, npm 122M *(verified 2026-04-06)*

## Backlog

- [ ] Fine-tune camera alert rules in dispatcher (frigate-person disabled 2026-07-06 — options: quiet hours, zone filter, armed-only gate via HA)

## Completed
- [x] Set up IPVanish/OpenVPN on Ubuntu after finding IPVanish credentials; import `.ovpn` profiles via NetworkManager and save creds. *(resolved 2026-06-10 — old migrated-project backlog)*
- [x] Phase 2: Connectivity (NPM proxy for ProfX services, MinIO external access) *(resolved 2026-06-10 — old migrated-project backlog)*
- [x] Phase 3: Life Log (ClickHouse life_events table) *(resolved 2026-06-10 — old migrated-project backlog)*
- [x] Phase 4: Health/Home integrations *(resolved 2026-06-10 — old migrated-project backlog)*
- [x] Phase 5: Semantic Memory (pgvector embeddings) *(resolved 2026-06-10 — old migrated-project backlog)*
- [x] Phase 6: Proactive Intelligence *(resolved 2026-06-10 — old migrated-project backlog)*

- [x] Phase 0: Provision ProfX LXC 107 *(completed 2026-04-04)*
  - Debian 12, 4 cores, 8GB RAM, 64GB disk, IP 192.168.50.57
  - Docker CE, Python 3.11, Node.js 22, Bitwarden CLI
  - SSH keys deployed to all infra hosts
  - TrueNAS `nas-pool/profx` + NFS mount at `/mnt/nas/profx`
  - CouchDB 3.5.1 deployed, `https://couchdb.hodgespot.com` proxied via NPM
  - Repo cloned, venv, deps installed, smoke test + morning_brief passing
- [x] Phase 0.5: Repo Normalization *(completed 2026-04-04)*
  - `requirements.txt` updated with all deps
  - `run_pipeline.sh` — universal Linux launcher (vault, venv, SSH tunnels)
  - `install-cron.sh` — cron + systemd installer
  - `blunderbus-telegram.service` — systemd unit for Telegram bot
  - Cron: 6:00 prep → 6:30 brief → 7:00 ingest → 7:30 finance
- [x] Phase 1: Brain on ProfX *(completed 2026-04-06)*
  - BW_MASTER_PASS + vault secrets loaded (.env has 91 vars, `vault.py --export` works)
  - CouchDB Livesync service running (`blunderbus-couchdb-sync.service` → `obsidian-livesync` db)
  - Telegram bot systemd service running
  - All 4 cron pipelines installed and executing (morning_prep, morning_brief, monarch_ingest, finance_intel)
  - All pipeline scripts tested on ProfX
- [x] Obsidian MCP server + skill integration *(completed 2026-04-02)*
- [x] Fix SecOnion auth (Kratos session) + budget pace projections *(completed 2026-04-02)*


