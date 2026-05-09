#!/usr/bin/env python3
"""
BlunderBus Morning Brief Push
Collects infra health across HodgeSpot, formats a markdown block, and injects
it into today's Obsidian daily note under ## Infrastructure.

Schedule: 06:30 daily (after morning_prep.py at 06:00)

Pipeline:
  06:00  morning_prep.py          ← create note + tasks + schedule
  06:30  morning_brief_push.py    ← this script (infra health)
  07:30  finance_intel.py         ← finance block
  21:00  daily_note_review.py     ← evening review

Env vars:
  BLUNDERBUS_NOTE_BACKEND  optional override for note backend selection
  BLUNDERBUS_VAULT_ROOT    filesystem backend root override
  OBSIDIAN_TOKEN           only required when using the obsidian-rest backend
  OBSIDIAN_URL             default: https://127.0.0.1:27124
  SECONION_API_KEY   optional — security alerts skipped if absent
  TRUENAS_API_KEY    optional — NAS health skipped if absent
"""

import io, json, os, re, ssl, subprocess, sys, time, urllib.request, urllib.error, urllib.parse
try:
    import paramiko as _paramiko
except ImportError:
    _paramiko = None
from datetime import date, datetime, timedelta
from pathlib import Path

from blunderbus_data import get_life_events_for_day, log_life_event
from note_store import NoteStoreError, resolve_note_store
from runtime import configure_utf8_stdio, read_env_file

configure_utf8_stdio()

# ─── Bootstrap secrets ────────────────────────────────────────────────────────
# Load .env first so BW_MASTER_PASS is available, then pull vault secrets.
# This makes the script self-sufficient whether run directly or via a PS1 wrapper.
for _k, _v in read_env_file().items():
    os.environ.setdefault(_k, _v)
try:
    from vault import load_secrets as _load_secrets
    _load_secrets()
except Exception as _vault_err:
    print(f"⚠️  Vault bootstrap failed: {_vault_err}", file=sys.stderr)
    print("   Falling back to .env values only.", file=sys.stderr)

# ─── Config ───────────────────────────────────────────────────────────────────

SECONION_USER = os.environ.get("SECONION_USER", "")
SECONION_PASS = os.environ.get("SECONION_PASS", "")
SECONION_URL  = os.environ.get("SECONION_URL", "https://soc.hodgespot.com")
TRUENAS_KEY  = os.environ.get("TRUENAS_API_KEY", "")
NOTE_STORE   = resolve_note_store()
VAULT_DAILY  = NOTE_STORE.daily_dir
SECTION      = "## Infrastructure"
PLACEHOLDER  = "*pending - BlunderBus will populate at 06:30*"

# SSH aliases from ~/.ssh/config
HOSTS = [
    ("Cortex",   "cortex"),
    ("Stark",    "stark"),
    ("Thor",     "thor"),
    ("Banner",   "banner"),
    ("Heimdall", "truenas"),   # SSH alias is 'truenas', not 'heimdall'
    ("Vision",   "vision"),
    ("Loki",     "loki"),
    ("ProfX",    "__local__"), # this host — collect_vm_health special-cases it
]
DOCKER_HOSTS = [("Cortex", "cortex"), ("Stark", "stark")]
SSH_FLAGS    = ["-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes"]

# Host roles for display
ROLES = {
    "Cortex":   "Docker Stack",
    "Stark":    "NPM · Web UI",
    "Thor":     "Ollama · GPU",
    "Banner":   "Grafana",
    "Heimdall": "TrueNAS",
    "Vision":   "MCP Server",
    "Loki":     "Log Agg.",
    "ProfX":    "BlunderBus brain",
}


# ─── SSL (Obsidian self-signed cert) ──────────────────────────────────────────

def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ssh_config_lookup(alias):
    """Resolve alias → (hostname, user, port, proxyjump_alias) via ~/.ssh/config."""
    config_path = os.path.expanduser("~/.ssh/config")
    if _paramiko and os.path.exists(config_path):
        cfg = _paramiko.SSHConfig()
        with open(config_path, encoding="utf-8", errors="replace") as f:
            cfg.parse(f)
        h = cfg.lookup(alias)
        proxy = h.get("proxyjump") or h.get("proxycommand")
        # Only use proxyjump string if it looks like a simple alias (not a full ProxyCommand)
        proxyjump = proxy if proxy and not proxy.startswith("ssh ") else None
        return h.get("hostname", alias), h.get("user", "root"), int(h.get("port", 22)), proxyjump
    return alias, "root", 22, None


