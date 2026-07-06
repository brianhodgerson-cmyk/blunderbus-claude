"""Provision a tls-workspace collection in JARVIS-Infra org and move russ items into it."""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

BW_BIN = os.environ.get("BW_BIN") or shutil.which("bw") or "bw"
ORG_NAME = "JARVIS-Infra"
COLLECTION_NAME = "tls-workspace"
ITEMS_TO_MOVE = ["mercury-linux-russ", "mercury-kasmvnc-russ"]


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


def _bw(s: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BW_BIN, *args, "--session", s], capture_output=True, text=True, timeout=30)


def _b64(o: dict) -> str:
    return base64.b64encode(json.dumps(o).encode()).decode()


def main() -> None:
    s = _unlock()
    _bw(s, "sync")

    orgs = json.loads(_bw(s, "list", "organizations").stdout or "[]")
    org = next((o for o in orgs if o["name"] == ORG_NAME), None)
    if not org:
        raise SystemExit(f"Org {ORG_NAME!r} not found")
    org_id = org["id"]

    cols = json.loads(_bw(s, "list", "collections").stdout or "[]")
    col = next((c for c in cols if c["name"] == COLLECTION_NAME and c.get("organizationId") == org_id), None)

    if not col:
        payload = {"organizationId": org_id, "name": COLLECTION_NAME, "externalId": None, "groups": [], "users": []}
        r = _bw(s, "create", "org-collection", _b64(payload), "--organizationid", org_id)
        if r.returncode != 0:
            raise SystemExit(f"create collection: {r.stderr.strip()}")
        col = json.loads(r.stdout)
        print(f"Created collection {COLLECTION_NAME!r} (id={col['id']})")
    else:
        print(f"Collection {COLLECTION_NAME!r} already exists (id={col['id']})")

    col_id = col["id"]
    _bw(s, "sync")
    items = json.loads(_bw(s, "list", "items").stdout or "[]")

    for name in ITEMS_TO_MOVE:
        item = next((i for i in items if i["name"] == name), None)
        if not item:
            print(f"  skip: {name} not found")
            continue
        if item.get("organizationId") == org_id:
            existing = item.get("collectionIds") or []
            if col_id in existing:
                print(f"  ok:   {name} already in {COLLECTION_NAME}")
                continue
            new_col_ids = list({*existing, col_id})
            r = _bw(s, "edit", "item-collections", item["id"], _b64(new_col_ids))
            print(f"  add:  {name} -> {COLLECTION_NAME}  ({r.returncode == 0 and 'ok' or r.stderr.strip()})")
        else:
            # share to org with this collection
            r = _bw(s, "share", item["id"], org_id, _b64([col_id]))
            print(f"  move: {name} -> org/{COLLECTION_NAME}  ({r.returncode == 0 and 'ok' or r.stderr.strip()})")

    print(f"\nOrg id:        {org_id}")
    print(f"Collection id: {col_id}")
    print(f"\nInvite Russ at https://vaultwarden.hodgespot.com/admin")
    print(f"  → Users → Invite User")
    print(f"  → Email: <russ-email>")
    print(f"  → Organization: {ORG_NAME}")
    print(f"  → Collections: {COLLECTION_NAME} (read-only or read/write)")


if __name__ == "__main__":
    main()
