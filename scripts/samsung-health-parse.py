#!/usr/bin/env python3
"""
Samsung Health Export Parser for BlunderBus
Parses Samsung Health CSV exports and outputs a formatted health report.
Optionally pushes metrics to InfluxDB v2 on Banner for Grafana dashboards.

Usage:
  python samsung-health-parse.py [--days N] [--export-path PATH]
  python samsung-health-parse.py --push-influx [--days N]

Environment variables:
  SAMSUNG_HEALTH_EXPORT   Override export directory path
  SAMSUNG_HEALTH_STEP_GOAL  Daily step goal (default: 10000)
  INFLUXDB_URL            InfluxDB URL (default: http://192.168.50.202:8086)
  INFLUXDB_TOKEN          InfluxDB API write token (required for --push-influx)
  INFLUXDB_ORG            InfluxDB org (default: hodgespot)
  INFLUXDB_BUCKET         InfluxDB bucket (default: samsung_health)
"""

import csv
import os
import sys
import glob
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ─── Config ─────────────────────────────────────────────────────────────────

STEP_GOAL = int(os.environ.get("SAMSUNG_HEALTH_STEP_GOAL", 10000))

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://192.168.50.202:8086")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "blunderbus")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "samsung_health")

SEARCH_ROOTS = [
    os.path.expanduser("~/OneDrive"),
    os.path.expanduser("~/Desktop/Samsung Health"),
    os.path.expanduser("~/Downloads"),
]

KG_TO_LBS = 2.20462

# ─── Export Discovery ────────────────────────────────────────────────────────

def find_export_dir(override=None):
    """Find the most recent Samsung Health export directory."""
    if override:
        return Path(override)

    env_path = os.environ.get("SAMSUNG_HEALTH_EXPORT")
    if env_path:
        return Path(env_path)

    candidates = []
    for root in SEARCH_ROOTS:
        pattern = str(Path(root) / "samsunghealth_*")
        candidates.extend(glob.glob(pattern))

    if not candidates:
        sys.exit("❌ No Samsung Health export found. Set SAMSUNG_HEALTH_EXPORT or use --export-path.")

    # Most recently modified wins
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(candidates[0])


def find_csv(export_dir, keyword):
    """Find a CSV by keyword in the export directory."""
    matches = list(export_dir.glob(f"*{keyword}*.csv"))
    return matches[0] if matches else None


# ─── CSV Parsing Helpers ─────────────────────────────────────────────────────

def read_csv(path, skip_meta=True):
    """Read a Samsung Health CSV, skipping the 2-line metadata header."""
    if not path or not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    # Samsung Health CSVs have a metadata line (line 1) before the real header (line 2)
    if skip_meta and len(lines) > 1:
        lines = lines[1:]  # drop metadata line, keep real header + data
    reader = csv.DictReader(lines)
    for row in reader:
        rows.append(row)
    return rows


def parse_dt(val):
    """Parse Samsung Health datetime string to datetime object (UTC-aware)."""
    if not val or val.strip() == "":
        return None
    val = val.strip()
    formats = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def epoch_ms_to_dt(val):
    """Convert epoch milliseconds to datetime."""
    try:
        ts = int(float(val)) / 1000
        return datetime.utcfromtimestamp(ts)
    except (ValueError, TypeError):
        return None


def safe_float(val, default=None):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=None):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def score_emoji(score, high=80, low=60):
    if score is None:
        return "—"
    if score >= high:
        return "✅"
    if score >= low:
        return "⚠️"
    return "❌"


