#!/usr/bin/env python3
"""
Build and push the Samsung Health Grafana dashboard to Banner.
Run after samsung-health-parse.py --push-influx has populated data.

Usage:
  python build-health-dashboard.py

Requires env vars:
  GRAFANA_URL    (default: http://192.168.50.202:3000)
  GRAFANA_TOKEN  Grafana API key with dashboard write permission
  GRAFANA_API_KEY Legacy alias still accepted
"""

import json, os, sys, urllib.request, urllib.error, io

from runtime import configure_utf8_stdio, env_first

configure_utf8_stdio()

GRAFANA_URL   = os.environ.get("GRAFANA_URL",   "http://192.168.50.202:3000")
DATASOURCE_UID = "bfdkkksgqpxj4f"
BUCKET        = "samsung_health"
ORG           = "blunderbus"

# ─── Colour palette ─────────────────────────────────────────────────────────
C_SLEEP    = "#3B82F6"   # blue
C_STEPS    = "#10B981"   # emerald
C_VITALITY = "#F59E0B"   # amber
C_WEIGHT   = "#8B5CF6"   # violet
C_FATPCT   = "#EC4899"   # pink
C_RESP     = "#06B6D4"   # cyan
C_MENTAL   = "#6366F1"   # indigo
C_PHYSICAL = "#14B8A6"   # teal
C_ACTIVITY = "#22C55E"   # green
C_HRV      = "#A78BFA"   # purple-300
C_HR       = "#F97316"   # orange

# ─── Query helpers ───────────────────────────────────────────────────────────

def flux(measurement, field, extra="", range_start="v.timeRangeStart", range_stop="v.timeRangeStop"):
    """Build a single-field Flux query. Use range_start/range_stop to override the time range."""
    if range_start.startswith("v."):
        r = f"start: {range_start}, stop: {range_stop}"
    else:
        r = f"start: {range_start}"
    q = f'from(bucket: "{BUCKET}")\n  |> range({r})\n  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n  |> filter(fn: (r) => r["_field"] == "{field}")'
    if extra:
        q += "\n  " + extra.strip()
    return q

def flux_multi(measurement, fields, extra="", range_start="v.timeRangeStart", range_stop="v.timeRangeStop"):
    """Build a multi-field Flux query."""
    if range_start.startswith("v."):
        r = f"start: {range_start}, stop: {range_stop}"
    else:
        r = f"start: {range_start}"
    cond = " or ".join(f'r["_field"] == "{f}"' for f in fields)
    q = f'from(bucket: "{BUCKET}")\n  |> range({r})\n  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n  |> filter(fn: (r) => {cond})'
    if extra:
        q += "\n  " + extra.strip()
    return q

def target(query, ref="A", alias=None):
    t = {"datasource": {"type": "influxdb", "uid": DATASOURCE_UID}, "query": query, "refId": ref, "hide": False}
    if alias:
        t["alias"] = alias
    return t

# ─── Field config helpers ────────────────────────────────────────────────────

def thresholds(steps):
    """steps = list of (value_or_None, color_name_or_hex)"""
    return {
        "mode": "absolute",
        "steps": [{"color": c, "value": v} for v, c in steps]
    }

def score_thresholds():
    return thresholds([(None, "red"), (60, "#EAB308"), (80, "green")])

def fixed_color(hex_color):
    return {"mode": "fixed", "fixedColor": hex_color}

def ts_custom(line_width=2, fill=12, gradient="opacity", points="auto", point_size=4, fill_below=None, span_nulls=False):
    c = {
        "lineWidth": line_width,
        "fillOpacity": fill,
        "gradientMode": gradient,
        "showPoints": points,
        "pointSize": point_size,
        "spanNulls": span_nulls,
        "lineInterpolation": "smooth",
    }
    if fill_below:
        c["fillBelowTo"] = fill_below
    return c

# ─── Panel builders ──────────────────────────────────────────────────────────

_pid = 0
def nxt():
    global _pid
    _pid += 1
    return _pid

def stat_panel(title, query, unit, thresholds_cfg, x, y, w=4, h=4, decimals=1, color_mode="background", graph_mode="area", suffix=""):
    return {
        "id": nxt(), "type": "stat", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "influxdb", "uid": DATASOURCE_UID},
        "targets": [target(query)],
        "options": {
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "orientation": "auto", "textMode": "auto",
            "colorMode": color_mode, "graphMode": graph_mode,
            "justifyMode": "center",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit, "decimals": decimals,
                "thresholds": thresholds_cfg,
                "color": {"mode": "thresholds"},
                "noValue": "—",
                "mappings": [],
            },
            "overrides": []
        },
    }

