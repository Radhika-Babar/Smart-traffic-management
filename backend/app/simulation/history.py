"""Synthetic history generator.

Running the micro-simulation for two weeks of simulated time would take hours,
so seeded history comes from a mesoscopic queue model driven by the same demand
profile. Rows are labelled SEEDED everywhere they appear. Live rows take over
as soon as the simulation runs and are labelled LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.algorithms.congestion import CongestionInput, CongestionThresholds, classify
from app.core.clock import local_hour_and_weekday

BIN_SECONDS = 300
CAPACITY_VPH = 400.0
INTERIOR_SHARE = 0.8


@dataclass(frozen=True)
class ApproachHistorySpec:
    intersection_id: str
    direction: str
    base_vph: float
    interior: bool


def _profile(bins: list[datetime]) -> np.ndarray:
    from app.simulation.demand import demand_factor

    values = []
    for moment in bins:
        epoch = (moment - datetime(1970, 1, 1)).total_seconds()
        hour, weekday = local_hour_and_weekday(epoch)
        values.append(demand_factor(hour, weekday))
    return np.array(values)


def generate_history(
    approaches: list[ApproachHistorySpec], end: datetime, days: int, seed: int = 20260101
) -> list[dict]:
    """Rows for traffic_metrics, ending at `end` (naive UTC)."""
    rng = np.random.default_rng(seed)
    thresholds = CongestionThresholds()
    start = (end - timedelta(days=days)).replace(second=0, microsecond=0)
    start -= timedelta(minutes=start.minute % 5)
    count = int((end - start).total_seconds() // BIN_SECONDS)
    bins = [start + timedelta(seconds=BIN_SECONDS * i) for i in range(count)]
    profile = _profile(bins)
    per_bin_capacity = CAPACITY_VPH * BIN_SECONDS / 3600.0
    # Day-to-day variation shared across the city, such as weather or events.
    daily = np.repeat(rng.normal(1.0, 0.07, size=days + 2), 288)[:count]

    rows: list[dict] = []
    for spec in approaches:
        scale = INTERIOR_SHARE if spec.interior else 1.0
        bias = rng.uniform(0.85, 1.2)
        # Red noise gives the demand short-term persistence that real traffic has.
        noise = np.zeros(count)
        shocks = rng.normal(0, 0.1, size=count)
        for i in range(1, count):
            noise[i] = 0.8 * noise[i - 1] + shocks[i]
        rates = np.clip(spec.base_vph * scale * bias * profile * daily * (1 + noise), 0, None)
        arrivals = rng.poisson(rates * BIN_SECONDS / 3600.0)
        # Rare capacity drops stand in for past incidents.
        capacity = np.full(count, per_bin_capacity)
        for _ in range(max(1, days // 3)):
            at = int(rng.integers(0, count))
            capacity[at:at + int(rng.integers(3, 10))] *= rng.uniform(0.3, 0.6)

        queue = 0.0
        for i, moment in enumerate(bins):
            demand = queue + arrivals[i]
            served = min(demand, capacity[i] * rng.uniform(0.92, 1.05))
            queue = demand - served
            ratio = min(1.3, served / max(capacity[i], 1.0))
            mean_queue = queue + 0.5 * arrivals[i] * ratio * 0.4
            wait = 12.0 + 40.0 * ratio**3 + 2.1 * queue
            speed = float(np.clip(13.2 * (1 - 0.72 * ratio**2.5) - 0.12 * queue, 1.0, 13.9))
            density = float(np.clip(6 + 42 * ratio + 2.4 * queue, 0, 100))
            occupancy = float(np.clip(5 + 48 * ratio**1.5 + 2.6 * queue, 0, 100))
            level = classify(CongestionInput(mean_queue, occupancy, wait, speed), thresholds)
            rows.append({
                "bucket_start": moment, "bin_seconds": BIN_SECONDS,
                "intersection_id": spec.intersection_id, "direction": spec.direction,
                "volume": int(served), "avg_queue": round(mean_queue, 2), "avg_wait_s": round(wait, 2),
                "avg_speed": round(speed, 2), "density": round(density, 2), "occupancy": round(occupancy, 2),
                "congestion_level": level, "source": "SEEDED",
            })
    return rows
