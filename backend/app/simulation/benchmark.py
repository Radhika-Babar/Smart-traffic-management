"""Measured comparison of adaptive and fixed-time control.

    python -m app.simulation.benchmark --seeds 5 --minutes 30

Identical demand per seed, a five-minute warm-up, and the same network. Prints
means and the per-seed spread as Markdown, then the incident detection and
emergency priority measurements. These are results from this simulation only;
they say nothing about a real road network.
"""

from __future__ import annotations

import argparse
import statistics
from concurrent.futures import ProcessPoolExecutor

from app.algorithms.preemption import PreemptionConfig
from app.simulation.headless import build_pipeline, run_case
from app.simulation.scenarios import SCENARIOS as ALL_SCENARIOS
from app.simulation.scenarios import Scenario

SCENARIOS = ("NORMAL", "MORNING_RUSH", "HEAVY_CONGESTION")
INCIDENT_TYPES = ("MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "MINOR_ACCIDENT", "SUDDEN_SLOWDOWN", "LANE_CLOSURE")
SITES = (("INT-002", "EAST"), ("INT-005", "WEST"), ("INT-001", "NORTH"), ("INT-006", "SOUTH"))


def _run(job: tuple[str, int, float, str]) -> dict:
    scenario, seed, minutes, strategy = job
    return run_case(scenario, minutes, seed, strategy)


class _Sink:
    def __init__(self) -> None:
        self.incidents: list[dict] = []
        self.updates: list[tuple] = []

    def add(self, kind: str, row: dict) -> None:
        if kind == "incident":
            self.incidents.append(row)

    def update(self, kind: str, key: object, values: dict) -> None:
        if kind in ("incident", "emergency"):
            self.updates.append((kind, key, values))


def _detection(job: tuple[str, int, tuple[str, str]]) -> dict:
    """Inject one incident in steady traffic and time how long the sensors take to reveal it."""
    incident_type, seed, (intersection, direction) = job
    sink = _Sink()
    pipeline = build_pipeline(seed, sink=sink)
    pipeline.engine.apply_scenario(Scenario("BENCH", "Bench", "Bench", demand_multiplier=1.2))
    pipeline.run(300)
    incident = pipeline.engine.inject_incident(incident_type, intersection, direction, 300)["incident_id"]
    for elapsed in range(5, 301, 5):
        pipeline.run(5)
        if any(key == incident and values.get("status") == "DETECTED" for _, key, values in sink.updates):
            return {"type": incident_type, "detected": True, "latency": elapsed}
    return {"type": incident_type, "detected": False, "latency": None}


def _false_alarms(job: tuple[str, int]) -> int:
    scenario, seed = job
    sink = _Sink()
    pipeline = build_pipeline(seed, sink=sink)
    pipeline.engine.apply_scenario(ALL_SCENARIOS[scenario])
    pipeline.run(3600)
    return len(sink.incidents)


def _emergency(job: tuple[int, bool]) -> float | None:
    """Travel time of an ambulance crossing the city, with or without priority."""
    seed, priority = job
    sink = _Sink()
    pipeline = build_pipeline(seed, sink=sink)
    if not priority:
        pipeline.processor.preemption.config = PreemptionConfig(0, 0)
    pipeline.engine.apply_scenario(ALL_SCENARIOS["MORNING_RUSH"])
    pipeline.run(300)
    pipeline.engine.dispatch_emergency("AMBULANCE", "INT-001", "INT-006")
    pipeline.run(600)
    arrived = [v for kind, _, v in sink.updates if kind == "emergency" and v.get("status") == "ARRIVED"]
    return arrived[0]["travel_time_s"] if arrived else None


def report_safety_features(pool: ProcessPoolExecutor, seeds: int) -> None:
    jobs = [(t, seed, SITES[seed % len(SITES)]) for t in INCIDENT_TYPES for seed in range(1, seeds + 1)]
    results = list(pool.map(_detection, jobs))
    print("\n| Incident type | Detected | Mean time to detect (s) |")
    print("| --- | ---: | ---: |")
    for incident_type in INCIDENT_TYPES:
        rows = [r for r in results if r["type"] == incident_type]
        found = [r["latency"] for r in rows if r["detected"]]
        mean = f"{statistics.mean(found):.0f}" if found else "-"
        print(f"| {incident_type} | {len(found)} of {len(rows)} | {mean} |")

    jobs = [(scenario, seed) for scenario in SCENARIOS for seed in range(1, seeds + 1)]
    alarms = list(pool.map(_false_alarms, jobs))
    print("\n| Scenario, no incident injected | False incidents per simulated hour |")
    print("| --- | ---: |")
    for index, scenario in enumerate(SCENARIOS):
        print(f"| {scenario} | {statistics.mean(alarms[index * seeds:(index + 1) * seeds]):.1f} |")

    with_priority = list(pool.map(_emergency, [(seed, True) for seed in range(1, seeds + 1)]))
    without = list(pool.map(_emergency, [(seed, False) for seed in range(1, seeds + 1)]))
    print("\n| Ambulance INT-001 to INT-006, morning rush | Mean travel time (s) | Arrived |")
    print("| --- | ---: | ---: |")
    for label, rows in (("With signal priority", with_priority), ("Without signal priority", without)):
        done = [r for r in rows if r is not None]
        mean = f"{statistics.mean(done):.1f}" if done else "-"
        print(f"| {label} | {mean} | {len(done)} of {len(rows)} |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--minutes", type=float, default=30)
    args = parser.parse_args()
    jobs = [
        (scenario, seed, args.minutes, strategy)
        for scenario in SCENARIOS for seed in range(1, args.seeds + 1) for strategy in ("ADAPTIVE", "FIXED_TIME")
    ]
    pool = ProcessPoolExecutor(max_workers=6)
    results = list(pool.map(_run, jobs))

    print("| Scenario | Control | Trips | Mean travel (s) | Mean wait (s) | Stops | Wait range across seeds (s) |")
    print("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for scenario in SCENARIOS:
        means = {}
        for strategy in ("ADAPTIVE", "FIXED_TIME"):
            rows = [r for r in results if r["scenario"] == scenario and r["strategy"] == strategy]
            waits = [r["mean_wait_s"] for r in rows]
            means[strategy] = statistics.mean(waits)
            print(
                f"| {scenario} | {strategy} | {statistics.mean(r['completed_trips'] for r in rows):.0f} "
                f"| {statistics.mean(r['mean_travel_s'] for r in rows):.1f} | {means[strategy]:.1f} "
                f"| {statistics.mean(r['mean_stops'] for r in rows):.1f} | {min(waits):.1f} to {max(waits):.1f} |"
            )
        change = 100 * (means["FIXED_TIME"] - means["ADAPTIVE"]) / means["FIXED_TIME"]
        print(f"| {scenario} | Adaptive wait reduction | | | {change:+.1f}% | | |")
    report_safety_features(pool, args.seeds)
    pool.shutdown()


if __name__ == "__main__":
    main()
