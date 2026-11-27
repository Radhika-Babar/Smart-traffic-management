"""Virtual IoT sensors.

Each sensor observes the live traffic on its road and reports through the bus
at its own interval, with a little measurement noise. No reading is random in
itself: it is the simulated traffic state seen through an imperfect instrument.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from app.mqtt import topics
from app.simulation.network import Link

ACTIVE = "ACTIVE"
FAILED = "FAILED"
DISABLED = "DISABLED"

JAM_SPACING_M = 7.5


@dataclass(frozen=True)
class SensorSpec:
    id: str
    sensor_type: str
    intersection_id: str
    direction: str | None
    interval_s: float
    enabled: bool = True


@dataclass
class Weather:
    condition: str = "CLEAR"
    temperature_c: float = 27.0
    rain_mm_h: float = 0.0
    visibility_m: float = 10000.0


class VirtualSensor:
    def __init__(self, spec: SensorSpec, link: Link | None, offset: float) -> None:
        self.id = spec.id
        self.sensor_type = spec.sensor_type
        self.intersection_id = spec.intersection_id
        self.direction = spec.direction
        self.interval_s = spec.interval_s
        self.link = link
        self.status = ACTIVE if spec.enabled else DISABLED
        self.fail_until: float | None = None
        self.next_report = offset
        self.seq = 0
        self.last_crossed = link.crossed_total if link else 0
        self.last_values: dict = {}

    def read(self, rng: random.Random, weather: Weather) -> dict:
        link = self.link
        if self.sensor_type == "ENVIRONMENTAL":
            return {
                "temperature_c": round(weather.temperature_c + rng.gauss(0, 0.2), 1),
                "rain_mm_h": round(max(0.0, weather.rain_mm_h + rng.gauss(0, 0.2 if weather.rain_mm_h else 0)), 1),
                "visibility_m": round(max(50.0, weather.visibility_m + rng.gauss(0, 40)), 0),
            }
        assert link is not None
        if self.sensor_type == "VEHICLE_DETECTION":
            on_link = len(link.vehicles)
            return {
                "queue_length": link.queue,
                "approaching": max(0, on_link - link.queue),
                "avg_wait_s": round(link.avg_wait, 1),
                "max_wait_s": round(link.max_wait, 1),
            }
        if self.sensor_type == "TRAFFIC_DENSITY":
            capacity = max(1.0, link.length / JAM_SPACING_M)
            density = 100.0 * len(link.vehicles) / capacity
            return {"traffic_density": round(min(100.0, max(0.0, density + rng.gauss(0, 1.0))), 1)}
        if self.sensor_type == "VEHICLE_COUNT":
            count = link.crossed_total - self.last_crossed
            self.last_crossed = link.crossed_total
            # Loop detectors occasionally miss a vehicle.
            missed = sum(rng.random() < 0.02 for _ in range(count))
            return {"vehicle_count": count - missed}
        if self.sensor_type == "ROAD_OCCUPANCY":
            mean = 100.0 * link.occupancy_sum / link.occupancy_ticks if link.occupancy_ticks else 0.0
            link.occupancy_sum = 0.0
            link.occupancy_ticks = 0
            return {"occupancy_percentage": round(min(100.0, max(0.0, mean + rng.gauss(0, 1.0))), 1)}
        if self.sensor_type == "AVERAGE_SPEED":
            value = None
            if link.speed_samples:
                value = round(max(0.0, link.speed_sum / link.speed_samples + rng.gauss(0, 0.2)), 2)
            link.speed_sum = 0.0
            link.speed_samples = 0
            return {"average_speed": value}
        raise ValueError(f"unknown sensor type {self.sensor_type}")


class SensorArray:
    def __init__(
        self,
        publish: Callable[[str, dict], None],
        timestamp: Callable[[], str],
        rng: random.Random,
        weather: Weather,
    ) -> None:
        self._publish = publish
        self._timestamp = timestamp
        self._rng = rng
        self.weather = weather
        self.sensors: dict[str, VirtualSensor] = {}
        # Mean simulated hours between spontaneous faults for one sensor. 0 disables them.
        self.mtbf_hours = 0.0

    def install(self, specs: list[SensorSpec], incoming: dict[tuple[str, str], Link]) -> None:
        self.sensors = {}
        for spec in specs:
            link = incoming.get((spec.intersection_id, spec.direction)) if spec.direction else None
            if link is None and spec.sensor_type != "ENVIRONMENTAL":
                continue
            # Spreading first reports over the interval avoids message bursts.
            offset = self._rng.uniform(0.5, spec.interval_s)
            self.sensors[spec.id] = VirtualSensor(spec, link, offset)

    def add(self, spec: SensorSpec, link: Link | None, now: float) -> None:
        self.sensors[spec.id] = VirtualSensor(spec, link, now + 1.0)

    def fail(self, sensor_id: str, now: float, duration: float) -> None:
        sensor = self.sensors[sensor_id]
        if sensor.status != DISABLED:
            sensor.status = FAILED
            sensor.fail_until = now + duration

    def restart(self, sensor_id: str, now: float) -> None:
        sensor = self.sensors[sensor_id]
        sensor.status = ACTIVE
        sensor.fail_until = None
        sensor.next_report = now + 0.5

    def set_enabled(self, sensor_id: str, enabled: bool, now: float) -> None:
        sensor = self.sensors[sensor_id]
        sensor.status = ACTIVE if enabled else DISABLED
        sensor.fail_until = None
        if enabled:
            sensor.next_report = now + 0.5

    def step(self, now: float, dt: float) -> None:
        fault_chance = dt / (self.mtbf_hours * 3600.0) if self.mtbf_hours > 0 else 0.0
        for sensor in self.sensors.values():
            if sensor.status == FAILED:
                if sensor.fail_until is not None and now >= sensor.fail_until:
                    self.restart(sensor.id, now)
                continue
            if sensor.status == DISABLED:
                continue
            if fault_chance and self._rng.random() < fault_chance:
                self.fail(sensor.id, now, self._rng.uniform(60, 240))
                continue
            if now >= sensor.next_report:
                sensor.next_report = now + sensor.interval_s
                self._report(sensor)

    def _report(self, sensor: VirtualSensor) -> None:
        values = sensor.read(self._rng, self.weather)
        sensor.seq += 1
        sensor.last_values = values
        self._publish(topics.sensor_readings(sensor.id), {
            "sensor_id": sensor.id,
            "sensor_type": sensor.sensor_type,
            "intersection_id": sensor.intersection_id,
            "direction": sensor.direction,
            "status": ACTIVE,
            "timestamp": self._timestamp(),
            "seq": sensor.seq,
            "simulated": True,
            **values,
        })
