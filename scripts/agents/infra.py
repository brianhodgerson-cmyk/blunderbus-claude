"""
Infrastructure domain agent.

Wraps the host/container/storage/alert collectors from morning_brief_push.py
into the standardized AgentReport contract. The orchestrator calls run() and
gets structured concerns + carried items + metrics — no markdown, no Obsidian
writes, no Telegram. Pure data.

Run standalone:
    py scripts/agents/infra.py
    py scripts/agents/infra.py --json
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "agents"))

from base import AgentReport, Concern, Event, parse_carried_from_learnings  # noqa: E402

INFRA_DIR = ROOT / "memory" / "infra"
INFRA_LEARNINGS = INFRA_DIR / "learnings.md"
INFRA_INVENTORY = INFRA_DIR / "inventory.md"
INFRA_DECISIONS = INFRA_DIR / "decisions.md"
INFRA_RECURRING = INFRA_DIR / "recurring.md"
INFRA_INCIDENTS = INFRA_DIR / "incidents.md"
INFRA_RUNBOOKS = INFRA_DIR / "runbooks.md"
INFRA_BASELINES = INFRA_DIR / "baselines.md"
GENERIC_LEARNINGS = ROOT / "memory" / "learnings.md"   # fallback for ancient files


def _severity_for_host_state(label: str, status: str, mem_pct: float | None) -> str:
    if "❌" in status or "offline" in status.lower():
        return "high"
    if mem_pct is not None and mem_pct >= 95:
        return "high"
    if mem_pct is not None and mem_pct >= 85:
        return "medium"
    return "low"


def _thor_ollama_check() -> tuple[bool, str]:
    """Thor is the Windows host running BlunderBus itself — no SSH server.
    Probe Ollama HTTP API instead. Returns (is_healthy, detail)."""
    import urllib.request, json
    try:
        req = urllib.request.Request("http://192.168.50.136:11434/api/version", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                return (False, f"Ollama returned HTTP {resp.status}")
            data = json.loads(resp.read())
            ver = data.get("version", "?")
        # Also check models loaded
        with urllib.request.urlopen("http://192.168.50.136:11434/api/tags", timeout=5) as resp:
            tags = json.loads(resp.read())
            n_models = len(tags.get("models", []))
        return (True, f"Ollama {ver}, {n_models} models")
    except Exception as exc:
        return (False, f"Ollama unreachable: {exc}")


def _build_host_concerns(vm_rows: list[tuple]) -> list[Concern]:
    """vm_rows is list[(label, status, mem, disk)] from collect_vm_health()."""
    out: list[Concern] = []
    for label, status, mem, disk in vm_rows:
        # Special-case Thor: it's the Windows host running BlunderBus, not a VM.
        # SSH probe always fails (no sshd on Windows), but Ollama HTTP works.
        if label == "Thor":
            ok, detail = _thor_ollama_check()
            if not ok:
                out.append(Concern(
                    severity="high",
                    summary=f"Thor: {detail}",
                    category="host-down",
                    metric={"host": "Thor", "probe": "ollama-http", "status": "down"},
                    source="ollama http probe",
                ))
            # If healthy, no concern emitted. (Thor is the BlunderBus host itself
            # — see memory/infra/decisions.md "Thor is the Windows host".)
            continue

        # Offline VMs
        if "❌" in status or "offline" in status.lower():
            out.append(Concern(
                severity="high",
                summary=f"{label} offline",
                category="host-down",
                metric={"host": label, "status": "offline"},
                source="ssh probe",
            ))
            continue

        # Memory pressure
        try:
            mem_pct = int(str(mem).rstrip("%").strip()) if mem and "%" in str(mem) else None
        except (ValueError, TypeError):
            mem_pct = None
        if mem_pct is not None and mem_pct >= 90:
            sev = _severity_for_host_state(label, status, mem_pct)
            out.append(Concern(
                severity=sev,
                summary=f"{label} memory at {mem_pct}%",
                category="host-resource",
                metric={"host": label, "mem_pct": mem_pct},
                suggested_action="Identify high-mem container or service; consider restart or scale-down.",
                source="ssh probe",
            ))

        # Disk pressure
        try:
            disk_pct = int(str(disk).rstrip("%").strip()) if disk and "%" in str(disk) else None
        except (ValueError, TypeError):
            disk_pct = None
        if disk_pct is not None and disk_pct >= 90:
            out.append(Concern(
                severity="high",
                summary=f"{label} disk at {disk_pct}%",
                category="host-resource",
                metric={"host": label, "disk_pct": disk_pct},
                suggested_action="Free space immediately or expand volume.",
                source="ssh probe",
            ))
    return out


def _build_container_concerns(containers: dict) -> list[Concern]:
    """containers: {host_label: (running_count, unhealthy_count) or None}."""
    out: list[Concern] = []
    for host, result in (containers or {}).items():
        if result is None:
            continue
        running, unhealthy = result if isinstance(result, tuple) else (result, 0)
        if unhealthy and unhealthy > 0:
            out.append(Concern(
                severity="medium",
                summary=f"{host}: {unhealthy} unhealthy container(s) of {running} running",
                category="container-health",
                metric={"host": host, "running": running, "unhealthy": unhealthy},
                source="docker ps",
            ))
    return out


def _build_alert_concerns(firing) -> list[Concern]:
    """firing: list of Prometheus firing alerts, or None if monitoring offline.
    Each firing alert becomes a Concern with days_seen derived from activeAt."""
    out: list[Concern] = []
    if firing is None:
        out.append(Concern(
            severity="medium",
            summary="Prometheus/monitoring stack offline — no alert visibility",
            category="monitoring",
            suggested_action="Bring Banner or upstream monitoring back online.",
            source="prometheus probe",
        ))
        return out
    for alert in firing or []:
        if not isinstance(alert, dict):
            out.append(Concern(severity="medium", summary=f"Prometheus firing: {alert}",
                               category="alert", source="prometheus"))
            continue
        labels = alert.get("labels", {}) if isinstance(alert.get("labels"), dict) else {}
        anno = alert.get("annotations", {}) if isinstance(alert.get("annotations"), dict) else {}
        name = labels.get("alertname") or alert.get("alertname") or "unknown"
        host = labels.get("host") or labels.get("instance") or ""
        sev_raw = (labels.get("severity") or alert.get("severity") or "medium").lower()
        sev = sev_raw if sev_raw in ("critical", "high", "medium", "low") else "medium"
        summary = anno.get("summary") or f"{name}{(' on ' + host) if host else ''}"
        # days_seen from activeAt timestamp (Alertmanager is the source of truth for duration)
        days_seen = 1
        first_seen = None
        last_seen = None
        active_at = alert.get("activeAt")
        if active_at:
            try:
                ts = datetime.fromisoformat(active_at.replace("Z", "+00:00"))
                first_seen = ts.date().isoformat()
                last_seen = datetime.now().date().isoformat()
                days_seen = max(1, (datetime.now(ts.tzinfo) - ts).days)
            except Exception:
                pass
        out.append(Concern(
            severity=sev,
            summary=summary,
            category="alert",
            metric={"alert": name, "host": host, "value": alert.get("value")},
            source="prometheus",
            days_seen=days_seen,
            first_seen=first_seen,
            last_seen=last_seen,
        ))
    return out


def _seconion_disabled() -> bool:
    return os.environ.get("SECONION_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _build_security_concerns(sec) -> list[Concern]:
    """SecOnion 24h alerts. May be None (unreachable) or empty list.

    If Brian has intentionally powered SecOnion down, suppress the missing-IDS
    concern with SECONION_DISABLED=1 instead of treating it as an incident.
    """
    out: list[Concern] = []
    if sec is None and not _seconion_disabled():
        out.append(Concern(
            severity="medium",
            summary="SecOnion unreachable — no IDS visibility",
            category="security-monitoring",
            source="seconion probe",
        ))
    return out


def _build_camera_concerns(cams) -> list[Concern]:
    out: list[Concern] = []
    if cams is None:
        out.append(Concern(
            severity="low",
            summary="Frigate NVR unreachable",
            category="camera",
            source="frigate probe",
        ))
    return out


def _build_nas_concerns(pools) -> list[Concern]:
    out: list[Concern] = []
    for entry in pools or []:
        if isinstance(entry, tuple) and len(entry) >= 3:
            name, status, healthy = entry[0], entry[1], entry[2]
            status_code = entry[3] if len(entry) > 3 else None
            status_detail = entry[4] if len(entry) > 4 else None
            online = status in ("ONLINE", "online")
            if healthy and online:
                continue
            if not online:
                sev = "critical"
                summary = f"ZFS pool {name} status={status}"
                action = "Investigate pool health on Heimdall immediately."
            else:
                # Middleware flags unhealthy while the pool is ONLINE (e.g.
                # FAILING_DEV after a corrected device error). Degraded, not
                # an outage — applications are typically unaffected.
                sev = "high"
                summary = (f"ZFS pool {name} flagged unhealthy "
                           f"({status_code or 'no status_code'}) despite status={status}")
                action = ("zpool status -v on Heimdall — if a device shows "
                          "errors, decide zpool clear vs replace.")
            out.append(Concern(
                severity=sev,
                summary=summary,
                detail=(status_detail or ""),
                category="storage",
                # kind → stable concern id; host → concern target
                metric={"pool": name, "status": status,
                        "status_code": status_code,
                        "kind": f"zfs-pool-{name}", "host": "heimdall"},
                suggested_action=action,
                source="truenas",
            ))
    return out


def _load_recurring_suppressions() -> list[dict]:
    """Parse memory/infra/recurring.md "Confirmed recurring" rules.
    Returns list of dicts: {match_summary_regex, match_host, reason, source}.
    Same pattern as finance-agent's recurring suppressions."""
    import re
    if not INFRA_RECURRING.exists():
        return []
    try:
        text = INFRA_RECURRING.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    rules: list[dict] = []
    # Look for explicit suppression rules — operator uses bullet items under
    # "### Confirmed recurring" with `Match:` and `Suppress: yes` lines.
    in_confirmed = False
    cur: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("### Confirmed recurring"):
            in_confirmed = True
            continue
        if s.startswith("### ") and in_confirmed and "Confirmed recurring" not in s:
            in_confirmed = False
        if not in_confirmed:
            continue
        # Match: <regex_or_condition>
        m_match = re.match(r"^-\s+Match:\s*(.+)", s)
        if m_match:
            cur["match"] = m_match.group(1).strip("`")
        m_sup = re.match(r"^-\s+Suppress:\s*yes", s, re.IGNORECASE)
        if m_sup and "match" in cur:
            cur["source"] = "recurring.md"
            rules.append(cur)
            cur = {}
    return rules


