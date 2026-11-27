"""Microscopic traffic simulation.

Vehicles follow a Krauss-style safe-speed car-following model along directed
links. The engine has no web, database or broker knowledge: it only receives a
`publish` callable. It can therefore run inside the backend, or headless.
"""

from __future__ import annotations

import logging
import math
import random
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.algorithms.signal_fsm import ControlMode, SignalTiming
from app.core.clock import local_hour_and_weekday
from app.mqtt import topics
from app.schemas.messages import SignalCommand
from app.simulation.demand import demand_factor
from app.simulation.field_controller import FieldController
from app.simulation.network import IntersectionSpec, Link, Network, build_network
from app.simulation.scenarios import SCENARIOS, Scenario, ScenarioAction
from app.simulation.sensors import ACTIVE, SensorArray, SensorSpec, Weather

logger = logging.getLogger(__name__)

ACCEL = 2.0
BRAKE = 3.5
MIN_GAP = 1.5
TURN_SPEED = 8.0
STOPPED_SPEED = 1.0
QUEUE_SPEED = 1.5
OCCUPANCY_ZONE_M = 40.0
MAX_BACKLOG = 25.0


@dataclass(frozen=True)
class Category:
    key: str
    length: float
    speed_factor: float
    share: float


DEFAULT_CATEGORIES: tuple[Category, ...] = (
    Category("CAR", 4.5, 1.0, 0.68),
    Category("MOTORCYCLE", 2.2, 1.05, 0.14),
    Category("BUS", 11.0, 0.85, 0.08),
    Category("TRUCK", 9.0, 0.8, 0.10),
)
EMERGENCY_TYPES: dict[str, tuple[float, float]] = {
    "AMBULANCE": (6.0, 1.3),
    "FIRE_ENGINE": (9.0, 1.2),
    "POLICE": (5.0, 1.35),
}

# type -> (duration, zone start, zone end as fractions of road length, speed factor, blocking, lanes, severity)
INCIDENT_PROFILES: dict[str, tuple[float, float, float, float, bool, str, str]] = {
    "MINOR_ACCIDENT": (180, 0.45, 0.65, 0.25, False, "Partial lane obstruction", "MINOR"),
    "MAJOR_ACCIDENT": (300, 0.60, 0.65, 0.0, True, "Lane fully blocked", "MAJOR"),
    "ROAD_BLOCKAGE": (240, 0.60, 0.65, 0.0, True, "Lane fully blocked", "MAJOR"),
    "SUDDEN_SLOWDOWN": (120, 0.10, 0.95, 0.3, False, "Whole approach slowed", "MINOR"),
    "LANE_CLOSURE": (240, 0.30, 0.80, 0.4, False, "Lane capacity reduced", "MINOR"),
}

WEATHER_PRESETS: dict[str, tuple[float, float, float, float]] = {
    # condition -> (speed factor, temperature, rain mm/h, visibility m)
    "CLEAR": (1.0, 27.0, 0.0, 10000.0),
    "RAIN": (0.78, 22.0, 9.0, 1800.0),
}


class Vehicle:
    __slots__ = (
        "id", "category", "length", "speed_factor", "emergency", "link", "pos", "speed", "next_link",
        "link_wait", "total_wait", "stops", "was_stopped", "spawned_at", "free_time",
        "box", "box_s", "route", "route_index", "path", "status",
    )

    def __init__(self, vehicle_id: str, category: str, length: float, speed_factor: float, spawned_at: float) -> None:
        self.id = vehicle_id
        self.category = category
        self.length = length
        self.speed_factor = speed_factor
        self.emergency = False
        self.link: Link | None = None
        self.pos = 0.0
        self.speed = 0.0
        self.next_link: Link | None = None
        self.link_wait = 0.0
        self.total_wait = 0.0
        self.stops = 0
        self.was_stopped = False
        self.spawned_at = spawned_at
        self.free_time = 0.0
        self.box: tuple | None = None
        self.box_s = 0.0
        self.route: list[Link] = []
        self.route_index = 0
        self.path: list[str] = []
        self.status = "EN_ROUTE"


@dataclass
class ActiveIncident:
    id: str
    incident_type: str
    intersection_id: str
    direction: str
    link: Link
    start: float
    end: float
    speed_factor: float
    blocking: bool
    affected_lanes: str
    severity: str
    expires_at: float