def _paramiko_connect(hostname, port, user, key):
    """Open and return a connected paramiko SSHClient. Caller must close it."""
    client = _paramiko.SSHClient()
    client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
    client.connect(hostname, port=port, username=user, pkey=key,
                   timeout=15, banner_timeout=15, auth_timeout=15)
    return client


def ssh_run(alias, cmd, timeout=15):
    # Use paramiko (pure Python SSH) to avoid Windows subprocess+SSH hang bug
    # where SSH hangs at "pledge: filesystem" after auth when called from Python subprocess.
    if _paramiko:
        hostname, user, port, proxyjump = _ssh_config_lookup(alias)
        key_path = os.path.expanduser("~/.ssh/id_ed25519")
        try:
            key = _paramiko.Ed25519Key.from_private_key_file(key_path)
            if proxyjump:
                # ProxyJump: open channel through the jump host
                jump_host, jump_user, jump_port, _ = _ssh_config_lookup(proxyjump)
                jump = _paramiko_connect(jump_host, jump_port, jump_user, key)
                sock = jump.get_transport().open_channel(
                    "direct-tcpip", (hostname, port), ("", 0))
                client = _paramiko.SSHClient()
                client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
                client.connect(hostname, port=port, username=user, pkey=key,
                               sock=sock, timeout=15, banner_timeout=15, auth_timeout=15)
            else:
                client = _paramiko_connect(hostname, port, user, key)
            _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            rc = stdout.channel.recv_exit_status()
            client.close()
            return rc == 0, out or err
        except Exception as e:
            return False, str(e)
    # Fallback: subprocess (may hang on Windows)
    try:
        r = subprocess.run(
            ["ssh"] + SSH_FLAGS + [alias, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def http_get(url, headers=None, timeout=8):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


def obs_get(path):
    rel_path = path.removeprefix("/vault/").lstrip("/")
    try:
        return 200, NOTE_STORE.read_text(rel_path)
    except FileNotFoundError:
        return 404, ""
    except NoteStoreError as exc:
        return None, str(exc)


def obs_put(path, content):
    try:
        rel_path = path.removeprefix("/vault/").lstrip("/")
        NOTE_STORE.write_text(rel_path, content)
        return 200, ""
    except NoteStoreError as exc:
        return None, str(exc)


def note_path(d):
    return f"/vault/{NOTE_STORE.daily_path(d)}"


# ─── Section injection ────────────────────────────────────────────────────────

def inject_section(note, header, new_body):
    """Replace header's content block, or insert before ## Morning Intentions."""
    pattern = re.compile(
        rf"^{re.escape(header)}\n.*?(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    replacement = f"{header}\n{new_body}\n"
    if pattern.search(note):
        return pattern.sub(replacement, note)
    # Section missing — insert before Morning Intentions
    anchor = "## Morning Intentions"
    idx = note.find(anchor)
    if idx >= 0:
        return note[:idx] + replacement + "\n" + note[idx:]
    return note + "\n" + replacement


# ─── Formatting helpers ───────────────────────────────────────────────────────

def pct_bar(pct, width=8):
    """Render a Unicode block bar with color dot: `████░░░░` 52% 🟢"""
    if pct is None:
        return "—"
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * width)
    dot = "🔴" if pct >= 90 else "🟡" if pct >= 75 else "🟢"
    return f"`{'█' * filled}{'░' * (width - filled)}` {pct}% {dot}"


def load_fmt(load_str):
    """Format load average with color dot."""
    try:
        v = float(load_str.strip().split()[0])
        dot = "🔴" if v >= 2.0 else "🟡" if v >= 1.0 else "🟢"
        return f"{dot} {v:.2f}"
    except Exception:
        return load_str or "—"


def parse_mem_pct(s):
    """Parse '3720M/16005M' → int percent."""
    try:
        used, total = s.replace("M", "").split("/")
        return round(int(used) / int(total) * 100)
    except Exception:
        return None


def parse_disk_pct(s):
    """Parse '51%' → 51."""
    try:
        return int(s.replace("%", ""))
    except Exception:
        return None


def callout(ctype, title, lines, foldable="+"):
    """Build a complete Obsidian callout block."""
    fold = foldable if foldable else ""
    out = [f"> [!{ctype}]{fold} {title}"]
    for line in lines:
        out.append(f"> {line}" if line else ">")
    return out


# ─── Collectors ───────────────────────────────────────────────────────────────

def _local_vm_stats():
    """Probe this host directly (no SSH). Used for ProfX since the brief runs here."""
    try:
        with open("/proc/loadavg") as fh:
            load = fh.read().split()[0]
    except Exception:
        load = "?"
    try:
        out = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=5).stdout
        mem_line = next(l for l in out.splitlines() if l.startswith("Mem:"))
        parts = mem_line.split()
        mem = f"{parts[2]}M/{parts[1]}M"
    except Exception:
        mem = "—"
    try:
        out = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5).stdout
        disk = out.splitlines()[1].split()[4]
    except Exception:
        disk = "—"
    return load, mem, disk


