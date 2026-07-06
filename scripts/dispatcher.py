#!/usr/bin/env python3
"""BlunderBus event dispatcher (roadmap §1 full).

Subscribes to MQTT (Mosquitto on Stark) and exposes an HTTP webhook endpoint.
Maps events → handlers via config/dispatch-rules.yaml:

  - discord: deterministic notification to Discord
  - claude:  headless `claude -p` invocation, result posted to Discord
  - script:  run a repo script with the event JSON on stdin

Per-rule debounce persisted in logs/dispatcher-state.json. Handler failures
file an agent_concerns row (agent=dispatcher). Runs as
blunderbus-dispatcher.service via run_pipeline.sh (vault-hydrated env).
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import PROJECT_DIR, configure_utf8_stdio, resolve_claude_command  # noqa: E402

RULES_PATH = PROJECT_DIR / "config" / "dispatch-rules.yaml"
STATE_PATH = PROJECT_DIR / "logs" / "dispatcher-state.json"
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.50.204")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
WEBHOOK_PORT = int(os.environ.get("DISPATCHER_PORT", "8790"))
DISCORD_CHANNEL_FALLBACK = "1477768383645749271"  # #general

_state_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- state


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def debounced(rule_name: str, key: str, seconds: int) -> bool:
    """True if this (rule, key) fired within the window. Records fire time otherwise."""
    with _state_lock:
        state = _load_state()
        slot = f"{rule_name}:{key}"
        now = time.time()
        last = state.get(slot, 0)
        if now - last < seconds:
            return True
        state[slot] = now
        # prune entries older than a week
        state = {k: v for k, v in state.items() if now - v < 7 * 86400}
        STATE_PATH.parent.mkdir(exist_ok=True)
        STATE_PATH.write_text(json.dumps(state))
        return False


# ---------------------------------------------------------------- helpers


def dig(obj, path: str):
    """Dotted-path lookup into nested dicts; None if absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def render(template: str, ctx: dict) -> str:
    """Replace {dotted.path} placeholders from ctx (topic/payload/json fields)."""
    out = template
    import re

    for m in set(re.findall(r"\{([a-zA-Z0-9_.]+)\}", template)):
        val = ctx.get(m)
        if val is None and "." in m:
            val = dig(ctx.get("event") or {}, m)
        if val is None:
            val = dig(ctx, m)
        out = out.replace("{%s}" % m, str(val) if val is not None else "?")
    return out


def post_discord(text: str, channel: str | None = None) -> bool:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        log("no DISCORD_BOT_TOKEN in env; skipping discord post")
        return False
    channel = channel or os.environ.get("DISCORD_CHANNEL_ID") or DISCORD_CHANNEL_FALLBACK
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel}/messages",
            headers={"Authorization": f"Bot {token}"},
            json={"content": text[:1990]},
            timeout=15,
        )
        return r.ok
    except Exception as exc:  # noqa: BLE001
        log(f"discord post failed: {exc}")
        return False


def file_concern(summary: str, target: str, severity: str = "medium") -> None:
    try:
        from blunderbus_memory import Concern, PostgresConcerns, Severity

        PostgresConcerns().upsert(
            Concern(
                id=f"dispatcher:handler-error:{target}".lower()[:120],
                tenant_id="blunderbus",
                agent="dispatcher",
                type="handler-error",
                target=target,
                severity=Severity(severity),
                summary=summary[:400],
                suggested_action="check logs/systemd-dispatcher.log",
            )
        )
    except Exception as exc:  # noqa: BLE001
        log(f"concern filing failed: {exc}")


# ---------------------------------------------------------------- handlers


