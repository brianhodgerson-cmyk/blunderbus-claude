#!/usr/bin/env bash
# PreToolUse hook: blocks dangerous bash patterns before execution.
# Reads JSON input from stdin (Claude Code hook protocol).
# Exit 0 = allow, Exit 2 = block with reason.
#
# All patterns are matched against the FULL command string, so payloads inside
# alias-style SSH commands (ssh fury '...', ssh cortex "...") are caught too.

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only check Bash tool calls
if [[ "$TOOL_NAME" != "Bash" ]] || [[ -z "$COMMAND" ]]; then
  exit 0
fi

# ── Hard blocks: never allowed, local or remote ───────────────────────────────
BLOCKED_PATTERNS=(
  "rm -rf /"
  "mkfs"
  "dd if="
  "> /dev/sd"
  "chmod -R 777 /"
  ":(){ :|:& };:"
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if [[ "$COMMAND" == *"$pattern"* ]]; then
    echo '{"decision": "block", "reason": "BLOCKED: Command matches destructive pattern: '"$pattern"'"}'
    exit 2
  fi
done

# ── Confirm-worthy: blocked until the operator explicitly confirms ───────────
# Regexes run against the whole command, so `ssh <alias> 'reboot'` is caught.
shopt -s nocasematch
CONFIRM_PATTERNS=(
  "DROP[[:space:]]+(DATABASE|TABLE)"
  "TRUNCATE[[:space:]]+[A-Za-z]"
  "docker[[:space:]]+system[[:space:]]+prune"
  "docker[[:space:]]+volume[[:space:]]+prune"
  "docker([[:space:]]+compose|-compose)[[:space:]]+down"
  "so-wipe"
  "(^|[;&|]|['\"])[[:space:]]*(sudo[[:space:]]+)?(reboot|poweroff|shutdown)([[:space:]]|['\"]|$)"
)

for pattern in "${CONFIRM_PATTERNS[@]}"; do
  if [[ "$COMMAND" =~ $pattern ]]; then
    echo '{"decision": "block", "reason": "CONFIRM REQUIRED: command matches a destructive pattern that needs explicit operator confirmation before running (see .claude/rules/safety.md). Ask the operator, then re-run."}'
    exit 2
  fi
done
shopt -u nocasematch

# Allow everything else
exit 0
