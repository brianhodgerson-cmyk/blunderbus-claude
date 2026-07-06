#!/usr/bin/env python3
"""BlunderBus SSH certificate authority (roadmap §4).

Subcommands:
  init            generate the CA keypair (~/.ssh/blunderbus_ca) + back up to Vaultwarden
  sign            sign ~/.ssh/id_ed25519.pub -> id_ed25519-cert.pub (default 30d)
  trust HOST..    deploy CA trust to hosts (additive: TrustedUserCAKeys drop-in + reload)
  verify HOST..   prove cert-path auth with a throwaway signed key (in no authorized_keys)
  status          show current cert validity

Design: hosts trust the CA once; the workstation renews a short-lived cert via
blunderbus-ssh-cert-renew.timer. Existing authorized_keys are never touched —
plain-key auth remains as fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import read_env_file  # noqa: E402

CA_KEY = Path.home() / ".ssh" / "blunderbus_ca"
CA_PUB = CA_KEY.with_suffix(".pub")
USER_KEY_PUB = Path.home() / ".ssh" / "id_ed25519.pub"
USER_CERT = Path.home() / ".ssh" / "id_ed25519-cert.pub"
PRINCIPALS = "root,brian,blunderbus,truenas_admin,russ"
VALIDITY = "+30d"
DROPIN = "/etc/ssh/sshd_config.d/60-blunderbus-ca.conf"
CA_DEST = "/etc/ssh/blunderbus_ca.pub"
VAULT_ITEM = "ssh-ca"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 60), **kw)


def ssh(host: str, remote_cmd: str, timeout: int = 30):
    return run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new", host, remote_cmd],
        timeout=timeout,
    )


# ---------------------------------------------------------------- init


def cmd_init(args) -> int:
    if CA_KEY.exists() and not args.force:
        print(f"CA already exists at {CA_KEY}; use --force to regenerate")
        return 0
    r = run(["ssh-keygen", "-t", "ed25519", "-f", str(CA_KEY), "-N", "", "-C", "blunderbus-ssh-ca"])
    if r.returncode != 0:
        print(r.stderr)
        return 1
    CA_KEY.chmod(0o600)
    print(f"CA generated: {CA_PUB.read_text().strip()[:60]}...")
    return vault_backup()


def vault_backup() -> int:
    """Store CA private+public key in Vaultwarden item 'ssh-ca' (create or update)."""
    os.environ.update({k: v for k, v in read_env_file().items() if k not in os.environ})
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import vault  # noqa: PLC0415

    session = vault._unlock()  # noqa: SLF001
    if not session:
        print("WARN: could not unlock Vaultwarden; CA not backed up. Re-run: ssh_ca.py backup")
        return 1
    env = {**os.environ, "BW_SESSION": session}
    bw = vault.BW_BIN
    existing = run([bw, "list", "items", "--search", VAULT_ITEM], env=env)
    items = json.loads(existing.stdout or "[]")
    match = next((i for i in items if i.get("name") == VAULT_ITEM), None)
    payload = {
        "type": 2,
        "secureNote": {"type": 0},
        "name": VAULT_ITEM,
        "notes": CA_KEY.read_text(),
        "fields": [
            {"name": "ca_pub", "value": CA_PUB.read_text().strip(), "type": 0},
            {"name": "principals", "value": PRINCIPALS, "type": 0},
        ],
    }
    import base64

    if match:
        payload = {**match, **payload}
        enc = base64.b64encode(json.dumps(payload).encode()).decode()
        r = run([bw, "edit", "item", match["id"], enc], env=env)
    else:
        enc = base64.b64encode(json.dumps(payload).encode()).decode()
        r = run([bw, "create", "item", enc], env=env)
    if r.returncode == 0:
        run([bw, "sync"], env=env)
        print(f"CA backed up to Vaultwarden item '{VAULT_ITEM}'")
        return 0
    print(f"WARN: vault backup failed: {r.stderr[:200]}")
    return 1


# ---------------------------------------------------------------- sign


def cmd_sign(args) -> int:
    if not CA_KEY.exists():
        print("no CA; run init first")
        return 1
    r = run(
        [
            "ssh-keygen", "-s", str(CA_KEY),
            "-I", f"blunderbus-workstation-{os.uname().nodename}",
            "-n", PRINCIPALS,
            "-V", args.validity,
            str(USER_KEY_PUB),
        ]
    )
    if r.returncode != 0:
        print(r.stderr)
        return 1
    print(f"signed {USER_CERT.name} ({args.validity}, principals: {PRINCIPALS})")
    return cmd_status(args)


def cmd_status(args) -> int:  # noqa: ARG001
    if not USER_CERT.exists():
        print("no cert")
        return 1
    r = run(["ssh-keygen", "-L", "-f", str(USER_CERT)])
    for line in r.stdout.splitlines():
        if any(k in line for k in ("Valid:", "Principals", "Key ID")):
            print(line.strip())
    return 0


# ---------------------------------------------------------------- trust


TRUST_SCRIPT = f"""
set -e
cat > {CA_DEST} <<'CAPUB'
{{ca_pub}}
CAPUB
chmod 644 {CA_DEST}
if grep -qs "^Include /etc/ssh/sshd_config.d" /etc/ssh/sshd_config && [ -d /etc/ssh/sshd_config.d ]; then
  printf 'TrustedUserCAKeys {CA_DEST}\\n' > {DROPIN}
