---
description: Global safety guardrails for all operations
---

# Safety Rules

- Never run `rm -rf /` or any recursive delete on root paths.
- Never execute `DROP DATABASE`, `DROP TABLE`, or `TRUNCATE` without explicit operator confirmation.
- Never run `docker system prune -a` or `docker volume prune` without confirmation.
- Never issue `reboot`, `shutdown`, or `poweroff` on any VM without confirmation.
- Always prefer `--dry-run` flags when available for destructive operations.
- If a command could cause data loss, show the command first and ask before executing.
- Never pipe untrusted input directly into `bash` or `eval`.
