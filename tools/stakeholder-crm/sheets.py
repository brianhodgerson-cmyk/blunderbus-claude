"""Thin wrapper around the `gws` Google Workspace CLI for Sheets I/O.

Reuses existing bh@hodgespot.com auth — no new credentials required.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

# Resolve gws executable path up-front. On Windows (Git Bash host),
# `gws` is an npm shim — subprocess.run with shell=False needs gws.cmd directly.
def _find_gws() -> str:
    # Try PATH first
    p = shutil.which("gws.cmd") or shutil.which("gws")
    if p:
        return p
    # Fallback: known npm global install path
    candidates = [
        os.path.expandvars(r"%APPDATA%\npm\gws.cmd"),
        r"C:\Users\brian\AppData\Roaming\npm\gws.cmd",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("Could not locate gws executable on PATH or in %APPDATA%\\npm")

GWS = _find_gws()

SPREADSHEET_ID = "1jy3yRGEZNb5WJiLFBhXp7f6Soa0ATYuUHPgtSQO_u-8"
COMMUNITRY_SHEET_ID = 1716097990
NOTES_FOLDER_ID = "1-l3LLlgD0y_0j7pRHnPbOB-2071DnIIl"
NOTES_SUBFOLDERS = [
    "19-ngQNOWiKEcS9kAQmaFgc3D8Mi1SNnH",  # Stakeholder Notes copy
    "1VIaUp1uenI2V3r-M_xFUOt9RKbohSiFm",  # Char Peterson
    "1nXWQna_7poAoEnutmqP2nh_thQRSNk6I",  # Mike Swindell
]

# Communitry range & column mapping
COMMUNITRY_RANGE = "Communitry!A2:J1005"
HEADERS = [
    "date_of_origin",      # A
    "action_complete",     # B
    "stakeholder",         # C
    "contact",             # D  <-- the one we care about most
    "location",            # E
    "contact_info",        # F
    "stakes",              # G
    "actions_implemented", # H
    "follow_up",           # I
    "notes",               # J
]
CONTACT_IDX = 3  # col D
EDITABLE_COLS = {
    "stakeholder": 2,
    "contact": 3,
    "location": 4,
    "contact_info": 5,
    "stakes": 6,
    "actions_implemented": 7,
    "follow_up": 8,
    "notes": 9,
}


def _run_gws(args: list[str], stdin_json: str | None = None) -> dict[str, Any]:
    """Invoke gws and return parsed JSON stdout. Strips the keyring banner line."""
    cmd = [GWS] + args
    result = subprocess.run(
        cmd,
        input=stdin_json,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} failed: {result.stderr}")
    out = result.stdout
    # strip "Using keyring backend:" preamble if present
    idx = out.find("{")
    if idx < 0:
        return {}
    return json.loads(out[idx:])


# ---------- cached reads ----------
_CACHE: dict[str, Any] = {"contacts": None, "contacts_at": 0, "notes": None, "notes_at": 0}
_CACHE_TTL = 60  # seconds


def _extract_link(cell: dict) -> str | None:
    ue = cell.get("userEnteredValue") or {}
    if "formulaValue" in ue:
        m = re.search(r'HYPERLINK\("([^"]+)"', ue["formulaValue"], re.I)
        if m:
            return m.group(1)
    ef = cell.get("effectiveFormat") or {}
    link = ((ef.get("textFormat") or {}).get("link") or {}).get("uri")
    if link:
        return link
    if cell.get("hyperlink"):
        return cell["hyperlink"]
    return None


def load_contacts(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _CACHE["contacts"] and (now - _CACHE["contacts_at"]) < _CACHE_TTL:
        return _CACHE["contacts"]
    params = {
        "spreadsheetId": SPREADSHEET_ID,
        "includeGridData": True,
        "ranges": [COMMUNITRY_RANGE],
    }
    data = _run_gws(["sheets", "spreadsheets", "get", "--params", json.dumps(params)])
    rows_out = []
    sheet = data["sheets"][0]
    for gd in sheet.get("data", []):
        for i, r in enumerate(gd.get("rowData", [])):
            vals = r.get("values", [])
            contact_cell = vals[CONTACT_IDX] if len(vals) > CONTACT_IDX else {}
            contact_name = (contact_cell.get("formattedValue") or "").strip()
            if not contact_name:
                continue
            record = {
                "row": i + 2,  # 1-indexed sheet row, +1 because we started at A2
                "contact_link": _extract_link(contact_cell),
            }
            for h, idx in zip(HEADERS, range(10)):
                c = vals[idx] if len(vals) > idx else {}
                record[h] = (c.get("formattedValue") or "").strip()
            rows_out.append(record)
    _CACHE["contacts"] = rows_out
    _CACHE["contacts_at"] = now
    return rows_out


def load_notes(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _CACHE["notes"] and (now - _CACHE["notes_at"]) < _CACHE_TTL:
        return _CACHE["notes"]
    parents = [NOTES_FOLDER_ID] + NOTES_SUBFOLDERS
    q = " or ".join(f"'{p}' in parents" for p in parents)
    q = f"({q}) and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
    params = {
        "q": q,
        "fields": "files(id,name,mimeType,modifiedTime)",
        "pageSize": 500,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    data = _run_gws(["drive", "files", "list", "--params", json.dumps(params)])
    files = data.get("files", [])
    # dedup: prefer non-"Copy of", newest modified
    from collections import defaultdict
    def _norm(name: str) -> str:
        clean = re.sub(r"^Copy of\s+", "", name, flags=re.I).strip()
        head = re.split(r"[-\u2013\u2014,\(:]| Notes| In Person", clean, maxsplit=1)[0].strip()
        return re.sub(r"\s+", " ", head).lower()
    groups = defaultdict(list)
    for f in files:
        f["_norm"] = _norm(f["name"])
        groups[f["_norm"]].append(f)
    dedup = []
    for k, group in groups.items():
        group.sort(key=lambda f: (
            f["name"].lower().startswith("copy of"),
            -int(re.sub(r"\D", "", f.get("modifiedTime", ""))[:14] or 0),
        ))
        dedup.append(group[0])
    _CACHE["notes"] = dedup
    _CACHE["notes_at"] = now
    return dedup


def invalidate_cache() -> None:
    _CACHE["contacts"] = None
    _CACHE["notes"] = None


# ---------- writes ----------
def update_cell_string(row: int, col_idx: int, value: str) -> None:
    """Update a single cell's string value on Communitry."""
    req = {
        "requests": [{
            "updateCells": {
                "range": {
                    "sheetId": COMMUNITRY_SHEET_ID,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1,
                },
                "rows": [{"values": [{"userEnteredValue": {"stringValue": value}}]}],
                "fields": "userEnteredValue",
            }
        }]
    }
    _run_gws(
        ["sheets", "spreadsheets", "batchUpdate",
         "--params", json.dumps({"spreadsheetId": SPREADSHEET_ID}),
         "--json", json.dumps(req)]
    )
    invalidate_cache()