def _load_known_pending(decisions_path) -> set[str]:
    """Parse `[?]` markers in decisions.md → set of substrings the agent uses
    to mark a concern as 'known-pending-decision' rather than 'new anomaly'."""
    import re
    if not decisions_path.exists():
        return set()
    try:
        text = decisions_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    out: set[str] = set()
    # ### [?] Thor (VM 101) network unreachability
    for m in re.finditer(r"^###\s*\[\?\]\s*(.+)$", text, flags=re.MULTILINE):
        # Pull short keywords
        title = m.group(1)
        for token in re.findall(r"\b([A-Z][a-zA-Z]+|VM\s*\d+|LXC\s*\d+)\b", title):
            out.add(token.lower())
    return out


def _annotate_with_memory(concerns: list[Concern], pending_keywords: set[str],
                          recurring_rules: list[dict]) -> list[Concern]:
    """Mutate concerns with decision/recurring context.

    Confirmed recurring rules mark concerns as operator-accepted expected state;
    run() later moves them to expected_events so they do not appear in action
    queues or background concern lists.
    """
    import re
    for c in concerns:
        sl = c.summary.lower()
        # Known-pending: drop high-severity to medium and annotate
        if pending_keywords:
            for kw in pending_keywords:
                if kw in sl:
                    if c.severity == "high":
                        c.severity = "medium"
                    if c.detail:
                        c.detail += " · "
                    c.detail += "(known-pending-decision — see memory/infra/decisions.md)"
                    break
        for rule in recurring_rules:
            try:
                if re.search(rule.get("match", "(?!)"), c.summary, re.IGNORECASE):
                    c.severity = "info"
                    c.category = c.category or "expected-state"
                    reason = rule.get("source", "recurring.md")
                    c.detail = (c.detail or "") + f" · expected-state per {reason}"
                    c.metric = {**(c.metric or {}), "expected_state": True}
            except re.error:
                continue
    return concerns


