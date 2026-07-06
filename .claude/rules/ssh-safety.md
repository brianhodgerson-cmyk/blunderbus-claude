---
description: SSH connection patterns and safety
---

# SSH Safety

- Always use key-based authentication. Never use password auth in commands.
- Keys are a **local key file** (`~/.ssh/id_ed25519`) referenced from `~/.ssh/config` — no SSH agent required or used.
- SSH connection pattern: `ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new <host-alias>`
- Preferred host aliases: `proxmox`, `cortex`, `stark`, `thor`, `banner`, `heimdall` (`truenas` legacy), `homeassistant`, `fury`, `groot`, `loki`, `mercury`, `vision`, `hawkeye-nvr`.
- Always set a connection timeout to avoid hanging on unreachable hosts.
- Never run interactive commands over SSH. Use non-interactive equivalents.
- For long-running commands, use `nohup` or redirect output appropriately.
- Test connectivity with a simple `ssh <host-alias> echo ok` before running complex commands.
