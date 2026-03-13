---
description: Output formatting standards
---

# Response Format

- Lead with status: ✅ healthy, ⚠️ degraded, ❌ down, 🔍 investigating.
- Use tables for multi-host or multi-service results.
- Keep responses concise. No preamble or filler.
- For errors, include: what failed, the raw error, and a suggested fix.
- When reporting metrics, include units and timestamps.
- For security findings, use severity labels: CRITICAL, HIGH, MEDIUM, LOW, INFO.