def _build_metrics(vm_rows, containers, pools, firing) -> dict:
    # Count VMs as "up" if SSH probe passed OR if it's Thor with Ollama responding.
    vm_up = sum(1 for label, st, _, _ in vm_rows if "✅" in st)
    # Thor: SSH always fails on Windows, but Ollama HTTP probe is the truth.
    thor_row = next((r for r in vm_rows if r[0] == "Thor"), None)
    if thor_row and "❌" in thor_row[1]:
        ok, _ = _thor_ollama_check()
        if ok:
            vm_up += 1
    vm_total = len(vm_rows)
    total_containers = sum((r[0] if isinstance(r, tuple) else 0)
                           for r in containers.values() if r)
    total_unhealthy = sum((r[1] if isinstance(r, tuple) else 0)
                          for r in containers.values() if r)
    healthy_pools = sum(1 for entry in (pools or [])
                        if isinstance(entry, tuple) and len(entry) >= 3
                        and entry[2] and entry[1] in ("ONLINE", "online"))
    total_pools = len(pools or [])
    return {
        "vm_up": vm_up,
        "vm_total": vm_total,
        "containers_running": total_containers,
        "containers_unhealthy": total_unhealthy,
        "pools_healthy": healthy_pools,
        "pools_total": total_pools,
        "alerts_firing": len(firing) if isinstance(firing, list) else None,
        "monitoring_online": firing is not None,
    }


