"""
One-time migration: pull facts that today live scattered across markdown into
the structured registry under memory/registry/.

Sources merged:
- ~/.claude/projects/.../memory/reference_key_contacts.md  → people
- ~/.claude/projects/.../memory/user_profile.md            → people (Brian, Jamie, kids)
- ~/.claude/projects/.../memory/project_*.md               → projects
- memory/workspace/people.md                                → people (cross-check)
- memory/finance/accounts.md                                → accounts
- memory/infra/inventory.md (+ CLAUDE.md)                  → inventory

This is idempotent — re-run safely. Existing entities are preserved (we only
fill blanks). Pass --overwrite to force.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Allow `python scripts/blunderbus_memory/migrate.py` to import the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blunderbus_memory import (  # noqa: E402
    Account, HostStatus, Inventory, MarkdownRegistry,
    Person, Project, ProjectStatus,
)


# ── Hand-curated seed data ───────────────────────────────────────────────────
# Pulled from reference_key_contacts.md, user_profile.md, project_*.md.
# This is the v1 ground truth — re-runs of agents will keep these in sync.

PEOPLE_SEED = [
    Person(
        id="brian-hodgerson",
        full_name="Brian Hodgerson",
        role="operator",
        title="Senior technical professional",
        firm="Nike",
        tags=["family/self", "operator"],
        triage="self",
        attributes={
            "income_w2_2025": 183684,
            "va_disability": "100% P&T",
            "va_pension_yr": 34680,
            "marginal_bracket_pct": 24,
            "filing_status": "MFJ",
            "tricare": True,
            "hsa_eligible": False,
            "address": "29238 Seabiscuit Dr, Boerne TX 78015",
        },
        notes="Senior technical professional at Nike. 100% VA Disabled P&T. Forming TX LLC for AI services.",
    ),
    Person(
        id="jamie-hodgerson",
        full_name="Jamie Hodgerson",
        role="spouse",
        firm="Mindscapes",
        tags=["family/spouse"],
        triage="high",
        relationships=["llc-formation", "tax-amendment-2025"],
        attributes={"income_2025": 13128},
        notes="Brian's wife. Joint financial decisions, household coordination. Potential LLC co-owner (constrained by SDVOSB 51% rule).",
    ),
    Person(
        id="evangeline-hodgerson",
        full_name="Evangeline Hodgerson",
        role="daughter",
        tags=["family/kids"],
        relationships=["eva-college-funding"],
        attributes={"dob": "2010-04-30"},
        notes="Custodial NFCU account (...0137).",
    ),
    Person(
        id="nathaniel-hodgerson",
        full_name="Nathaniel Hodgerson",
        role="son",
        tags=["family/kids"],
        relationships=["nate-college-funding"],
        attributes={"dob": "2013-03-15"},
        notes="Custodial NFCU account (...0723).",
    ),
    Person(
        id="sheila-streeter",
        full_name="Sheila Streeter",
        role="financial planner",
        title="Director of Client Service",
        tags=["professional/advisor"],
        triage="high",
        relationships=["tax-amendment-2025"],
        attributes={"is_tax_cpa": False, "handles": ["Roth IRA", "investment accounts"]},
        notes="NOT a tax CPA. Handles Roth IRA and investment accounts. Needs corrected MAGI and K-1 PDF for 2025 amendment.",
    ),
    Person(
        id="chris",
        full_name="Chris (last name unknown)",
        role="executes Roth sales at Sheila's firm",
        firm="Sheila Streeter Financial",
        tags=["professional/sheila-firm"],
        relationships=["tax-amendment-2025"],
        notes="Was set to sell Roth holdings; on hold pending Roth excess recalc.",
    ),
    Person(
        id="vanessa-franco",
        full_name="Vanessa Franco",
        role="trustee",
        title="Trustee, J&C Hodgerson Family Trust",
        tags=["family/sibling", "professional/trustee"],
        relationships=["tax-amendment-2025"],
        attributes={"trust_ein": "87-6845957", "relationship": "Brian's sister"},
        notes="Brian's sister. Issued the surprise K-1 from J&C Hodgerson Family Trust.",
    ),
    Person(
        id="rusty",
        full_name="Rusty (TLS Company)",
        role="client",
        firm="TLS",
        tags=["professional/client"],
        triage="medium",
        relationships=["stakeholder-crm", "llc-formation"],
        attributes={
            "tracking_sheet": "Communitry",
            "sheet_id": "1jy3yRGEZNb5WJiLFBhXp7f6Soa0ATYuUHPgtSQO_u-8",
            "stalled_threshold_days": 14,
        },
        notes="Brian builds agentic tooling for TLS workflow consulting. Potential retainer post-LLC formation. Mercury hosts TLS workspace.",
    ),
    Person(
        id="mike-hess",
        full_name="Mike Hess",
        role="client",
        firm="Mike Hess Brewing",
        tags=["professional/client"],
        triage="medium",
        relationships=["stakeholder-crm", "llc-formation"],
        notes="Agentic project ongoing. Potential retainer post-LLC formation.",
    ),
]


PROJECTS_SEED = [
    Project(
        id="tax-amendment-2025",
        name="2025 Tax Amendment (1040-X) — K-1 from Family Trust",
        status=ProjectStatus.BLOCKED,
        summary="J&C Hodgerson Family Trust K-1 ($10,515 ordinary dividends) raises 2025 MAGI from $239,644 → $250,159. Triggers ~$2,530 additional federal tax + full Roth IRA phase-out for both spouses. Return-of-excess deadline 10/15/2026 with extension.",
        people=["sheila-streeter", "chris", "vanessa-franco", "brian-hodgerson", "jamie-hodgerson"],
        tags=["finance", "tax"],
        blockers=[
            "Who is actually preparing the 1040-X (Sheila is not a tax CPA)",
            "K-1 timing — does 1040-X go concurrent with original or after",
            "Whose Roth had the excess (both spouses?), and Chris pending recalc",
        ],
        attributes={
            "trust_ein": "87-6845957",
            "k1_amount": 10515,
            "old_magi": 239644,
            "new_magi": 250159,
            "additional_tax_estimate": 2530,
            "roth_phaseout_threshold_mfj_2025": 246000,
            "deadline_excess_return": "2026-10-15",
            "ca_filing": False,
        },
        notes="Surprise K-1 arrived post-filing. Sheila's prior Roth recalc was based on intermediate MAGI and needs updating. No CA filing obligation (TX resident, no CA-source income).",
    ),
    Project(
        id="llc-formation",
        name="AI Services TX LLC formation",
        status=ProjectStatus.ACTIVE,
        summary="TX single-member LLC (sole prop taxation initially) for agentic AI consulting. Potential S-corp election in 2027 if net profit exceeds ~$50K.",
        people=["brian-hodgerson", "jamie-hodgerson", "rusty", "mike-hess"],
        tags=["business", "tax"],
        blockers=[
            "EIN status — obtained or pending?",
            "Operating agreement draft state",
            "S-corp election decision (2027 trigger)",
        ],
        attributes={
            "structure": "single-member LLC",
            "state": "TX",
            "scorp_threshold_net_profit": 50000,
            "sdvosb_certification_planned": True,
            "kids_payroll_strategy": True,
        },
        notes="Reduce tax burden ($10K-14K/yr potential), formalize Rusty + Mike Hess consulting, leverage 100% P&T for SDVOSB cert.",
    ),
    Project(
        id="stakeholder-crm",
        name="Stakeholder CRM (Communitry)",
        status=ProjectStatus.ACTIVE,
        people=["rusty", "mike-hess"],
        tags=["business", "tooling"],
        attributes={
            "sheet_id": "1jy3yRGEZNb5WJiLFBhXp7f6Soa0ATYuUHPgtSQO_u-8",
            "tool_path": "tools/stakeholder-crm/",
        },
        notes="Tracking active client engagements. Rusty + Mike Hess primary, Char Peterson + Mike Swindell secondary.",
    ),
    Project(
        id="frigate-gpu-deferred",
        name="Frigate GPU passthrough (deferred)",
        status=ProjectStatus.PAUSED,
        summary="Frigate runs CPU-only on Hawkeye after 2026-04-30 reboot. Proxmox host has no /dev/dri/. Calendar reminder set for 2026-05-07 to revisit.",
        tags=["infra", "deferred"],
        attributes={"revisit_date": "2026-05-07", "hawkeye_lxc": 205},
        notes="Backup files at /opt/frigate/docker-compose.yml.bak and /opt/frigate/config/config.yml.bak. Either install proper NVIDIA driver on host with render-node support, or accept CPU-only as permanent.",
    ),
    Project(
        id="eva-college-funding",
        name="Eva college funding (529 decision pending)",
        status=ProjectStatus.PAUSED,
        people=["evangeline-hodgerson"],
        tags=["family", "finance"],
        notes="529 decision pending; camping trip June.",
    ),
    Project(
        id="nate-college-funding",
        name="Nate college funding (529 decision pending)",
        status=ProjectStatus.PAUSED,
        people=["nathaniel-hodgerson"],
        tags=["family", "finance"],
        notes="529 decision pending.",
    ),
]


# Inventory pulled from CLAUDE.md table — single source for hosts.
INVENTORY_SEED = [
    Inventory(id="multiverse", hostname="Multiverse", ip="192.168.50.100", kind="host",
              role="hypervisor", ssh_alias="proxmox", monitored=True,
              notes="Proxmox VE hypervisor — manages all VMs and LXC containers."),
    Inventory(id="thor", hostname="Thor", ip="192.168.50.136", kind="vm",
              role="workstation", ssh_alias="thor", monitored=True,
              attributes={"gpu": "RTX 4080", "ollama_model": "qwen3:14b"},
              notes="Workstation / Ollama. windows_exporter on :9182. THIS host."),
    Inventory(id="heimdall", hostname="Heimdall", ip="192.168.50.50", vmid=100, kind="vm",
              role="storage", ssh_alias="truenas", monitored=True,
              notes="TrueNAS SCALE — NAS storage, ZFS pools, PCIe passthrough."),
    Inventory(id="jarvis", hostname="Jarvis", ip="192.168.50.206", vmid=102, kind="vm",
              role="home-automation", ssh_alias="homeassistant", monitored=True,
              attributes={"prometheus_endpoint": "/api/prometheus"},
              notes="Home Assistant — bearer-token Prometheus integration."),
    Inventory(id="fury", hostname="Fury", ip="192.168.50.103", vmid=103, kind="vm",
              role="ids", ssh_alias="fury", monitored=True,
              attributes={"os": "Oracle Linux 9", "selinux": True, "iptables_persistent": False},
              notes="SecOnion IDS/IPS sensor. node_exporter at /usr/sbin/. iptables ACCEPT for Banner→9100 is ephemeral."),
    Inventory(id="stark", hostname="Stark", ip="192.168.50.204", vmid=104, kind="vm",
              role="services", ssh_alias="stark", monitored=True,
              attributes={"ram_gb": 2, "ram_pressure_pct": 93},
              notes="NPM, Open WebUI, Mosquitto MQTT, Portainer. RAM-starved; persistent ~93% memory."),
    Inventory(id="cortex", hostname="Cortex", ip="192.168.50.106", vmid=106, kind="vm",
              role="stack", ssh_alias="cortex", monitored=True,
              attributes={"proxy_jump": "stark", "host_postgres": True},
              notes="Docker stack: postgres, redis, litellm, langfuse, minio, clickhouse. ProxyJump through Stark."),
    Inventory(id="profx", hostname="ProfX", ip="192.168.50.107", vmid=107, kind="lxc",
              role="pipelines", ssh_alias="profx", monitored=True,
              notes="BlunderBus brain — pipelines, NFS to TrueNAS at /mnt/nas/profx."),
    Inventory(id="mercury", hostname="Mercury", ip="192.168.50.109", vmid=108, kind="lxc",
              role="tls-workspace", monitored=True,
              attributes={"tenant": "tls", "host_for": "rusty"},
              notes="Hosting TLS AI workspace for Rusty. Future target for blunderbus_memory v1 deploy."),
    Inventory(id="groot", hostname="Groot", ip="192.168.50.53", vmid=200, kind="lxc",
              role="dns", ssh_alias="groot", monitored=True,
              attributes={"web_port": 80, "dns_port": 53},
              notes="AdGuard Home DNS. Watch query log size; previously full at 3GB."),
    Inventory(id="banner", hostname="Banner", ip="192.168.50.202", vmid=202, kind="lxc",
              role="monitoring", ssh_alias="banner", monitored=True,
              attributes={"prometheus_port": 9090, "alertmanager_port": 9093, "grafana_port": 3000},
              notes="Grafana + InfluxDB + Prometheus + Alertmanager. Note systemd unit overrides for LXC mount-namespacing."),
    Inventory(id="hawkeye", hostname="Hawkeye", ip="192.168.50.205", vmid=205, kind="lxc",
              role="nvr", ssh_alias="hawkeye-nvr", monitored=True,
              attributes={"frigate_port": 5000, "gpu_acceleration": False},
              notes="Frigate NVR. Currently CPU-only; GPU passthrough deferred to 2026-05-07."),
    Inventory(id="loki", hostname="Loki", ip="192.168.50.207", vmid=207, kind="lxc",
              role="logs", ssh_alias="loki", monitored=True,
              attributes={"port": 3100},
              notes="Loki log aggregation."),
    Inventory(id="ultron", hostname="Ultron", ip="192.168.50.209", vmid=209, kind="lxc",
              role="utility", ssh_alias="ultron", monitored=True,
              notes="SSH bastion / minimal services."),
    Inventory(id="vision", hostname="Vision", ip="192.168.50.210", vmid=210, kind="lxc",
              role="mcp", ssh_alias="vision", monitored=True,
              attributes={"mcp_port": 8788, "vision_server_port": 8787},
              notes="BlunderBus MCP server, vision_server, Frigate MQTT bridge."),
]


# Accounts seed — minimal for v1; finance agent will fill the rest from ClickHouse.
ACCOUNTS_SEED = [
    Account(id="nfcu-everyday-checking",
            name="EveryDay Checking",
            institution="NFCU",
            account_type="checking",
            last_four="0958",
            owner="UNKNOWN",
            tags=["finance", "owner-unknown"],
            notes="Owner not yet confirmed."),
    Account(id="nfcu-share-savings",
            name="Share Savings",
            institution="NFCU",
            account_type="savings",
            last_four="8001",
            owner="UNKNOWN",
            tags=["finance", "owner-unknown"],
            notes="Owner (...8001/4685) not yet confirmed."),
    Account(id="brian-ira-nfs",
            name="Brian L Hodgerson IRA NFS Brokerage",
            institution="Fidelity NFS",
            account_type="IRA",
            last_four="0723",
            owner="brian-hodgerson",
            tags=["finance", "retirement"],
            notes="Status unclear — closed or pre-funded for 2026 contribution?"),
    Account(id="eva-custodial-nfcu",
            name="Eva (NFCU custodial)",
            institution="NFCU",
            account_type="custodial",
            last_four="0137",
            owner="evangeline-hodgerson",
            tags=["finance", "kids"],
            attributes={"balance_at_seed": 2334}),
    Account(id="nate-custodial-nfcu",
            name="Nate (NFCU custodial)",
            institution="NFCU",
            account_type="custodial",
            last_four="0723",
            owner="nathaniel-hodgerson",
            tags=["finance", "kids"],
            attributes={"balance_at_seed": 648}),
]


# ── Driver ───────────────────────────────────────────────────────────────────


def _seed(reg: MarkdownRegistry, *, overwrite: bool) -> dict[str, int]:
    counts = {"people": 0, "projects": 0, "accounts": 0, "inventory": 0,
              "skipped_existing": 0}
    for collection_name, items in [
        ("people", PEOPLE_SEED),
        ("projects", PROJECTS_SEED),
        ("accounts", ACCOUNTS_SEED),
        ("inventory", INVENTORY_SEED),
    ]:
        coll = getattr(reg, collection_name)
        for item in items:
            if not overwrite and item.id in coll:
                counts["skipped_existing"] += 1
                continue
            coll.upsert(item, agent="migrate")
            counts[collection_name] += 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--overwrite", action="store_true",
                   help="Replace existing entries (default: skip)")
    p.add_argument("--registry", default=None,
                   help="Custom registry root (default: <repo>/memory/registry)")
    args = p.parse_args()

    if args.registry:
        reg = MarkdownRegistry(Path(args.registry))
    else:
        repo = Path(__file__).resolve().parent.parent.parent
        reg = MarkdownRegistry(repo / "memory" / "registry")

    print(f"Seeding registry at {reg.root}")
    print(f"Overwrite existing: {args.overwrite}")
    counts = _seed(reg, overwrite=args.overwrite)
    print()
    for k, v in counts.items():
        print(f"  {k:20s} {v}")
    print()
    print("Stats after seed:")
    for k, v in reg.stats().counts.items():
        print(f"  {k:12s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
