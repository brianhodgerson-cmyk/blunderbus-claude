#!/usr/bin/env python3
"""Operator helper for the agent_concerns table.

Usage:
    list_active_concerns.py                 # list all active concerns
    list_active_concerns.py --resolve <id>  # mark one resolved (full or short id ok)
    list_active_concerns.py --resolve-where target=ProfX
                                            # mark all matching concerns resolved
                                            # supported keys: target, agent, type
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from runtime import read_env_file
for k, v in read_env_file().items():
    os.environ.setdefault(k, v)
from vault import load_secrets
load_secrets()
from blunderbus_memory.concerns import PostgresConcerns


def _short(cid: str) -> str:
    return cid[:8]


def _matches(c, key: str, value: str) -> bool:
    if key == "target":
        return (c.target or "") == value
    if key == "agent":
        return c.agent == value
    if key == "type":
        return c.type == value
    raise SystemExit(f"unknown match key: {key}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--resolve", metavar="ID", help="Resolve one concern (full id or 8-char prefix).")
    p.add_argument("--resolve-where", metavar="KEY=VALUE",
                   help="Resolve all active concerns matching KEY=VALUE (target/agent/type).")
    args = p.parse_args()

    with PostgresConcerns() as s:
        active = s.list_active()

        if args.resolve:
            needle = args.resolve
            match = [c for c in active if c.id == needle or _short(c.id) == needle]
            if not match:
                print(f"no active concern with id matching {needle!r}")
                return 1
            if len(match) > 1:
                print(f"ambiguous id prefix {needle!r} matched {len(match)} concerns; pass full id")
                return 1
            c = match[0]
            ok = s.resolve(c.id)
            print(f"{'resolved' if ok else 'no-op'}: id={_short(c.id)} [{c.agent}] {c.summary[:80]}")
            return 0 if ok else 1

        if args.resolve_where:
            if "=" not in args.resolve_where:
                p.error("--resolve-where requires KEY=VALUE")
            key, value = args.resolve_where.split("=", 1)
            targets = [c for c in active if _matches(c, key, value)]
            if not targets:
                print(f"no active concerns matching {key}={value}")
                return 0
            print(f"resolving {len(targets)} concern(s) matching {key}={value}:")
            n = 0
            for c in targets:
                if s.resolve(c.id):
                    n += 1
                    print(f"  resolved id={_short(c.id)} [{c.agent}] {c.summary[:80]}")
            print(f"{n}/{len(targets)} resolved")
            return 0 if n == len(targets) else 1

        print(f"{len(active)} active concerns:")
        for c in active:
            first = c.first_seen.date().isoformat() if c.first_seen else "?"
            print(f"  id={_short(c.id)} [{c.agent}] sev={c.severity.value} type={c.type} "
                  f"target={c.target or '-'} first={first} summary={c.summary[:90]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
