# Tasker: Health Connect → InfluxDB Sync
## BlunderBus Health Pipeline — Android Setup

Runs nightly, reads Health Connect data, POSTs directly to InfluxDB on Banner.
No cloud services, no exports — all local network.

---

## Prerequisites

- [Tasker](https://play.google.com/store/apps/details?id=net.dinglisch.android.taskerm) installed
- Health Connect enabled on device (Settings → Apps → Health Connect)
- Samsung Health granted read permission in Health Connect
- On same WiFi as homelab (or VPN in)
- InfluxDB token from vault (or set as Tasker variable)

---

## Part 1: Store your InfluxDB token in Tasker

**Tasker → Preferences → Variables (or use a locked variable)**

1. Open Tasker → long-press any empty space → **Create Variable**
2. Name: `INFLUX_TOKEN`
3. Value: *(paste your InfluxDB token)*
4. Check **Encrypted** ← keeps it out of backups

> Your token is in Vaultwarden → InfluxDB → token field.

---

## Part 2: Create the sync task

**Tasker → Tasks tab → `+` → Name: `HealthConnect Sync`**

Add actions in this order:

---

### Action 1 — Get current timestamp (nanoseconds)

`Code → JavaScriptlet`

```javascript
// InfluxDB requires nanosecond epoch timestamps
var nowNs = Date.now() + "000000";  // ms → ns (append 6 zeros)
setLocal("ts_ns", nowNs);

// Also compute start of today (midnight UTC)
var d = new Date();
d.setHours(0, 0, 0, 0);
var startNs = d.getTime() + "000000";
setLocal("start_ns", startNs);
setLocal("today_date", d.toISOString().split("T")[0]);
```

---

### Action 2 — Read today's steps

`Plugin → Health Connect → Read Records`
- Record Type: **Steps**
- Start Time: `%start_ns` (or use "Start of Today" if the plugin supports it)
- End Time: `%ts_ns`
- Result Variable: `%hc_steps`

> If the plugin returns a list, use the **sum** option or add a JavaScriptlet to sum them.

---

### Action 3 — Read last sleep session

`Plugin → Health Connect → Read Records`
- Record Type: **Sleep Session**
- Start Time: yesterday midnight
- End Time: `%ts_ns`
- Result Variable: `%hc_sleep`

Then extract duration:

`Code → JavaScriptlet`
```javascript
// hc_sleep may return JSON — extract duration in minutes
try {
  var s = JSON.parse(local("hc_sleep"));
  // Take most recent session
  var session = Array.isArray(s) ? s[s.length - 1] : s;
  var dur = (session.endTime - session.startTime) / 60000;
  setLocal("sleep_mins", Math.round(dur).toString());
} catch(e) {
  setLocal("sleep_mins", "0");
}
```

---

### Action 4 — Read latest weight (optional, changes infrequently)

`Plugin → Health Connect → Read Records`
- Record Type: **Weight**
- Result Variable: `%hc_weight_kg`

---

### Action 5 — Build InfluxDB line protocol body

`Code → JavaScriptlet`
```javascript
var ts   = local("ts_ns");
var date = local("today_date");

var lines = [];

// Steps
var steps = parseInt(local("hc_steps") || "0");
if (steps > 0) {
  lines.push("samsung_health_steps,source=healthconnect steps=" + steps + "i " + ts);
}

// Sleep
var sleepMins = parseInt(local("sleep_mins") || "0");
if (sleepMins > 0) {
  lines.push("samsung_health_sleep,source=healthconnect duration_min=" + sleepMins + "i " + ts);
}

// Weight
var weightKg = parseFloat(local("hc_weight_kg") || "0");
if (weightKg > 30) {
  var weightLbs = (weightKg * 2.20462).toFixed(1);
  lines.push("samsung_health_weight,source=healthconnect weight_kg=" + weightKg + ",weight_lbs=" + weightLbs + " " + ts);
}

setLocal("influx_body", lines.join("\n"));
setLocal("has_data", lines.length > 0 ? "1" : "0");
```

---

### Action 6 — IF has data, POST to InfluxDB

`Task → If` → `%has_data eq 1`

Inside the If block:

`Net → HTTP Request`
- Method: **POST**
- URL:
  ```
  http://192.168.50.202:8086/api/v2/write?org=blunderbus&bucket=samsung_health&precision=ns
  ```
- Headers:
  ```
  Authorization: Token %INFLUX_TOKEN
  Content-Type: text/plain; charset=utf-8
  ```
- Body: `%influx_body`
- Result Code Variable: `%http_code`
- Timeout: 15

`Task → End If`

---

### Action 7 — Notify result

`Alert → Notify`
- Title: `Health Sync`
- Text: `%http_code eq 204 ? Synced to InfluxDB : Failed (%http_code)`
- Priority: Low
- Icon: heart (optional)

---

## Part 3: Set up the profile (nightly trigger)

**Tasker → Profiles tab → `+` → Time**

- From: `23:50`
- To: `23:55`
- Days: every day

Link to task: `HealthConnect Sync`

---

## Part 4: Test it manually

1. Open the task in Tasker
2. Tap ▶ (Play) to run it now
3. Check the notification — should say `Synced to InfluxDB`
4. Verify in Grafana: http://192.168.50.202:3000/d/samsung-health-brian

If you get HTTP 401: token is wrong
If you get HTTP 204: success (InfluxDB returns no-content on write success)
If you get HTTP 000: can't reach Banner — check WiFi/VPN

---

## Data written to InfluxDB

| Measurement | Fields | Tag |
|---|---|---|
| `samsung_health_steps` | steps | source=healthconnect |
| `samsung_health_sleep` | duration_min | source=healthconnect |
| `samsung_health_weight` | weight_kg, weight_lbs | source=healthconnect |

The `source=healthconnect` tag lets you distinguish live Tasker data
from the historical Samsung Health export data in Grafana queries.

---

## Troubleshooting

**Health Connect action not showing in Tasker**
→ Update Tasker to 6.2+. Enable Health Connect in Android Settings → Apps → Special App Access.

**Steps returning 0**
→ Samsung Health may need to sync to Health Connect first. Open Samsung Health → Settings → Health Connect → check sync is enabled.

**Task runs but no data in Grafana**
→ Check the `samsung_health` bucket time range. The Grafana default is 90 days — new live data will appear at the right edge.
