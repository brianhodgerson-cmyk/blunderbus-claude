#!/usr/bin/env python3
"""Shared ClickHouse helpers for BlunderBus life log and health summaries."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

from runtime import env_first

try:
    from clickhouse_driver import Client as CHClient
except ImportError:  # pragma: no cover - handled gracefully at runtime
    CHClient = None


BLUNDERBUS_DB = env_first("BLUNDERBUS_CLICKHOUSE_DATABASE", "BLUNDERBUS_DB", default="blunderbus") or "blunderbus"
CH_HOST = env_first("CLICKHOUSE_HOST", default="192.168.50.106") or "192.168.50.106"
CH_PORT = int(env_first("CLICKHOUSE_PORT", default="9000") or "9000")
CH_USER = env_first("CLICKHOUSE_USER", default="clickhouse") or "clickhouse"
CH_PASS = env_first("CLICKHOUSE_PASSWORD", "CLICKHOUSE_PASS", default="clickhouse") or "clickhouse"

INSERT_SETTINGS = {
    "async_insert": 1,
    "wait_for_async_insert": 1,
    "async_insert_busy_timeout_ms": 1000,
}

_SCHEMA_READY = False


def clickhouse_available() -> bool:
    return CHClient is not None


def _client(database: str = "default"):
    if CHClient is None:
        return None
    return CHClient(
        host=CH_HOST,
        port=CH_PORT,
        user=CH_USER,
        password=CH_PASS,
        database=database,
        connect_timeout=5,
        send_receive_timeout=15,
    )


def _rows_to_dicts(rows, cols):
    names = [col[0] for col in cols]
    return [dict(zip(names, row)) for row in rows]


def ensure_blunderbus_schema() -> bool:
    global _SCHEMA_READY

    if _SCHEMA_READY or CHClient is None:
        return CHClient is not None

    client = _client("default")
    if client is None:
        return False

    try:
        client.execute(f"CREATE DATABASE IF NOT EXISTS {BLUNDERBUS_DB}")
        client.execute(
            f"""
CREATE TABLE IF NOT EXISTS {BLUNDERBUS_DB}.life_events (
    event_time DateTime,
    domain LowCardinality(String),
    event_type LowCardinality(String),
    source LowCardinality(String),
    summary String,
    detail String,
    tags Array(String)
) ENGINE = MergeTree()
ORDER BY (event_time, domain, source)
"""
        )
        client.execute(
            f"""