def row_panel(title, y, collapsed=False):
    return {
        "id": nxt(), "type": "row", "title": title,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "collapsed": collapsed, "panels": []
    }

def ts_panel(title, targets_list, x, y, w, h, unit="short", overrides=None, fill=10, grad="opacity", legend_pos="bottom", min_val=None, max_val=None, threshold_cfg=None, threshold_line=False, decimals=None):
    fc_defaults = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "custom": ts_custom(fill=fill, gradient=grad),
        "noValue": "—",
    }
    if threshold_cfg:
        fc_defaults["thresholds"] = threshold_cfg
    if threshold_line:
        fc_defaults["custom"]["thresholdsStyle"] = {"mode": "line+area"}
    if min_val is not None:
        fc_defaults["min"] = min_val
    if max_val is not None:
        fc_defaults["max"] = max_val
    if decimals is not None:
        fc_defaults["decimals"] = decimals

    return {
        "id": nxt(), "type": "timeseries", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "influxdb", "uid": DATASOURCE_UID},
        "targets": targets_list,
        "options": {
            "tooltip": {"mode": "multi", "sort": "none"},
            "legend": {"displayMode": "list", "placement": legend_pos, "showLegend": True},
        },
        "fieldConfig": {
            "defaults": fc_defaults,
            "overrides": overrides or [],
        },
    }

def bar_panel(title, targets_list, x, y, w, h, unit="short", color_hex=None, threshold_cfg=None, threshold_line=False, decimals=0):
    fc_defaults = {
        "unit": unit, "decimals": decimals,
        "color": fixed_color(color_hex) if color_hex else {"mode": "palette-classic"},
        "custom": {"lineWidth": 1, "fillOpacity": 80},
        "noValue": "—",
    }
    if threshold_cfg:
        fc_defaults["thresholds"] = threshold_cfg
    if threshold_line:
        fc_defaults["custom"]["thresholdsStyle"] = {"mode": "line"}
    return {
        "id": nxt(), "type": "barchart", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "influxdb", "uid": DATASOURCE_UID},
        "targets": targets_list,
        "options": {
            "orientation": "auto",
            "xTickLabelRotation": -30,
            "barRadius": 0.1,
            "groupWidth": 0.7,
            "barWidth": 0.75,
            "tooltip": {"mode": "multi", "sort": "none"},
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "colorByField": "value",
        },
        "fieldConfig": {
            "defaults": fc_defaults,
            "overrides": [],
        },
    }

def gauge_panel(title, query, unit, min_val, max_val, threshold_steps, x, y, w=4, h=4):
    return {
        "id": nxt(), "type": "gauge", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": {"type": "influxdb", "uid": DATASOURCE_UID},
        "targets": [target(query)],
        "options": {
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "orientation": "auto",
            "showThresholdLabels": False,
            "showThresholdMarkers": True,
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit, "min": min_val, "max": max_val,
                "thresholds": thresholds(threshold_steps),
                "color": {"mode": "thresholds"},
                "noValue": "—",
            },
            "overrides": []
        },
    }

# ─── Build dashboard ─────────────────────────────────────────────────────────

