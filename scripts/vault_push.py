"""Push (or update) a single login item into Vaultwarden via the bw CLI.

Reads BW_MASTER_PASS from .env. Idempotent: if an item with the given
name exists, it's updated; otherwise it's created.

Usage:
    python scripts/vault_push.py "<item name>" "<username>" "<password>" ["<notes>"]
"""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BW_BIN = os.environ.get("BW_BIN") or shutil.which("bw") or "bw"


def _load_env() -> str:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("BW_MASTER_PASS="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("BW_MASTER_PASS not in .env")


def _unlock(master: str) -> str:
    r = subprocess.run(
        [BW_BIN, "unlock", master, "--raw"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise SystemExit(f"bw unlock failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _bw(session: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BW_BIN, *args, "--session", session],
        input=stdin, capture_output=True, text=True, timeout=30,
    )


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    name, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    notes = sys.argv[4] if len(sys.argv) > 4 else ""

    session = _unlock(_load_env())
    _bw(session, "sync")

    # Look for existing item
    r = _bw(session, "list", "items", "--search", name)
    items = json.loads(r.stdout) if r.stdout else []
    existing = next((i for i in items if i["name"] == name), None)

    item = {
        "type": 1,  # login
        "name": name,
        "notes": notes,
        "login": {"username": username, "password": password, "uris": []},
    }

    if existing:
        item["id"] = existing["id"]
        encoded = base64.b64encode(json.dumps(item).encode()).decode()
        r = _bw(session, "edit", "item", existing["id"], encoded)
        action = "updated"
    else:
        encoded = base64.b64encode(json.dumps(item).encode()).decode()
        r = _bw(session, "create", "item", encoded)
        action = "created"

    if r.returncode != 0:
        raise SystemExit(f"bw {action} failed: {r.stderr.strip()}")
    print(f"OK: {action} '{name}'")


if __name__ == "__main__":
    main()
