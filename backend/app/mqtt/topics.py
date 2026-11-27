"""MQTT topic names. Everything that touches a topic string goes through here."""

SENSOR_READINGS = "traffic/sensors/{sensor_id}/readings"
INTERSECTION_STATUS = "traffic/intersections/{intersection_id}/status"
SIGNAL_STATE = "traffic/signals/{intersection_id}/state"
SIGNAL_COMMAND = "traffic/signals/{intersection_id}/command"
ALERTS = "traffic/alerts"
EMERGENCY = "traffic/emergency/{vehicle_id}"
SYSTEM_HEALTH = "traffic/system/health"
SIMULATION_EVENTS = "traffic/simulation/events"

# The backend listens to the field side of the network.
BACKEND_SUBSCRIPTIONS = (
    "traffic/sensors/+/readings",
    "traffic/intersections/+/status",
    "traffic/signals/+/state",
    "traffic/emergency/+",
    "traffic/simulation/events",
)
# The field gateway listens only for commands from the central system.
FIELD_SUBSCRIPTIONS = ("traffic/signals/+/command",)


def sensor_readings(sensor_id: str) -> str:
    return SENSOR_READINGS.format(sensor_id=sensor_id)


def intersection_status(intersection_id: str) -> str:
    return INTERSECTION_STATUS.format(intersection_id=intersection_id)


def signal_state(intersection_id: str) -> str:
    return SIGNAL_STATE.format(intersection_id=intersection_id)


def signal_command(intersection_id: str) -> str:
    return SIGNAL_COMMAND.format(intersection_id=intersection_id)


def emergency(vehicle_id: str) -> str:
    return EMERGENCY.format(vehicle_id=vehicle_id)
