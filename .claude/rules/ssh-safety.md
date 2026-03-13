---
description: SSH connection patterns and safety
---

# SSH Safety

- Always use key-based authentication. Never use password auth in commands.
- SSH connection pattern: `ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new user@host`
- Always set a connection timeout to avoid hanging on unreachable hosts.
- Never run interactive commands over SSH. Use non-interactive equivalents.
- For long-running commands, use `nohup` or redirect output appropriately.
- Test connectivity with a simple `ssh host echo ok` before running complex commands.
