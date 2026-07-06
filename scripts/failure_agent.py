"""
failure_agent.py — systemd OnFailure= hook (roadmap §1-lite).

Invoked as `blunderbus-failure-agent@<failed-unit>.service` via
`run_pipeline.sh failure_agent.py <failed-unit>` (env/vault hydrated by the
launcher). It:

  1. rate-limits per unit (30 min) so a crash-looping unit can't spam
  2. gathers `systemctl --user status` + recent journal for the failed unit
  3. asks the local `claude` CLI for a diagnosis (runtime.resolve_claude_command;
     falls back to raw log excerpt if the CLI is unavailable)
  4. posts to Discord #general (same bot/channel as the daily brief)
  5. upserts an agent_concerns row (agent='onfailure') — no reconcile, so
     multiple failed units coexist; drift/operator resolves them

Never raises: a broken failure handler must not create its own failure storm.
The template unit deliberately has no OnFailure= of its own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

STATE_PATH = ROOT / "logs" / "failure-agent-state.json"
RATE_LIMIT_S = 30 * 60
JOURNAL_LINES = 120
PROMPT_LOG_CHARS = 6000
DISCORD_CHANNEL_FALLBACK = "1477768383645749271"   # Hermes #general


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"<{' '.join(cmd[:3])} failed: {exc}>"


def rate_limited(unit: str) -> bool:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    except Exception:  # noqa: BLE001
        state = {}
    last = state.get(unit, 0)
    now = time.time()
    if now - last < RATE_LIMIT_S:
        return True
    state[unit] = now
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return False


def diagnose(unit: str, status: str, journal: str) -> str:
    """LLM diagnosis via local claude CLI; raw-excerpt fallback."""
    prompt = f"""You are BlunderBus, the HodgeSpot home-lab infrastructure agent.
The systemd user unit `{unit}` on AI-Workstation (Ubuntu, VM 109) just entered
failed state. Diagnose from the evidence below.

Reply in under 150 words, plain text: (1) one-line root cause, (2) 2-3 line
explanation, (3) the single most likely fix command. No preamble.

## systemctl status
{status[:1500]}

## journal (last {JOURNAL_LINES} lines, truncated)
{journal[-PROMPT_LOG_CHARS:]}
"""
    try:
        from runtime import resolve_claude_command
        claude_cmd = resolve_claude_command()
        if claude_cmd:
            r = subprocess.run(
                [claude_cmd, "--print", "--output-format", "text"],
                input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=120, cwd=os.path.expanduser("~"),   # ~ so CLAUDE.md doesn't lock role
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            print(f"claude CLI rc={r.returncode}: {(r.stderr or '')[:200]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"claude CLI unavailable: {exc}", file=sys.stderr)
    tail = "\n".join(journal.strip().splitlines()[-10:])
    return f"(no AI diagnosis available — raw tail)\n{tail}"


def post_discord(msg: str) -> bool:
    import urllib.request
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channel_id = (os.environ.get("DISCORD_ALERT_CHANNEL_ID")
                  or os.environ.get("DISCORD_BRIEF_CHANNEL_ID")
                  or os.environ.get("DISCORD_CHANNEL_ID")
                  or DISCORD_CHANNEL_FALLBACK)
    if not token:
        print("Discord skipped: DISCORD_BOT_TOKEN not set", file=sys.stderr)
        return False
    try:
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=json.dumps({"content": msg[:1990],
                             "allowed_mentions": {"parse": []}}).encode("utf-8"),
            headers={"Authorization": f"Bot {token}",
                     "Content-Type": "application/json",
                     "User-Agent": "BlunderBus-FailureAgent/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except Exception as exc:  # noqa: BLE001
        print(f"Discord send failed: {exc}", file=sys.stderr)
        return False


def file_concern(unit: str, diagnosis: str) -> None:
    """Direct upsert, no reconcile — coexists with other agents' concerns."""
    try:
        from blunderbus_memory import Concern, ConcernStatus, Severity
        from blunderbus_memory.concerns import PostgresConcerns
        with PostgresConcerns() as store:
            store.upsert(Concern(
                id=f"onfailure:systemd-failure:{unit.lower()}:ai-workstation",
                agent="onfailure",
                type="systemd-failure",
                target="ai-workstation",
                severity=Severity.HIGH,
                status=ConcernStatus.ACTIVE,
                summary=f"{unit} entered failed state"[:500],
                suggested_action=diagnosis[:500],
                verifier="failure_agent.py",
                payload={"unit": unit, "at": datetime.now().isoformat(timespec="seconds")},
            ))
    except Exception as exc:  # noqa: BLE001
        print(f"concern upsert skipped: {exc}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: failure_agent.py <failed-unit-name>", file=sys.stderr)
        return 0   # never propagate failure
    unit = sys.argv[1]
    if rate_limited(unit):
        print(f"{unit}: rate-limited (<{RATE_LIMIT_S // 60}m since last alert)")
        return 0

    status = _run(["systemctl", "--user", "status", "--no-pager", "-l", unit])
    journal = _run(["journalctl", "--user", "-u", unit, "-n", str(JOURNAL_LINES),
                    "--no-pager", "-o", "short-iso"])

    diagnosis = diagnose(unit, status, journal)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (f"🚨 **{unit}** failed on AI-Workstation ({ts} CT)\n\n"
           f"{diagnosis}\n\n"
           f"`journalctl --user -u {unit} -n 120` for full log")
    sent = post_discord(msg)
    print(f"{unit}: diagnosis posted={sent}")
    file_concern(unit, diagnosis)
    return 0


if __name__ == "__main__":
    sys.exit(main())
