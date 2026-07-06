"""
Drift sentinel — roadmap §3.

Nightly agent that compares the documented world (memory/registry/inventory,
.ssh-config.example, deploy/ai-workstation units) against observed reality
(qm/pct list on Proxmox, ssh reachability, systemd user unit states, docker ps
per host) and files agent_concerns for every diff.

Checks:
  1. proxmox-vs-registry   — guests missing/extra/status-mismatched/vmid drift
  2. ssh-config            — live ~/.ssh/config aliases vs .ssh-config.example
  3. ssh-reachability      — every running registry host with an alias answers
  4. systemd-units         — AI-Workstation timers/services vs deploy/ expectations
  5. docker-baseline       — per-host running-container set vs last run's baseline
                             (baseline: memory/infra/drift-baseline.json)

Registry entries can opt out of individual checks via frontmatter:
  attributes:
    drift_ignore: [ssh]        # e.g. jarvis — key not in HA addon yet

Concerns sync to Postgres agent_concerns (agent='drift') via concerns_sync;
re-firing updates rows, cleared drift auto-resolves + journals. Per the memory
contract this run itself journals nothing on a clean pass.

Run standalone:
    .venv/bin/python scripts/agents/drift.py [--json] [--no-sync]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "agents"))

from base import AgentReport, Concern  # noqa: E402

BASELINE_PATH = ROOT / "memory" / "infra" / "drift-baseline.json"
SSH_CONFIG_LIVE = Path.home() / ".ssh" / "config"
SSH_CONFIG_CANON = ROOT / ".ssh-config.example"

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new"]

# ── Expected systemd user units on AI-Workstation ────────────────────────────
# Keep in sync with deploy/ai-workstation/install.sh.
EXPECTED_TIMERS = [
    "blunderbus-daily-brief.timer",
    "blunderbus-monarch-ingest.timer",
    "blunderbus-drift-sentinel.timer",
    "blunderbus-ssh-cert-renew.timer",
    "blunderbus-memory-hygiene.timer",
]
EXPECTED_SERVICES_ACTIVE = [
    "bb-mcp.service",
    "blunderbus-dispatcher.service",
    "bbm-api.service",
    "blunderbus-couchdb-sync.service",
    "hermes-gateway.service",
    "canary-stt.service",
    "wyoming-canary.service",
]
# udev starts it when the deck is plugged in — enabled is enough.
EXPECTED_SERVICES_ENABLED_ONLY = ["jarvis-streamdeck.service"]


def _run(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _ssh(alias: str, remote_cmd: str, timeout: int = 25) -> tuple[int, str]:
    return _run(["ssh", *SSH_OPTS, alias, remote_cmd], timeout=timeout)


# ── 1. Proxmox truth ─────────────────────────────────────────────────────────


def collect_proxmox() -> dict[int, dict] | None:
    """{vmid: {name, status, kind}} from qm list + pct list. None if unreachable."""
    rc, out = _ssh("proxmox", "qm list; echo ---PCT---; pct list")
    if rc != 0:
        return None
    guests: dict[int, dict] = {}
    section = "qm"
    for line in out.splitlines():
        s = line.strip()
        if "---PCT---" in s:
            section = "pct"
            continue
        if not s or s.lower().startswith(("vmid", "warning")):
            continue
        parts = s.split()
        if not parts[0].isdigit():
            continue
        vmid = int(parts[0])
        if section == "qm":       # VMID NAME STATUS MEM BOOTDISK PID
            guests[vmid] = {"name": parts[1], "status": parts[2], "kind": "vm"}
        else:                     # VMID Status [Lock] Name
            guests[vmid] = {"name": parts[-1], "status": parts[1], "kind": "lxc"}
    return guests or None


def load_inventory() -> list:
    from blunderbus_memory.registry import get_default_registry
    reg = get_default_registry()
    return reg.inventory.all()


def diff_proxmox(inv: list, guests: dict[int, dict]) -> list[Concern]:
    out: list[Concern] = []
    by_vmid = {e.vmid: e for e in inv if e.vmid is not None}
    by_name = {e.hostname.lower(): e for e in inv}

    for e in inv:
        ignores = set((e.attributes or {}).get("drift_ignore", []))
        if e.kind not in ("vm", "lxc") or "proxmox" in ignores:
            continue
        actual = guests.get(e.vmid) if e.vmid is not None else None
        if actual is None:
            # vmid unknown or gone — try matching by name to detect vmid drift
            named = next((v for v, g in guests.items()
                          if g["name"].lower() == e.hostname.lower()), None)
            if named is not None:
                out.append(Concern(
                    severity="medium",
                    summary=f"Registry {e.id}: vmid says {e.vmid!r} but Proxmox has "
                            f"'{guests[named]['name']}' as VMID {named}",
                    category="registry-drift",
                    metric={"kind": "vmid-drift", "host": e.id, "actual_vmid": named},
                    suggested_action=f"Set vmid: {named} in memory/registry/inventory/{e.id}.md",
                    source="qm/pct list",
                ))
            else:
                out.append(Concern(
                    severity="medium",
                    summary=f"Registry {e.id} (vmid {e.vmid}) not found on Proxmox — "
                            "removed guest still documented",
                    category="registry-drift",
                    metric={"kind": "registry-orphan", "host": e.id},
                    suggested_action=f"Delete or archive memory/registry/inventory/{e.id}.md",
                    source="qm/pct list",
                ))
            continue

        reg_status = getattr(e.status, "value", str(e.status))
        if reg_status != actual["status"]:
            sev = "high" if actual["status"] != "running" and e.monitored else "medium"
            out.append(Concern(
                severity=sev,
                summary=f"{e.id}: registry says {reg_status}, Proxmox says {actual['status']}",
                category="registry-drift",
                metric={"kind": "status-drift", "host": e.id, "actual": actual["status"]},
                suggested_action=f"Update status in memory/registry/inventory/{e.id}.md "
                                 "or investigate why the guest changed state",
                source="qm/pct list",
            ))
        if e.kind != actual["kind"]:
            out.append(Concern(
                severity="low",
                summary=f"{e.id}: registry kind={e.kind}, Proxmox kind={actual['kind']}",
                category="registry-drift",
                metric={"kind": "kind-drift", "host": e.id},
                source="qm/pct list",
            ))

    for vmid, g in guests.items():
        if vmid in by_vmid:
            continue
        # A name match only counts if that entry has no vmid of its own —
        # otherwise 'hawkeye' VM 105 hides behind the Hawkeye NVR LXC 205.
        named = by_name.get(g["name"].lower())
        if named is None or named.vmid is not None:
            out.append(Concern(
                severity="medium",
                summary=f"Proxmox guest '{g['name']}' (VMID {vmid}, {g['status']}) "
                        "has no registry entry",
                category="registry-drift",
                metric={"kind": "unregistered-guest", "host": g["name"].lower()},
                suggested_action=f"Create memory/registry/inventory/ entry for VMID {vmid}",
                source="qm/pct list",
            ))
    return out


# ── 2. SSH config vs canonical example ───────────────────────────────────────


def _parse_ssh_aliases(path: Path) -> set[str]:
    if not path.exists():
        return set()
    aliases: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*Host\s+(.+)$", line, re.IGNORECASE)
        if m:
            for tok in m.group(1).split():
                if "*" not in tok and "?" not in tok:
                    aliases.add(tok)
    return aliases


def check_ssh_config() -> list[Concern]:
    canon = _parse_ssh_aliases(SSH_CONFIG_CANON)
    live = _parse_ssh_aliases(SSH_CONFIG_LIVE)
    if not canon:
        return [Concern(severity="medium", summary=".ssh-config.example missing or empty — "
                        "cannot verify alias drift", category="ssh-config",
                        metric={"kind": "ssh-canon-missing", "host": "ai-workstation"},
                        source="ssh-config diff")]
    missing = canon - live
    out: list[Concern] = []
    if missing:
        out.append(Concern(
            severity="high",
            summary=f"~/.ssh/config missing {len(missing)} canonical alias(es): "
                    f"{', '.join(sorted(missing))}",
            category="ssh-config",
            metric={"kind": "ssh-alias-missing", "host": "ai-workstation",
                    "missing": sorted(missing)},
            suggested_action="Restore from .ssh-config.example (2026-07-06 freeze wiped "
                             "11 of 13 aliases — this is the recurrence guard)",
            source="ssh-config diff",
        ))
    return out


# ── 3. SSH reachability ──────────────────────────────────────────────────────


def check_ssh_reachability(inv: list, guests: dict[int, dict] | None) -> list[Concern]:
    """Probe every host that *should* be up: registry running + (if a Proxmox
    guest) actually running per qm/pct. Skips hosts with drift_ignore: [ssh]."""
    targets: list = []
    for e in inv:
        ignores = set((e.attributes or {}).get("drift_ignore", []))
        if not e.ssh_alias or "ssh" in ignores:
            continue
        reg_status = getattr(e.status, "value", str(e.status))
        if reg_status != "running":
            continue
        if guests and e.vmid is not None and guests.get(e.vmid, {}).get("status") != "running":
            continue  # actually stopped — status-drift check covers it
        if e.id == "ai-workstation":
            continue  # we are here
        targets.append(e)

    alias_gaps = [
        Concern(
            severity="low",
            summary=f"{e.id}: running {e.kind} with no ssh_alias in registry — "
                    "unreachable by fleet tooling",
            category="registry-drift",
            metric={"kind": "no-ssh-alias", "host": e.id},
            suggested_action=f"Add ssh_alias to memory/registry/inventory/{e.id}.md "
                             "and ~/.ssh/config if missing",
            source="registry",
        )
        for e in inv
        if e.kind in ("vm", "lxc") and not e.ssh_alias
        and getattr(e.status, "value", str(e.status)) == "running"
        and "ssh" not in set((e.attributes or {}).get("drift_ignore", []))
    ]

    def probe(e) -> Concern | None:
        # 40s: groot's sshd stalls ~25s on cold connections (DNS/PAM) but succeeds.
        rc, out = _ssh(e.ssh_alias, "true", timeout=40)
        if rc != 0:
            return Concern(
                severity="high",
                summary=f"{e.id}: ssh {e.ssh_alias} failed (rc={rc}) — {out.strip()[:80]}",
                category="ssh-reachability",
                metric={"kind": "ssh-unreachable", "host": e.id},
                suggested_action="Check host, sshd, and authorized_keys for the "
                                 "workstation key",
                source="ssh probe",
            )
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, targets))
    return alias_gaps + [c for c in results if c]


# ── 4. Systemd user units (local) ────────────────────────────────────────────


def check_systemd_units() -> list[Concern]:
    out: list[Concern] = []

    def state(unit: str, verb: str) -> str:
        rc, txt = _run(["systemctl", "--user", verb, unit], timeout=10)
        return txt.strip().splitlines()[0] if txt.strip() else f"rc={rc}"

    for unit in EXPECTED_TIMERS:
        enabled = state(unit, "is-enabled")
        active = state(unit, "is-active")
        if enabled != "enabled" or active != "active":
            out.append(Concern(
                severity="high",
                summary=f"{unit}: enabled={enabled}, active={active} (expected enabled+active)",
                category="systemd-drift",
                metric={"kind": "timer-state", "host": "ai-workstation", "alert": unit},
                suggested_action=f"systemctl --user enable --now {unit}",
                source="systemctl --user",
            ))
    for unit in EXPECTED_SERVICES_ACTIVE:
        active = state(unit, "is-active")
        if active != "active":
            out.append(Concern(
                severity="high",
                summary=f"{unit}: {active} (expected active)",
                category="systemd-drift",
                metric={"kind": "service-state", "host": "ai-workstation", "alert": unit},
                suggested_action=f"journalctl --user -u {unit} -n 50; then restart",
                source="systemctl --user",
            ))
    for unit in EXPECTED_SERVICES_ENABLED_ONLY:
        enabled = state(unit, "is-enabled")
        if enabled != "enabled":
            out.append(Concern(
                severity="medium",
                summary=f"{unit}: is-enabled={enabled} (expected enabled; udev-started)",
                category="systemd-drift",
                metric={"kind": "service-enabled", "host": "ai-workstation", "alert": unit},
                suggested_action=f"systemctl --user enable {unit}",
                source="systemctl --user",
            ))
    return out


# ── 5. Docker running-set baseline ───────────────────────────────────────────

DOCKER_FMT = "docker ps -a --format '{{.Names}}\\t{{.State}}\\t{{.Status}}'"


def _docker_ps(e) -> tuple[str, list[tuple[str, str, str]] | None]:
    """Returns (host_id, rows|None). None = docker absent/unreachable (skip)."""
    cmd = f"command -v docker >/dev/null 2>&1 && {DOCKER_FMT} || echo __NODOCKER__"
    if e.id == "ai-workstation":
        rc, out = _run(["bash", "-c", cmd])
    else:
        rc, out = _ssh(e.ssh_alias, cmd)
    if rc != 0 or "__NODOCKER__" in out or "permission denied" in out.lower():
        return e.id, None
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return e.id, rows


def check_docker(inv: list, guests: dict[int, dict] | None) -> tuple[list[Concern], dict]:
    hosts = []
    for e in inv:
        ignores = set((e.attributes or {}).get("drift_ignore", []))
        reg_status = getattr(e.status, "value", str(e.status))
        if "docker" in ignores or reg_status != "running":
            continue
        if guests and e.vmid is not None and guests.get(e.vmid, {}).get("status") != "running":
            continue
        if e.ssh_alias or e.id == "ai-workstation":
            hosts.append(e)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(_docker_ps, hosts))

    baseline: dict = {}
    if BASELINE_PATH.exists():
        try:
            baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            baseline = {}
    prev: dict = baseline.get("docker_running", {})

    out: list[Concern] = []
    new_running: dict[str, list[str]] = {}
    for host, rows in results.items():
        if rows is None:
            continue
        running = sorted(n for n, s, _ in rows if s == "running")
        new_running[host] = running
        for name, s, status in rows:
            if "unhealthy" in status.lower():
                out.append(Concern(
                    severity="medium",
                    summary=f"{host}: container {name} unhealthy ({status})",
                    category="container-health",
                    metric={"kind": "container-unhealthy", "host": host, "alert": name},
                    source="docker ps",
                ))
            elif s == "restarting":
                out.append(Concern(
                    severity="high",
                    summary=f"{host}: container {name} restart-looping ({status})",
                    category="container-health",
                    metric={"kind": "container-restarting", "host": host, "alert": name},
                    suggested_action=f"ssh {host} 'docker logs --tail 50 {name}'",
                    source="docker ps",
                ))
        gone = sorted(set(prev.get(host, [])) - set(running))
        if gone and host in prev:
            out.append(Concern(
                severity="medium",
                summary=f"{host}: container(s) no longer running vs last sentinel pass: "
                        f"{', '.join(gone)}",
                category="container-health",
                metric={"kind": "container-gone", "host": host, "gone": gone},
                suggested_action="Verify intentional; baseline updates automatically "
                                 "next pass",
                source="docker baseline diff",
            ))

    baseline["docker_running"] = new_running
    baseline["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    skipped = sorted(h for h, rows in results.items() if rows is None)
    return out, {"docker_hosts": sorted(new_running), "docker_skipped": skipped}


# ── run ──────────────────────────────────────────────────────────────────────


def run(today: date | None = None, *, sync: bool = True) -> AgentReport:
    started = datetime.now()
    try:
        inv = load_inventory()
        guests = collect_proxmox()

        real: list[Concern] = []
        if guests is None:
            real.append(Concern(
                severity="critical",
                summary="Proxmox (Multiverse) unreachable — cannot verify guest truth",
                category="registry-drift",
                metric={"kind": "proxmox-unreachable", "host": "multiverse"},
                source="ssh probe",
            ))
        else:
            real += diff_proxmox(inv, guests)
        real += check_ssh_config()
        real += check_ssh_reachability(inv, guests)
        real += check_systemd_units()
        docker_concerns, docker_meta = check_docker(inv, guests)
        real += docker_concerns

        if sync:
            from concerns_sync import sync as _sync_concerns
            _sync_concerns("drift", real)

        metrics = {
            "registry_entries": len(inv),
            "proxmox_guests": len(guests) if guests else None,
            "drift_findings": len(real),
            **docker_meta,
        }
        worst = next((c for c in real if c.severity in ("critical", "high")), None)
        headline = (f"{len(real)} drift finding(s), worst: {worst.summary[:70]}"
                    if worst else
                    f"{len(real)} drift finding(s)" if real else
                    f"no drift — registry matches reality across "
                    f"{metrics['proxmox_guests'] or '?'} guests")
        status = "degraded" if worst else "ok"

        return AgentReport(
            agent="drift",
            status=status,
            as_of=datetime.now(),
            headline=headline,
            real_concerns=real,
            metrics=metrics,
            memory_consulted=["memory/registry/inventory/", ".ssh-config.example",
                              "memory/infra/drift-baseline.json"],
            raw_data={"proxmox": {str(k): v for k, v in (guests or {}).items()}},
            duration_ms=int((datetime.now() - started).total_seconds() * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        return AgentReport.failed("drift", str(exc), started)


def _print_human(r: AgentReport) -> None:
    print(f"\n=== drift-sentinel · {r.status_emoji} {r.status.upper()} · {r.duration_ms}ms ===")
    print(f"Headline: {r.headline}")
    if r.error:
        print(f"ERROR: {r.error}")
        return
    for c in sorted(r.real_concerns, key=lambda c: ["critical", "high", "medium", "low", "info"].index(c.severity)):
        print(f"  [{c.severity:8s}] ({c.category}) {c.summary}")
        if c.suggested_action:
            print(f"             ↳ {c.suggested_action}")
    print("\nMetrics:")
    for k, v in r.metrics.items():
        print(f"  {k:20s} {v}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-sync", action="store_true", help="skip Postgres concerns sync")
    args = p.parse_args()
    report = run(sync=not args.no_sync)
    if args.json:
        print(report.to_json())
    else:
        _print_human(report)
    sys.exit(0 if report.status != "failed" else 1)
