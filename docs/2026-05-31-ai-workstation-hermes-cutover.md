---
date: 2026-05-31
type: report
source: jarvis
tags: [infra, hermes, jarvis, ai-workstation, profx, gateway, dashboard]
---

# AI-Workstation Hermes/Jarvis Cutover Log

## Executive Summary

Jarvis/Hermes primary runtime was moved from ProfX to AI-Workstation (`192.168.50.208`). AI-Workstation is now the live Hermes gateway host and ProfX is retained as rollback/control-plane backup.

Current state at log time (`2026-05-31 21:32:51 CDT`):

| Component | State |
|---|---|
| AI-Workstation Hermes gateway | ✅ active |
| AI-Workstation dashboard | ✅ listening on `127.0.0.1:9119` |
| Dashboard Chat tab | ✅ embedded TUI enabled and PTY bridge verified |
| ProfX Hermes gateway | ✅ stopped/disabled backup only |
| Discord bot | ✅ connected as `jarvis#6146` during cutover verification |
| MCP bb-memory | ✅ enabled and repointed to AI repo path |
| Local Hermes smoke test on AI | ✅ `AI-CUTOVER-OK` returned |

## Live Primary Host

```text
Host:       AI-Workstation
SSH alias:  ai-workstation / ai
IP:         192.168.50.208
Hermes:     /home/brian/.hermes/hermes-agent
Hermes bin: /home/brian/.local/bin/hermes -> /home/brian/.hermes/hermes-agent/venv/bin/hermes
BlunderBus: /home/brian/blunderbus-claude
Gateway:    systemctl --user status hermes-gateway.service
Dashboard:  http://127.0.0.1:9119
Chat GUI:   http://127.0.0.1:9119/chat
```

## ProfX Rollback Host

```text
ProfX gateway service: /etc/systemd/system/hermes-gateway.service
Expected state:        disabled + inactive
Role:                  rollback/control-plane backup only
```

Rollback from ProfX:

```bash
ssh ai-workstation 'systemctl --user stop hermes-gateway.service'
sudo systemctl enable --now hermes-gateway.service
```

## Actions Completed

1. Confirmed ProfX Hermes runtime, gateway, Discord config, Codex OAuth, sessions, skills, and memory state.
2. Confirmed AI-Workstation at `192.168.50.208` with SSH access, hostname `AI-Workstation`, Ubuntu 24.04, RTX 4080, and repo at `/home/brian/blunderbus-claude`.
3. Added local SSH config mapping on ProfX:

   ```sshconfig
   Host ai-workstation ai
     HostName 192.168.50.208
     User brian
     ConnectTimeout 5
     StrictHostKeyChecking accept-new
     PreferredAuthentications publickey
   ```

4. Synced Hermes source/runtime from ProfX to AI-Workstation, excluding venv/cache/build artifacts.
5. Created AI Python 3.12 venv and installed Hermes editable.
6. Snapshotted AI Hermes directory before import under:

   ```text
   /home/brian/.hermes-migration-backups/pre-import-<timestamp>.tgz
   ```

7. Copied selected ProfX brain state to AI:
   - `~/.hermes/config.yaml`
   - `~/.hermes/.env` `[REDACTED]`
   - `~/.hermes/auth.json` `[REDACTED]`
   - `~/.hermes/channel_directory.json`
   - `~/.hermes/state.db*`
   - `~/.hermes/sessions/`
   - `~/.hermes/skills/`
   - `~/.hermes/memories/`
   - Google credential/token files if present `[REDACTED]`
8. Patched AI Hermes config path references from `/opt/blunderbus-claude` to `/home/brian/blunderbus-claude`.
9. Verified AI `hermes status --all` / `hermes doctor` enough for core operation.
10. Installed missing gateway deps (`discord.py`, Telegram package) on AI.
11. Verified AI local Hermes chat before cutover: `AI-HERMES-OK`.
12. Staged AI user systemd gateway unit at:

    ```text
    /home/brian/.config/systemd/user/hermes-gateway.service
    ```

