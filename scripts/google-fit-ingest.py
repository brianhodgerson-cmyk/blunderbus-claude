#!/usr/bin/env python3
"""
Google Fit → InfluxDB ingestion script for BlunderBus.
Pulls all health metrics from Google Fit REST API and writes to InfluxDB.

Sources aggregated by Google Fit:
  - Samsung Health (steps, sleep, HR, weight, SpO2, resp rate)
  - Health Connect (all of the above + Omron BP, scale data)
  - Google Fit native (activity, calories)

Usage:
  python google-fit-ingest.py [--days N] [--dry-run]

Env vars (load from vault before running):
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
  INFLUXDB_TOKEN
  INFLUXDB_URL     (default: http://192.168.50.202:8086)
  INFLUXDB_ORG     (default: blunderbus)
  INFLUXDB_BUCKET  (default: samsung_health)
"""

import io, json, os, sys, time, urllib.parse, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from blunderbus_data import log_life_event, upsert_daily_activity, upsert_heart_rate_summary, upsert_sleep_summary

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── Config ──────────────────────────────────────────────────────────────────

TOKEN_URL      = "https://oauth2.googleapis.com/token"
FIT_AGGREGATE  = "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"
FIT_SESSIONS   = "https://www.googleapis.com/fitness/v1/users/me/sessions"

INFLUXDB_URL    = os.environ.get("INFLUXDB_URL",    "http://192.168.50.202:8086")
INFLUXDB_ORG    = os.environ.get("INFLUXDB_ORG",    "blunderbus")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "samsung_health")

# ─── OAuth2 ──────────────────────────────────────────────────────────────────

def get_access_token(client_id, client_secret, refresh_token):
    data = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("access_token")
    except Exception as e:
        print(f"Token refresh failed: {e}")
        return None


# ─── Google Fit API ──────────────────────────────────────────────────────────

def fit_aggregate(access_token, data_type_name, start_ms, end_ms, bucket_ms=86400000):
    """Query Google Fit aggregate API for a single data type."""
    body = json.dumps({
        "aggregateBy": [{"dataTypeName": data_type_name}],
        "bucketByTime": {"durationMillis": bucket_ms},
        "startTimeMillis": start_ms,
        "endTimeMillis":   end_ms,
    }).encode()
    req = urllib.request.Request(
        FIT_AGGREGATE,
        data=body,
        headers={
            "Authorization":  f"Bearer {access_token}",
            "Content-Type":   "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        if e.code == 403:
            print(f"  403 on {data_type_name} — scope not granted or data type unavailable")
        else:
            print(f"  HTTP {e.code} on {data_type_name}: {err[:120]}")
        return None
    except Exception as e:
        print(f"  Error on {data_type_name}: {e}")
        return None


def fit_sessions(access_token, start_ms, end_ms):
    """Get sleep/activity sessions from Google Fit."""
    params = urllib.parse.urlencode({
        "startTime": datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endTime":   datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "activityType": 72,  # 72 = sleep
    })
    req = urllib.request.Request(
        f"{FIT_SESSIONS}?{params}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  Sessions error: {e}")
        return None


# ─── InfluxDB write ──────────────────────────────────────────────────────────

def lp(measurement, fields, ts_ns, tags=None):
    """Build InfluxDB line protocol string."""
    tag_str = ""
    if tags:
        tag_str = "," + ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
    field_parts = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, float):
            field_parts.append(f"{k}={v}")
        elif isinstance(v, int):
            field_parts.append(f"{k}={v}i")
        else:
            field_parts.append(f'{k}="{v}"')
    if not field_parts:
        return None
    return f"{measurement}{tag_str} {','.join(field_parts)} {ts_ns}"


def influx_write(lines, token):
    body = "\n".join(l for l in lines if l).encode("utf-8")
    if not body:
        return 204, None
    url = f"{INFLUXDB_URL}/api/v2/write?org={INFLUXDB_ORG}&bucket={INFLUXDB_BUCKET}&precision=ns"
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Token {token}", "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)


# ─── Parsers ─────────────────────────────────────────────────────────────────

def ms_to_ns(ms):
    return int(ms) * 1_000_000


def extract_fp(point, idx, kind="fpVal"):
    """Extract a field value from a Google Fit data point."""
    vals = point.get("value", [])
    if idx < len(vals):
        return vals[idx].get(kind)
    return None


