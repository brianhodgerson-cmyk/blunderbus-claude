---
name: mqtt-bridge
description: Publish and subscribe to MQTT topics on the Mosquitto broker (Stark). Use for IoT messaging, Home Assistant integration, and device communication.
allowed-tools: Bash
---

# MQTT Bridge — Mosquitto

## What This Does
Interfaces with the Mosquitto MQTT broker on Stark (192.168.50.204) to publish messages, subscribe to topics, and monitor IoT device traffic.

## How To Run

### Publish a message
```bash
mosquitto_pub -h 192.168.50.204 -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "<TOPIC>" -m "<MESSAGE>"
```

### Subscribe and listen (with timeout)
```bash
mosquitto_sub -h 192.168.50.204 -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "<TOPIC>" -C 10 -W 5
```
- `-C 10` = exit after 10 messages
- `-W 5` = timeout after 5 seconds of no messages

### Monitor all topics (brief snapshot)
```bash
mosquitto_sub -h 192.168.50.204 -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "#" -v -C 20 -W 5
```

### Monitor Home Assistant topics
```bash
mosquitto_sub -h 192.168.50.204 -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "homeassistant/#" -v -C 20 -W 5
```

### Check broker status
```bash
mosquitto_sub -h 192.168.50.204 -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "\$SYS/#" -v -C 20 -W 3
```

## Common Topics
- `homeassistant/#` — HA discovery and state
- `frigate/#` — Frigate NVR events
- `zigbee2mqtt/#` — Zigbee device events (if configured)
- `$SYS/#` — Broker stats (clients, messages, bytes)