13. Stopped ProfX system Hermes gateway after Brian approved cutover.
14. Enabled linger for Brian on AI-Workstation.
15. Enabled and started AI user gateway:

    ```bash
    systemctl --user enable --now hermes-gateway.service
    ```

16. Disabled and reset-failed ProfX system gateway to prevent split-brain on reboot.
17. Verified AI gateway active, Discord connected, cron ticker running, kanban dispatcher embedded.
18. Verified AI post-cutover local Hermes chat: `AI-CUTOVER-OK`.
19. Verified AI resources during cutover:

    ```text
    RAM: 15Gi total / ~8.5Gi available
    Disk /: 484G total / 460G free / 6% used
    GPU: NVIDIA RTX 4080, 16GB, low utilization, ~48C
    ```

20. Started Hermes dashboard on AI at `127.0.0.1:9119` with embedded TUI:

    ```bash
    hermes dashboard --host 127.0.0.1 --port 9119 --no-open --tui --skip-build
    ```

21. Installed missing dashboard/web deps:
    - `fastapi`
    - `uvicorn[standard]`
    - `starlette`
22. Fixed Chat tab dependency issue by installing:
    - `ptyprocess`
    - `python-multipart`
23. Restarted dashboard and verified:

    ```text
    Port 9119 listening on 127.0.0.1
    / loads
    /chat loads
    PTY bridge spawn returns PTY-OK
    ```

## AI Gateway Unit

```ini
[Unit]
Description=Hermes Agent Gateway - AI-Workstation Brain
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/brian/.hermes/hermes-agent
Environment=HERMES_HOME=/home/brian/.hermes
ExecStart=/home/brian/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

## Dashboard Access

From AI-Workstation browser:

```text
http://127.0.0.1:9119
http://127.0.0.1:9119/chat
```

From ProfX or another host, use SSH tunnel:

```bash
ssh -L 9119:127.0.0.1:9119 ai-workstation
```

Then open locally:

```text
http://127.0.0.1:9119/chat
```

Dashboard intentionally remains localhost-only. Do **not** use `--insecure` unless deliberately exposing API/auth surfaces.

## Known Non-Critical Gaps

- DNS/AdGuard may still resolve `ai-workstation`/FQDN incorrectly to Stark (`192.168.50.204`) outside the SSH config override.
- `ripgrep` missing on AI host; Hermes file search falls back to slower search.
- Node/npm missing on AI host; browser/computer-use tooling may be limited.
- Optional Discord voice deps missing:
  - `PyNaCl`
  - `davey`
- Optional provider/API keys not all present; OpenAI Codex OAuth works.
- Obsidian Local REST API was not reachable from ProfX/AI at log time (`https://127.0.0.1:27124` returned no connection), so this report was written to the BlunderBus repo instead of the Obsidian vault.

## Next Work

1. Wire Stream Deck/STT direct-submit to local AI Hermes instead of browser paste/KasmVNC.
2. Install `ripgrep` on AI host.
3. Install Node/npm if browser tooling/dashboard builds are needed there.
4. Fix DNS/AdGuard for `ai-workstation` → `192.168.50.208`.
5. Decide whether ProfX should receive periodic cold-standby Hermes state sync.
6. Add resource/status panel for AI host: gateway, Discord, GPU, RAM, disk, Docker, dashboard.

## Verification Commands

```bash
ssh ai-workstation 'systemctl --user status hermes-gateway.service --no-pager'
ssh ai-workstation 'ss -ltnp | grep 9119'
ssh ai-workstation '/home/brian/.hermes/hermes-agent/venv/bin/python - <<"PY"
from hermes_cli.pty_bridge import PtyBridge
b=PtyBridge.spawn(["/bin/bash", "-lc", "echo PTY-OK; exit"])
print((b.read(timeout=2) or b"").decode(errors="replace").strip())
b.close()
PY'
```
