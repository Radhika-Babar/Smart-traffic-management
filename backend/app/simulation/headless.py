"""Run the whole control loop with no web server, database or broker.

    python -m app.simulation.headless --scenario MORNING_RUSH --minutes 30
    python -m app.simulation.headless --compare --minutes 30 --seed 7

Sensors, field controllers and the central algorithm still talk only through the
message bus, here in synchronous local mode so runs are fast and reproducible.
`--compare` runs adaptive and fixed-time control on identical demand.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from app.mqtt import topics
from app.mqtt.bus import BusEndpoint, LocalHub
from app.services.processing import SensorLive, TrafficProcessor
from app.services.runtime_config import RuntimeConfig
from app.simulation.defaults import default_sensor_specs, default_specs
from app.simulation.engine import TrafficEngine
from app.simulation.scenarios import SCENARIOS


@dataclass
class Pipeline:
    engine: TrafficEngine
    processor: TrafficProcessor
    field: BusEndpoint
    backend: BusEndpoint

    def run(self, seconds: float) -> None:
        for _ in range(round(seconds / self.engine.dt)):
            self.engine.step()


def build_pipeline(seed: int | None = 1, strategy: str = "ADAPTIVE", trip_sink=None, sink=None) -> Pipeline:
    """Wire engine and processor together over a synchronous local bus."""
    hub = LocalHub(synchronous=True)
    specs = default_specs()
    sensor_specs = default_sensor_specs()
    holder: dict = {}

    field = BusEndpoint("field", hub, topics.FIELD_SUBSCRIPTIONS, lambda t, p: holder["engine"].on_command(t, p))
    backend = BusEndpoint("backend", hub, topics.BACKEND_SUBSCRIPTIONS, lambda t, p: holder["processor"].handle(t, p))
    engine = TrafficEngine(specs, sensor_specs, {}, field.publish, seed=seed, trip_sink=trip_sink)
    engine.set_expect_commands(strategy == "ADAPTIVE")
    sensors = [
        SensorLive(s.id, s.sensor_type, s.intersection_id, s.direction, s.interval_s) for s in sensor_specs
    ]
    processor = TrafficProcessor(
        [(s.id, s.name, s.directions, s.id.replace("INT", "SIG")) for s in specs],
        sensors, RuntimeConfig(strategy=strategy), backend.publish, sink,
    )
    holder["engine"] = engine
    holder["processor"] = processor
    return Pipeline(engine, processor, field, backend)


def run_case(scenario: str, minutes: float, seed: int, strategy: str, warmup_s: float = 300.0) -> dict:
    trips: list[dict] = []
    pipeline = build_pipeline(seed, strategy, trips.append)
    pipeline.engine.apply_scenario(SCENARIOS[scenario])
    pipeline.run(warmup_s)
    trips.clear()
    pipeline.run(minutes * 60)
    count = len(trips)
    mean = lambda key: round(sum(t[key] for t in trips) / count, 1) if count else None  # noqa: E731
    return {
        "strategy": strategy,
        "scenario": scenario,
        "seed": seed,
        "completed_trips": count,
        "mean_travel_s": mean("travel_time_s"),
        "mean_wait_s": mean("wait_time_s"),
        "mean_stops": mean("stops"),
        "vehicles_in_network_at_end": pipeline.engine.vehicle_count(),
        "decisions": pipeline.processor.stats["decisions"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="NORMAL", choices=sorted(SCENARIOS))
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    strategies = ("ADAPTIVE", "FIXED_TIME") if args.compare else ("ADAPTIVE",)
    for strategy in strategies:
        print(json.dumps(run_case(args.scenario, args.minutes, args.seed, strategy)))


if __name__ == "__main__":
    main()
