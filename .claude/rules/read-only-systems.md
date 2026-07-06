---
description: Operational guidance for production systems
---

# Production System Guidance

Treat all production hosts with care. Confirm destructive operations before executing.
There are no per-host write restrictions in BlunderBus — Fury (SecOnion) was previously
read-only but is now a normal operational target as of 2026-05-01.

## Always requires confirmation
- Service restarts that affect availability (`so-restart`, `systemctl restart` on critical daemons, `docker compose down`)
- Destructive resets (`so-wipe`, drop database, truncate, `rm -rf`)
- Changes to network capture interfaces, firewall rules, or routing
- Disabling sensors, monitoring, or any visibility-providing service entirely
