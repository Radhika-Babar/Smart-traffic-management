# MQTT Topics

Broker: Eclipse Mosquitto 2.0, local, on port 1883. All messages are JSON at QoS 0, because a reading is superseded by the
next one within seconds. All payloads carry `"simulated": true`.

| Topic | Publisher | Subscriber | Rate at 1x |
| --- | --- | --- | --- |
| `traffic/sensors/{sensor_id}/readings` | Field gateway | Backend | About 32 per second in total |
| `traffic/signals/{intersection_id}/state` | Field gateway | Backend | 1 per second per intersection, plus each transition |
| `traffic/signals/{intersection_id}/command` | Backend | Field gateway | Per decision |
| `traffic/intersections/{intersection_id}/status` | Field gateway | Backend | Every 5 s |
| `traffic/emergency/{vehicle_id}` | Field gateway | Backend | 1 per second per active vehicle |
| `traffic/simulation/events` | Field gateway | Backend | Per injected event |
| `traffic/alerts` | Backend | Any observer | Per alert |
| `traffic/system/health` | Backend | Any observer | Every 5 s |

`traffic/signals/.../command` and `traffic/simulation/events` were added to the specified topic list. Commands close the
control loop. Simulation events carry ground truth so that an injected incident can be matched to a detection afterwards.
The detector itself never reads them.

## Watching the traffic

```powershell
docker compose exec mosquitto mosquitto_sub -t "traffic/#" -v
```

## Sensor reading

There are five road sensor types on each approach, plus one environmental sensor per intersection. Each reports only what
that instrument can measure.

| Sensor type | Default interval | Fields |
| --- | ---: | --- |
| `VEHICLE_DETECTION` | 2 s | `queue_length`, `approaching`, `avg_wait_s`, `max_wait_s` |
| `TRAFFIC_DENSITY` | 5 s | `traffic_density` |
| `VEHICLE_COUNT` | 5 s | `vehicle_count` |
| `ROAD_OCCUPANCY` | 5 s | `occupancy_percentage` |
| `AVERAGE_SPEED` | 5 s | `average_speed`, null when nothing passed |
| `ENVIRONMENTAL` | 30 s | `temperature_c`, `rain_mm_h`, `visibility_m` |

```json
{
  "sensor_id": "SNS-002",
  "sensor_type": "TRAFFIC_DENSITY",
  "intersection_id": "INT-001",
  "direction": "NORTH",
  "status": "ACTIVE",
  "timestamp": "2026-11-06T05:37:35.082709+00:00",
  "seq": 3,
  "simulated": true,
  "traffic_density": 42.1
}
```

A failed sensor publishes nothing, as a real one would. The backend notices the silence.

## Signal state

```json
{
  "intersection_id": "INT-001",
  "phase": "GREEN",
  "active_direction": "NORTH",
  "heads": { "NORTH": "GREEN", "EAST": "RED", "SOUTH": "RED", "WEST": "RED" },
  "elapsed": 12.5,
  "planned_green": 24.0,
  "remaining": 11.5,
  "mode": "AUTOMATIC",
  "controller": "ONLINE",
  "preempt_vehicle": null,
  "preempt_direction": null,
  "transition": false,
  "min_green": 8.0,
  "max_green": 60.0,
  "timestamp": "2026-11-06T05:37:35.082709+00:00",
  "simulated": true
}
```

## Signal command

```json
{
  "action": "SWITCH",
  "intersection_id": "INT-001",
  "direction": "EAST",
  "seconds": 18.0,
  "reason": "EAST has highest priority 44 over NORTH 21",
  "source": "ADAPTIVE"
}
```

| Action | Effect, always subject to the FSM |
| --- | --- |
| `SWITCH` | Serve this direction next, ending the current green once minimum green has passed |
| `EXTEND` | Lengthen the running green, never past maximum green |
| `PREEMPT` | Request priority for an emergency vehicle |
| `RELEASE` | Withdraw priority |
| `SET_MODE` | Switch between AUTOMATIC and MANUAL |

## Emergency telemetry

```json
{
  "vehicle_id": "AMB-453621",
  "vehicle_type": "AMBULANCE",
  "status": "EN_ROUTE",
  "x": 120.5, "y": 340.0, "speed": 15.2,
  "next_intersection": "INT-004",
  "approach": "NORTH",
  "distance_m": 177.0, "eta_s": 10.0,
  "route": ["INT-001", "INT-004", "INT-005", "INT-006"],
  "route_index": 1,
  "timestamp": "2026-11-06T05:37:35.082709+00:00",
  "simulated": true
}
```

## Validation

Every message is validated in `app/schemas/messages.py` before use: unknown fields, out-of-range values, malformed IDs,
missing type-specific fields and `simulated: false` are all rejected. A state report with two moving directions is also
rejected, so the backend independently checks the safety rule. Rejections are counted on the System Settings page.

## Reconnection

Paho retries with a delay of 1 to 15 seconds. Connections and disconnections are logged, added to the event feed, and
raised as an alert.

The local fallback delivers the same messages in-process while the broker is away. It is reported as `LOCAL_FALLBACK` in
the API and shown in the top bar, and is never presented as MQTT.
