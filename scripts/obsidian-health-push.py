#!/usr/bin/env python3
"""
Render a daily health summary into the daily note.

The script prefers a local Samsung Health export when available and falls back
to the ClickHouse daily health rollup created by the Google Fit ingest path.
"""

import argparse
import csv
import glob
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from blunderbus_data import get_health_snapshot, log_life_event
from note_store import NoteStoreError, resolve_note_store, upsert_section
from runtime import configure_utf8_stdio


configure_utf8_stdio()

NOTE_STORE = resolve_note_store()
SEARCH_ROOTS = [
    os.path.expanduser("~/OneDrive"),
    os.path.expanduser("~/Desktop/Samsung Health"),
    os.path.expanduser("~/Downloads"),
]
KG_TO_LBS = 2.20462
SKIP_EVENT_LOG = os.environ.get("BLUNDERBUS_SKIP_EVENT_LOG", "").strip().lower() in {"1", "true", "yes"}


def find_export_dir():
    env_path = os.environ.get("SAMSUNG_HEALTH_EXPORT")
    if env_path:
        return Path(env_path)

    candidates = []
    for root in SEARCH_ROOTS:
        candidates.extend(glob.glob(str(Path(root) / "samsunghealth_*")))

    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(candidates[0])


def find_csv(export_dir, keyword):
    matches = list(export_dir.glob(f"*{keyword}*.csv"))
    return matches[0] if matches else None


def read_csv(path):
    if not path or not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as handle:
        lines = handle.readlines()
    if len(lines) > 1:
        lines = lines[1:]
    return list(csv.DictReader(lines))


def parse_dt(value):
    if not value or not value.strip():
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def safe_float(value, default=None):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value, default=None):
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def epoch_ms_to_dt(value):
    try:
        return datetime.utcfromtimestamp(int(float(value)) / 1000)
    except (ValueError, TypeError):
        return None


def mins_to_hm(minutes):
    if not minutes:
        return "—"
    return f"{int(minutes) // 60}h {int(minutes) % 60:02d}m"


