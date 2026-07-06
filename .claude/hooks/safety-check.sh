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
  "mkfs"
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

# ── Hard-block regexes: rm -rf on root/system paths, raw dd to devices ───────
HARD_REGEX_PATTERNS=(
  "rm[[:space:]]+(-[A-Za-z]*[rR][A-Za-z]*[[:space:]]+)+(-[A-Za-z]+[[:space:]]+)*(\"|')?/([[:space:]]|\"|'|$)"
  "rm[[:space:]].*[[:space:]](\"|')?/(bin|boot|dev|etc|lib|proc|root|sbin|sys|usr|var)(/|[[:space:]]|\"|'|$)"
  "dd[[:space:]]+[^|;&]*of=/dev/"
)

for pattern in "${HARD_REGEX_PATTERNS[@]}"; do
  if [[ "$COMMAND" =~ $pattern ]]; then
    echo '{"decision": "block", "reason": "BLOCKED: Command matches destructive pattern (rm -rf on system path / raw device write). Never allowed — see .claude/rules/safety.md."}'
    exit 2
  fi
done

# ── Confirm-worthy: blocked until the operator explicitly confirms ───────────
# Regexes run against the whole command, so `ssh <alias> 'reboot'` and
# unquoted `ssh <alias> reboot` are both caught.
shopt -s nocasematch
CONFIRM_PATTERNS=(
  "DROP[[:space:]]+(DATABASE|TABLE)"
  "TRUNCATE[[:space:]]+[A-Za-z]"
  "docker[[:space:]]+system[[:space:]]+prune"
  "docker[[:space:]]+volume[[:space:]]+prune"
  "docker([[:space:]]+compose|-compose)[[:space:]]+down"
  "docker[[:space:]]+(rm|rmi)[[:space:]]"
  "so-wipe"
  "(^|[[:space:];&|]|['\"])(sudo[[:space:]]+)?(reboot|poweroff|shutdown|halt)([[:space:]]|['\"]|$)"
  # any recursive rm — always confirm (must-hold rule; /tmp cleanups included)
  "rm[[:space:]]+(-[A-Za-z]*[rR][A-Za-z]*|--recursive)[[:space:]]"
  # hypervisor guest lifecycle
  "(pct|qm)[[:space:]]+(stop|shutdown|destroy|delete)[[:space:]]"
  # storage destruction
  "(zpool|zfs)[[:space:]]+(destroy|detach|remove|clear)[[:space:]]"
  "wipefs|sgdisk[[:space:]]+(-Z|--zap)"
)

# systemctl restart/stop/disable/mask at system level needs confirmation;
# `systemctl --user ...` (blunderbus unit deploys) is routine and exempt.
if [[ "$COMMAND" =~ systemctl[[:space:]]+(restart|stop|disable|mask)[[:space:]] ]] \
   && ! [[ "$COMMAND" =~ systemctl[[:space:]]+--user ]]; then
  echo '{"decision": "block", "reason": "CONFIRM REQUIRED: system-level systemctl restart/stop/disable/mask affects availability. Ask the operator first (see .claude/rules/safety.md)."}'
  exit 2
fi

for pattern in "${CONFIRM_PATTERNS[@]}"; do
  if [[ "$COMMAND" =~ $pattern ]]; then
    echo '{"decision": "block", "reason": "CONFIRM REQUIRED: command matches a destructive pattern that needs explicit operator confirmation before running (see .claude/rules/safety.md). Ask the operator, then re-run."}'
    exit 2
  fi
done
shopt -u nocasematch

# Allow everything else
exit 0