def build_dashboard():
    panels = []

    # ── Row 0: Hero stats (y=0, h=5) ─────────────────────────────────────────
    # Use dashboard time range + lastNotNull reducer — no hardcoded ranges needed.
    # Steps: aggregate to daily totals first, then Grafana picks the last non-null.
    panels.append(stat_panel(
        "Steps Today",
        flux("samsung_health_steps","steps","|> aggregateWindow(every: 1d, fn: sum, createEmpty: false)"),
        "locale",
        thresholds([(None,"red"),(7000,"#EAB308"),(10000,"green")]),
        x=0, y=0, w=4, h=5, decimals=0
    ))

    panels.append(stat_panel(
        "Sleep Score",
        flux("samsung_health_sleep","score"),
        "none", score_thresholds(),
        x=4, y=0, w=4, h=5, decimals=0
    ))

    panels.append(stat_panel(
        "Vitality Score",
        flux("samsung_health_vitality","total"),
        "none", score_thresholds(),
        x=8, y=0, w=4, h=5, decimals=0
    ))

    panels.append(stat_panel(
        "Weight (lbs)",
        flux("samsung_health_weight","weight_lbs"),
        "none",
        thresholds([(None,"blue")]),
        x=12, y=0, w=4, h=5, decimals=1, graph_mode="none"
    ))

    panels.append(stat_panel(
        "Body Fat %",
        flux("samsung_health_weight","body_fat_pct"),
        "percent",
        thresholds([(None,"green"),(20,"#EAB308"),(28,"red")]),
        x=16, y=0, w=4, h=5, decimals=1
    ))

    panels.append(stat_panel(
        "Resp Rate",
        flux("samsung_health_respiratory_rate","bpm"),
        "none",
        thresholds([(None,"red"),(12,"green"),(21,"red")]),
        x=20, y=0, w=4, h=5, decimals=1
    ))

    # ── Row 1: Sleep separator (y=5) ─────────────────────────────────────────
    panels.append(row_panel("💤  Sleep", y=5))

    # ── Sleep Score Trend + Recovery side-by-side (y=6, h=10) ────────────────
    panels.append(ts_panel(
        "Sleep Score — Nightly Trend",
        [target(flux("samsung_health_sleep","score"), "A")],
        x=0, y=6, w=16, h=10, unit="none",
        fill=20, grad="opacity",
        min_val=0, max_val=100,
        threshold_cfg=score_thresholds(),
        threshold_line=True,
        decimals=0,
        overrides=[{
            "matcher": {"id": "byName", "options": "score"},
            "properties": [
                {"id": "color", "value": fixed_color(C_SLEEP)},
                {"id": "custom.lineWidth", "value": 3},
                {"id": "custom.fillOpacity", "value": 25},
                {"id": "displayName", "value": "Sleep Score"},
            ]
        }]
    ))

    panels.append(ts_panel(
        "Recovery Scores",
        [
            target(flux("samsung_health_sleep","mental_recovery"), "A"),
            target(flux("samsung_health_sleep","physical_recovery"), "B"),
        ],
        x=16, y=6, w=8, h=10, unit="none",
        fill=15, min_val=0, max_val=100,
        decimals=0,
        overrides=[
            {"matcher": {"id": "byName", "options": "mental_recovery"},
             "properties": [{"id": "color", "value": fixed_color(C_MENTAL)}, {"id": "displayName", "value": "Mental"}]},
            {"matcher": {"id": "byName", "options": "physical_recovery"},
             "properties": [{"id": "color", "value": fixed_color(C_PHYSICAL)}, {"id": "displayName", "value": "Physical"}]},
        ]
    ))

    # Sleep Duration + Stages (y=16, h=9)
    panels.append(ts_panel(
        "Sleep Duration & Stage Scores",
        [
            target(flux("samsung_health_sleep","duration_min","|> map(fn: (r) => ({r with _value: float(v: r._value) / 60.0}))"), "A"),
            target(flux("samsung_health_sleep","deep_score"), "B"),
            target(flux("samsung_health_sleep","rem_score"), "C"),
        ],
        x=0, y=16, w=24, h=9, unit="none",
        fill=12,
        overrides=[
            {"matcher": {"id": "byName", "options": "duration_min"},
             "properties": [
                 {"id": "color", "value": fixed_color(C_SLEEP)},
                 {"id": "displayName", "value": "Duration (hrs)"},
                 {"id": "custom.lineWidth", "value": 3},
                 {"id": "custom.fillOpacity", "value": 20},
                 {"id": "unit", "value": "none"},
                 {"id": "min", "value": 0},
                 {"id": "max", "value": 14},
             ]},
            {"matcher": {"id": "byName", "options": "deep_score"},
             "properties": [
                 {"id": "color", "value": fixed_color("#1E40AF")},
                 {"id": "displayName", "value": "Deep Score"},
                 {"id": "custom.lineStyle", "value": {"dash": [8,4], "fill": "dash"}},
             ]},
            {"matcher": {"id": "byName", "options": "rem_score"},
             "properties": [
                 {"id": "color", "value": fixed_color(C_MENTAL)},
                 {"id": "displayName", "value": "REM Score"},
                 {"id": "custom.lineStyle", "value": {"dash": [8,4], "fill": "dash"}},
             ]},
        ]
    ))

    # ── Row 2: Activity separator (y=25) ─────────────────────────────────────
    panels.append(row_panel("🏃  Activity", y=25))

    # Daily Steps bar (y=26, h=10)
    step_q = flux("samsung_health_steps","steps","|> aggregateWindow(every: 1d, fn: sum, createEmpty: false)")
    panels.append({
        "id": nxt(), "type": "barchart", "title": "Daily Steps vs 10,000 Goal",
        "gridPos": {"x": 0, "y": 26, "w": 24, "h": 10},
        "datasource": {"type": "influxdb", "uid": DATASOURCE_UID},
        "targets": [target(step_q)],
        "options": {
            "orientation": "auto",
            "xTickLabelRotation": -30,
            "barRadius": 0.08,
            "groupWidth": 0.7,
            "barWidth": 0.9,
            "tooltip": {"mode": "single"},
            "legend": {"displayMode": "hidden", "showLegend": False},
        },
        "fieldConfig": {
            "defaults": {
                "unit": "locale", "decimals": 0,
                "color": {"mode": "thresholds"},
                "thresholds": thresholds([(None,"red"),(7000,"#EAB308"),(10000,"green")]),
                "custom": {
                    "lineWidth": 1, "fillOpacity": 85,
                    "thresholdsStyle": {"mode": "line"},
                },
                "noValue": "—",
            },
            "overrides": []
        },
    })

    # ── Row 3: Vitality separator (y=36) ─────────────────────────────────────
    panels.append(row_panel("⚡  Vitality Score", y=36))

    # Vitality breakdown (y=37, h=10)
    panels.append(ts_panel(
        "Vitality Score — Total & Sub-scores",
        [
            target(flux("samsung_health_vitality","total"), "A"),
            target(flux("samsung_health_vitality","activity"), "B"),
            target(flux("samsung_health_vitality","sleep"), "C"),
            target(flux("samsung_health_vitality","shr"), "D"),
            target(flux("samsung_health_vitality","shrv"), "E"),
        ],
        x=0, y=37, w=24, h=10, unit="none",
        fill=8, min_val=0, max_val=100,
        decimals=0,
        overrides=[
            {"matcher": {"id": "byName", "options": "total"},
             "properties": [
                 {"id": "color", "value": fixed_color(C_VITALITY)},
                 {"id": "displayName", "value": "Vitality Total"},
                 {"id": "custom.lineWidth", "value": 3},
                 {"id": "custom.fillOpacity", "value": 20},
             ]},
            {"matcher": {"id": "byName", "options": "activity"},
             "properties": [{"id": "color", "value": fixed_color(C_ACTIVITY)}, {"id": "displayName", "value": "Activity"}]},
            {"matcher": {"id": "byName", "options": "sleep"},
             "properties": [{"id": "color", "value": fixed_color(C_SLEEP)}, {"id": "displayName", "value": "Sleep"}]},
            {"matcher": {"id": "byName", "options": "shr"},
             "properties": [{"id": "color", "value": fixed_color(C_HR)}, {"id": "displayName", "value": "Resting HR"}]},
            {"matcher": {"id": "byName", "options": "shrv"},
             "properties": [{"id": "color", "value": fixed_color(C_HRV)}, {"id": "displayName", "value": "HRV"}]},
        ]
    ))

    # ── Row 4: Blood Pressure separator (y=47) ───────────────────────────────
    panels.append(row_panel("🩺  Blood Pressure & Heart Rate", y=47))

    # BP trend — systolic + diastolic (y=48, h=10, w=14)
    # Data from Omron cuff → Health Connect → Google Fit → InfluxDB
    BP_FLUX_S = f'from(bucket: "{BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "health_blood_pressure")\n  |> filter(fn: (r) => r["_field"] == "systolic")'
    BP_FLUX_D = f'from(bucket: "{BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "health_blood_pressure")\n  |> filter(fn: (r) => r["_field"] == "diastolic")'
    BP_FLUX_P = f'from(bucket: "{BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "health_blood_pressure")\n  |> filter(fn: (r) => r["_field"] == "pulse")'
    HR_FLUX   = f'from(bucket: "{BUCKET}")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r["_measurement"] == "health_heart_rate")\n  |> filter(fn: (r) => r["_field"] == "bpm_avg")'

    panels.append(ts_panel(
        "Blood Pressure — Systolic / Diastolic (Omron)",
        [
            target(BP_FLUX_S, "A"),
            target(BP_FLUX_D, "B"),
            target(BP_FLUX_P, "C"),
        ],
        x=0, y=48, w=14, h=10, unit="none",
        fill=10, grad="none", min_val=40, max_val=200,
        decimals=0,
        threshold_cfg=thresholds([
            (None,  "green"),
            (120,   "#EAB308"),   # elevated systolic
            (130,   "orange"),    # stage 1 hypertension
            (140,   "red"),       # stage 2 hypertension
        ]),
        threshold_line=True,
        overrides=[
            {"matcher": {"id": "byName", "options": "systolic"},
             "properties": [
                 {"id": "color", "value": fixed_color("#EF4444")},
                 {"id": "displayName", "value": "Systolic"},
                 {"id": "custom.lineWidth", "value": 2},
                 {"id": "custom.fillOpacity", "value": 0},
             ]},
            {"matcher": {"id": "byName", "options": "diastolic"},
             "properties": [
                 {"id": "color", "value": fixed_color("#3B82F6")},
                 {"id": "displayName", "value": "Diastolic"},
                 {"id": "custom.lineWidth", "value": 2},
                 {"id": "custom.fillOpacity", "value": 8},
             ]},
            {"matcher": {"id": "byName", "options": "pulse"},
             "properties": [
                 {"id": "color", "value": fixed_color("#F97316")},
                 {"id": "displayName", "value": "Pulse"},
                 {"id": "custom.lineStyle", "value": {"dash": [4,4], "fill": "dash"}},
             ]},
        ]
    ))

    # BP stat + Heart Rate stat (y=48, w=5 each)
    panels.append(stat_panel(
        "Latest BP",
        BP_FLUX_S,
        "none",
        thresholds([(None,"green"),(120,"#EAB308"),(130,"orange"),(140,"red")]),
        x=14, y=48, w=5, h=5, decimals=0
    ))

    panels.append(stat_panel(
        "Heart Rate (avg)",
        HR_FLUX,
        "none",
        thresholds([(None,"blue"),(100,"#EAB308"),(110,"red")]),
        x=19, y=48, w=5, h=5, decimals=0
    ))

    # Heart Rate trend (y=53, w=10, h=5)
    panels.append(ts_panel(
        "Heart Rate Trend",
        [target(HR_FLUX, "A")],
        x=14, y=53, w=10, h=5, unit="none",
        fill=12, grad="opacity",
        min_val=40, max_val=120, decimals=0,
        overrides=[{
            "matcher": {"id": "byName", "options": "bpm_avg"},
            "properties": [
                {"id": "color", "value": fixed_color(C_HR)},
                {"id": "displayName", "value": "BPM"},
                {"id": "custom.lineWidth", "value": 2},
            ]
        }]
    ))

    # ── Row 5: Body & Vitals separator (y=58) ────────────────────────────────
    panels.append(row_panel("⚖️  Body & Vitals", y=58))

    # Weight trend (y=59, h=10, w=14)
    panels.append(ts_panel(
        "Weight & Body Fat Trend",
        [
            target(flux("samsung_health_weight","weight_lbs"), "A"),
            target(flux("samsung_health_weight","body_fat_pct"), "B"),
        ],
        x=0, y=59, w=14, h=10, unit="none",
        fill=15, grad="opacity",
        decimals=1,
        overrides=[
            {"matcher": {"id": "byName", "options": "weight_lbs"},
             "properties": [
                 {"id": "color", "value": fixed_color(C_WEIGHT)},
                 {"id": "displayName", "value": "Weight (lbs)"},
                 {"id": "custom.lineWidth", "value": 3},
                 {"id": "custom.fillOpacity", "value": 18},
                 {"id": "unit", "value": "none"},
                 {"id": "min", "value": 150},
                 {"id": "max", "value": 260},
             ]},
            {"matcher": {"id": "byName", "options": "body_fat_pct"},
             "properties": [
                 {"id": "color", "value": fixed_color(C_FATPCT)},
                 {"id": "displayName", "value": "Body Fat (%)"},
                 {"id": "unit", "value": "percent"},
                 {"id": "min", "value": 0},
                 {"id": "max", "value": 45},
                 {"id": "custom.axisPlacement", "value": "right"},
                 {"id": "custom.lineStyle", "value": {"dash": [6,3], "fill": "dash"}},
             ]},
        ]
    ))

    # Gauge: body fat (y=59, h=5, w=5)
    panels.append(gauge_panel(
        "Body Fat %",
        flux("samsung_health_weight","body_fat_pct"),
        "percent", 0, 45,
        [(None,"green"),(20,"#EAB308"),(28,"red")],
        x=14, y=59, w=5, h=5
    ))

    # Gauge: weight (y=64, h=5, w=5)
    panels.append(gauge_panel(
        "Current Weight",
        flux("samsung_health_weight","weight_lbs"),
        "none", 140, 260,
        [(None, "blue")],
        x=14, y=64, w=5, h=5
    ))

    # Respiratory Rate (y=59, h=10, w=5)
    panels.append(ts_panel(
        "Respiratory Rate (Sleep)",
        [target(flux("samsung_health_respiratory_rate","bpm"), "A")],
        x=19, y=59, w=5, h=10, unit="none",
        fill=15, grad="opacity",
        min_val=8, max_val=28, decimals=1,
        threshold_cfg=thresholds([(None,"red"),(12,"green"),(20,"#EAB308"),(25,"red")]),
        threshold_line=True,
        overrides=[{
            "matcher": {"id": "byName", "options": "bpm"},
            "properties": [
                {"id": "color", "value": fixed_color(C_RESP)},
                {"id": "displayName", "value": "Breaths/min"},
                {"id": "custom.lineWidth", "value": 2},
                {"id": "custom.fillOpacity", "value": 20},
            ]
        }]
    ))

    return panels

