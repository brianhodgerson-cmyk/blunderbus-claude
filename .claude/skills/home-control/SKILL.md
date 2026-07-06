---
name: home-control
description: HodgeSpot smart home control via Home Assistant — use this skill for ANY request involving: turning lights on/off or dimming them (backyard, bedroom, garage, front yard, sconces, bedside, patio); checking or setting the thermostat or temperature; controlling Sonos speakers (bedroom, living room, Move 2) or Samsung TVs; checking if Brian is home or away; querying door/window sensors (front door open?); managing garage camera floodlight or PTZ; triggering or enabling automations; checking printer ink; or any other smart home device query or command. When in doubt about whether a request is home-automation-related, use this skill — it covers everything connected to Home Assistant at 192.168.50.206.
allowed-tools: Bash, mcp__blunderbus__blunderbus_home_assistant
---

# Home Control — Home Assistant

Home Assistant is at `192.168.50.206:8123`. Use the `mcp__blunderbus__blunderbus_home_assistant` MCP tool for all queries and control. Fall back to curl with `$HA_LONG_LIVED_TOKEN` only if the MCP tool is unavailable.

## MCP Tool Reference

```
action: get_state       — entity_id required
action: list_entities   — no args needed
action: call_service    — entity_id, service ("domain.service_name"), service_data (object)
action: get_history     — entity_id, start_time, end_time (ISO timestamps)
```

---

## Device Map

### Lights (WiZ RGBW — support color, brightness, effects)

| Entity | Location |
|--------|----------|
| `light.front_sconce_left` / `light.front_sconce_right` | Front exterior sconces |
| `light.front_door_right` | Front door |
| `light.garage_sconce_front` | Garage front sconce |
| `light.garage_side_door` | Garage side door |
| `light.garage_flood_light` | Garage flood (also `light.garage_floodlight` for camera flood) |
| `light.front_yard_lights` | Front yard group |
| `light.backyard_lights` | Backyard group |
| `light.back_patio_right` | Back patio |
| `light.master_entry_left` / `light.master_entry_right` | Master entry |
| `light.b_bed_side` | Brian's bedside |
| `light.j_bed_side` | J's bedside |
| `light.bedroom` | Bedroom (group) |
| `light.wiz_rgbw_tunable_56a212` / `light.wiz_rgbw_tunable_568f36` | Additional WiZ bulbs |

### Climate
| Entity | Notes |
|--------|-------|
| `climate.master_bedroom_thermostat` | Ecobee thermostat |
| `sensor.master_bedroom_thermostat_temperature` | Current temp (°F) |
| `sensor.master_bedroom_thermostat_humidity` | Current humidity (%) |

### Media Players (Sonos)
| Entity | Location |
|--------|----------|
| `media_player.bedroom` | Bedroom Sonos |
| `media_player.living_room` / `media_player.living_room_2` | Living room Sonos |
| `media_player.move_2` | Sonos Move 2 (portable) |
| `media_player.bedroom_tv` | Bedroom TV |
| `media_player.living_room` | Living room (also used for TV audio) |

### Sensors & Presence
| Entity | Notes |
|--------|-------|
| `person.brian_hodgerson` | Brian's location (home/away) |
| `device_tracker.fold_7` | Brian's phone |
| `device_tracker.tablet` | Tablet |
| `binary_sensor.bzzzzr_window_door_is_open` | Front door open/closed |
| `binary_sensor.bzzzzr_window_door_is_open_2` | Second door sensor |
| `binary_sensor.door_window_sensor_7_sensor_state_door_window` | Additional door/window |
| `binary_sensor.bzzzzr_motion_detection_location_provided` | Motion |
| `sensor.bzzzzr_battery_level` | Door sensor battery |
| `sun.sun` | Sunrise/sunset state |

### Garage Camera (Reolink)
| Entity | Notes |
|--------|-------|
| `camera.garage_fluent` | Live feed |
| `binary_sensor.garage_motion` / `garage_person` / `garage_vehicle` / `garage_animal` | AI detections |
| `light.garage_floodlight` | Camera floodlight |
| `siren.garage_siren` | Siren |
| `switch.garage_auto_tracking` | PTZ auto-track |
| `select.garage_floodlight_mode` | Floodlight mode |
| `select.garage_ptz_preset` | PTZ preset |

### Automations
| Entity | Purpose |
|--------|---------|
| `automation.lights_on_sunset` | Exterior lights at sunset |
| `automation.backyard_lights_out` | Backyard lights off |
| `automation.bzzzzr_re_check_after_open` | Door sensor recheck |
| `automation.test` / `automation.test2` | Test automations |

### Other
| Entity | Notes |
|--------|-------|
| `weather.forecast_home` | Local weather |
| `calendar.family` / `calendar.brian_hodgerson_gmail_com` | Calendars |
| `todo.shopping_list` | Shopping list |
| `binary_sensor.thehodge22` | Xbox online status |
| `remote.office_tv_the_frame_qn65ls03dafxza` | Office Samsung Frame TV |
| `sensor.hp_officejet_pro_6970_*` | Printer ink levels |

---

## Common Operations

### Turn a light on/off
```
action: call_service
entity_id: light.backyard_lights
service: light.turn_on   # or light.turn_off
```

### Set brightness and color temperature
```
action: call_service
entity_id: light.b_bed_side
service: light.turn_on
service_data: {"brightness_pct": 40, "color_temp_kelvin": 2700}
```

### Set RGB color
```
action: call_service
entity_id: light.front_sconce_left
service: light.turn_on
service_data: {"rgb_color": [255, 100, 0]}
```

### Set thermostat temperature
```
action: call_service
entity_id: climate.master_bedroom_thermostat
service: climate.set_temperature
service_data: {"temperature": 70, "hvac_mode": "cool"}  # hvac_mode optional
```

### Set thermostat HVAC mode
```
action: call_service
entity_id: climate.master_bedroom_thermostat
service: climate.set_hvac_mode
service_data: {"hvac_mode": "heat"}  # heat | cool | heat_cool | off
```

### Control Sonos
```
action: call_service
entity_id: media_player.bedroom
service: media_player.volume_set
service_data: {"volume_level": 0.3}   # 0.0–1.0

service: media_player.media_play
service: media_player.media_pause
service: media_player.media_next_track
```

### Trigger an automation
```
action: call_service
entity_id: automation.lights_on_sunset
service: automation.trigger
```

### Enable/disable an automation
```
action: call_service
entity_id: automation.backyard_lights_out
service: automation.turn_on   # or automation.turn_off
```

### Check who's home
```
action: get_state
entity_id: person.brian_hodgerson
# state: "home" or zone name or "not_home"
```

### Get sensor history (last N hours)
```
action: get_history
entity_id: sensor.master_bedroom_thermostat_temperature
start_time: 2026-03-12T00:00:00Z
end_time: 2026-03-12T23:59:59Z
```

---

## Response Format

- Lead with current state before making changes when relevant
- For multi-entity results, use a table
- Include units (°F, %, W) and `last_changed` timestamps when reporting sensor data
- For destructive actions (siren, privacy mask off, etc.), confirm with operator first
- Garage camera PTZ and siren controls: confirm before executing