CREATE TABLE IF NOT EXISTS {BLUNDERBUS_DB}.daily_activity (
    day Date,
    source LowCardinality(String),
    steps UInt32,
    distance_m Float64,
    calories_kcal Float64,
    updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (day, source)
"""
        )
        client.execute(
            f"""
CREATE TABLE IF NOT EXISTS {BLUNDERBUS_DB}.sleep (
    day Date,
    source LowCardinality(String),
    duration_min Float64,
    session_count UInt32,
    source_names Array(String),
    updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (day, source)
"""
        )
        client.execute(
            f"""
CREATE TABLE IF NOT EXISTS {BLUNDERBUS_DB}.heart_rate (
    day Date,
    source LowCardinality(String),
    avg_bpm Float64,
    min_bpm Float64,
    max_bpm Float64,
    sample_count UInt32,
    updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (day, source)
"""
        )
    except Exception:
        return False
    _SCHEMA_READY = True
    return True


def log_life_event(
    domain: str,
    event_type: str,
    source: str,
    summary: str,
    detail: str | dict | list | None = None,
    tags: list[str] | None = None,
    event_time: datetime | None = None,
) -> bool:
    if not ensure_blunderbus_schema():
        return False

    detail_text = ""
    if isinstance(detail, (dict, list)):
        detail_text = json.dumps(detail, ensure_ascii=True)
    elif detail is not None:
        detail_text = str(detail)

    payload = [
        (
            event_time or datetime.now(),
            domain,
            event_type,
            source,
            summary,
            detail_text,
            tags or [],
        )
    ]
    client = _client(BLUNDERBUS_DB)
    client.execute(
        f"INSERT INTO {BLUNDERBUS_DB}.life_events (event_time, domain, event_type, source, summary, detail, tags) VALUES",
        payload,
        settings=INSERT_SETTINGS,
    )
    return True


def get_life_events_for_day(target_day: date, limit: int = 10) -> list[dict]:
    if not ensure_blunderbus_schema():
        return []

    start = datetime.combine(target_day, time.min)
    end = start + timedelta(days=1)
    client = _client(BLUNDERBUS_DB)
    rows, cols = client.execute(
        f"""
SELECT event_time, domain, event_type, source, summary, detail, tags
FROM {BLUNDERBUS_DB}.life_events
WHERE event_time >= %(start)s AND event_time < %(end)s
ORDER BY event_time DESC
LIMIT %(limit)s
""",
        {"start": start, "end": end, "limit": limit},
        with_column_types=True,
    )
    return _rows_to_dicts(rows, cols)


def upsert_daily_activity(
    target_day: date,
    *,
    source: str,
    steps: int,
    distance_m: float,
    calories_kcal: float,
) -> bool:
    if not ensure_blunderbus_schema():
        return False
    client = _client(BLUNDERBUS_DB)
    client.execute(
        f"INSERT INTO {BLUNDERBUS_DB}.daily_activity (day, source, steps, distance_m, calories_kcal) VALUES",
        [(target_day, source, int(steps), float(distance_m), float(calories_kcal))],
        settings=INSERT_SETTINGS,
    )
    return True


def upsert_sleep_summary(
    target_day: date,
    *,
    source: str,
    duration_min: float,
    session_count: int,
    source_names: list[str] | None = None,
) -> bool:
    if not ensure_blunderbus_schema():
        return False
    client = _client(BLUNDERBUS_DB)
    client.execute(
        f"INSERT INTO {BLUNDERBUS_DB}.sleep (day, source, duration_min, session_count, source_names) VALUES",
        [(target_day, source, float(duration_min), int(session_count), source_names or [])],
        settings=INSERT_SETTINGS,
    )
    return True


def upsert_heart_rate_summary(
    target_day: date,
    *,
    source: str,
    avg_bpm: float,
    min_bpm: float,
    max_bpm: float,
    sample_count: int,
) -> bool:
    if not ensure_blunderbus_schema():
        return False
    client = _client(BLUNDERBUS_DB)
    client.execute(
        f"INSERT INTO {BLUNDERBUS_DB}.heart_rate (day, source, avg_bpm, min_bpm, max_bpm, sample_count) VALUES",
        [(target_day, source, float(avg_bpm), float(min_bpm), float(max_bpm), int(sample_count))],
        settings=INSERT_SETTINGS,
    )
    return True


def get_health_snapshot(target_day: date, source: str = "googlefit") -> dict:
    if not ensure_blunderbus_schema():
        return {}

    client = _client(BLUNDERBUS_DB)
    snapshot: dict[str, dict] = {}

    for table, key in (
        ("daily_activity", "activity"),
        ("sleep", "sleep"),
        ("heart_rate", "heart_rate"),
    ):
        rows, cols = client.execute(
            f"SELECT * FROM {BLUNDERBUS_DB}.{table} FINAL WHERE day = %(day)s AND source = %(source)s LIMIT 1",
            {"day": target_day, "source": source},
            with_column_types=True,
        )
        if rows:
            snapshot[key] = _rows_to_dicts(rows, cols)[0]

    return snapshot


def format_health_summary(snapshot: dict) -> str:
    if not snapshot:
        return "No health summary available."

    activity = snapshot.get("activity", {})
    sleep = snapshot.get("sleep", {})
    hr = snapshot.get("heart_rate", {})

    parts = []
    steps = activity.get("steps")
    if steps:
        parts.append(f"{steps:,} steps")

    duration_min = sleep.get("duration_min")
    if duration_min:
        hours = duration_min / 60
        parts.append(f"{hours:.1f}h sleep")

    avg_bpm = hr.get("avg_bpm")
    if avg_bpm:
        parts.append(f"{avg_bpm:.1f} avg HR")

    return ", ".join(parts) if parts else "No health summary available."