elif ! grep -qs "^TrustedUserCAKeys {CA_DEST}" /etc/ssh/sshd_config; then
  printf '\\nTrustedUserCAKeys {CA_DEST}\\n' >> /etc/ssh/sshd_config
fi
sshd -t
systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || service ssh reload
echo TRUST_OK
"""


def cmd_trust(args) -> int:
    ca_pub = CA_PUB.read_text().strip()
    script = TRUST_SCRIPT.replace("{ca_pub}", ca_pub)
    failures = 0
    for host in args.hosts:
        r = ssh(host, script, timeout=40)
        ok = "TRUST_OK" in r.stdout
        print(f"{host}: {'✅ trusted' if ok else '❌ ' + (r.stderr.strip() or r.stdout.strip())[:120]}")
        failures += 0 if ok else 1
    return 1 if failures else 0


# ---------------------------------------------------------------- verify


def cmd_verify(args) -> int:
    """Sign a throwaway key (present in no authorized_keys) and log in with it."""
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        tk = Path(td) / "probe"
        run(["ssh-keygen", "-t", "ed25519", "-f", str(tk), "-N", "", "-C", "bb-ca-probe"])
        run(
            ["ssh-keygen", "-s", str(CA_KEY), "-I", "bb-ca-probe", "-n", PRINCIPALS,
             "-V", "+10m", str(tk.with_suffix(".pub"))]
        )
        for host in args.hosts:
            r = run(
                ["ssh", "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes",
                 "-o", "StrictHostKeyChecking=accept-new", "-i", str(tk), host, "echo CERT_OK"],
                timeout=30,
            )
            ok = "CERT_OK" in r.stdout
            print(f"{host}: {'✅ cert auth works' if ok else '❌ cert auth failed: ' + r.stderr.strip()[:100]}")
            failures += 0 if ok else 1
    return 1 if failures else 0


# ---------------------------------------------------------------- main


def main() -> int:
    p = argparse.ArgumentParser(description="BlunderBus SSH CA")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("backup")
    s = sub.add_parser("sign"); s.add_argument("--validity", default=VALIDITY)
    sub.add_parser("status")
    s = sub.add_parser("trust"); s.add_argument("hosts", nargs="+")
    s = sub.add_parser("verify"); s.add_argument("hosts", nargs="+")
    args = p.parse_args()
    return {
        "init": cmd_init,
        "backup": lambda a: vault_backup(),  # noqa: ARG005
        "sign": cmd_sign,
        "status": cmd_status,
        "trust": cmd_trust,
        "verify": cmd_verify,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