def apply_hyperlink(row: int, name: str, doc_url: str) -> None:
    """Apply a whole-cell hyperlink to the Contact column for the given row."""
    req = {
        "requests": [{
            "updateCells": {
                "range": {
                    "sheetId": COMMUNITRY_SHEET_ID,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": CONTACT_IDX,
                    "endColumnIndex": CONTACT_IDX + 1,
                },
                "rows": [{
                    "values": [{
                        "userEnteredValue": {"stringValue": name},
                        "userEnteredFormat": {
                            "hyperlinkDisplayType": "LINKED",
                            "textFormat": {"link": {"uri": doc_url}},
                        },
                    }]
                }],
                "fields": "userEnteredValue,userEnteredFormat.hyperlinkDisplayType,userEnteredFormat.textFormat.link",
            }
        }]
    }
    _run_gws(
        ["sheets", "spreadsheets", "batchUpdate",
         "--params", json.dumps({"spreadsheetId": SPREADSHEET_ID}),
         "--json", json.dumps(req)]
    )
    invalidate_cache()


# ---------- matching ----------
def score_match(contact: str, note_norm: str) -> float:
    import difflib
    c = re.sub(r"\([^)]*\)", " ", contact or "")
    c = re.sub(r"[\-\u2013\u2014:,].*$", " ", c)
    c = re.sub(r"[^A-Za-z' ]", " ", c)
    c = re.sub(r"\s+", " ", c).strip().lower()
    if not c or not note_norm:
        return 0.0
    c_toks = c.split()
    n_toks = note_norm.split()
    if not c_toks or not n_toks:
        return 0.0
    first_match = difflib.SequenceMatcher(None, c_toks[0], n_toks[0]).ratio() >= 0.85
    last_match = difflib.SequenceMatcher(None, c_toks[-1], n_toks[-1]).ratio() >= 0.85
    whole = difflib.SequenceMatcher(None, c, note_norm).ratio()
    if first_match and last_match:
        return 0.85 + 0.15 * whole
    if last_match and len(c_toks[-1]) >= 4:
        return 0.55 + 0.20 * whole
    if first_match and len(c_toks[0]) >= 4:
        return 0.45 + 0.15 * whole
    return 0.0


def suggest_links(contact_name: str, top: int = 5) -> list[dict]:
    notes = load_notes()
    ranked = []
    for n in notes:
        s = score_match(contact_name, n["_norm"])
        if s > 0:
            ranked.append({
                "score": round(s, 3),
                "name": n["name"],
                "id": n["id"],
                "url": f"https://docs.google.com/document/d/{n['id']}/edit?usp=sharing",
            })
    ranked.sort(key=lambda x: -x["score"])
    return ranked[:top]