def collect_vm_health():
    rows = []
    for label, alias in HOSTS:
        if alias == "__local__":
            load, mem, disk = _local_vm_stats()
            rows.append((label, f"✅ {load}", mem, disk))
            continue
        ok, _ = ssh_run(alias, "echo ok")
        if not ok:
            rows.append((label, "❌", "—", "—"))
            continue
        _, load  = ssh_run(alias, "uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | tr -d ','")
        _, mem   = ssh_run(alias, "free -m | awk '/Mem:/{printf \"%dM/%dM\", $3, $2}'")
        _, disk  = ssh_run(alias, "df -h / | awk 'NR==2{print $5}'")
        rows.append((label, f"✅ {load or '?'}", mem or "—", disk or "—"))
    return rows


def collect_containers():
    results = {}
    for label, alias in DOCKER_HOSTS:
        ok, out = ssh_run(alias, "docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null")
        if not ok:
            results[label] = None
            continue
        running = unhealthy = 0
        for line in out.splitlines():
            if ":" not in line:
                continue
            status = line.split(":", 1)[1].lower()
            if "up" in status:
                running += 1
            if "unhealthy" in status or "exited" in status:
                unhealthy += 1
        results[label] = (running, unhealthy)
    return results


def _soc_session():
    """Authenticate to SecOnion via Kratos and return a session token."""
    if not SECONION_USER or not SECONION_PASS:
        return None
    ctx = ssl_ctx()
    try:
        # Step 1: init login flow
        req = urllib.request.Request(
            f"{SECONION_URL}/auth/self-service/login/api",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            flow = json.loads(r.read().decode())
        action = flow.get("ui", {}).get("action", "")
        if not action:
            return None

        # Step 2: submit credentials
        payload = json.dumps({
            "method": "password", "identifier": SECONION_USER, "password": SECONION_PASS,
        }).encode()
        req2 = urllib.request.Request(
            action, data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req2, timeout=10, context=ctx) as r:
            return json.loads(r.read().decode()).get("session_token")
    except Exception as e:
        print(f"⚠️  SecOnion login failed: {e}", file=sys.stderr)
    return None


def collect_security():
    """Collect SecOnion alerts via Kratos session auth → GET /api/events/."""
    ctx = ssl_ctx()
    session = _soc_session()
    if not session:
        print("⚠️  SecOnion: no session — check SECONION_USER/PASS in vault", file=sys.stderr)
        return None

    try:
        from datetime import timezone as _tz
        now   = datetime.now(_tz.utc)
        begin = now - timedelta(hours=24)
        # SOC uses Go time format '2006/01/02 3:04:05 PM' as the format specifier
        range_str = (f"{begin.strftime('%Y/%m/%d %I:%M:%S %p')}"
                     f" - {now.strftime('%Y/%m/%d %I:%M:%S %p')}")
        params = urllib.parse.urlencode({
            "query":       "tags:alert | groupby event.severity_label",
            "range":       range_str,
            "format":      "2006/01/02 3:04:05 PM",
            "zone":        "America/Chicago",
            "metricLimit": "10",
            "eventLimit":  "25",
        })
        req = urllib.request.Request(
            f"{SECONION_URL}/api/events/?{params}",
            headers={"Authorization": f"Bearer {session}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            data = json.loads(r.read().decode())

        total = data.get("totalEvents", 0)
        if total == 0:
            return {}  # no alerts — return empty dict (distinct from None = failure)

        # Parse severity counts from the groupby metric
        counts = {}
        for bucket in data.get("metrics", {}).get("groupby_0|event.severity_label", []):
            keys = bucket.get("keys", [])
            if keys:
                sev = str(keys[0]).upper()
                counts[sev] = bucket.get("value", 0)

        # Also extract top rule names for the report
        rules = {}
        for event in data.get("events", []):
            p = event.get("payload", {})
            rule = p.get("rule.name", "unknown")
            rules[rule] = rules.get(rule, 0) + 1
        # Stash top rules for the block builder
        collect_security._top_rules = sorted(rules.items(), key=lambda x: -x[1])[:5]

        return counts

    except Exception as e:
        print(f"⚠️  SecOnion API query failed: {e}", file=sys.stderr)
        return None


def collect_cameras():
    since = int(time.time()) - 86400
    code, body = http_get(f"http://192.168.50.205:5000/api/events?after={since}&limit=500")
    if code != 200:
        return None
    try:
        cams = {}
        for e in json.loads(body):
            cam = e.get("camera", "unknown")
            cams[cam] = cams.get(cam, 0) + 1
        return cams
    except Exception:
        return None


def collect_nas():
    if not TRUENAS_KEY:
        return None
    code, body = http_get(
        "http://192.168.50.50/api/v2.0/pool",
        headers={"Authorization": f"Bearer {TRUENAS_KEY}"},
    )
    if code != 200:
        return None
    try:
        return [(p["name"], p["status"], p.get("healthy", False)) for p in json.loads(body)]
    except Exception:
        return None


def collect_prometheus():
    code, body = http_get("http://192.168.50.202:9090/api/v1/alerts")
    if code != 200:
        return None
    try:
        alerts = json.loads(body).get("data", {}).get("alerts", [])
        return [a for a in alerts if a.get("state") == "firing"]
    except Exception:
        return None


# ─── Block builder ────────────────────────────────────────────────────────────

def load_carried_concerns(max_items: int = 5):
    """Pull persistent items from memory/learnings.md so the morning brief opens
    with what's already known to be broken. Returns [] if file missing/empty."""
    learnings = Path(__file__).resolve().parent.parent / "memory" / "learnings.md"
    if not learnings.exists():
        return []
    try:
        text = learnings.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    # Pull the "Active concerns" section
    m = re.search(r"^## Active concerns.*?$(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return []

    out = []
    # Each item starts with "- 🔴 **category** — sample" then a metadata line.
    # We collapse to a short, daily-brief-friendly one-liner.
    item_re = re.compile(
        r"-\s+(🔴|🟡|🆕|✅)\s+\*\*([^*]+)\*\*\s+—\s+(.*?)\s*\n\s+_seen\s+(\d+)×.*?last\s+(\d{4}-\d{2}-\d{2})",
        re.DOTALL,
    )
    for icon, category, sample, count, last_seen in item_re.findall(m.group(1)):
        # Trim sample to ~80 chars and strip noisy markdown table residue
        clean = re.sub(r"\s+", " ", sample).strip()
        clean = re.sub(r"[~|*`]+", "", clean)[:80].strip()
        out.append(f"{icon} **{category}** · {clean} · _{count} days, last {last_seen}_")
        if len(out) >= max_items:
            break
    return out


def build_block(today):
    now = datetime.now().strftime("%H:%M")

    # ── Collect ──────────────────────────────────────────────────────────────
    vm_rows    = collect_vm_health()
    containers = collect_containers()
    sec        = collect_security()
    cams       = collect_cameras()
    pools      = collect_nas()
    firing     = collect_prometheus()

    # ── Derived stats ─────────────────────────────────────────────────────────
    vm_up    = sum(1 for _, st, _, _ in vm_rows if "✅" in st)
    vm_total = len(vm_rows)

    total_containers = sum((r[0] if r else 0) for r in containers.values())
    total_unhealthy  = sum((r[1] if r else 0) for r in containers.values())

    alert_count = len(firing) if firing else 0

    # ── Overall cluster status ────────────────────────────────────────────────
    if vm_up == vm_total and alert_count == 0 and total_unhealthy == 0 and firing is not None:
        cluster_type   = "abstract"
        cluster_icon   = "🟢"
        cluster_status = "All Systems Nominal"
    elif alert_count > 0 or total_unhealthy > 0:
        cluster_type   = "danger"
        cluster_icon   = "🔴"
        cluster_status = "Degraded — Action Required"
    else:
        cluster_type   = "warning"
        cluster_icon   = "🟡"
        cluster_status = f"Partial — {vm_up}/{vm_total} Hosts Reachable"

    lines = [f"*{today.strftime('%A, %B')} {today.day} · checked {now}*", ""]

    # ── Summary banner ────────────────────────────────────────────────────────
    summary_parts = [f"**{vm_up}/{vm_total}** hosts up"]
    if total_containers:
        summary_parts.append(f"**{total_containers}** containers running")
    if pools:
        healthy_pools = sum(1 for _, s, h in pools if h and s == "ONLINE")
        summary_parts.append(f"**{healthy_pools}/{len(pools)}** storage pools healthy")
    if firing is None:
        summary_parts.append("⚠️ monitoring offline")
    elif alert_count:
        summary_parts.append(f"🔴 **{alert_count}** alerts firing")

    banner = callout(cluster_type, f"{cluster_icon} HodgeSpot Cluster — {cluster_status}",
                     [" · ".join(summary_parts)], foldable="")
    lines += banner + [""]

    # ── Carried concerns from memory/learnings.md ─────────────────────────────
    carried = load_carried_concerns()
    if carried:
        lines += callout("warning", f"🧠 Still Open · {len(carried)} concern(s) carried", carried, foldable="-") + [""]

    yesterday = today - timedelta(days=1)
    recent_events = get_life_events_for_day(yesterday, limit=8)
    if recent_events:
        review_lines = []
        for event in recent_events:
            review_lines.append(
                f"- {event['event_time'].strftime('%H:%M')} [{event['domain']}] {event['summary']}"
            )
        lines += callout("info", "Yesterday in Review", review_lines, foldable="-") + [""]

    # ── Virtual Machines ──────────────────────────────────────────────────────
    vm_callout = "success" if vm_up == vm_total else "warning" if vm_up > 0 else "danger"
    vm_title   = (f"✅ Virtual Machines — All {vm_total} Online"
                  if vm_up == vm_total
                  else f"⚠️ Virtual Machines — {vm_up} / {vm_total} Online")

    vm_lines = [
        "| Host | Role | Load | Memory | Disk |",
        "|---|---|---|---|---|",
    ]
    for label, status, mem, disk in vm_rows:
        role = ROLES.get(label, "")
        if "✅" in status:
            load_raw  = status.replace("✅", "").strip()
            mem_p     = parse_mem_pct(mem)
            disk_p    = parse_disk_pct(disk)
            vm_lines.append(
                f"| **{label}** | {role} | {load_fmt(load_raw)} | {pct_bar(mem_p)} | {pct_bar(disk_p)} |"
            )
        else:
            vm_lines.append(f"| ~~{label}~~ | {role} | ❌ offline | — | — |")

    lines += callout(vm_callout, vm_title, vm_lines) + [""]

    # ── Containers ────────────────────────────────────────────────────────────
    c_type  = "success" if not total_unhealthy else "warning"
    c_icon  = "✅" if not total_unhealthy else "⚠️"
    c_title = f"{c_icon} Containers — {total_containers} Running"

    c_lines = ["| Host | Running | Health |", "|---|---|---|"]
    for label, result in containers.items():
        if result is None:
            c_lines.append(f"| **{label}** | ❌ | Unreachable |")
        else:
            running, unhealthy = result
            health = f"⚠️ {unhealthy} unhealthy" if unhealthy else "✅ All healthy"
            c_lines.append(f"| **{label}** | {running} | {health} |")

    lines += callout(c_type, c_title, c_lines) + [""]

    # ── Security ──────────────────────────────────────────────────────────────
    if sec is None:
        lines += callout("note", "🔒 Security · 24h — No Data",
                         ["SecOnion unreachable — check Fury connectivity or vault credentials"],
                         foldable="-") + [""]
    elif not sec:
        lines += callout("success", "🔒 Security · 24h — Clear",
                         ["No IDS/IPS alerts in the last 24 hours."],
                         foldable="-") + [""]
    else:
        total_sec = sum(sec.values())
        has_crit  = sec.get("CRITICAL", 0) + sec.get("HIGH", 0) > 0
        sec_type  = "danger" if has_crit else "warning" if total_sec > 50 else "success"
        sec_lines = ["| Severity | Count |", "|---|---|"]
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
            if sev in sec:
                icon = "🔴" if sev in ("CRITICAL","HIGH") else "🟡" if sev == "MEDIUM" else "🔵"
                sec_lines.append(f"| {icon} **{sev}** | {sec[sev]} |")
        # Add top rules if available
        top_rules = getattr(collect_security, "_top_rules", [])
        if top_rules:
            sec_lines.append("|  |  |")
            sec_lines.append("| **Top Rules** | **Count** |")
            for rule, count in top_rules:
                sec_lines.append(f"| {rule} | {count} |")
        lines += callout(sec_type, f"🔒 Security · 24h — {total_sec} Alerts", sec_lines) + [""]

    # ── Cameras ───────────────────────────────────────────────────────────────
    if cams is None:
        lines += callout("warning", "📷 Cameras · 24h — Offline",
                         ["Frigate NVR unreachable (`192.168.50.205:5000`)"],
                         foldable="-") + [""]
    elif not cams:
        lines += callout("success", "📷 Cameras · 24h — Quiet",
                         ["No motion events detected in the last 24 hours."],
                         foldable="-") + [""]
    else:
        total_cam  = sum(cams.values())
        max_events = max(cams.values())
        cam_lines  = ["| Camera | Events | Activity |", "|---|---|---|"]
        for cam, count in sorted(cams.items(), key=lambda x: -x[1]):
            bar_w   = 10
            filled  = max(1, round(count / max_events * bar_w))
            act_bar = f"`{'█' * filled}{'░' * (bar_w - filled)}`"
            cam_lines.append(
                f"| {cam.replace('_', ' ').title()} | **{count}** | {act_bar} |"
            )
        lines += callout("info", f"📷 Cameras · 24h — {total_cam} Events", cam_lines) + [""]

    # ── NAS ───────────────────────────────────────────────────────────────────
    if pools is None:
        lines += callout("note", "💾 NAS Storage — No Data",
                         ["TrueNAS API key not configured — add `TRUENAS_API_KEY` to `.env`"],
                         foldable="-") + [""]
    else:
        all_ok   = all(h and s == "ONLINE" for _, s, h in pools)
        nas_type = "success" if all_ok else "warning"
        nas_icon = "✅" if all_ok else "⚠️"
        nas_lines = ["| Pool | Status | Health |", "|---|---|---|"]
        for name, status, healthy in pools:
            icon = "✅" if healthy and status == "ONLINE" else "⚠️"
            nas_lines.append(f"| **{name}** | `{status}` | {icon} Healthy |"
                             if healthy else f"| **{name}** | `{status}` | ⚠️ Degraded |")
        lines += callout(nas_type, f"{nas_icon} NAS Storage", nas_lines) + [""]

    # ── Prometheus Alerts ─────────────────────────────────────────────────────
    if firing is None:
        lines += callout("danger", "⚠️ Prometheus — Unreachable",
                         ["Monitoring stack offline — check **Banner** (`192.168.50.202:9090`)"],
                         foldable="-") + [""]
    elif not firing:
        lines += callout("success", "⚠️ Prometheus — Clear",
                         ["No alerts firing."],
                         foldable="-") + [""]
    else:
        fire_lines = ["| Alert | Severity |", "|---|---|"]
        for a in firing[:8]:
            name = a.get("labels", {}).get("alertname", "unknown")
            sev  = a.get("labels", {}).get("severity", "—")
            icon = "🔴" if sev in ("critical","page") else "🟡"
            fire_lines.append(f"| **{name}** | {icon} {sev} |")
        if len(firing) > 8:
            fire_lines.append(f"| *…and {len(firing) - 8} more* | |")
        lines += callout("danger", f"⚠️ Prometheus — {len(firing)} Firing", fire_lines) + [""]

    return "\n".join(lines) + "\n"


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    default=None, help="Override date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print block, don't push")
    parser.add_argument("--force",   action="store_true", help="Overwrite even if already populated")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    print(f"\n🌅 BlunderBus Morning Brief — {today.isoformat()}")
    print("=" * 48)

    print("  Collecting infra data (this takes ~15s)...")
    block = build_block(today)

    if args.dry_run:
        print("\n" + block)
        return

    code, note_body = obs_get(note_path(today))
    if code != 200:
        sys.exit(f"ERROR: Could not fetch daily note (HTTP {code})")

    # Skip if already populated (unless --force)
    if SECTION in note_body and PLACEHOLDER not in note_body and not args.force:
        print("✅ Infrastructure section already populated — skipping (use --force to overwrite)")
        return

    updated = inject_section(note_body, SECTION, block)
    code, err = obs_put(note_path(today), updated)

    if code in (200, 204):
        print(f"✅ Infrastructure block injected → {VAULT_DAILY}/{today.isoformat()}.md")
        log_life_event(
            domain="infra",
            event_type="daily_brief",
            source="morning_brief",
            summary=f"Infrastructure brief generated for {today.isoformat()}",
            detail={"backend": NOTE_STORE.backend_name, "note": NOTE_STORE.daily_path(today)},
            tags=["infra", "daily-note"],
        )
    else:
        print(f"❌ Failed: HTTP {code} — {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