def _headline(metrics: dict, real: list[Concern], carried: list[Concern]) -> str:
    vm = f"{metrics.get('vm_up', '?')}/{metrics.get('vm_total', '?')} hosts"
    parts = [vm]
    if metrics.get("containers_running") is not None:
        parts.append(f"{metrics['containers_running']} containers")
    if metrics.get("pools_total"):
        parts.append(f"{metrics['pools_healthy']}/{metrics['pools_total']} pools")
    base = " · ".join(parts)

    if real:
        worst = next((c for c in real if c.severity in ("critical", "high")), real[0])
        return f"{base} · {len(real)} real concern(s), worst: {worst.summary[:60]}"
    if carried:
        return f"{base} · {len(carried)} carried, no new concerns"
    return f"{base} · all clear"


# NOTE: We tried parallelizing host probes via ThreadPoolExecutor + paramiko.
# In benchmark it ran 36s vs 17s serial — paramiko has thread contention and/or
# the SSH servers serialize simultaneous connections from one source. The
# parallel COLLECTOR fan-out below (containers/security/cameras/nas/prometheus
# concurrent with vm_health) still gives the win. Don't re-parallelize per-host
# without first eliminating that contention.


def run(today: date | None = None) -> AgentReport:
    started = datetime.now()
    today = today or date.today()

    try:
        # Reuse the existing collectors. They're already battle-tested.
        import morning_brief_push as mb   # noqa: E402
        from concurrent.futures import ThreadPoolExecutor

        # Run all 6 collection paths in parallel. vm_health uses the legacy
        # serial probe — paramiko thread contention made parallel-by-host
        # slower (see note above _parallel_vm_health was removed).
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_vm   = pool.submit(mb.collect_vm_health)
            f_ctr  = pool.submit(mb.collect_containers)
            f_sec  = None if _seconion_disabled() else pool.submit(mb.collect_security)
            f_cam  = pool.submit(mb.collect_cameras)
            f_nas  = pool.submit(mb.collect_nas)
            f_prom = pool.submit(mb.collect_prometheus)
            vm_rows    = f_vm.result()
            containers = f_ctr.result()
            sec        = [] if f_sec is None else f_sec.result()
            cams       = f_cam.result()
            pools      = f_nas.result()
            firing     = f_prom.result()

        real: list[Concern] = []
        real += _build_host_concerns(vm_rows)
        real += _build_container_concerns(containers)
        real += _build_alert_concerns(firing)
        real += _build_security_concerns(sec)
        real += _build_camera_concerns(cams)
        real += _build_nas_concerns(pools)

        # Memory pass: annotate with decisions.md pending-keywords + recurring.md rules
        pending_kw = _load_known_pending(INFRA_DECISIONS)
        recurring_rules = _load_recurring_suppressions()
        real = _annotate_with_memory(real, pending_kw, recurring_rules)
        expected_events = [
            Event(summary=c.summary, category=c.category or "expected-state",
                  reason=(c.detail or "operator-confirmed expected state").strip(" ·"),
                  source=c.source or "recurring.md")
            for c in real
            if (c.metric or {}).get("expected_state")
        ]
        real = [c for c in real if not (c.metric or {}).get("expected_state")]

        # Sync to Postgres agent_concerns so concerns persist across runs and
        # auto-resolve when conditions clear. Prometheus is still the live truth;
        # the table gives us cross-agent visibility, history, and a single
        # surface for the daily brief to query.
        from concerns_sync import sync as _sync_concerns
        _sync_concerns("infra", real)

        # Carried concerns:
        # - When Prometheus is online, Alertmanager IS the source of truth.
        #   Firing alerts with days_seen >= 2 are the "carried" set; they
        #   self-resolve when the underlying condition clears.
        # - When Prometheus is offline, fall back to learnings.md so we don't
        #   lose all memory during a monitoring outage.
        if firing is not None:
            carried = [c for c in real if c.source == "prometheus" and c.days_seen >= 2]
            # The real list keeps fresh (today) alerts; carried holds the persistent ones.
            real = [c for c in real if not (c.source == "prometheus" and c.days_seen >= 2)]
        else:
            carried_path = INFRA_LEARNINGS if INFRA_LEARNINGS.exists() else GENERIC_LEARNINGS
            carried = parse_carried_from_learnings(carried_path) if carried_path.exists() else []
            for c in real:
                for k in carried:
                    if any(token in c.summary.lower() for token in k.summary.lower().split()[:3] if len(token) > 4):
                        c.days_seen = max(c.days_seen, k.days_seen)
                        c.first_seen = c.first_seen or k.first_seen
                        c.last_seen = k.last_seen
                        break

        metrics = _build_metrics(vm_rows, containers, pools, firing)

        # Status: degraded if monitoring is offline or any high/critical real concern exists;
        # failed only if every host probe is dead.  A report that says "🔴 ok" is confusing.
        if metrics["vm_up"] == 0:
            status = "failed"
        elif (not metrics.get("monitoring_online")) or any(c.severity in ("critical", "high") for c in real):
            status = "degraded"
        else:
            status = "ok"

        memory_consulted: list[str] = []
        for f in (INFRA_LEARNINGS, INFRA_INVENTORY, INFRA_DECISIONS, INFRA_RECURRING,
                  INFRA_INCIDENTS, INFRA_RUNBOOKS, INFRA_BASELINES):
            if f.exists():
                memory_consulted.append(str(f.relative_to(ROOT)).replace("\\", "/"))
        if not memory_consulted and GENERIC_LEARNINGS.exists():
            memory_consulted.append(str(GENERIC_LEARNINGS.relative_to(ROOT)) + " (generic fallback)")

        elapsed = int((datetime.now() - started).total_seconds() * 1000)
        return AgentReport(
            agent="infra",
            status=status,
            as_of=datetime.now(),
            headline=_headline(metrics, real, carried),
            real_concerns=real,
            carried_concerns=carried,
            expected_events=expected_events,
            metrics=metrics,
            questions=[],
            raw_data={
                "vm_rows": [{"label": v[0], "status": v[1], "mem": v[2], "disk": v[3]} for v in vm_rows],
                "containers": {k: list(v) if isinstance(v, tuple) else v for k, v in (containers or {}).items()},
                "pools": [list(p) if isinstance(p, tuple) else p for p in (pools or [])],
                "firing_count": len(firing) if isinstance(firing, list) else None,
            },
            memory_consulted=memory_consulted,
            duration_ms=elapsed,
        )
    except Exception as exc:
        return AgentReport.failed("infra", str(exc), started)


