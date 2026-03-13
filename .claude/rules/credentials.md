---
description: Credential handling rules for all operations
---

# Credential Handling

- Never echo, print, or log any environment variable containing keys, tokens, or passwords.
- Never hardcode credentials in commands, scripts, or skill files.
- Always reference credentials as `$ENV_VAR` in shell commands.
- Never include credentials in commit messages or git diffs.
- If a command output contains a credential, redact it before displaying.
- The `.env` file is gitignored. Never attempt to read or cat it directly.
