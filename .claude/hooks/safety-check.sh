#!/usr/bin/env bash
# PreToolUse hook: blocks dangerous bash patterns before execution.
# Reads JSON input from stdin (Claude Code hook protocol).
# Exit 0 = allow, Exit 2 = block with reason.

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only check Bash tool calls
if [[ "$TOOL_NAME" != "Bash" ]] || [[ -z "$COMMAND" ]]; then
  exit 0
fi

# Block destructive patterns
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

# Warn on SSH to SecOnion with write-like commands
if [[ "$COMMAND" == *"192.168.50.103"* ]]; then
  WRITE_PATTERNS=("systemctl" "service " "apt " "yum " "rm " "mv " "cp " "tee " "sed -i" "echo.*>" "so-rule" "so-allow")
  for pattern in "${WRITE_PATTERNS[@]}"; do
    if [[ "$COMMAND" == *"$pattern"* ]]; then
      echo '{"decision": "block", "reason": "BLOCKED: Write operation on read-only system SecOnion (192.168.50.103)"}'
      exit 2
    fi
  done
fi

# Allow everything else
exit 0