@dataclass
class Params:
    volume_factor: float = 1.0
    spawn_rate_vph: float | None = None
    intersection_factors: dict[str, float] = field(default_factory=dict)


class TrafficEngine:
    def __init__(
        self,
        specs: list[IntersectionSpec],
        sensor_specs: list[SensorSpec],
        timings: dict[str, SignalTiming],
        publish: Callable[[str, dict], None],
        modes: dict[str, ControlMode] | None = None,
        categories: tuple[Category, ...] = DEFAULT_CATEGORIES,
        seed: int | None = None,
        dt: float = 0.5,
        epoch: float | None = None,
        trip_sink: Callable[[dict], None] | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.dt = dt
        self.rng = random.Random(seed)
        self._outbound = publish
        self._muted = False
        self.categories = categories
        self._category_weights = [c.share for c in categories]
        self.trip_sink = trip_sink
        self.epoch = epoch if epoch is not None else datetime.now(UTC).timestamp()
        self.params = Params()
        self.weather = Weather()
        self.weather_speed = 1.0
        self.scenario: Scenario = SCENARIOS["NORMAL"]
        self.expect_commands = True
        self._vehicle_serial = 0
        self._emergency_serial = 0
        self._incident_serial = 0
        self._build(specs, sensor_specs, timings, modes or {})

    # ---- lifecycle -------------------------------------------------------

    def _build(
        self,
        specs: list[IntersectionSpec],
        sensor_specs: list[SensorSpec],
        timings: dict[str, SignalTiming],
        modes: dict[str, ControlMode],
    ) -> None:
        self.time = 0.0
        self._timestamp = ""
        self._refresh_timestamp()
        self.specs = specs
        self.sensor_specs = sensor_specs
        self.network: Network = build_network(specs)
        self.heads: dict[tuple[str, str], str] = {}
        self.boxes: list[Vehicle] = []
        self.emergencies: dict[str, Vehicle] = {}
        self.incidents: dict[str, ActiveIncident] = {}
        self.pending_actions: list[tuple[float, ScenarioAction]] = []
        self.completed_trips = 0
        self.spawned_total = 0
        self.recent_trips: deque[tuple[float, float, float]] = deque(maxlen=300)
        self._next_status = 5.0
        self._next_telemetry = 1.0
        self.controllers: dict[str, FieldController] = {}
        for spec in specs:
            directions = tuple(d for d in spec.directions if (spec.id, d) in self.network.incoming)
            controller = FieldController(
                spec.id, directions or spec.directions, timings.get(spec.id, SignalTiming()), self._publish,
                lambda: self._timestamp, modes.get(spec.id, ControlMode.AUTOMATIC),
            )
            controller.expect_commands = self.expect_commands
            self.controllers[spec.id] = controller
        self.sensors = SensorArray(self._publish, lambda: self._timestamp, self.rng, self.weather)
        self.sensors.install(sensor_specs, self.network.incoming)

    def _publish(self, topic: str, payload: dict) -> None:
        if not self._muted:
            self._outbound(topic, payload)

    def warm_up(self, seconds: float) -> None:
        """Fill the empty roads before going live. Nothing is published or recorded.

        Signals run their local rotation meanwhile, and the city clock ends where
        it started, so warm-up leaves no trace in any stored history.
        """
        with self.lock:
            sink, self.trip_sink = self.trip_sink, None
            self._muted = True
            start = self.sim_epoch_now
            for _ in range(round(seconds / self.dt)):
                self.step()
            self._muted = False
            self.trip_sink = sink
            self.epoch = start - self.time
            self.completed_trips = 0
            self.recent_trips.clear()
            self._refresh_timestamp()
            for controller in self.controllers.values():
                controller.last_command_at = controller.fsm.clock

    def reset(
        self,
        specs: list[IntersectionSpec] | None = None,
        sensor_specs: list[SensorSpec] | None = None,
        timings: dict[str, SignalTiming] | None = None,
        modes: dict[str, ControlMode] | None = None,
    ) -> None:
        """Drop all traffic and start again. The city clock keeps moving forward."""
        with self.lock:
            self.epoch += self.time
            self.scenario = SCENARIOS["NORMAL"]
            self.params.volume_factor = 1.0
            self.params.spawn_rate_vph = None
            self.params.intersection_factors = {}
            self.set_weather("CLEAR")
            current_timings = {k: c.fsm.timing for k, c in self.controllers.items()}
            current_modes = {k: c.fsm.mode for k, c in self.controllers.items()}
            self._build(
                specs or self.specs, sensor_specs or self.sensor_specs,
                timings or current_timings, modes or current_modes,
            )

    @property
    def sim_epoch_now(self) -> float:
        return self.epoch + self.time

    def _refresh_timestamp(self) -> None:
        self._timestamp = datetime.fromtimestamp(self.epoch + self.time, tz=UTC).isoformat()

    def _event(self, event: str, **data) -> None:
        self._publish(topics.SIMULATION_EVENTS, {"event": event, "timestamp": self._timestamp, "simulated": True, **data})

    # ---- bus input -------------------------------------------------------

    def on_command(self, topic: str, payload: dict) -> None:
        """Field gateway handler. Runs on a bus thread, so it only queues."""
        try:
            command = SignalCommand.model_validate(payload)
        except ValueError as error:
            logger.warning("Dropped invalid signal command on %s: %s", topic, error)
            return
        controller = self.controllers.get(command.intersection_id)
        if controller is not None:
            controller.enqueue(command.model_dump(mode="json"))

    # ---- stepping --------------------------------------------------------

    def step(self) -> None:
        with self.lock:
            dt = self.dt
            self.time += dt
            self._refresh_timestamp()
            self._run_actions()
            self._spawn(dt)
            heads = self.heads
            for intersection_id, controller in self.controllers.items():
                controller.tick(dt)
                for direction, aspect in controller.fsm.heads().items():
                    heads[(intersection_id, direction)] = aspect
            for link in self.network.links:
                self._advance_link(link, dt)
            self._advance_boxes(dt)
            self._check_arrivals()
            self.sensors.step(self.time, dt)
            self._expire_incidents()
            if self.time >= self._next_telemetry:
                self._next_telemetry = self.time + 1.0
                self._emergency_telemetry()
            if self.time >= self._next_status:
                self._next_status = self.time + 5.0
                self._publish_status()

    def _demand_scale(self, link: Link) -> float:
        scenario = self.scenario
        scale = scenario.demand_multiplier * self.params.volume_factor
        if scenario.follow_clock:
            hour, weekday = local_hour_and_weekday(self.sim_epoch_now)
            scale *= demand_factor(hour, weekday)
        scale *= scenario.direction_bias.get(link.approach or "", 1.0)
        scale *= self.params.intersection_factors.get(link.to_intersection or "", 1.0)
        return scale

    def _spawn(self, dt: float) -> None:
        for link in self.network.entries:
            base = self.params.spawn_rate_vph if self.params.spawn_rate_vph is not None else link.demand_vph
            rate = base * self._demand_scale(link) / 3600.0
            if self.rng.random() < rate * dt:
                link.backlog = min(MAX_BACKLOG, link.backlog + 1)
            if link.backlog >= 1 and self._has_entry_room(link):
                link.backlog -= 1
                self._spawn_vehicle(link)

    @staticmethod
    def _has_entry_room(link: Link) -> bool:
        if not link.vehicles:
            return True
        tail = link.vehicles[-1]
        return tail.pos - tail.length > 8.0

    def _spawn_vehicle(self, link: Link) -> None:
        category = self.rng.choices(self.categories, self._category_weights)[0]
        self._vehicle_serial += 1
        vehicle = Vehicle(f"V{self._vehicle_serial}", category.key, category.length, category.speed_factor, self.time)
        vehicle.speed = min(link.speed_limit * category.speed_factor, 9.0)
        self._enter_link(vehicle, link, 0.0)
        self.spawned_total += 1

    def _enter_link(self, vehicle: Vehicle, link: Link, pos: float) -> None:
        vehicle.link = link
        vehicle.pos = pos
        vehicle.link_wait = 0.0
        vehicle.free_time += link.length / link.speed_limit
        if vehicle.emergency:
            vehicle.next_link = (
                vehicle.route[vehicle.route_index + 1] if vehicle.route_index + 1 < len(vehicle.route) else None
            )
            link.vehicles.append(vehicle)
            link.vehicles.sort(key=lambda v: v.pos, reverse=True)
            return
        vehicle.next_link = self._choose_turn(link)
        link.vehicles.append(vehicle)

    def _choose_turn(self, link: Link) -> Link | None:
        if not link.turns:
            return None
        if len(link.turns) == 1:
            return link.turns[0][0]
        outs = [t[0] for t in link.turns]
        weights = [t[1] for t in link.turns]
        return self.rng.choices(outs, weights)[0]

    @staticmethod
    def _has_exit_room(link: Link, vehicle: Vehicle) -> bool:
        needed = vehicle.length + 2.0 + link.reserved_m
        if not link.vehicles:
            return link.length > needed
        tail = link.vehicles[-1]
        return tail.pos - tail.length >= needed

    def _may_enter(self, vehicle: Vehicle, aspect: str | None, distance: float) -> bool:
        if aspect == "GREEN":
            pass
        elif aspect == "YELLOW":
            # Past the point of no return, stopping would be the unsafe choice.
            if vehicle.speed * vehicle.speed / (2 * BRAKE) <= distance:
                return False
        else:
            return False
        if vehicle.emergency or vehicle.next_link is None:
            return True
        return self._has_exit_room(vehicle.next_link, vehicle)

    def _advance_link(self, link: Link, dt: float) -> None:
        vehicles = link.vehicles
        link.occupancy_ticks += 1
        if not vehicles:
            link.queue = 0
            link.avg_wait = 0.0
            link.max_wait = 0.0
            return
        aspect = self.heads.get((link.to_intersection, link.approach)) if link.to_intersection else None
        controlled = link.to_intersection is not None
        limit = link.speed_limit * self.weather_speed
        length = link.length
        zone_start = length - OCCUPANCY_ZONE_M
        speed_low, speed_high = length * 0.35, length * 0.7
        incidents = link.incidents
        queue = 0
        wait_sum = 0.0
        max_wait = 0.0
        covered = 0.0
        leader: Vehicle | None = None
        brake_dt = BRAKE * dt

        for index, vehicle in enumerate(vehicles):
            desired = limit * vehicle.speed_factor
            gap = math.inf
            lead_speed = 0.0
            emergency = vehicle.emergency
            pos = vehicle.pos
            if leader is not None and not emergency:
                gap = leader.pos - leader.length - pos
                lead_speed = leader.speed
            for incident in incidents:
                if incident.start <= pos <= incident.end and not emergency:
                    desired = min(desired, limit * incident.speed_factor) if incident.speed_factor > 0 else desired
                if incident.blocking and not emergency and pos < incident.start:
                    distance = incident.start - pos - 1.0
                    if distance < gap:
                        gap = distance
                        lead_speed = 0.0
            if controlled and (index == 0 or emergency):
                distance = length - pos
                if not self._may_enter(vehicle, aspect, distance) and distance - 1.0 < gap:
                    gap = distance - 1.0
                    lead_speed = 0.0
            speed = vehicle.speed + ACCEL * dt
            if speed > desired:
                speed = max(desired, vehicle.speed - BRAKE * dt)
            if gap < math.inf:
                free = gap - MIN_GAP
                safe = 0.0 if free <= 0 else -brake_dt + math.sqrt(
                    brake_dt * brake_dt + lead_speed * lead_speed + 2 * BRAKE * free
                )
                if speed > safe:
                    speed = safe
            vehicle.speed = speed
            vehicle.pos = pos = pos + speed * dt

            if speed < STOPPED_SPEED:
                vehicle.link_wait += dt
                vehicle.total_wait += dt
                if not vehicle.was_stopped:
                    vehicle.was_stopped = True
                    vehicle.stops += 1
            else:
                vehicle.was_stopped = False
            if speed < QUEUE_SPEED:
                queue += 1
                wait_sum += vehicle.link_wait
                if vehicle.link_wait > max_wait:
                    max_wait = vehicle.link_wait
            if pos >= zone_start:
                covered += vehicle.length
            if speed_low <= pos <= speed_high:
                link.speed_sum += speed
                link.speed_samples += 1
            leader = vehicle

        link.queue = queue
        link.avg_wait = wait_sum / queue if queue else 0.0
        link.max_wait = max_wait
        link.occupancy_sum += min(1.0, covered / OCCUPANCY_ZONE_M)

        while vehicles and vehicles[0].pos >= length:
            vehicle = vehicles.pop(0)
            link.crossed_total += 1
            if vehicle.next_link is not None:
                self._enter_box(vehicle, link)
            elif vehicle.emergency:
                self._arrive(vehicle)
            else:
                self._finish_trip(vehicle)

    def _enter_box(self, vehicle: Vehicle, link: Link) -> None:
        out = vehicle.next_link
        out.reserved_m += vehicle.length + 2.0
        p0, p2 = link.end, out.start
        if abs(link.ux * out.ux + link.uy * out.uy) > 0.5:
            control = ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2)
        elif abs(link.ux) > 0.5:
            control = (p2[0], p0[1])
        else:
            control = (p0[0], p2[1])
        span = math.hypot(control[0] - p0[0], control[1] - p0[1]) + math.hypot(p2[0] - control[0], p2[1] - control[1])
        vehicle.box = (p0, control, p2, span * 0.9, out)
        vehicle.box_s = 0.0
        vehicle.link = None
        if vehicle.emergency:
            vehicle.route_index += 1
        self.boxes.append(vehicle)

    def _advance_boxes(self, dt: float) -> None:
        remaining: list[Vehicle] = []
        for vehicle in self.boxes:
            vehicle.speed = max(vehicle.speed, min(TURN_SPEED, vehicle.speed + ACCEL * dt))
            vehicle.box_s += vehicle.speed * dt
            out: Link = vehicle.box[4]
            if vehicle.box_s >= vehicle.box[3]:
                tail = out.vehicles[-1] if out.vehicles else None
                if tail is not None and tail.pos - tail.length < MIN_GAP and not vehicle.emergency:
                    # The vehicle ahead has not cleared the road entrance yet.
                    vehicle.box_s = vehicle.box[3]
                    vehicle.speed = 0.0
                    remaining.append(vehicle)
                    continue
                out.reserved_m -= vehicle.length + 2.0
                vehicle.box = None
                self._enter_link(vehicle, out, 0.0)
            else:
                remaining.append(vehicle)
        self.boxes = remaining

    def _finish_trip(self, vehicle: Vehicle) -> None:
        travel = self.time - vehicle.spawned_at
        self.completed_trips += 1
        self.recent_trips.append((travel, vehicle.free_time, vehicle.total_wait))
        if self.trip_sink is not None:
            self.trip_sink({
                "vehicle_ref": vehicle.id,
                "category": vehicle.category,
                "travel_time_s": round(travel, 1),
                "wait_time_s": round(vehicle.total_wait, 1),
                "stops": vehicle.stops,
                "free_flow_time_s": round(vehicle.free_time, 1),
                "completed_at": self._timestamp,
            })

    # ---- emergencies -----------------------------------------------------

    def dispatch_emergency(self, vehicle_type: str, origin: str, destination: str) -> dict:
        with self.lock:
            if vehicle_type not in EMERGENCY_TYPES:
                raise ValueError(f"unknown emergency vehicle type {vehicle_type}")
            if origin == destination:
                raise ValueError("origin and destination must differ")
            path = self.network.shortest_path(origin, destination)
            if not path:
                raise ValueError(f"no route from {origin} to {destination}")
            middle = self.network.links_between(path)
            first = self._straight_feeder(origin, middle[0])
            last = self._straight_exit(middle[-1])
            route = [first, *middle, *([last] if last else [])]
            self._emergency_serial += 1
            prefix = {"AMBULANCE": "AMB", "FIRE_ENGINE": "FIR", "POLICE": "POL"}[vehicle_type]
            stamp = int(self.sim_epoch_now) % 100000
            vehicle_id = f"{prefix}-{stamp:05d}{self._emergency_serial}"
            length, factor = EMERGENCY_TYPES[vehicle_type]
            vehicle = Vehicle(vehicle_id, vehicle_type, length, factor, self.time)
            vehicle.emergency = True
            vehicle.route = route
            vehicle.path = path
            vehicle.speed = 8.0
            self._enter_link(vehicle, first, max(0.0, first.length - 230.0))
            self.emergencies[vehicle_id] = vehicle
            details = {
                "vehicle_id": vehicle_id, "vehicle_type": vehicle_type, "origin": origin,
                "destination": destination, "route": path,
            }
            self._event("EMERGENCY_DISPATCHED", **details)
            return details

    def _straight_feeder(self, intersection: str, out: Link) -> Link:
        feeders = [
            link for (node, _), link in self.network.incoming.items()
            if node == intersection and any(t[0] is out for t in link.turns)
        ]
        if not feeders:
            raise ValueError(f"no approach leads into {intersection}")
        straight = [f for f in feeders if any(t[0] is out and t[2] == "STRAIGHT" for t in f.turns)]
        return (straight or feeders)[0]

    @staticmethod
    def _straight_exit(link: Link) -> Link | None:
        for out, _, kind in link.turns:
            if kind == "STRAIGHT":
                return out
        return link.turns[0][0] if link.turns else None

    def cancel_emergency(self, vehicle_id: str) -> None:
        with self.lock:
            vehicle = self.emergencies.get(vehicle_id)
            if vehicle is None:
                raise KeyError(vehicle_id)
            if vehicle.link is not None:
                vehicle.link.vehicles.remove(vehicle)
            else:
                vehicle.box[4].reserved_m -= vehicle.length + 2.0
                self.boxes.remove(vehicle)
            vehicle.status = "CANCELLED"
            self._send_emergency(vehicle)
            del self.emergencies[vehicle_id]

    def _check_arrivals(self) -> None:
        for vehicle in list(self.emergencies.values()):
            link = vehicle.link
            if link is None or vehicle.route_index < len(vehicle.route) - 1:
                continue
            if link.to_intersection is None and vehicle.pos >= min(60.0, link.length * 0.5):
                link.vehicles.remove(vehicle)
                self._arrive(vehicle)

    def _arrive(self, vehicle: Vehicle) -> None:
        vehicle.status = "ARRIVED"
        self._send_emergency(vehicle)
        self.emergencies.pop(vehicle.id, None)
        self._event(
            "EMERGENCY_ARRIVED", vehicle_id=vehicle.id, travel_time_s=round(self.time - vehicle.spawned_at, 1),
            wait_time_s=round(vehicle.total_wait, 1), stops=vehicle.stops,
        )

    def _emergency_telemetry(self) -> None:
        for vehicle in list(self.emergencies.values()):
            self._send_emergency(vehicle)

    def _send_emergency(self, vehicle: Vehicle) -> None:
        x, y, _ = self._position(vehicle)
        next_intersection = approach = distance = eta = None
        link = vehicle.link if vehicle.link is not None else (vehicle.box[4] if vehicle.box else None)
        if link is not None and link.to_intersection is not None and vehicle.status == "EN_ROUTE":
            next_intersection, approach = link.to_intersection, link.approach
            distance = max(0.0, link.length - (vehicle.pos if vehicle.link is not None else 0.0))
            eta = distance / max(vehicle.speed, 8.0)
        self._publish(topics.emergency(vehicle.id), {
            "vehicle_id": vehicle.id,
            "vehicle_type": vehicle.category,
            "status": vehicle.status,
            "x": round(x, 1),
            "y": round(y, 1),
            "speed": round(vehicle.speed, 1),
            "next_intersection": next_intersection,
            "approach": approach,
            "distance_m": round(distance, 1) if distance is not None else None,
            "eta_s": round(eta, 1) if eta is not None else None,
            "route": vehicle.path,
            "route_index": vehicle.route_index,
            "timestamp": self._timestamp,
            "simulated": True,
        })

    # ---- incidents -------------------------------------------------------

    def inject_incident(
        self, incident_type: str, intersection_id: str, direction: str, duration_s: float | None = None
    ) -> dict:
        with self.lock:
            profile = INCIDENT_PROFILES.get(incident_type)
            if profile is None:
                raise ValueError(f"unknown incident type {incident_type}")
            link = self.network.incoming.get((intersection_id, direction))
            if link is None:
                raise ValueError(f"{intersection_id} has no {direction} approach")
            default_duration, start, end, factor, blocking, lanes, severity = profile
            duration = duration_s or default_duration
            self._incident_serial += 1
            incident = ActiveIncident(
                f"INC-{int(self.sim_epoch_now) % 100000:05d}{self._incident_serial}", incident_type, intersection_id,
                direction, link, link.length * start, link.length * end, factor, blocking, lanes, severity,
                self.time + duration,
            )
            link.incidents.append(incident)
            self.incidents[incident.id] = incident
            details = {
                "incident_id": incident.id, "incident_type": incident_type, "intersection_id": intersection_id,
                "direction": direction, "severity": severity, "affected_lanes": lanes, "duration_s": duration,
            }
            self._event("INCIDENT_INJECTED", **details)
            return details

    def clear_incident(self, incident_id: str) -> None:
        with self.lock:
            incident = self.incidents.pop(incident_id, None)
            if incident is None:
                raise KeyError(incident_id)
            incident.link.incidents.remove(incident)
            self._event("INCIDENT_CLEARED", incident_id=incident_id, intersection_id=incident.intersection_id)

    def _expire_incidents(self) -> None:
        for incident_id, incident in list(self.incidents.items()):
            if self.time >= incident.expires_at:
                self.clear_incident(incident_id)

    # ---- controls --------------------------------------------------------

    def set_weather(self, condition: str) -> None:
        with self.lock:
            if condition not in WEATHER_PRESETS:
                raise ValueError(f"unknown weather {condition}")
            speed, temperature, rain, visibility = WEATHER_PRESETS[condition]
            self.weather_speed = speed
            self.weather.condition = condition
            self.weather.temperature_c = temperature
            self.weather.rain_mm_h = rain
            self.weather.visibility_m = visibility

    def apply_scenario(self, scenario: Scenario) -> None:
        with self.lock:
            self.scenario = scenario
            self.set_weather(scenario.weather)
            self.pending_actions = sorted(
                ((self.time + a.delay_s, a) for a in scenario.actions), key=lambda item: item[0]
            )
            self._event("SCENARIO_APPLIED", key=scenario.key, name=scenario.name, description=scenario.description)

    def _run_actions(self) -> None:
        while self.pending_actions and self.pending_actions[0][0] <= self.time:
            _, action = self.pending_actions.pop(0)
            try:
                if action.kind == "EMERGENCY":
                    self.dispatch_emergency(**action.params)
                elif action.kind == "INCIDENT":
                    self.inject_incident(**action.params)
                elif action.kind == "SENSOR_FAILURE":
                    self.fail_sensors(**action.params)
            except (ValueError, KeyError) as error:
                logger.warning("Scenario action %s skipped: %s", action.kind, error)

    def fail_sensors(
        self, intersection_id: str | None = None, direction: str | None = None, count: int = 1, duration_s: float = 180
    ) -> list[str]:
        with self.lock:
            candidates = [
                s for s in self.sensors.sensors.values()
                if s.status == ACTIVE
                and (intersection_id is None or s.intersection_id == intersection_id)
                and (direction is None or s.direction == direction)
            ]
            chosen = self.rng.sample(candidates, min(count, len(candidates)))
            for sensor in chosen:
                self.sensors.fail(sensor.id, self.time, duration_s)
            if chosen:
                self._event("SENSOR_FAILURE_INJECTED", sensors=[s.id for s in chosen], duration_s=duration_s)
            return [s.id for s in chosen]

    def add_sensor(self, spec: SensorSpec) -> None:
        with self.lock:
            self.sensor_specs = [*self.sensor_specs, spec]
            link = self.network.incoming.get((spec.intersection_id, spec.direction)) if spec.direction else None
            self.sensors.add(spec, link, self.time)

    def restart_sensor(self, sensor_id: str) -> None:
        with self.lock:
            self.sensors.restart(sensor_id, self.time)

    def set_sensor_enabled(self, sensor_id: str, enabled: bool) -> None:
        with self.lock:
            self.sensors.set_enabled(sensor_id, enabled, self.time)

    def set_sensor_interval(self, sensor_id: str, interval_s: float) -> None:
        with self.lock:
            self.sensors.sensors[sensor_id].interval_s = interval_s

    def set_demand(self, intersection_id: str, direction: str, demand_vph: float) -> None:
        with self.lock:
            link = self.network.incoming.get((intersection_id, direction))
            if link is not None and link.is_entry:
                link.demand_vph = demand_vph

    def set_timing(self, intersection_id: str, timing: SignalTiming) -> None:
        with self.lock:
            self.controllers[intersection_id].fsm.set_timing(timing)

    def fail_controller(self, intersection_id: str, duration_s: float) -> None:
        with self.lock:
            self.controllers[intersection_id].fail(duration_s)
            self._event("CONTROLLER_FAILURE_INJECTED", intersection_id=intersection_id, duration_s=duration_s)

    def set_expect_commands(self, expected: bool) -> None:
        with self.lock:
            self.expect_commands = expected
            for controller in self.controllers.values():
                controller.expect_commands = expected

    # ---- observation -----------------------------------------------------

    def _publish_status(self) -> None:
        counts: dict[str, list[int]] = {}
        for sensor in self.sensors.sensors.values():
            bucket = counts.setdefault(sensor.intersection_id, [0, 0])
            bucket[1] += 1
            bucket[0] += sensor.status == ACTIVE
        for intersection_id, controller in self.controllers.items():
            on_roads = sum(
                len(link.vehicles) for (node, _), link in self.network.incoming.items() if node == intersection_id
            )
            active, total = counts.get(intersection_id, [0, 0])
            self._publish(topics.intersection_status(intersection_id), {
                "intersection_id": intersection_id,
                "vehicles_on_approaches": on_roads,
                "active_sensors": active,
                "total_sensors": total,
                "controller": controller.status,
                "timestamp": self._timestamp,
                "simulated": True,
            })

    @staticmethod
    def _position(vehicle: Vehicle) -> tuple[float, float, float]:
        if vehicle.box is not None:
            p0, c, p2, span, _ = vehicle.box
            t = min(1.0, vehicle.box_s / span)
            u = 1 - t
            x = u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0]
            y = u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]
            heading = math.atan2(
                2 * u * (c[1] - p0[1]) + 2 * t * (p2[1] - c[1]),
                2 * u * (c[0] - p0[0]) + 2 * t * (p2[0] - c[0]),
            )
            return x, y, heading
        link = vehicle.link
        x, y = link.point_at(vehicle.pos)
        return x, y, link.heading

    def vehicle_count(self) -> int:
        return sum(len(link.vehicles) for link in self.network.links) + len(self.boxes)

    def frame(self) -> dict:
        """Compact view for the live map. This is visualisation, not sensor data."""
        with self.lock:
            rows = []
            counts: dict[str, int] = {}
            for link in self.network.links:
                for vehicle in link.vehicles:
                    x, y, heading = self._position(vehicle)
                    rows.append([vehicle.id, round(x, 1), round(y, 1), round(heading, 2), vehicle.category,
                                 round(vehicle.speed, 1)])
                    counts[vehicle.category] = counts.get(vehicle.category, 0) + 1
            for vehicle in self.boxes:
                x, y, heading = self._position(vehicle)
                rows.append([vehicle.id, round(x, 1), round(y, 1), round(heading, 2), vehicle.category,
                             round(vehicle.speed, 1)])
                counts[vehicle.category] = counts.get(vehicle.category, 0) + 1
            signals = {
                key: {"phase": c.fsm.phase.value, "heads": c.fsm.heads(), "active": c.fsm.active,
                      "elapsed": round(c.fsm.elapsed, 1), "remaining": round(c.fsm.remaining, 1),
                      "mode": c.fsm.mode.value, "preempt": c.fsm.preempt.vehicle_id if c.fsm.preempt else None,
                      "status": c.status}
                for key, c in self.controllers.items()
            }
            return {
                "time": round(self.time, 1),
                "clock": self._timestamp,
                "vehicles": rows,
                "counts": counts,
                "signals": signals,
                "incidents": [
                    {"id": i.id, "type": i.incident_type, "intersection_id": i.intersection_id,
                     "direction": i.direction, "x": round(i.link.point_at(i.start)[0], 1),
                     "y": round(i.link.point_at(i.start)[1], 1)}
                    for i in self.incidents.values()
                ],
            }

    def topology(self) -> dict:
        """Static geometry for drawing the map."""
        with self.lock:
            return {
                "intersections": [
                    {"id": s.id, "name": s.name, "x": self.network.positions[s.id][0],
                     "y": self.network.positions[s.id][1], "directions": list(s.directions)}
                    for s in self.specs
                ],
                "links": [
                    {"id": link.id, "x1": link.start[0], "y1": link.start[1], "x2": link.end[0], "y2": link.end[1],
                     "to_intersection": link.to_intersection, "approach": link.approach}
                    for link in self.network.links
                ],
            }

    def performance(self) -> dict:
        with self.lock:
            trips = self.recent_trips
            if trips:
                total_travel = sum(t[0] for t in trips)
                efficiency = 100.0 * sum(t[1] for t in trips) / total_travel if total_travel else None
                average_wait = sum(t[2] for t in trips) / len(trips)
                average_travel = total_travel / len(trips)
            else:
                efficiency = average_wait = average_travel = None
            return {
                "vehicles": self.vehicle_count(),
                "spawned_total": self.spawned_total,
                "completed_trips": self.completed_trips,
                "flow_efficiency": round(min(efficiency, 100.0), 1) if efficiency is not None else None,
                "average_trip_wait_s": round(average_wait, 1) if average_wait is not None else None,
                "average_travel_time_s": round(average_travel, 1) if average_travel is not None else None,
                "active_emergencies": len(self.emergencies),
                "active_incidents": len(self.incidents),
                "weather": self.weather.condition,
                "scenario": self.scenario.key,
                "scenario_name": self.scenario.name,
            }