def most_recent_before(rows, date_fn, target_date, lookback_days=3):
    cutoff = target_date - timedelta(days=lookback_days)
    candidates = []
    for row in rows:
        row_date = date_fn(row)
        if row_date and cutoff <= row_date <= target_date:
            candidates.append((row_date, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def get_metrics_for_date(export_dir, target_date):
    metrics = {}

    path = find_csv(export_dir, "step_daily_trend")
    rows = read_csv(path)
    daily_steps = {}
    daily_dist = {}
    daily_cal = {}
    for row in rows:
        day_raw = row.get("day_time", "")
        dt = epoch_ms_to_dt(day_raw) if day_raw.strip().lstrip("-").isdigit() else parse_dt(day_raw)
        if not dt:
            continue
        day_key = dt.date()
        daily_steps[day_key] = daily_steps.get(day_key, 0) + safe_int(row.get("count", 0), 0)
        daily_dist[day_key] = daily_dist.get(day_key, 0) + safe_float(row.get("distance", 0), 0)
        daily_cal[day_key] = daily_cal.get(day_key, 0) + safe_float(row.get("calorie", 0), 0)

    steps_date = target_date
    if target_date not in daily_steps:
        cutoff = target_date - timedelta(days=7)
        recent = [day_key for day_key in daily_steps if cutoff <= day_key <= target_date]
        if recent:
            steps_date = max(recent)
    metrics["steps"] = daily_steps.get(steps_date, 0)
    metrics["steps_dist_km"] = daily_dist.get(steps_date, 0) / 1000
    metrics["steps_cal"] = daily_cal.get(steps_date, 0)
    if steps_date != target_date and metrics["steps"]:
        metrics["steps_date_note"] = steps_date.strftime("%d %b").lstrip("0")

    path = find_csv(export_dir, "sleep_combined")
    rows = read_csv(path)

    def sleep_date(row):
        end_dt = parse_dt(row.get("end_time", ""))
        return end_dt.date() if end_dt else None

    sleep_row = most_recent_before(rows, sleep_date, target_date, lookback_days=7)
    if sleep_row:
        metrics["sleep_score"] = safe_int(sleep_row.get("sleep_score"))
        metrics["sleep_duration"] = safe_int(sleep_row.get("sleep_duration"))
        metrics["sleep_start"] = parse_dt(sleep_row.get("start_time"))
        metrics["sleep_end"] = parse_dt(sleep_row.get("end_time"))
        metrics["mental_rec"] = safe_float(sleep_row.get("mental_recovery"))
        metrics["physical_rec"] = safe_float(sleep_row.get("physical_recovery"))
        end_dt = parse_dt(sleep_row.get("end_time", ""))
        if end_dt and end_dt.date() < target_date:
            if sys.platform == "win32":
                metrics["sleep_date_note"] = end_dt.strftime("%d %b").lstrip("0")
            else:
                metrics["sleep_date_note"] = end_dt.strftime("%-d %b")

    path = find_csv(export_dir, "vitality_score")
    rows = read_csv(path)

    def vitality_date(row):
        day_raw = row.get("day_time", "")
        if day_raw.strip().lstrip("-").replace(".", "").isdigit() and len(day_raw.strip()) > 10:
            dt = epoch_ms_to_dt(day_raw)
        else:
            dt = parse_dt(day_raw)
        return dt.date() if dt else None

    vitality_row = most_recent_before(rows, vitality_date, target_date, lookback_days=7)
    if vitality_row:
        metrics["vitality"] = safe_float(vitality_row.get("total_score"))
        metrics["vitality_activity"] = safe_float(vitality_row.get("activity_score"))
        metrics["vitality_sleep"] = safe_float(vitality_row.get("sleep_score"))

    path = find_csv(export_dir, "com.samsung.health.weight")
    rows = read_csv(path)
    weight_rows = [(parse_dt(row.get("start_time", "")), row) for row in rows]
    weight_rows = [
        (dt, row)
        for dt, row in weight_rows
        if dt and dt.date() <= target_date and safe_float(row.get("weight", 0), 0) > 30
    ]
    if weight_rows:
        weight_rows.sort(key=lambda item: item[0], reverse=True)
        _, weight_row = weight_rows[0]
        metrics["weight_kg"] = safe_float(weight_row.get("weight"))
        if metrics.get("weight_kg"):
            metrics["weight_lbs"] = metrics["weight_kg"] * KG_TO_LBS
        metrics["body_fat"] = safe_float(weight_row.get("body_fat"))

    path = find_csv(export_dir, "respiratory_rate")
    rows = read_csv(path)

    def rr_date(row):
        dt = parse_dt(row.get("start_time", ""))
        return dt.date() if dt else None

    rr_row = most_recent_before(rows, rr_date, target_date, lookback_days=7)
    if rr_row:
        avg = safe_float(rr_row.get("average"))
        if avg:
            metrics["resp_rate"] = avg

    return metrics


def score_bar(score, width=10):
    if score is None:
        return "—"
    pct = max(0, min(100, int(score)))
    filled = round(pct / 100 * width)
    return f"`{'█' * filled}{'░' * (width - filled)}` {pct}"


def steps_bar(steps, goal=10000, width=10):
    pct = min(int(steps / goal * 100), 100)
    filled = round(pct / 100 * width)
    dot = "🔴" if pct < 40 else "🟡" if pct < 70 else "🟢"
    return f"`{'█' * filled}{'░' * (width - filled)}` {pct}% {dot}"


def health_callout_type(score, high=75, low=50):
    if score is None:
        return "note"
    return "success" if score >= high else "warning" if score >= low else "danger"


def build_health_block(metrics, target_date, step_goal=10000):
    lines = []
    day_str = target_date.strftime("%A, %B") + " " + str(target_date.day)
    lines.append(f"*{day_str} — via BlunderBus*")
    lines.append("")

    if metrics.get("sleep_score") is not None:
        score = metrics["sleep_score"]
        duration = mins_to_hm(metrics.get("sleep_duration"))
        start = metrics["sleep_start"].strftime("%I:%M %p").lstrip("0") if metrics.get("sleep_start") else "—"
        end = metrics["sleep_end"].strftime("%I:%M %p").lstrip("0") if metrics.get("sleep_end") else "—"
        mental = f"{metrics['mental_rec']:.0f}" if metrics.get("mental_rec") else "—"
        physical = f"{metrics['physical_rec']:.0f}" if metrics.get("physical_rec") else "—"
        callout = health_callout_type(score)
        bar = score_bar(score)
        date_note = f" · {metrics['sleep_date_note']}" if metrics.get("sleep_date_note") else ""
        lines += [
            f"> [!{callout}] 😴 Sleep — {score}/100{date_note}",
            f"> {bar}  ·  **{duration}**  ·  {start} → {end}",
            f"> Mental recovery: **{mental}** · Physical recovery: **{physical}**",
            "",
        ]
    elif metrics.get("sleep_duration"):
        duration = mins_to_hm(metrics.get("sleep_duration"))
        lines += [
            f"> [!note] 😴 Sleep — {duration}",
            f"> Daily summary from ClickHouse health rollup",
            "",
        ]

    steps = metrics.get("steps", 0)
    if steps:
        dist = f"{metrics.get('steps_dist_km', 0):.1f} km"
        calories = f"{metrics.get('steps_cal', 0):.0f} kcal"
        callout = "success" if steps >= step_goal else "warning" if steps >= step_goal * 0.5 else "danger"
        bar = steps_bar(steps, step_goal)
        lines += [
            f"> [!{callout}] 👟 Steps — {steps:,} / {step_goal:,}",
            f"> {bar}  ·  **{dist}**  ·  {calories}",
            "",
        ]

    if metrics.get("avg_bpm"):
        bpm = metrics["avg_bpm"]
        min_bpm = metrics.get("min_bpm")
        max_bpm = metrics.get("max_bpm")
        extra = ""
        if min_bpm is not None and max_bpm is not None:
            extra = f"  ·  range {min_bpm:.1f}-{max_bpm:.1f}"
        lines += [
            f"> [!note]- ❤️ Heart Rate — {bpm:.1f} bpm",
            f"> Daily average from ClickHouse health rollup{extra}",
            "",
        ]

    if metrics.get("vitality") is not None:
        vitality = metrics["vitality"]
        activity = f"{metrics['vitality_activity']:.0f}" if metrics.get("vitality_activity") else "—"
        sleep_score = f"{metrics['vitality_sleep']:.0f}" if metrics.get("vitality_sleep") else "—"
        callout = health_callout_type(vitality)
        bar = score_bar(vitality)
        lines += [
            f"> [!{callout}] ⚡ Vitality — {vitality:.0f}/100",
            f"> {bar}  ·  Activity: **{activity}** · Sleep sub-score: **{sleep_score}**",
            "",
        ]

    if metrics.get("weight_lbs"):
        lbs = f"{metrics['weight_lbs']:.1f}"
        kg = f"{metrics['weight_kg']:.1f}"
        fat = f"  ·  Body fat: **{metrics['body_fat']:.1f}%**" if metrics.get("body_fat") else ""
        lines += [
            f"> [!note] ⚖️ Body — {lbs} lbs",
            f"> {kg} kg{fat}",
            "",
        ]

    if metrics.get("resp_rate"):
        rr = metrics["resp_rate"]
        callout = "success" if 12 <= rr <= 20 else "warning"
        status = "Normal" if 12 <= rr <= 20 else "Outside range"
        lines += [
            f"> [!{callout}]- 🫁 Respiratory Rate — {rr:.1f} br/min",
            f"> Sleep-time average  ·  {status} (normal: 12-20 br/min)",
            "",
        ]

    return "\n".join(lines).rstrip() + "\n"


def snapshot_to_metrics(snapshot):
    metrics = {}
    activity = snapshot.get("activity", {})
    sleep = snapshot.get("sleep", {})
    heart_rate = snapshot.get("heart_rate", {})

    if activity:
        metrics["steps"] = int(activity.get("steps") or 0)
        metrics["steps_dist_km"] = float(activity.get("distance_m") or 0) / 1000
        metrics["steps_cal"] = float(activity.get("calories_kcal") or 0)

    if sleep:
        duration_min = sleep.get("duration_min")
        if duration_min:
            metrics["sleep_duration"] = int(round(float(duration_min)))

    if heart_rate:
        avg_bpm = heart_rate.get("avg_bpm")
        min_bpm = heart_rate.get("min_bpm")
        max_bpm = heart_rate.get("max_bpm")
        if avg_bpm is not None:
            metrics["avg_bpm"] = float(avg_bpm)
        if min_bpm is not None:
            metrics["min_bpm"] = float(min_bpm)
        if max_bpm is not None:
            metrics["max_bpm"] = float(max_bpm)

    return metrics


def load_metrics(target_date, export_dir=None):
    if export_dir and export_dir.exists():
        metrics = get_metrics_for_date(export_dir, target_date)
        if metrics:
            return metrics, "samsung_export"

    snapshot = get_health_snapshot(target_date)
    metrics = snapshot_to_metrics(snapshot)
    if metrics:
        return metrics, "clickhouse_rollup"

    return {}, None


def main():
    parser = argparse.ArgumentParser(description="Push a health summary into the daily note")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--export-path", default=None, help="Samsung Health export directory")
    parser.add_argument("--dry-run", action="store_true", help="Print the markdown block, do not write it")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing health block")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    export_dir = Path(args.export_path) if args.export_path else find_export_dir()
    metrics, metrics_source = load_metrics(target_date, export_dir)

    if not metrics:
        sys.exit(
            "No health data found. Supply a Samsung Health export or populate the ClickHouse health rollup first."
        )

    block = build_health_block(metrics, target_date)
    if args.dry_run:
        print(block)
        return

    try:
        note_body = NOTE_STORE.read_daily(target_date)
    except FileNotFoundError:
        sys.exit(f"Could not find the daily note for {target_date.isoformat()}.")
    except NoteStoreError as exc:
        sys.exit(f"Could not read daily note via {NOTE_STORE.backend_name}: {exc}")

    placeholders = {
        "*pending — run `python scripts/obsidian-health-push.py` to populate*",
        "*pending — BlunderBus will populate*",
        "*pending - BlunderBus will populate*",
    }
    if "## Health" in note_body and not any(token in note_body for token in placeholders) and not args.force:
        print(f"Health data already present in {target_date} daily note — skipping (use --force to overwrite).")
        return

    updated = upsert_section(note_body, "## Health", block, anchor="## Infrastructure")
    try:
        NOTE_STORE.write_daily(target_date, updated)
    except NoteStoreError as exc:
        sys.exit(f"Could not write daily note via {NOTE_STORE.backend_name}: {exc}")

    if not SKIP_EVENT_LOG:
        try:
            log_life_event(
                domain="health",
                event_type="daily_note",
                source="obsidian_health_push",
                summary=f"Wrote health block for {target_date.isoformat()}",
                detail={
                    "date": target_date.isoformat(),
                    "note_backend": NOTE_STORE.backend_name,
                    "metrics_source": metrics_source,
                    "metric_keys": sorted(metrics.keys()),
                },
                tags=["health", "daily-note", metrics_source or "unknown"],
            )
        except Exception as exc:
            print(f"Warning: could not log health note event: {exc}", file=sys.stderr)
    print(
        f"Health block injected into {target_date} daily note via "
        f"{NOTE_STORE.backend_name} using {metrics_source}."
    )


if __name__ == "__main__":
    main()