def run_claude(prompt: str, timeout: int = 120) -> str | None:
    cmd = resolve_claude_command()
    if not cmd:
        return None
    try:
        r = subprocess.run(
            [cmd, "--print", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=os.path.expanduser("~"),
            env=os.environ,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception as exc:  # noqa: BLE001
        log(f"claude invocation failed: {exc}")
        return None


def execute(rule: dict, ctx: dict) -> None:
    name = rule["name"]
    handler = rule.get("handler", "discord")
    key = render(rule.get("debounce_key", "all"), ctx)
    window = int(rule.get("debounce_seconds", 60))
    if debounced(name, key, window):
        log(f"rule {name}: debounced ({key})")
        return
    log(f"rule {name}: firing handler={handler} key={key}")
    try:
        if handler == "discord":
            post_discord(render(rule["message"], ctx), rule.get("channel"))
        elif handler == "claude":
            prompt = render(rule["prompt"], ctx)
            answer = run_claude(prompt, int(rule.get("timeout", 120)))
            if answer is None:
                answer = f"(claude unavailable) event on `{ctx.get('topic') or ctx.get('path')}`:\n```{str(ctx.get('payload'))[:600]}```"
            prefix = rule.get("discord_prefix", f"🤖 **{name}**")
            post_discord(f"{prefix}\n{answer}", rule.get("channel"))
        elif handler == "script":
            subprocess.run(
                [str(PROJECT_DIR / ".venv/bin/python"), str(PROJECT_DIR / rule["script"])],
                input=json.dumps(ctx.get("event") or {}),
                text=True,
                timeout=int(rule.get("timeout", 120)),
                cwd=str(PROJECT_DIR),
                env=os.environ,
            )
        else:
            log(f"rule {name}: unknown handler {handler}")
    except Exception as exc:  # noqa: BLE001
        log(f"rule {name}: handler error: {exc}")
        file_concern(f"dispatcher rule {name} handler error: {exc}", target=name)


def matches(rule: dict, ctx: dict) -> bool:
    if "mqtt_topic" in rule:
        if ctx.get("topic") is None or not fnmatch.fnmatch(ctx["topic"], rule["mqtt_topic"]):
            return False
    if "webhook_path" in rule:
        if ctx.get("path") is None or not fnmatch.fnmatch(ctx["path"], rule["webhook_path"]):
            return False
    if "mqtt_topic" not in rule and "webhook_path" not in rule:
        return False
    for cond in rule.get("conditions", []):
        path, _, want = cond.partition("=")
        if str(dig(ctx.get("event") or {}, path.strip())) != want.strip():
            return False
    return True


def dispatch(ctx: dict) -> None:
    for rule in RULES:
        if matches(rule, ctx):
            threading.Thread(target=execute, args=(rule, ctx), daemon=True).start()


# ---------------------------------------------------------------- inputs


def on_mqtt_message(client, userdata, msg):  # noqa: ANN001
    try:
        payload = msg.payload.decode("utf-8", "replace")
        try:
            event = json.loads(payload)
        except Exception:  # noqa: BLE001
            event = {"raw": payload}
        dispatch({"topic": msg.topic, "payload": payload[:2000], "event": event})
    except Exception as exc:  # noqa: BLE001
        log(f"mqtt handling error: {exc}")


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default access log
        pass

    def do_POST(self):  # noqa: N802
        secret = os.environ.get("DISPATCHER_WEBHOOK_TOKEN")
        if secret and self.headers.get("X-BB-Token") != secret:
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(length, 512 * 1024)).decode("utf-8", "replace")
        try:
            event = json.loads(body) if body else {}
        except Exception:  # noqa: BLE001
            event = {"raw": body}
        log(f"webhook POST {self.path} ({length}b)")
        dispatch({"path": self.path, "payload": body[:2000], "event": event})
        self.send_response(204)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "rules": [r["name"] for r in RULES]}).encode())


# ---------------------------------------------------------------- main

RULES: list[dict] = []


def main() -> None:
    configure_utf8_stdio()
    global RULES
    RULES = yaml.safe_load(RULES_PATH.read_text())["rules"]
    log(f"loaded {len(RULES)} rules from {RULES_PATH.name}")

    topics = sorted({r["mqtt_topic"].split("+")[0].split("#")[0].rstrip("/") + "/#"
                     if ("+" in r["mqtt_topic"] or "#" in r["mqtt_topic"] or "*" in r["mqtt_topic"])
                     else r["mqtt_topic"]
                     for r in RULES if "mqtt_topic" in r})
    # fnmatch globs use * — convert to broad MQTT subscription roots
    subs = sorted({t.replace("*", "#").split("#")[0].rstrip("/") + ("/#" if "#" in t.replace("*", "#") else "")
                   for t in topics})

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="blunderbus-dispatcher")
    if os.environ.get("MQTT_USER"):
        client.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS", ""))
    client.on_message = on_mqtt_message
    client.reconnect_delay_set(min_delay=2, max_delay=60)

    def on_connect(c, u, flags, rc, props=None):  # noqa: ANN001
        log(f"mqtt connected rc={rc}; subscribing: {subs}")
        for s in subs:
            c.subscribe(s)

    client.on_connect = on_connect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    server = ThreadingHTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    log(f"webhook endpoint on :{WEBHOOK_PORT}")
    try:
        server.serve_forever()
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