# ── CLI for parallel-run validation ──────────────────────────────────────────


def _print_human(r: AgentReport) -> None:
    print(f"\n=== infra-agent · {r.status_emoji} {r.status.upper()} · {r.duration_ms}ms ===")
    print(f"Headline: {r.headline}")
    if r.error:
        print(f"ERROR: {r.error}")
        return
    if r.real_concerns:
        print(f"\nReal concerns ({len(r.real_concerns)}):")
        for c in r.real_concerns:
            note = f"  (seen {c.days_seen}×)" if c.days_seen >= 2 else ""
            print(f"  [{c.severity:8s}] {c.summary}{note}")
    if r.carried_concerns:
        print(f"\nCarried ({len(r.carried_concerns)}):")
        for c in r.carried_concerns:
            print(f"  [{c.severity:8s}] {c.summary}  (seen {c.days_seen}×)")
    print(f"\nMetrics:")
    for k, v in r.metrics.items():
        print(f"  {k:24s} {v}")
    print(f"\nMemory consulted: {', '.join(r.memory_consulted) or '(none)'}")


if __name__ == "__main__":
    import argparse, io
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--date", type=date.fromisoformat, default=None)
    args = p.parse_args()
    report = run(args.date)
    if args.json:
        print(report.to_json())
    else:
        _print_human(report)
    sys.exit(0 if report.status != "failed" else 1)
