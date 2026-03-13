---
description: Systems that must never be modified
paths:
  - "**/seconion/**"
  - "**/fury/**"
---

# Read-Only Systems

**Fury / Security Onion (VM 103 — 192.168.50.103)** is the IDS/IPS sensor.

- ONLY read operations: API queries, log retrieval, alert listing.
- NEVER execute write commands: no config changes, no rule modifications, no service restarts.
- NEVER SSH with write intent. Use `so-elasticsearch-query` or the API for data retrieval.
- If an investigation requires changes to SecOnion, escalate to the operator.