def bar(value, max_val, width=10):
    """Simple ASCII progress bar."""
    if value is None or max_val == 0:
        return "░" * width
    filled = int(min(value / max_val, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def mins_to_hm(mins):
    """Convert minutes to Xh Ym string."""
    if mins is None:
        return "—"
    h = int(mins) // 60
    m = int(mins) % 60
    return f"{h}h {m:02d}m"


# ─── Data Sections ───────────────────────────────────────────────────────────

def get_steps(export_dir, days=7):
    """Parse daily step data. Returns list of (date, steps, distance_m, calories)."""
    path = find_csv(export_dir, "step_daily_trend")
    rows = read_csv(path)

    daily = {}
    for row in rows:
        day_raw = row.get("day_time", "")
        dt = epoch_ms_to_dt(day_raw) if day_raw.strip().lstrip("-").isdigit() else parse_dt(day_raw)
        if not dt:
            continue
        date_key = dt.date()
        steps = safe_int(row.get("count", 0), 0)
        dist = safe_float(row.get("distance", 0), 0)
        cal = safe_float(row.get("calorie", 0), 0)
        if date_key not in daily:
            daily[date_key] = {"steps": 0, "distance": 0, "calories": 0}
        daily[date_key]["steps"] += steps
        daily[date_key]["distance"] += dist
        daily[date_key]["calories"] += cal

    cutoff = (datetime.utcnow() - timedelta(days=days)).date()
    result = []
    for date_key in sorted(daily.keys(), reverse=True):
        if date_key < cutoff:
            break
        d = daily[date_key]
        result.append((date_key, d["steps"], d["distance"], d["calories"]))

    return result[:days]


def get_sleep(export_dir, days=7):
    """Parse sleep records. Returns list of dicts with sleep metrics."""
    path = find_csv(export_dir, "sleep_combined")
    rows = read_csv(path)

    results = []
    cutoff = datetime.utcnow() - timedelta(days=days)

    for row in rows:
        start = parse_dt(row.get("start_time", ""))
        if not start or start < cutoff:
            continue

        end = parse_dt(row.get("end_time", ""))
        duration = safe_int(row.get("sleep_duration"))
        score = safe_int(row.get("sleep_score"))
        mental = safe_float(row.get("mental_recovery"))
        physical = safe_float(row.get("physical_recovery"))
        deep = safe_int(row.get("deep_score"))
        rem = safe_int(row.get("rem_score"))
        efficiency = safe_float(row.get("efficiency"))

        results.append({
            "start": start,
            "end": end,
            "duration_min": duration,
            "score": score,
            "mental_recovery": mental,
            "physical_recovery": physical,
            "deep_score": deep,
            "rem_score": rem,
            "efficiency": efficiency,
        })

    return sorted(results, key=lambda r: r["start"], reverse=True)


def get_vitality(export_dir, days=7):
    """Parse vitality scores. Returns list of dicts."""
    path = find_csv(export_dir, "vitality_score")
    rows = read_csv(path)

    results = []
    cutoff = datetime.utcnow() - timedelta(days=days)

    for row in rows:
        # day_time can be epoch ms OR datetime string depending on version
        day_raw = row.get("day_time", "")
        if not day_raw.strip():
            continue
        if day_raw.strip().lstrip("-").replace(".", "").isdigit() and len(day_raw.strip()) > 10:
            dt = epoch_ms_to_dt(day_raw)
        else:
            dt = parse_dt(day_raw)
        if not dt or dt < cutoff:
            continue

        results.append({
            "date": dt.date(),
            "total": safe_float(row.get("total_score")),
            "activity": safe_float(row.get("activity_score")),
            "sleep": safe_float(row.get("sleep_score")),
            "shr": safe_float(row.get("shr_score")),   # stable heart rate score
            "shrv": safe_float(row.get("shrv_score")),  # HRV score
            "sleep_duration_ms": safe_int(row.get("sleep_duration")),
        })

    return sorted(results, key=lambda r: r["date"], reverse=True)


def get_weight(export_dir, count=5):
    """Parse weight records. Returns list of dicts with full body composition."""
    path = find_csv(export_dir, "com.samsung.health.weight")
    rows = read_csv(path)

    results = []
    for row in rows:
        dt = parse_dt(row.get("start_time", ""))
        if not dt:
            continue
        weight_kg = safe_float(row.get("weight"))
        if not weight_kg or weight_kg < 30:  # filter obvious outliers
            continue
        results.append({
            "date": dt,
            "weight_kg":          weight_kg,
            "weight_lbs":         weight_kg * KG_TO_LBS,
            "body_fat_pct":       safe_float(row.get("body_fat")),
            "body_fat_mass_kg":   safe_float(row.get("body_fat_mass")),
            "muscle_mass_kg":     safe_float(row.get("skeletal_muscle_mass")),
            "fat_free_mass_kg":   safe_float(row.get("fat_free_mass")),
            "total_body_water_kg":safe_float(row.get("total_body_water")),
            "basal_metabolic_rate":safe_float(row.get("basal_metabolic_rate")),
            "vfa_level":          safe_float(row.get("vfa_level")),
            "height_cm":          safe_float(row.get("height")),
        })

    return sorted(results, key=lambda r: r["date"], reverse=True)[:count]


def get_respiratory_rate(export_dir, days=7):
    """Parse respiratory rate records (measured during sleep)."""
    path = find_csv(export_dir, "respiratory_rate")
    rows = read_csv(path)

    results = []
    cutoff = datetime.utcnow() - timedelta(days=days)

    for row in rows:
        dt = parse_dt(row.get("start_time", ""))
        if not dt or dt < cutoff:
            continue
        avg = safe_float(row.get("average"))
        if avg and avg > 0:
            results.append({"date": dt, "bpm": avg})

    return sorted(results, key=lambda r: r["date"], reverse=True)


# ─── Report Rendering ────────────────────────────────────────────────────────

def render_report(export_dir, days=7):
    print(f"\n{'═' * 60}")
    print(f"  🏃 Samsung Health Report  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Source: {export_dir.name}")
    print(f"{'═' * 60}\n")

    # ── Sleep ────────────────────────────────────────────────────────────────
    sleep_data = get_sleep(export_dir, days)

    print("## Sleep")
    if sleep_data:
        print(f"{'Date':<12} {'Score':>5} {'Duration':>9} {'Bedtime':>8} {'Wake':>8}  {'Mental':>6} {'Phys':>5} {'Deep':>4} {'REM':>4}")
        print("─" * 73)
        for s in sleep_data[:days]:
            score = s["score"] or 0
            emoji = score_emoji(score)
            bedtime = s["start"].strftime("%H:%M") if s["start"] else "—"
            waketime = s["end"].strftime("%H:%M") if s["end"] else "—"
            mental = f"{s['mental_recovery']:.0f}" if s["mental_recovery"] else "—"
            phys = f"{s['physical_recovery']:.0f}" if s["physical_recovery"] else "—"
            deep = f"{s['deep_score']}" if s["deep_score"] else "—"
            rem = f"{s['rem_score']}" if s["rem_score"] else "—"
            print(f"{s['start'].strftime('%Y-%m-%d'):<12} {emoji}{score:>3}  {mins_to_hm(s['duration_min']):>9} {bedtime:>8} {waketime:>8}  {mental:>6} {phys:>5} {deep:>4} {rem:>4}")

        scores = [s["score"] for s in sleep_data if s["score"]]
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_dur = sum(s["duration_min"] or 0 for s in sleep_data) / len(sleep_data)
        print(f"\n  7-day avg score: {avg_score:.0f}  |  avg duration: {mins_to_hm(avg_dur)}")
    else:
        print("  No sleep data in range.")

    # ── Steps ────────────────────────────────────────────────────────────────
    steps_data = get_steps(export_dir, days)

    print(f"\n## Steps  (goal: {STEP_GOAL:,})")
    if steps_data:
        print(f"{'Date':<12} {'Steps':>7}  {'Bar':<12} {'Dist (km)':>9} {'Cals':>6}")
        print("─" * 52)
        for date_key, steps, dist_m, cals in steps_data:
            b = bar(steps, STEP_GOAL, 10)
            pct = min(steps / STEP_GOAL * 100, 100)
            goal_icon = "✅" if steps >= STEP_GOAL else ("⚠️" if steps >= STEP_GOAL * 0.7 else "❌")
            print(f"{date_key}  {goal_icon}{steps:>6,}  {b}  {dist_m/1000:>7.1f}km  {cals:>6.0f}")

        all_steps = [s for _, s, _, _ in steps_data]
        avg_steps = sum(all_steps) / len(all_steps) if all_steps else 0
        goal_days = sum(1 for s in all_steps if s >= STEP_GOAL)
        print(f"\n  7-day avg: {avg_steps:,.0f} steps  |  goal met: {goal_days}/{len(steps_data)} days")
    else:
        print("  No step data in range.")

    # ── Vitality Score ───────────────────────────────────────────────────────
    vitality_data = get_vitality(export_dir, days)

    print(f"\n## Vitality Score")
    if vitality_data:
        print(f"{'Date':<12} {'Total':>6} {'Activity':>9} {'Sleep':>6} {'HR':>6} {'HRV':>6}")
        print("─" * 52)
        for v in vitality_data[:days]:
            total = v["total"] or 0
            emoji = score_emoji(total)
            activity = f"{v['activity']:.0f}" if v["activity"] else "—"
            sleep = f"{v['sleep']:.0f}" if v["sleep"] else "—"
            shr = f"{v['shr']:.0f}" if v["shr"] else "—"
            shrv = f"{v['shrv']:.0f}" if v["shrv"] else "—"
            print(f"{v['date']}  {emoji}{total:>4.0f}  {activity:>9} {sleep:>6} {shr:>6} {shrv:>6}")

        totals = [v["total"] for v in vitality_data if v["total"]]
        avg_vitality = sum(totals) / len(totals) if totals else 0
        print(f"\n  7-day avg vitality: {avg_vitality:.0f}")
    else:
        print("  No vitality data in range.")

    # ── Weight ──────────────────────────────────────────────────────────────
    weight_data = get_weight(export_dir)

    print(f"\n## Body Metrics")
    if weight_data:
        latest = weight_data[0]
        prev = weight_data[1] if len(weight_data) > 1 else None
        delta = ""
        if prev:
            diff = latest["weight_kg"] - prev["weight_kg"]
            arrow = "↑" if diff > 0 else "↓"
            delta = f" ({arrow}{abs(diff):.1f} kg)"
        print(f"  Weight:          {latest['weight_lbs']:.1f} lbs / {latest['weight_kg']:.1f} kg{delta}")
        if latest["body_fat_pct"]:
            print(f"  Body Fat:        {latest['body_fat_pct']:.1f}%  ({latest['body_fat_mass_kg']:.1f} kg fat mass)" if latest["body_fat_mass_kg"] else f"  Body Fat:        {latest['body_fat_pct']:.1f}%")
        if latest["muscle_mass_kg"]:
            print(f"  Muscle Mass:     {latest['muscle_mass_kg']:.1f} kg")
        if latest["fat_free_mass_kg"]:
            print(f"  Fat-Free Mass:   {latest['fat_free_mass_kg']:.1f} kg")
        if latest["total_body_water_kg"]:
            print(f"  Total Body Water:{latest['total_body_water_kg']:.1f} kg")
        if latest["basal_metabolic_rate"]:
            print(f"  BMR:             {latest['basal_metabolic_rate']:.0f} kcal/day")
        if latest["vfa_level"]:
            vfa_status = "✅" if latest["vfa_level"] < 100 else "⚠️"
            print(f"  Visceral Fat:    {vfa_status} {latest['vfa_level']:.0f}")
        print(f"  As of:           {latest['date'].strftime('%Y-%m-%d %H:%M')}")
    else:
        print("  No weight data available.")

    # ── Respiratory Rate ─────────────────────────────────────────────────────
    rr_data = get_respiratory_rate(export_dir, days)

    print(f"\n## Respiratory Rate (sleep-time)")
    if rr_data:
        latest_rr = rr_data[0]
        avg_rr = sum(r["bpm"] for r in rr_data) / len(rr_data)
        rr_status = "✅" if 12 <= latest_rr["bpm"] <= 20 else "⚠️"
        print(f"  Latest:  {rr_status} {latest_rr['bpm']:.1f} breaths/min  ({latest_rr['date'].strftime('%Y-%m-%d')})")
        print(f"  7-day avg: {avg_rr:.1f} breaths/min")
        print(f"  Range:   {min(r['bpm'] for r in rr_data):.1f} – {max(r['bpm'] for r in rr_data):.1f}")
    else:
        print("  No respiratory rate data in range.")

    print(f"\n{'═' * 60}\n")


# ─── InfluxDB Push ───────────────────────────────────────────────────────────

def dt_to_ns(dt):
    """Convert datetime to nanosecond epoch for InfluxDB line protocol."""
    epoch = datetime(1970, 1, 1)
    return int((dt - epoch).total_seconds() * 1_000_000_000)


def lp_field(val):
    """Format a value for InfluxDB line protocol field set."""
    if isinstance(val, float):
        return f"{val}"
    if isinstance(val, int):
        return f"{val}i"
    return f'"{val}"'


def build_line_protocol(measurement, fields, timestamp_ns, tags=None):
    """Build a single InfluxDB v2 line protocol string."""
    tag_str = ""
    if tags:
        tag_str = "," + ",".join(f"{k}={v}" for k, v in sorted(tags.items()))

    field_str = ",".join(
        f"{k}={lp_field(v)}"
        for k, v in fields.items()
        if v is not None
    )
    if not field_str:
        return None

    return f"{measurement}{tag_str} {field_str} {timestamp_ns}"


def influx_write(lines, token):
    """POST line protocol data to InfluxDB v2."""
    url = f"{INFLUXDB_URL}/api/v2/write?org={INFLUXDB_ORG}&bucket={INFLUXDB_BUCKET}&precision=ns"
    body = "\n".join(l for l in lines if l).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


def push_to_influx(export_dir, token, days=365):
    """
    Push all Samsung Health metrics to InfluxDB.
    Uses a wide historical window (default 365d) to backfill everything.
    Safe to re-run — InfluxDB deduplicates by timestamp.
    """
    print(f"\nPushing Samsung Health data to InfluxDB...")
    print(f"  Target: {INFLUXDB_URL}  org={INFLUXDB_ORG}  bucket={INFLUXDB_BUCKET}")
    total_points = 0
    errors = []

    # ── Steps ────────────────────────────────────────────────────────────────
    steps_data = get_steps(export_dir, days)
    lines = []
    for date_key, steps, dist_m, cals in steps_data:
        ts = dt_to_ns(datetime(date_key.year, date_key.month, date_key.day, 12, 0, 0))
        line = build_line_protocol("samsung_health_steps", {
            "steps":      int(steps),
            "distance_m": float(dist_m),
            "calories":   float(cals),
            "goal":       int(STEP_GOAL),
            "goal_met":   int(steps >= STEP_GOAL),
        }, ts)
        if line:
            lines.append(line)

    if lines:
        code, err = influx_write(lines, token)
        if err:
            errors.append(f"steps: HTTP {code} — {err[:120]}")
        else:
            print(f"  ✅ Steps:          {len(lines)} days written")
            total_points += len(lines)

    # ── Sleep ────────────────────────────────────────────────────────────────
    sleep_data = get_sleep(export_dir, days)
    lines = []
    for s in sleep_data:
        ts = dt_to_ns(s["start"])
        line = build_line_protocol("samsung_health_sleep", {
            "score":            s["score"],
            "duration_min":     s["duration_min"],
            "mental_recovery":  s["mental_recovery"],
            "physical_recovery": s["physical_recovery"],
            "deep_score":       s["deep_score"],
            "rem_score":        s["rem_score"],
            "efficiency":       s["efficiency"],
        }, ts)
        if line:
            lines.append(line)

    if lines:
        code, err = influx_write(lines, token)
        if err:
            errors.append(f"sleep: HTTP {code} — {err[:120]}")
        else:
            print(f"  ✅ Sleep:          {len(lines)} nights written")
            total_points += len(lines)

    # ── Vitality ─────────────────────────────────────────────────────────────
    vitality_data = get_vitality(export_dir, days)
    lines = []
    for v in vitality_data:
        ts = dt_to_ns(datetime(v["date"].year, v["date"].month, v["date"].day, 12, 0, 0))
        line = build_line_protocol("samsung_health_vitality", {
            "total":    v["total"],
            "activity": v["activity"],
            "sleep":    v["sleep"],
            "shr":      v["shr"],
            "shrv":     v["shrv"],
        }, ts)
        if line:
            lines.append(line)

    if lines:
        code, err = influx_write(lines, token)
        if err:
            errors.append(f"vitality: HTTP {code} — {err[:120]}")
        else:
            print(f"  ✅ Vitality:       {len(lines)} days written")
            total_points += len(lines)

    # ── Weight ───────────────────────────────────────────────────────────────
    weight_data = get_weight(export_dir, count=9999)
    lines = []
    for w in weight_data:
        ts = dt_to_ns(w["date"])
        line = build_line_protocol("samsung_health_weight", {
            "weight_kg":           w["weight_kg"],
            "weight_lbs":          w["weight_lbs"],
            "body_fat_pct":        w["body_fat_pct"],
            "body_fat_mass_kg":    w["body_fat_mass_kg"],
            "muscle_mass_kg":      w["muscle_mass_kg"],
            "fat_free_mass_kg":    w["fat_free_mass_kg"],
            "total_body_water_kg": w["total_body_water_kg"],
            "basal_metabolic_rate":w["basal_metabolic_rate"],
            "vfa_level":           w["vfa_level"],
        }, ts)
        if line:
            lines.append(line)

    if lines:
        code, err = influx_write(lines, token)
        if err:
            errors.append(f"weight: HTTP {code} — {err[:120]}")
        else:
            print(f"  ✅ Weight:         {len(lines)} readings written")
            total_points += len(lines)

    # ── Respiratory Rate ─────────────────────────────────────────────────────
    rr_data = get_respiratory_rate(export_dir, days)
    lines = []
    for r in rr_data:
        ts = dt_to_ns(r["date"])
        line = build_line_protocol("samsung_health_respiratory_rate", {
            "bpm": r["bpm"],
        }, ts)
        if line:
            lines.append(line)

    if lines:
        code, err = influx_write(lines, token)
        if err:
            errors.append(f"respiratory_rate: HTTP {code} — {err[:120]}")
        else:
            print(f"  ✅ Respiratory:    {len(lines)} readings written")
            total_points += len(lines)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n  Total points written: {total_points}")
    if errors:
        print(f"\n  ⚠️  Errors ({len(errors)}):")
        for e in errors:
            print(f"     {e}")
    else:
        print(f"  ✅ All metrics written successfully.")
        print(f"\n  Grafana: http://192.168.50.202:3000")
        print(f"  Bucket:  {INFLUXDB_BUCKET}  |  Org: {INFLUXDB_ORG}")


def provision_influx_bucket(token):
    """
    Create the samsung_health bucket in InfluxDB if it doesn't exist.
    Run once during setup. Retention: 0 = infinite.
    """
    # First get org ID
    url = f"{INFLUXDB_URL}/api/v2/orgs"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Token {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = __import__("json").loads(resp.read())
            orgs = data.get("orgs", [])
            org_id = next((o["id"] for o in orgs if o["name"] == INFLUXDB_ORG), None)
    except Exception as e:
        print(f"❌ Could not fetch orgs: {e}")
        return False

    if not org_id:
        print(f"❌ Org '{INFLUXDB_ORG}' not found in InfluxDB.")
        return False

    # Check if bucket exists
    url = f"{INFLUXDB_URL}/api/v2/buckets?org={INFLUXDB_ORG}&name={INFLUXDB_BUCKET}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Token {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = __import__("json").loads(resp.read())
            if data.get("buckets"):
                print(f"✅ Bucket '{INFLUXDB_BUCKET}' already exists.")
                return True
    except Exception:
        pass

    # Create bucket
    import json
    payload = json.dumps({
        "name": INFLUXDB_BUCKET,
        "orgID": org_id,
        "retentionRules": [],  # infinite retention
        "description": "Samsung Health personal metrics",
    }).encode("utf-8")

    url = f"{INFLUXDB_URL}/api/v2/buckets"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"✅ Bucket '{INFLUXDB_BUCKET}' created (org: {INFLUXDB_ORG}, retention: infinite).")
            return True
    except urllib.error.HTTPError as e:
        print(f"❌ Failed to create bucket: HTTP {e.code} — {e.read().decode()[:200]}")
        return False


# ─── Entry Point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Samsung Health Export Parser for BlunderBus")
    parser.add_argument("--days",        type=int,  default=7,    help="Days of data to report/push (default: 7; use 0 for all)")
    parser.add_argument("--export-path", type=str,  default=None, help="Path to Samsung Health export directory")
    parser.add_argument("--push-influx", action="store_true",     help="Push metrics to InfluxDB on Banner")
    parser.add_argument("--setup-bucket",action="store_true",     help="Create InfluxDB bucket (run once during setup)")
    parser.add_argument("--no-report",   action="store_true",     help="Skip terminal report (useful with --push-influx)")
    args = parser.parse_args()

    export_dir = find_export_dir(args.export_path)
    if not export_dir.exists():
        sys.exit(f"❌ Export directory not found: {export_dir}")

    days = args.days if args.days > 0 else 3650  # 0 = all (10 years)

    # Bucket setup (one-time)
    if args.setup_bucket:
        token = os.environ.get("INFLUXDB_TOKEN")
        if not token:
            sys.exit("❌ INFLUXDB_TOKEN env var required for --setup-bucket")
        provision_influx_bucket(token)
        return

    # Push to InfluxDB
    if args.push_influx:
        token = os.environ.get("INFLUXDB_TOKEN")
        if not token:
            sys.exit(
                "❌ INFLUXDB_TOKEN not set.\n"
                "   Get your token from: http://192.168.50.202:8086 → Data → API Tokens\n"
                "   Then: set INFLUXDB_TOKEN=<your-token>  (Windows)\n"
                "      or: export INFLUXDB_TOKEN=<your-token>  (bash)"
            )
        push_to_influx(export_dir, token, days=days)

    # Terminal report (default, skip with --no-report)
    if not args.no_report:
        render_report(export_dir, days=min(days, 7))


if __name__ == "__main__":
    main()