# ─── POST to Grafana ─────────────────────────────────────────────────────────

def post_dashboard(token):
    panels = build_dashboard()

    dashboard = {
        "id": None,
        "uid": "samsung-health-brian",
        "title": "🏃 Samsung Health — Brian",
        "tags": ["health", "personal", "samsung"],
        "timezone": "browser",
        "schemaVersion": 38,
        "version": 1,
        "refresh": "1h",
        "time": {"from": "now-90d", "to": "now"},
        "timepicker": {
            "refresh_intervals": ["1h", "6h", "12h", "1d"],
            "time_options": ["7d", "30d", "90d", "180d", "1y", "2y"],
        },
        "panels": panels,
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "liveNow": False,
        "style": "dark",
        "templating": {"list": []},
        "annotations": {"list": []},
    }

    payload = json.dumps({
        "dashboard": dashboard,
        "folderId": 0,
        "overwrite": True,
        "message": "BlunderBus auto-provisioned Samsung Health dashboard",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GRAFANA_URL}/api/dashboards/db",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("status"), result.get("uid"), result.get("url")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP {e.code}: {body[:400]}")
        return None, None, None
    except Exception as e:
        print(f"❌ {e}")
        return None, None, None

# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument("--save-json", metavar="PATH", help="Save dashboard JSON to file instead of posting")
    args = p.parse_args()

    panels = build_dashboard()
    dashboard = {
        "id": None, "uid": "samsung-health-brian",
        "title": "Samsung Health - Brian",
        "tags": ["health", "personal", "samsung"],
        "timezone": "browser",
        "schemaVersion": 38,
        "version": 1,
        "refresh": "1h",
        "time": {"from": "now-90d", "to": "now"},
        "timepicker": {"time_options": ["7d","30d","90d","180d","1y"]},
        "panels": panels,
        "editable": True,
        "graphTooltip": 1,
        "style": "dark",
        "templating": {"list": []},
        "annotations": {"list": []},
    }
    payload = {"dashboard": dashboard, "folderId": 0, "overwrite": True, "message": "BlunderBus Samsung Health"}

    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        print(f"Saved {args.save_json}  ({len(json.dumps(payload).encode()):,} bytes,  {len(panels)} panels)")
        return

    token = env_first("GRAFANA_TOKEN", "GRAFANA_API_KEY")
    if not token:
        sys.exit("GRAFANA_TOKEN not set (GRAFANA_API_KEY legacy alias also supported).")

    print(f"Posting to {GRAFANA_URL} ...")
    status, uid, url = post_dashboard(token)
    if status == "success":
        print(f"Dashboard created: {GRAFANA_URL}{url}")
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
