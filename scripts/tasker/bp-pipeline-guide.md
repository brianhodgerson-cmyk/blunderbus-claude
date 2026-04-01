# Tasker: Omron BP → InfluxDB Pipeline
## BlunderBus — Blood Pressure Ingest from Health Connect

Omron syncs to Samsung Health, Samsung Health writes to Health Connect.
Tasker reads directly from Health Connect and POSTs to InfluxDB on Banner.
Timestamps from the actual reading — safe to re-run, InfluxDB deduplicates.

```
Omron Cuff → Samsung Health → Health Connect → Tasker → InfluxDB → Grafana
```

---

## Prerequisites

- Tasker 6.2+ installed
- [Health Connect](https://play.google.com/store/apps/details?id=com.google.android.apps.healthdata) installed
- Samsung Health → Settings → Connected services → Health Connect → **enabled**
- Under Health Connect permissions, Samsung Health has **Blood Pressure** write permission
- Tasker has Health Connect **Blood Pressure read** permission
  - Android Settings → Apps → Health Connect → App permissions → Tasker → Blood Pressure → Allow

---

## Part 1: Store your InfluxDB token in Tasker

1. Tasker → **Vars** tab → `+`
2. Name: `INFLUX_TOKEN`
3. Value: *(your token from Vaultwarden → InfluxDB → token field)*
4. ✅ Check **Encrypted**

> Banner InfluxDB: `http://192.168.50.202:8086`
> Org: `blunderbus` · Bucket: `samsung_health`

---

## Part 2: Create task `BP Sync`

**Tasker → Tasks → `+` → Name: `BP Sync`**

---

### Action 1 — Compute time window

`Code → JavaScriptlet`

```javascript
// Look back 2 hours for new BP readings
// Using ms timestamps — Health Connect plugin uses ms, not ns
var now = Date.now();
var lookback = now - (2 * 60 * 60 * 1000);   // 2 hours ago

setLocal("window_start_ms", lookback.toString());
setLocal("window_end_ms",   now.toString());
```

---

### Action 2 — Read Blood Pressure records

`Plugin → Health Connect → Read Records`

| Field | Value |
|-------|-------|
| Record Type | **BloodPressure** |
| Start Time | `%window_start_ms` |
| End Time | `%window_end_ms` |
| Result Variable | `%hc_bp_raw` |

> If the plugin offers "Latest N records" instead of a time range, use **Latest: 5** to catch any recent readings.

---

### Action 3 — Parse BP + build line protocol

`Code → JavaScriptlet`

```javascript
var raw = local("hc_bp_raw") || "";
var influxLines = [];

try {
  var records = JSON.parse(raw);
  if (!Array.isArray(records)) records = [records];

  records.forEach(function(r) {
    // Health Connect field names (may vary by Tasker plugin version)
    var sys  = r.systolic       || r.systolicPressure  || null;
    var dia  = r.diastolic      || r.diastolicPressure || null;
    var ts_ms = r.time          || r.startTime          || Date.now();

    if (!sys || !dia) return;  // skip incomplete records

    // Convert reading timestamp ms → ns for InfluxDB
    var ts_ns = ts_ms.toString() + "000000";

    // BP classification tag
    var category = "normal";
    if      (sys >= 180 || dia >= 120) category = "crisis";
    else if (sys >= 140 || dia >= 90)  category = "stage2";
    else if (sys >= 130 || dia >= 80)  category = "stage1";
    else if (sys >= 120)               category = "elevated";

    var line = "health_blood_pressure,source=omron,category=" + category +
               " systolic=" + sys.toFixed(1) +
               ",diastolic=" + dia.toFixed(1);

    // Pulse — Omron records pulse alongside BP in some Health Connect implementations
    var pulse = r.pulse || r.heartRateBpm || null;
    if (pulse) line += ",pulse=" + pulse.toFixed(1);

    line += " " + ts_ns;
    influxLines.push(line);
  });

} catch(e) {
  setLocal("parse_error", e.toString());
}

setLocal("influx_body", influxLines.join("\n"));
setLocal("record_count", influxLines.length.toString());
```

---

### Action 4 — Skip if no new readings

`Task → If` → `%record_count eq 0`

  `Alert → Notify` Title: `BP Sync` Text: `No new BP readings in window`

`Task → End If`

`Task → Stop` → **If** `%record_count eq 0`

---

### Action 5 — POST to InfluxDB

`Net → HTTP Request`

| Field | Value |
|-------|-------|
| Method | **POST** |
| URL | `http://192.168.50.202:8086/api/v2/write?org=blunderbus&bucket=samsung_health&precision=ns` |
| Headers | `Authorization: Token %INFLUX_TOKEN` |
| Content-Type Header | `text/plain; charset=utf-8` |
| Body | `%influx_body` |
| Result Code Variable | `%http_code` |
| Timeout (Seconds) | 15 |

---

### Action 6 — Notify result

`Alert → Notify`

| Field | Value |
|-------|-------|
| Title | `BP Sync` |
| Text | `%http_code ~ 204 ? %record_count reading(s) sent ✅ : Failed HTTP %http_code` |
| Priority | Low |

---

## Part 3: Profile — trigger automatically

You have two good options. **Use both.**

### Option A: Trigger when Samsung Health syncs (recommended)

`Tasker → Profiles → + → App`
- Application: **Samsung Health**
- State: **Not in foreground** (fires when app goes to background after syncing)

Link to task: `BP Sync`

> After you take a BP reading, Samsung Health opens briefly to sync. When it closes, this fires.

---

### Option B: Hourly poll (safety net)

`Tasker → Profiles → + → Time`
- Repeat every: **1 hour**

Link to task: `BP Sync`

> Catches any readings that synced silently in the background.

---

## Part 4: Heart Rate (bonus — reads alongside BP)

Add a second Read Records action after Action 2:

`Plugin → Health Connect → Read Records`

| Field | Value |
|-------|-------|
| Record Type | **HeartRate** |
| Start Time | `%window_start_ms` |
| End Time | `%window_end_ms` |
| Result Variable | `%hc_hr_raw` |

Then append to the JavaScriptlet in Action 3:

```javascript
// --- Heart Rate (add after the BP block) ---
try {
  var hrRecords = JSON.parse(local("hc_hr_raw") || "[]");
  if (!Array.isArray(hrRecords)) hrRecords = [hrRecords];

  hrRecords.forEach(function(r) {
    var bpm   = r.beatsPerMinute || r.bpm || null;
    var ts_ms = r.time || r.startTime || Date.now();
    if (!bpm) return;
    var ts_ns = ts_ms.toString() + "000000";
    influxLines.push("health_heart_rate,source=omron bpm=" + bpm.toFixed(1) + " " + ts_ns);
  });
} catch(e) {}
```

---

## Part 5: Test it manually

1. Take a BP reading with your Omron cuff
2. Open Samsung Health — confirm the reading appears under **Heart** → **Blood Pressure**
3. Open Tasker → **Tasks** → tap `BP Sync` → ▶ **Play**
4. Watch the notification — should say `1 reading(s) sent ✅`
5. Verify in Grafana: http://192.168.50.202:3000/d/samsung-health-brian
   - Look at the **🩺 Blood Pressure & HR** row
   - Set time range to **Last 1 hour** to find the new point

---

## Data written to InfluxDB

| Measurement | Fields | Tags |
|---|---|---|
| `health_blood_pressure` | systolic, diastolic, pulse | source=omron, category=normal\|elevated\|stage1\|stage2\|crisis |
| `health_heart_rate` | bpm | source=omron |

The `category` tag lets you create Grafana threshold alerts by BP classification.

---

## Troubleshooting

**`hc_bp_raw` is empty or `[]`**
→ Check Health Connect permissions: Settings → Apps → Health Connect → App permissions → Tasker → Blood Pressure → **Allow**
→ Check Samsung Health → Settings → Connected services → Health Connect → Blood Pressure write is **on**
→ Try expanding the lookback window in Action 1 from 2h to 24h for the first test

**HTTP 401**
→ Token is wrong or expired — re-paste from Vaultwarden

**HTTP 204 but no data in Grafana**
→ Check the time range — new live data appears at the right edge of the chart
→ Run this on Banner to confirm: `curl -s "http://192.168.50.202:8086/api/v2/query?org=blunderbus" -H "Authorization: Token $TOKEN" -H "Content-Type: application/vnd.flux" --data 'from(bucket:"samsung_health") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "health_blood_pressure")'`

**Pulse field missing**
→ Omron may not write pulse to Health Connect's BloodPressure record type — it may be separate in HeartRate. The HR section in Part 4 handles that.

**Task fires but parse_error is set**
→ Your Tasker Health Connect plugin version uses different field names. Flash `%hc_bp_raw` to a notification to see the raw JSON, then adjust the field names in Action 3.
