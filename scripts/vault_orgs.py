"""List Vaultwarden organizations and collections via bw CLI."""

import json
import os
import shutil
import subprocess
from pathlib import Path

BW_BIN = os.environ.get("BW_BIN") or shutil.which("bw") or "bw"


def _master() -> str:
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        if line.startswith("BW_MASTER_PASS="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("BW_MASTER_PASS not in .env")


def _unlock() -> str:
    r = subprocess.run([BW_BIN, "unlock", _master(), "--raw"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise SystemExit(f"unlock: {r.stderr.strip()}")
    return r.stdout.strip()


def main() -> None:
    s = _unlock()
    subprocess.run([BW_BIN, "sync", "--session", s], capture_output=True, timeout=30)

    orgs = json.loads(subprocess.run(
        [BW_BIN, "list", "organizations", "--session", s], capture_output=True, text=True, timeout=30
    ).stdout or "[]")
    print(f"Organizations ({len(orgs)}):")
    for o in orgs:
        print(f"  - {o['name']!r}  id={o['id']}  status={o.get('status')}")

    cols = json.loads(subprocess.run(
        [BW_BIN, "list", "collections", "--session", s], capture_output=True, text=True, timeout=30
    ).stdout or "[]")
    print(f"\nCollections ({len(cols)}):")
    for c in cols:
        print(f"  - {c['name']!r}  org={c.get('organizationId')}  id={c['id']}")


if __name__ == "__main__":
    main()
