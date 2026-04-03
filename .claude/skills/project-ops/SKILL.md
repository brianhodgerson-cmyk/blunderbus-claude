---
name: project-ops
description: Git operations, task tracking, documentation management, and general project workflow support for BlunderBus development.
allowed-tools: Bash, mcp__obsidian__obsidian_read, mcp__obsidian__obsidian_write, mcp__obsidian__obsidian_append, mcp__obsidian__obsidian_search
---

# Project Ops — Git, Tasks, Docs

## What This Does
Manages the BlunderBus project itself — git workflows, documentation, task tracking, and repo maintenance.

## How To Run

### Git status and recent history
```bash
git status
git log --oneline -20
```

### Create a feature branch
```bash
git checkout -b feature/<BRANCH_NAME>
```

### Stage, commit, and push
```bash
git add -A
git commit -m "<COMMIT_MESSAGE>"
git push origin $(git branch --show-current)
```

### View diff of recent changes
```bash
git diff HEAD~1
```

### List all skills
```bash
ls -1 .claude/skills/*/SKILL.md | sed 's|.claude/skills/||;s|/SKILL.md||'
```

### Validate repo structure
```bash
echo "=== CLAUDE.md ===" && wc -l CLAUDE.md
echo "=== Rules ===" && ls .claude/rules/
echo "=== Skills ===" && ls .claude/skills/
echo "=== Agents ===" && ls .claude/agents/
echo "=== Settings ===" && cat .claude/settings.json | jq '.permissions | keys'
```

### Add a new skill scaffold
```bash
mkdir -p .claude/skills/<SKILL_NAME>
cat > .claude/skills/<SKILL_NAME>/SKILL.md << 'EOF'
---
name: <SKILL_NAME>
description: <DESCRIPTION>
allowed-tools: Bash
---

# <Title>

## What This Does
<description>

## How To Run
<commands>
EOF
```
