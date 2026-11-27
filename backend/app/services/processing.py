"""Traffic processing engine: everything between the MQTT subscriber and the database.

Handles one validated message at a time. Time always comes from the message's
simulated timestamp, so behaviour is identical at 1x and 10x, and a paused
simulation never trips a timeout.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from app.algorithms.congestion import ApproachCongestion, CongestionInput, classify, level_rank, recommend, update
from app.algorithms.incident_detection import ApproachDetector, Sample
from app.algorithms.preemption import PreemptionManager
from app.algorithms.priority import ApproachInput, score_all
from app.core.clock import iso_utc, to_naive_utc, utcnow
from app.mqtt import topics
from app.schemas.messages import (
    EmergencyMessage,
    IntersectionStatusMessage,
    SensorReadingMessage,
    SignalStateMessage,
)
from app.services.central_controller import CentralController
from app.services.runtime_config import RuntimeConfig

logger = logging.getLogger(__name__)

STATUS_ONLINE = "ONLINE"
STATUS_OFFLINE = "OFFLINE"
STATUS_DISABLED = "DISABLED"

RECOMMENDED_RESPONSE = {
    "MINOR_ACCIDENT": "Dispatch a patrol unit and hold the adaptive plan",
    "MAJOR_ACCIDENT": "Dispatch emergency services and divert traffic to parallel roads",
    "ROAD_BLOCKAGE": "Clear the obstruction and divert traffic upstream",
    "SUDDEN_SLOWDOWN": "Inspect the approach and warn upstream drivers",
    "LANE_CLOSURE": "Review the closure and reduce upstream inflow",
    "UNCLASSIFIED": "Verify the approach; sensors show unusual traffic with no injected incident",
}


class Sink(Protocol):
    def add(self, kind: str, row: dict) -> None: ...
    def update(self, kind: str, key: object, values: dict) -> None: ...


class NullSink:
    def add(self, kind: str, row: dict) -> None:
        pass

    def update(self, kind: str, key: object, values: dict) -> None:
        pass


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class SensorLive:
    id: str
    sensor_type: str
    intersection_id: str
    direction: str | None
    interval_s: float
    enabled: bool = True
    status: str = STATUS_ONLINE
    last_seen: float | None = None
    last_seq: int = 0
    received: int = 0
    latest: dict = field(default_factory=dict)


@dataclass
class ApproachLive:
    intersection_id: str
    direction: str
    queue: float = 0.0
    approaching: float = 0.0
    avg_wait: float = 0.0
    max_wait: float = 0.0
    density: float = 0.0
    occupancy: float = 0.0
    speed: float | None = None
    last_count: float = 0.0
    served_since_sample: float = 0.0
    green_since_sample: bool = False
    last_green: float | None = None
    level: str = "LOW"
    updated: dict[str, float] = field(default_factory=dict)
    congestion: ApproachCongestion = field(default_factory=ApproachCongestion)
    detector: ApproachDetector = field(default_factory=ApproachDetector)
    incident_record: str | None = None
    # one-minute accumulator
    acc_volume: int = 0
    acc_queue: float = 0.0
    acc_wait: float = 0.0
    acc_density: float = 0.0
    acc_occupancy: float = 0.0
    acc_speed: float = 0.0
    n_detect: int = 0
    n_density: int = 0
    n_occupancy: int = 0
    n_speed: int = 0
    acc_level: str = "LOW"

    def public(self) -> dict:
        return {
            "direction": self.direction,
            "queue": round(self.queue, 1),
            "approaching": round(self.approaching, 1),
            "avg_wait_s": round(self.avg_wait, 1),
            "max_wait_s": round(self.max_wait, 1),
            "density": round(self.density, 1),
            "occupancy": round(self.occupancy, 1),
            "speed": round(self.speed, 2) if self.speed is not None else None,
            "level": self.level,
        }


@dataclass
class IntersectionLive:
    id: str
    name: str
    directions: tuple[str, ...]
    signal_id: str
    state: SignalStateMessage | None = None
    last_state_at: float | None = None
    phase_started: datetime | None = None
    green_serial: int = 0
    vehicles: int = 0
    active_sensors: int = 0
    total_sensors: int = 0
    controller: str = "ONLINE"
    last_decision: dict | None = None
    rejected: int = 0


class TrafficProcessor:
    def __init__(
        self,
        intersections: list[tuple[str, str, tuple[str, ...], str]],
        sensors: list[SensorLive],
        config: RuntimeConfig,
        publish: Callable[[str, dict], None],
        sink: Sink | None = None,
        notify: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.config = config
        self.publish = publish
        self.sink: Sink = sink or NullSink()
        self.notify = notify or (lambda kind, data: None)
        self.central = CentralController(config.algorithm, config.strategy)
        self.preemption = PreemptionManager(config.preemption)
        self.stats: Counter[str] = Counter()
        self.alerts: dict[str, dict] = {}
        self.recent_events: deque[dict] = deque(maxlen=60)
        self.series: deque[dict] = deque(maxlen=240)
        self._recent_volume: deque[float] = deque(maxlen=12)
        self.emergencies: dict[str, dict] = {}
        self.open_incidents: dict[tuple[str, str], str] = {}
        self.incident_alerts: dict[str, str] = {}
        self.simulation_id: int | None = None
        self.load_network(intersections, sensors)

    def load_network(
        self, intersections: list[tuple[str, str, tuple[str, ...], str]], sensors: list[SensorLive]
    ) -> None:
        with self.lock:
            self.intersections = {
                i: IntersectionLive(i, name, directions, signal_id) for i, name, directions, signal_id in intersections
            }
            self.approaches = {
                (i.id, d): ApproachLive(i.id, d) for i in self.intersections.values() for d in i.directions
            }
            self.sensors = {s.id: s for s in sensors}
            self.now = 0.0
            self.minute: int | None = None
            self._next_check = 0.0
            self._next_series = 0.0
            self._minute_trips = 0

    def apply_config(self, config: RuntimeConfig) -> None:
        with self.lock:
            self.config = config
            self.central.config = config.algorithm
            self.central.strategy = config.strategy
            self.preemption.config = config.preemption

    def reset(self) -> None:
        """Forget volatile state after a simulation reset. Stored history is untouched."""
        with self.lock:
            for alert_key in list(self.alerts):
                self.resolve_alert(alert_key)
            for record_id in self.open_incidents.values():
                self.sink.update("incident", record_id, {"status": "RESOLVED", "resolved_at": self._naive(self.now)})
            for approach in self.approaches.values():
                if approach.congestion.record_id:
                    self.sink.update("congestion", approach.congestion.record_id, {
                        "status": "RESOLVED", "resolved_at": self._naive(self.now),
                    })
            self.open_incidents.clear()
            self.emergencies.clear()
            self.preemption.clear()
            self.central.reset()
            for key, intersection in self.intersections.items():
                self.intersections[key] = IntersectionLive(
                    intersection.id, intersection.name, intersection.directions, intersection.signal_id,
                )
            for key, approach in self.approaches.items():
                self.approaches[key] = ApproachLive(approach.intersection_id, approach.direction)
            for sensor in self.sensors.values():
                sensor.last_seen = None
                sensor.status = STATUS_ONLINE if sensor.enabled else STATUS_DISABLED
            self.minute = None
            self._next_check = 0.0

    @staticmethod
    def _naive(epoch: float) -> datetime:
        return datetime.fromtimestamp(epoch, tz=UTC).replace(tzinfo=None)

    # ---- dispatch --------------------------------------------------------

    def handle(self, topic: str, payload: dict) -> None:
        try:
            with self.lock:
                if topic.startswith("traffic/sensors/"):
                    self._on_reading(SensorReadingMessage.model_validate(payload))
                elif topic.startswith("traffic/signals/") and topic.endswith("/state"):
                    self._on_signal(SignalStateMessage.model_validate(payload))
                elif topic.startswith("traffic/intersections/"):
                    self._on_status(IntersectionStatusMessage.model_validate(payload))
                elif topic.startswith("traffic/emergency/"):
                    self._on_emergency(EmergencyMessage.model_validate(payload))
                elif topic == topics.SIMULATION_EVENTS:
                    self._on_simulation_event(payload)
        except ValidationError as error:
            self.stats["invalid_payloads"] += 1
            first = error.errors()[0]
            logger.warning("Rejected payload on %s: %s at %s", topic, first["msg"], first["loc"])

    def _tick_time(self, stamp: datetime) -> None:
        now = stamp.timestamp()
        if now > self.now:
            self.now = now
        minute = int(self.now // 60)
        if self.minute is None:
            self.minute = minute
        elif minute > self.minute:
            self._flush_minute(self.minute)
            self.minute = minute
        if self.now >= self._next_check:
            self._next_check = self.now + 5.0
            self._check_health()
        if self.now >= self._next_series:
            self._next_series = self.now + 5.0
            self._sample_series()

    # ---- sensors ---------------------------------------------------------

    def _on_reading(self, message: SensorReadingMessage) -> None:
        sensor = self.sensors.get(message.sensor_id)
        if sensor is None:
            self.stats["unknown_sensor"] += 1
            return
        self._tick_time(message.timestamp)
        self.stats["readings"] += 1
        now = message.timestamp.timestamp()
        sensor.last_seen = now
        sensor.last_seq = message.seq
        sensor.received += 1
        values = message.model_dump(
            exclude={"sensor_id", "sensor_type", "intersection_id", "direction", "status", "timestamp", "seq", "simulated"},
            exclude_none=True,
        )
        sensor.latest = {**values, "timestamp": message.timestamp.isoformat()}
        if sensor.status == STATUS_OFFLINE:
            sensor.status = STATUS_ONLINE
            self.resolve_alert(f"sensor:{sensor.id}")
            self.event("SENSOR", f"{sensor.id} back online", "INFO", sensor.intersection_id)
        self.sink.add("reading", {
            "sensor_id": sensor.id, "recorded_at": to_naive_utc(message.timestamp),
            **{k: v for k, v in values.items()},
        })
        if message.direction is None:
            return
        approach = self.approaches.get((message.intersection_id, message.direction))
        if approach is None:
            return
        approach.updated[message.sensor_type] = now
        kind = message.sensor_type
        if kind == "VEHICLE_DETECTION":
            approach.queue = float(message.queue_length)
            approach.approaching = float(message.approaching)
            approach.avg_wait = message.avg_wait_s
            approach.max_wait = message.max_wait_s
            approach.acc_queue += approach.queue
            approach.acc_wait += approach.avg_wait
            approach.n_detect += 1
            self._update_congestion(approach, now)
        elif kind == "TRAFFIC_DENSITY":
            approach.density = message.traffic_density
            approach.acc_density += approach.density
            approach.n_density += 1
        elif kind == "ROAD_OCCUPANCY":
            approach.occupancy = message.occupancy_percentage
            approach.acc_occupancy += approach.occupancy
            approach.n_occupancy += 1
        elif kind == "VEHICLE_COUNT":
            approach.last_count = float(message.vehicle_count)
            approach.served_since_sample += approach.last_count
            approach.acc_volume += message.vehicle_count
        elif kind == "AVERAGE_SPEED":
            approach.speed = message.average_speed
            if approach.speed is not None:
                approach.acc_speed += approach.speed
                approach.n_speed += 1
            self._update_detector(approach, now)

    def _update_congestion(self, approach: ApproachLive, now: float) -> None:
        thresholds = self.config.congestion
        level = classify(CongestionInput(approach.queue, approach.occupancy, approach.avg_wait, approach.speed), thresholds)
        approach.level = level
        if level_rank(level) > level_rank(approach.acc_level):
            approach.acc_level = level
        change = update(approach.congestion, level, now, thresholds)
        if change is None:
            return
        key = f"congestion:{approach.intersection_id}:{approach.direction}"
        name = self.intersections[approach.intersection_id].name
        if change.kind == "RESOLVED":
            self.resolve_alert(key)
            if approach.congestion.record_id:
                self.sink.update("congestion", approach.congestion.record_id, {
                    "status": "RESOLVED", "resolved_at": self._naive(now), "severity": change.level,
                })
                approach.congestion.record_id = None
            self.event("CONGESTION", f"Congestion cleared at {name} {approach.direction}", "INFO", approach.intersection_id)
            return
        action = recommend(change.level, approach.direction)
        severity = "CRITICAL" if change.level == "SEVERE" else "WARNING"
        description = f"{change.level} congestion on {approach.direction} approach of {name}"
        if change.kind == "OPENED":
            approach.congestion.record_id = short_id("CNG")
            self.sink.add("congestion", {
                "id": approach.congestion.record_id, "intersection_id": approach.intersection_id,
                "direction": approach.direction, "severity": change.level, "detected_at": self._naive(now),
                "status": "ACTIVE", "recommended_action": action,
            })
        elif approach.congestion.record_id:
            self.sink.update("congestion", approach.congestion.record_id, {
                "severity": change.level, "recommended_action": action,
            })
        self.resolve_alert(key)
        self.raise_alert("SEVERE_CONGESTION", severity, description, approach.intersection_id, key)

    def _update_detector(self, approach: ApproachLive, now: float) -> None:
        sample = Sample(
            now, approach.speed, approach.queue, approach.avg_wait,
            approach.served_since_sample, approach.green_since_sample, approach.occupancy,
        )
        approach.served_since_sample = 0.0
        approach.green_since_sample = False
        result = approach.detector.update(sample)
        if result is None:
            return
        key = (approach.intersection_id, approach.direction)
        name = self.intersections[approach.intersection_id].name
        if result.kind == "CLEARED":
            if approach.incident_record and key not in self.open_incidents:
                self.sink.update("incident", approach.incident_record, {
                    "status": "RESOLVED", "resolved_at": self._naive(now),
                })
                self.resolve_alert(f"anomaly:{approach.incident_record}")
            approach.incident_record = None
            return
        evidence = list(result.evidence)
        injected = self.open_incidents.get(key)
        if injected is not None:
            approach.incident_record = injected
            self.sink.update("incident", injected, {
                "status": "DETECTED", "detected_at": self._naive(now),
                "detection_score": result.score, "evidence": evidence,
            })
            self.raise_alert(
                "ACCIDENT_DETECTED", "CRITICAL",
                f"Simulated incident detected on {approach.direction} approach of {name}: {evidence[0]}",
                approach.intersection_id, f"incident:{injected}",
            )
            self.notify("incident", {"id": injected, "status": "DETECTED"})
            return
        record = short_id("ANM")
        approach.incident_record = record
        self.sink.add("incident", {
            "id": record, "incident_type": "UNCLASSIFIED", "origin": "DETECTED",
            "intersection_id": approach.intersection_id, "direction": approach.direction,
            "severity": result.severity, "status": "DETECTED", "detected_at": self._naive(now),
            "detection_score": result.score, "evidence": evidence,
            "recommended_response": RECOMMENDED_RESPONSE["UNCLASSIFIED"],
        })
        self.raise_alert(
            "UNUSUAL_TRAFFIC", "WARNING", f"Unusual traffic on {approach.direction} approach of {name}: {evidence[0]}",
            approach.intersection_id, f"anomaly:{record}",
        )
        self.notify("incident", {"id": record, "status": "DETECTED"})

    # ---- signals ---------------------------------------------------------

    def approach_inputs(self, intersection: IntersectionLive) -> list[ApproachInput]:
        """Algorithm inputs, degrading gracefully when sensors have gone quiet."""
        result = []
        for direction in intersection.directions:
            approach = self.approaches[(intersection.id, direction)]
            detect_age = self.now - approach.updated.get("VEHICLE_DETECTION", 0)
            queue, approaching, waiting = approach.queue, approach.approaching, approach.max_wait
            degraded = detect_age > 15.0
            if degraded:
                # Fall back to the other instruments, then to elapsed red time.
                density_age = self.now - approach.updated.get("TRAFFIC_DENSITY", 0)
                estimate = approach.density * 0.35 if density_age <= 20.0 else 4.0
                queue, approaching = estimate, 0.0
                waiting = self.now - approach.last_green if approach.last_green else 30.0
            red_seconds = self.now - approach.last_green if approach.last_green else 0.0
            result.append(ApproachInput(
                direction, queue, waiting, approach.density, approaching, approach.speed, degraded, red_seconds,
            ))
        return result

    def _on_signal(self, message: SignalStateMessage) -> None:
        self._tick_time(message.timestamp)
        intersection = self.intersections.get(message.intersection_id)
        if intersection is None:
            return
        now = message.timestamp.timestamp()
        previous = intersection.state
        intersection.state = message
        intersection.last_state_at = now
        intersection.controller = message.controller
        if intersection.controller != "FAILSAFE":
            self.resolve_alert(f"failsafe:{intersection.id}")
        if message.active_direction and message.phase == "GREEN":
            approach = self.approaches.get((intersection.id, message.active_direction))
            if approach is not None:
                approach.last_green = now
                approach.green_since_sample = True
        if message.transition:
            stamp = to_naive_utc(message.timestamp)
            if previous is not None and intersection.phase_started is not None:
                self.sink.add("signal_history", {
                    "signal_id": intersection.signal_id,
                    "phase": previous.phase,
                    "direction": previous.active_direction,
                    "started_at": intersection.phase_started,
                    "ended_at": stamp,
                    "duration_s": (stamp - intersection.phase_started).total_seconds(),
                    "end_reason": (message.reason or "")[:200],
                    "mode": previous.mode,
                })
            intersection.phase_started = stamp
            if message.phase == "GREEN":
                intersection.green_serial += 1
            self.notify("signal", {
                "intersection_id": intersection.id, "phase": message.phase,
                "direction": message.active_direction, "reason": message.reason,
            })
        decision = self.central.evaluate(message, intersection.green_serial, self.approach_inputs(intersection))
        if decision is None:
            return
        self.stats["decisions"] += 1
        command = {
            "action": decision.action, "intersection_id": intersection.id, "direction": decision.direction,
            "seconds": round(decision.green_seconds, 1), "reason": decision.reason, "source": "ADAPTIVE",
        }
        self.publish(topics.signal_command(intersection.id), command)
        scores = [s.to_dict() for s in decision.scores]
        intersection.last_decision = {
            "action": decision.action, "direction": decision.direction,
            "green_s": round(decision.green_seconds, 1), "reason": decision.reason,
            "scores": scores, "decided_at": message.timestamp.isoformat(),
        }
        self.sink.add("decision", {
            "signal_id": intersection.signal_id, "decided_at": to_naive_utc(message.timestamp),
            "action": decision.action, "direction": decision.direction,
            "green_s": round(decision.green_seconds, 1), "reason": decision.reason[:200], "scores": scores,
        })
        if decision.action == "SWITCH":
            self.notify("decision", {"intersection_id": intersection.id, **intersection.last_decision})

    def _on_status(self, message: IntersectionStatusMessage) -> None:
        self._tick_time(message.timestamp)
        intersection = self.intersections.get(message.intersection_id)
        if intersection is None:
            return
        intersection.vehicles = message.vehicles_on_approaches
        intersection.active_sensors = message.active_sensors
        intersection.total_sensors = message.total_sensors

    # ---- emergencies -----------------------------------------------------

    def _on_emergency(self, message: EmergencyMessage) -> None:
        self._tick_time(message.timestamp)
        record = self.emergencies.setdefault(message.vehicle_id, {"preemptions": 0})
        record.update(message.model_dump(mode="json"))
        actions = self.preemption.on_telemetry(
            message.vehicle_id, message.status, message.next_intersection, message.approach,
            message.distance_m, message.eta_s,
        )
        for action in actions:
            self.publish(topics.signal_command(action.intersection_id), {
                "action": action.action, "intersection_id": action.intersection_id,
                "direction": action.direction, "vehicle_id": action.vehicle_id,
                "reason": "emergency vehicle", "source": "EMERGENCY",
            })
            name = self.intersections[action.intersection_id].name if action.intersection_id in self.intersections else ""
            if action.action == "PREEMPT":
                record["preemptions"] += 1
                self.raise_alert(
                    "EMERGENCY_APPROACHING", "WARNING",
                    f"{message.vehicle_type.replace('_', ' ').title()} {message.vehicle_id} approaching {name} "
                    f"from {action.direction}, priority requested",
                    action.intersection_id, f"emergency:{message.vehicle_id}:{action.intersection_id}",
                )
                self.sink.update("emergency", message.vehicle_id, {"preemptions": record["preemptions"]})
            else:
                self.resolve_alert(f"emergency:{message.vehicle_id}:{action.intersection_id}")
                self.event("EMERGENCY", f"Priority released at {name} after {message.vehicle_id}", "INFO", action.intersection_id)
        if message.status != "EN_ROUTE":
            self.emergencies.pop(message.vehicle_id, None)
            if message.status == "CANCELLED":
                self.sink.update("emergency", message.vehicle_id, {
                    "status": "CANCELLED", "finished_at": to_naive_utc(message.timestamp),
                })
        self.notify("emergency", record)

    # ---- simulation ground-truth events ---------------------------------

    def _on_simulation_event(self, payload: dict) -> None:
        event = payload.get("event")
        stamp = datetime.fromisoformat(payload["timestamp"])
        naive = to_naive_utc(stamp)
        self._tick_time(stamp)
        if event == "INCIDENT_INJECTED":
            incident_id = payload["incident_id"]
            key = (payload["intersection_id"], payload["direction"])
            self.open_incidents[key] = incident_id
            self.sink.add("incident", {
                "id": incident_id, "incident_type": payload["incident_type"], "origin": "INJECTED",
                "intersection_id": payload["intersection_id"], "direction": payload["direction"],
                "severity": payload["severity"], "affected_lanes": payload["affected_lanes"],
                "status": "INJECTED", "injected_at": naive,
                "recommended_response": RECOMMENDED_RESPONSE[payload["incident_type"]],
            })
            label = payload["incident_type"].replace("_", " ").title()
            self.raise_alert(
                "SIMULATED_INCIDENT", "WARNING",
                f"Simulated {label} injected on {payload['direction']} approach, watching sensors for its effect",
                payload["intersection_id"], f"injected:{incident_id}",
            )
            self.notify("incident", {"id": incident_id, "status": "INJECTED"})
        elif event == "INCIDENT_CLEARED":
            incident_id = payload["incident_id"]
            for key, value in list(self.open_incidents.items()):
                if value == incident_id:
                    del self.open_incidents[key]
                    approach = self.approaches.get(key)
                    if approach is not None and approach.incident_record == incident_id:
                        approach.incident_record = None
            self.sink.update("incident", incident_id, {"status": "RESOLVED", "resolved_at": naive})
            self.resolve_alert(f"injected:{incident_id}")
            self.resolve_alert(f"incident:{incident_id}")
            self.event("INCIDENT", f"Simulated incident {incident_id} cleared", "INFO", payload.get("intersection_id"))
            self.notify("incident", {"id": incident_id, "status": "RESOLVED"})
        elif event == "EMERGENCY_DISPATCHED":
            self.sink.add("emergency", {
                "id": payload["vehicle_id"], "vehicle_type": payload["vehicle_type"],
                "origin_id": payload["origin"], "destination_id": payload["destination"],
                "route": payload["route"], "status": "EN_ROUTE", "dispatched_at": naive,
            })
            self.event(
                "EMERGENCY", f"{payload['vehicle_id']} dispatched from {payload['origin']} to {payload['destination']}",
                "WARNING", payload["origin"],
            )
        elif event == "EMERGENCY_ARRIVED":
            self.sink.update("emergency", payload["vehicle_id"], {
                "status": "ARRIVED", "finished_at": naive, "travel_time_s": payload["travel_time_s"],
                "wait_time_s": payload["wait_time_s"], "stops": payload["stops"],
            })
            self.event(
                "EMERGENCY", f"{payload['vehicle_id']} arrived in {payload['travel_time_s']:.0f}s "
                f"with {payload['wait_time_s']:.0f}s stopped", "INFO",
            )
        elif event == "SCENARIO_APPLIED":
            self.event("SIMULATION", f"Scenario applied: {payload['name']}", "INFO")
        elif event == "SENSOR_FAILURE_INJECTED":
            self.event("SIMULATION", f"Sensor failure injected on {', '.join(payload['sensors'])}", "WARNING")
        elif event == "CONTROLLER_FAILURE_INJECTED":
            self.event("SIMULATION", f"Controller failure injected at {payload['intersection_id']}", "WARNING",
                       payload["intersection_id"])

    # ---- periodic checks -------------------------------------------------

    def _check_health(self) -> None:
        now = self.now
        for sensor in self.sensors.values():
            if not sensor.enabled:
                sensor.status = STATUS_DISABLED
                continue
            if sensor.status == STATUS_DISABLED:
                sensor.status = STATUS_ONLINE
                sensor.last_seen = now
            if sensor.last_seen is None:
                sensor.last_seen = now
                continue
            if sensor.status == STATUS_ONLINE and now - sensor.last_seen > max(3 * sensor.interval_s, 15.0):
                sensor.status = STATUS_OFFLINE
                self.raise_alert(
                    "SENSOR_FAILURE", "WARNING",
                    f"{sensor.id} ({sensor.sensor_type.replace('_', ' ').lower()}) stopped reporting",
                    sensor.intersection_id, f"sensor:{sensor.id}",
                )
        for intersection in self.intersections.values():
            if intersection.last_state_at is None:
                intersection.last_state_at = now
                continue
            key = f"controller:{intersection.id}"
            silent = now - intersection.last_state_at > 6.0
            if silent and key not in self.alerts:
                intersection.controller = "FAILED"
                self.raise_alert(
                    "SIGNAL_CONTROLLER_FAILURE", "CRITICAL",
                    f"Signal controller at {intersection.name} stopped reporting", intersection.id, key,
                )
            elif not silent and key in self.alerts:
                self.resolve_alert(key)
            if intersection.controller == "FAILSAFE" and f"failsafe:{intersection.id}" not in self.alerts:
                self.raise_alert(
                    "SIGNAL_CONTROLLER_FAILURE", "WARNING",
                    f"{intersection.name} running local failsafe rotation: no central commands",
                    intersection.id, f"failsafe:{intersection.id}",
                )

    def _sample_series(self) -> None:
        overview = self.network_metrics()
        # A single 5 s count scaled to an hour is too noisy to read, so use a rolling minute.
        self._recent_volume.append(overview["volume"])
        overview["volume"] = round(sum(self._recent_volume) / len(self._recent_volume), 0)
        point = {"t": iso_utc(self._naive(self.now)), **overview}
        self.series.append(point)

    def _flush_minute(self, minute: int) -> None:
        bucket = self._naive(minute * 60.0)
        for approach in self.approaches.values():
            if approach.n_detect == 0 and approach.n_density == 0 and approach.acc_volume == 0:
                continue
            self.sink.add("metric", {
                "bucket_start": bucket, "bin_seconds": 60,
                "intersection_id": approach.intersection_id, "direction": approach.direction,
                "volume": approach.acc_volume,
                "avg_queue": round(approach.acc_queue / max(1, approach.n_detect), 2),
                "avg_wait_s": round(approach.acc_wait / max(1, approach.n_detect), 2),
                "avg_speed": round(approach.acc_speed / approach.n_speed, 2) if approach.n_speed else None,
                "density": round(approach.acc_density / max(1, approach.n_density), 2),
                "occupancy": round(approach.acc_occupancy / max(1, approach.n_occupancy), 2),
                "congestion_level": approach.acc_level, "source": "LIVE",
            })
            approach.acc_volume = 0
            approach.acc_queue = approach.acc_wait = approach.acc_density = approach.acc_occupancy = approach.acc_speed = 0.0
            approach.n_detect = approach.n_density = approach.n_occupancy = approach.n_speed = 0
            approach.acc_level = approach.level

    # ---- alerts and events ----------------------------------------------

    def raise_alert(
        self, category: str, severity: str, description: str, intersection_id: str | None, key: str
    ) -> None:
        with self.lock:
            if key in self.alerts:
                return
            stamp = self._naive(self.now) if self.now else utcnow()
            alert = {
                "id": short_id("ALT"), "category": category, "severity": severity,
                "description": description[:300], "intersection_id": intersection_id,
                "raised_at": stamp, "acknowledged": False, "resolved": False, "source": "LIVE",
            }
            self.alerts[key] = alert
            self.sink.add("alert", dict(alert))
            public = {**alert, "raised_at": iso_utc(stamp)}
            self.publish(topics.ALERTS, {**public, "simulated": True})
            self.notify("alert", public)
            self.event("ALERT", description, severity, intersection_id)

    def resolve_alert(self, key: str) -> None:
        with self.lock:
            alert = self.alerts.pop(key, None)
            if alert is None:
                return
            stamp = self._naive(self.now) if self.now else utcnow()
            self.sink.update("alert", alert["id"], {"resolved": True, "resolved_at": stamp})
            self.notify("alert_resolved", {"id": alert["id"]})

    def event(self, category: str, message: str, severity: str = "INFO", intersection_id: str | None = None) -> None:
        stamp = self._naive(self.now) if self.now else utcnow()
        record = {
            "occurred_at": stamp, "category": category, "severity": severity,
            "message": message[:300], "intersection_id": intersection_id,
        }
        self.sink.add("event", dict(record))
        public = {**record, "occurred_at": iso_utc(stamp)}
        self.recent_events.appendleft(public)
        self.notify("event", public)

    # ---- read models -----------------------------------------------------

    def network_metrics(self) -> dict:
        approaches = list(self.approaches.values())
        if not approaches:
            return {"density": 0.0, "queue": 0.0, "wait": 0.0, "speed": None, "volume": 0.0}
        speeds = [a.speed for a in approaches if a.speed is not None]
        queued = [a for a in approaches if a.queue > 0]
        return {
            "density": round(sum(a.density for a in approaches) / len(approaches), 1),
            "queue": round(sum(a.queue for a in approaches), 1),
            "wait": round(sum(a.avg_wait for a in queued) / len(queued), 1) if queued else 0.0,
            "speed": round(sum(speeds) / len(speeds), 2) if speeds else None,
            "volume": round(sum(a.last_count for a in approaches) * 720.0, 0),
        }

    def intersection_view(self, intersection: IntersectionLive) -> dict:
        state = intersection.state
        approaches = [self.approaches[(intersection.id, d)].public() for d in intersection.directions]
        worst = max((a["level"] for a in approaches), key=level_rank, default="LOW")
        scores = score_all(self.approach_inputs(intersection), self.config.algorithm) if self.now else ()
        return {
            "id": intersection.id,
            "name": intersection.name,
            "phase": state.phase if state else None,
            "active_direction": state.active_direction if state else None,
            "heads": state.heads if state else {},
            "elapsed": state.elapsed if state else 0,
            "planned_green": state.planned_green if state else 0,
            "remaining": state.remaining if state else 0,
            "mode": state.mode if state else "AUTOMATIC",
            "controller": intersection.controller,
            "preempt_vehicle": state.preempt_vehicle if state else None,
            "preempt_direction": state.preempt_direction if state else None,
            "approaches": approaches,
            "congestion": worst,
            "vehicles": intersection.vehicles,
            "active_sensors": intersection.active_sensors,
            "total_sensors": intersection.total_sensors,
            "last_decision": intersection.last_decision,
            "scores": [s.to_dict() for s in scores],
        }

    def sensor_view(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "id": s.id, "sensor_type": s.sensor_type, "intersection_id": s.intersection_id,
                    "direction": s.direction, "reporting_interval_s": s.interval_s, "enabled": s.enabled,
                    "status": s.status, "readings_received": s.received, "latest": s.latest,
                }
                for s in self.sensors.values()
            ]

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "intersections": [self.intersection_view(i) for i in self.intersections.values()],
                "network": self.network_metrics(),
                "active_alerts": [
                    {**a, "raised_at": iso_utc(a["raised_at"])} for a in self.alerts.values()
                ],
                "emergencies": [dict(e) for e in self.emergencies.values()],
                "sensors_online": sum(s.status == STATUS_ONLINE for s in self.sensors.values()),
                "sensors_total": len(self.sensors),
                "sensors_offline": sum(s.status == STATUS_OFFLINE for s in self.sensors.values()),
                "stats": dict(self.stats),
            }
