---
name: camera-events
description: Query Frigate NVR for camera events, detections, recording status, and camera health. Use for security footage review and motion detection analysis.
allowed-tools: Bash, mcp__obsidian__obsidian_append
---

# Camera Events — Frigate NVR

## What This Does
Queries Frigate NVR at 192.168.50.205:5000 for detection events, camera status, and recording availability.

## Obsidian Integration
After formatting results, offer to append to today's daily note under the Infrastructure heading. If `--save` is passed, append automatically.

## How To Run

### List recent events (last 50)
```bash
ssh hawkeye-nvr 'curl -s "http://localhost:5000/api/events?limit=50"' | jq '.[] | {id: .id, camera: .camera, label: .label, score: .top_score, start: .start_time, end: .end_time, zones: .zones}'
```

### Filter events by camera
```bash
ssh hawkeye-nvr 'curl -s "http://localhost:5000/api/events?camera=<CAMERA_NAME>&limit=25"' | jq '.[] | {label: .label, score: .top_score, start: .start_time}'
```

### Filter events by label (person, car, dog, etc.)
```bash
ssh hawkeye-nvr 'curl -s "http://localhost:5000/api/events?label=<LABEL>&limit=25"' | jq '.[] | {camera: .camera, score: .top_score, start: .start_time, zones: .zones}'
```

### Get camera status / stats
```bash
ssh hawkeye-nvr 'curl -s http://localhost:5000/api/stats' | jq '.cameras | to_entries[] | {camera: .key, fps: .value.camera_fps, detection_fps: .value.detection_fps, pid: .value.pid}'
```

### Get Frigate config
```bash
ssh hawkeye-nvr 'curl -s http://localhost:5000/api/config' | jq '.cameras | keys'
```

### Get event thumbnail
```bash
# Returns JPEG image
ssh hawkeye-nvr 'curl -s http://localhost:5000/api/events/<EVENT_ID>/thumbnail.jpg' > /tmp/event_thumb.jpg
```

### Events in a time range
```bash
# Unix timestamps
ssh hawkeye-nvr 'curl -s "http://localhost:5000/api/events?after=<START_UNIX>&before=<END_UNIX>&limit=50"' | jq '.[] | {camera: .camera, label: .label, score: .top_score, start: .start_time}'
```

## Report Format
| Time | Camera | Detection | Confidence | Zones |
|------|--------|-----------|------------|-------|