def parse_steps(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        ts_ms = int(bucket.get("startTimeMillis", 0))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                steps = extract_fp(pt, 0, "intVal")
                if steps and steps > 0:
                    lines.append(lp("health_steps", {"steps": int(steps)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_heart_rate(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        ts_ms = int(bucket.get("startTimeMillis", 0))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                avg = extract_fp(pt, 0, "fpVal")  # average BPM
                if avg and avg > 0:
                    lines.append(lp("health_heart_rate", {"bpm_avg": float(avg)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_weight(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                ts_ms = int(pt.get("startTimeNanos", 0)) // 1_000_000
                if not ts_ms:
                    ts_ms = int(bucket.get("startTimeMillis", 0))
                weight_kg = extract_fp(pt, 0, "fpVal")
                if weight_kg and weight_kg > 20:
                    lines.append(lp("health_weight", {
                        "weight_kg":  float(weight_kg),
                        "weight_lbs": float(weight_kg * 2.20462),
                    }, ms_to_ns(ts_ms), tags))
    return lines


def parse_body_fat(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                ts_ms = int(pt.get("startTimeNanos", 0)) // 1_000_000
                if not ts_ms:
                    ts_ms = int(bucket.get("startTimeMillis", 0))
                fat = extract_fp(pt, 0, "fpVal")
                if fat and 0 < fat < 100:
                    lines.append(lp("health_weight", {"body_fat_pct": float(fat)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_blood_pressure(data, tags):
    """
    Blood pressure from Omron cuff via Health Connect → Google Fit.
    Fields: systolic (fp[0]), diastolic (fp[1]), pulse may be in fp[2] or separate.
    """
    lines = []
    for bucket in data.get("bucket", []):
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                ts_ms = int(pt.get("startTimeNanos", 0)) // 1_000_000
                if not ts_ms:
                    ts_ms = int(bucket.get("startTimeMillis", 0))
                systolic  = extract_fp(pt, 0, "fpVal")
                diastolic = extract_fp(pt, 1, "fpVal")
                if systolic and diastolic and systolic > 0:
                    fields = {
                        "systolic":  float(systolic),
                        "diastolic": float(diastolic),
                    }
                    # Some Omron records include pulse as 3rd field
                    pulse = extract_fp(pt, 2, "fpVal")
                    if pulse and pulse > 0:
                        fields["pulse"] = float(pulse)
                    lines.append(lp("health_blood_pressure", fields, ms_to_ns(ts_ms), tags))
    return lines


def parse_spo2(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        ts_ms = int(bucket.get("startTimeMillis", 0))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                spo2 = extract_fp(pt, 0, "fpVal")
                if spo2 and spo2 > 0:
                    lines.append(lp("health_spo2", {"pct": float(spo2)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_calories(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        ts_ms = int(bucket.get("startTimeMillis", 0))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                cals = extract_fp(pt, 0, "fpVal")
                if cals and cals > 0:
                    lines.append(lp("health_calories", {"kcal": float(cals)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_distance(data, tags):
    lines = []
    for bucket in data.get("bucket", []):
        ts_ms = int(bucket.get("startTimeMillis", 0))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                dist_m = extract_fp(pt, 0, "fpVal")
                if dist_m and dist_m > 0:
                    lines.append(lp("health_steps", {"distance_m": float(dist_m)}, ms_to_ns(ts_ms), tags))
    return lines


def parse_sleep_sessions(session_data, tags):
    """Parse sleep sessions — Google Fit stores sleep as activity segments."""
    lines = []
    for session in session_data.get("session", []):
        start_ms = int(session.get("startTimeMillis", 0))
        end_ms   = int(session.get("endTimeMillis", 0))
        if not start_ms or not end_ms:
            continue
        duration_min = (end_ms - start_ms) / 60000
        if duration_min < 30:  # skip naps < 30 min
            continue
        name = session.get("name", "")
        lines.append(lp("health_sleep", {
            "duration_min": float(duration_min),
            "source_name":  f'"{name}"' if name else '"unknown"',
        }, ms_to_ns(start_ms), tags))
    return lines


def local_day(ms):
    return datetime.fromtimestamp(ms / 1000).date()


def summarize_steps(data, activity_by_day):
    for bucket in data.get("bucket", []):
        day = local_day(int(bucket.get("startTimeMillis", 0)))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                steps = extract_fp(pt, 0, "intVal")
                if steps and steps > 0:
                    activity_by_day[day]["steps"] += int(steps)


def summarize_distance(data, activity_by_day):
    for bucket in data.get("bucket", []):
        day = local_day(int(bucket.get("startTimeMillis", 0)))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                dist_m = extract_fp(pt, 0, "fpVal")
                if dist_m and dist_m > 0:
                    activity_by_day[day]["distance_m"] += float(dist_m)


def summarize_calories(data, activity_by_day):
    for bucket in data.get("bucket", []):
        day = local_day(int(bucket.get("startTimeMillis", 0)))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                cals = extract_fp(pt, 0, "fpVal")
                if cals and cals > 0:
                    activity_by_day[day]["calories_kcal"] += float(cals)


def summarize_heart_rate(data, heart_rate_by_day):
    for bucket in data.get("bucket", []):
        day = local_day(int(bucket.get("startTimeMillis", 0)))
        for ds in bucket.get("dataset", []):
            for pt in ds.get("point", []):
                bpm = extract_fp(pt, 0, "fpVal")
                if bpm and bpm > 0:
                    heart_rate_by_day[day]["values"].append(float(bpm))


def summarize_sleep(session_data, sleep_by_day):
    for session in session_data.get("session", []):
        start_ms = int(session.get("startTimeMillis", 0))
        end_ms = int(session.get("endTimeMillis", 0))
        if not start_ms or not end_ms:
            continue
        duration_min = (end_ms - start_ms) / 60000
        if duration_min < 30:
            continue
        day = local_day(start_ms)
        sleep_by_day[day]["duration_min"] += float(duration_min)
        sleep_by_day[day]["session_count"] += 1
        name = session.get("name", "")
        if name:
            sleep_by_day[day]["source_names"].add(name)


def persist_health_summaries(activity_by_day, heart_rate_by_day, sleep_by_day, dry_run=False):
    days = sorted(set(activity_by_day) | set(heart_rate_by_day) | set(sleep_by_day))
    if not days:
        return

    for day in days:
        activity = activity_by_day.get(day, {})
        heart = heart_rate_by_day.get(day, {})
        sleep = sleep_by_day.get(day, {})

        if dry_run:
            print(f"  [dry-run] summary {day}: steps={activity.get('steps', 0)} sleep={sleep.get('duration_min', 0):.0f}m")
            continue

        upsert_daily_activity(
            day,
            source="googlefit",
            steps=activity.get("steps", 0),
            distance_m=activity.get("distance_m", 0.0),
            calories_kcal=activity.get("calories_kcal", 0.0),
        )

        values = heart.get("values", [])
        if values:
            upsert_heart_rate_summary(
                day,
                source="googlefit",
                avg_bpm=sum(values) / len(values),
                min_bpm=min(values),
                max_bpm=max(values),
                sample_count=len(values),
            )

        if sleep:
            upsert_sleep_summary(
                day,
                source="googlefit",
                duration_min=sleep.get("duration_min", 0.0),
                session_count=sleep.get("session_count", 0),
                source_names=sorted(sleep.get("source_names", set())),
            )

        summary_parts = []
        if activity.get("steps"):
            summary_parts.append(f"{activity['steps']:,} steps")
        if sleep.get("duration_min"):
            summary_parts.append(f"{sleep['duration_min'] / 60:.1f}h sleep")
        if values:
            summary_parts.append(f"{sum(values) / len(values):.1f} avg HR")

        log_life_event(
            domain="health",
            event_type="daily_summary",
            source="google_fit_ingest",
            summary=f"Health summary materialized for {day.isoformat()}",
            detail={"day": day.isoformat(), "summary": ", ".join(summary_parts)},
            tags=["health", "googlefit", "daily-summary"],
        )


# ─── Main ingestion ──────────────────────────────────────────────────────────

DATA_TYPES = [
    ("com.google.step_count.delta",       "steps",          parse_steps),
    ("com.google.heart_rate.bpm",         "heart_rate",     parse_heart_rate),
    ("com.google.weight",                 "weight",         parse_weight),
    ("com.google.body.fat.percentage",    "body_fat",       parse_body_fat),
    ("com.google.blood_pressure",         "blood_pressure", parse_blood_pressure),
    ("com.google.oxygen_saturation",      "spo2",           parse_spo2),
    ("com.google.calories.expended",      "calories",       parse_calories),
    ("com.google.distance.delta",         "distance",       parse_distance),
]


def ingest(access_token, influx_token, days=1, dry_run=False):
    now_ms    = int(time.time() * 1000)
    start_ms  = now_ms - (days * 86400 * 1000)
    tags      = {"source": "googlefit"}
    activity_by_day = defaultdict(lambda: {"steps": 0, "distance_m": 0.0, "calories_kcal": 0.0})
    heart_rate_by_day = defaultdict(lambda: {"values": []})
    sleep_by_day = defaultdict(lambda: {"duration_min": 0.0, "session_count": 0, "source_names": set()})

    total_written = 0
    total_lines   = []

    print(f"\nFetching Google Fit data ({days} day{'s' if days != 1 else ''})...")
    print(f"  Range: {datetime.fromtimestamp(start_ms/1000).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(now_ms/1000).strftime('%Y-%m-%d')}\n")

    for data_type, label, parser in DATA_TYPES:
        data = fit_aggregate(access_token, data_type, start_ms, now_ms)
        if data is None:
            print(f"  ⚠️  {label:<20} — skipped (no data or error)")
            continue

        if label == "steps":
            summarize_steps(data, activity_by_day)
        elif label == "distance":
            summarize_distance(data, activity_by_day)
        elif label == "calories":
            summarize_calories(data, activity_by_day)
        elif label == "heart_rate":
            summarize_heart_rate(data, heart_rate_by_day)

        lines = parser(data, tags)
        lines = [l for l in lines if l]

        if not lines:
            print(f"  ⬜  {label:<20} — no data in range")
            continue

        if dry_run:
            print(f"  📋  {label:<20} — {len(lines)} points (dry run)")
            for l in lines[:2]:
                print(f"      {l}")
        else:
            code, err = influx_write(lines, influx_token)
            if err:
                print(f"  ❌  {label:<20} — write failed: HTTP {code} {err[:80]}")
            else:
                print(f"  ✅  {label:<20} — {len(lines)} points written")
                total_written += len(lines)

        total_lines.extend(lines)

    # Sleep sessions (separate endpoint)
    print()
    session_data = fit_sessions(access_token, start_ms, now_ms)
    if session_data:
        sleep_lines = parse_sleep_sessions(session_data, tags)
        sleep_lines = [l for l in sleep_lines if l]
        summarize_sleep(session_data, sleep_by_day)
        if sleep_lines:
            if dry_run:
                print(f"  📋  {'sleep':<20} — {len(sleep_lines)} sessions (dry run)")
            else:
                code, err = influx_write(sleep_lines, influx_token)
                if err:
                    print(f"  ❌  {'sleep':<20} — write failed: HTTP {code}")
                else:
                    print(f"  ✅  {'sleep':<20} — {len(sleep_lines)} sessions written")
                    total_written += len(sleep_lines)
        else:
            print(f"  ⬜  {'sleep':<20} — no sessions in range")

    if not dry_run:
        persist_health_summaries(activity_by_day, heart_rate_by_day, sleep_by_day, dry_run=False)
        print(f"\n  Total points written: {total_written}")
        print(f"  Grafana: http://192.168.50.202:3000/d/samsung-health-brian")
    else:
        persist_health_summaries(activity_by_day, heart_rate_by_day, sleep_by_day, dry_run=True)
        print(f"\n  [Dry run — {len(total_lines)} total points, nothing written]")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Google Fit → InfluxDB ingestion")
    p.add_argument("--days",    type=int,          default=1,     help="Days of history to pull (default: 1)")
    p.add_argument("--dry-run", action="store_true",               help="Print what would be written, don't write")
    args = p.parse_args()

    client_id      = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret  = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token  = os.environ.get("GOOGLE_REFRESH_TOKEN")
    influx_token   = os.environ.get("INFLUXDB_TOKEN")

    missing = [k for k, v in {
        "GOOGLE_CLIENT_ID":     client_id,
        "GOOGLE_CLIENT_SECRET": client_secret,
        "GOOGLE_REFRESH_TOKEN": refresh_token,
        "INFLUXDB_TOKEN":       influx_token,
    }.items() if not v]

    if missing and not args.dry_run:
        sys.exit(f"Missing env vars: {', '.join(missing)}\nLoad from vault first.")
    elif missing and args.dry_run:
        print(f"[dry-run] Missing: {missing} — using placeholder token")
        client_id = client_id or "test"
        client_secret = client_secret or "test"
        refresh_token = refresh_token or "test"
        influx_token = influx_token or "test"

    print("Getting access token...")
    access_token = get_access_token(client_id, client_secret, refresh_token)
    if not access_token:
        sys.exit("Failed to get access token. Check credentials.")
    print("Access token obtained.")

    ingest(access_token, influx_token, days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
