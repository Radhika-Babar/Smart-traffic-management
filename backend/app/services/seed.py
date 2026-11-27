"""First-run demo data. Every row is either network configuration or labelled SEEDED."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.models import (
    CongestionEvent,
    Intersection,
    SystemConfiguration,
    TrafficAlert,
    TrafficApproach,
    TrafficEvent,
    TrafficMetric,
    TrafficSensor,
    TrafficSignal,
    VehicleCategory,
)
from app.services.processing import short_id
from app.services.runtime_config import SystemSettings
from app.simulation.defaults import BASE_DEMAND_VPH, CITY, DIRECTIONS, default_sensor_specs
from app.simulation.engine import DEFAULT_CATEGORIES
from app.simulation.history import ApproachHistorySpec, generate_history

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {"CAR": "Car", "MOTORCYCLE": "Motorcycle", "BUS": "Bus", "TRUCK": "Truck"}


def seed_network(session: Session) -> None:
    for intersection_id, name, grid_x, grid_y in CITY:
        session.add(Intersection(id=intersection_id, name=name, grid_x=grid_x, grid_y=grid_y))
        session.add(TrafficSignal(id=intersection_id.replace("INT", "SIG"), intersection_id=intersection_id))
    session.flush()
    for intersection_id, *_ in CITY:
        for direction in DIRECTIONS:
            session.add(TrafficApproach(
                intersection_id=intersection_id, direction=direction, base_demand_vph=BASE_DEMAND_VPH,
            ))
    for spec in default_sensor_specs():
        session.add(TrafficSensor(
            id=spec.id, sensor_type=spec.sensor_type, intersection_id=spec.intersection_id,
            direction=spec.direction, reporting_interval_s=spec.interval_s,
        ))
    for category in DEFAULT_CATEGORIES:
        session.add(VehicleCategory(
            key=category.key, label=CATEGORY_LABELS[category.key], length_m=category.length,
            speed_factor=category.speed_factor, share=category.share,
        ))
    session.add(SystemConfiguration(
        key="system", value=SystemSettings().model_dump(mode="json"),
        description="Algorithm weights, congestion thresholds and sensor fault rate",
    ))
    session.flush()


def _is_interior(intersection_id: str, direction: str, positions: dict[tuple[int, int], str]) -> bool:
    grid = {v: k for k, v in positions.items()}
    x, y = grid[intersection_id]
    step = {"NORTH": (0, -1), "SOUTH": (0, 1), "EAST": (1, 0), "WEST": (-1, 0)}[direction]
    return (x + step[0], y + step[1]) in positions


def seed_history(session: Session, days: int) -> int:
    positions = {(x, y): i for i, _, x, y in CITY}
    specs = [
        ApproachHistorySpec(i, d, BASE_DEMAND_VPH, _is_interior(i, d, positions))
        for i, *_ in CITY for d in DIRECTIONS
    ]
    now = utcnow()
    rows = generate_history(specs, now, days)
    for start in range(0, len(rows), 10000):
        session.execute(insert(TrafficMetric), rows[start:start + 10000])
    _seed_congestion_history(session, rows)
    _seed_sample_alerts(session, now)
    return len(rows)


def _seed_congestion_history(session: Session, rows: list[dict]) -> None:
    """Turn sustained HIGH or SEVERE stretches of the seeded series into closed events."""
    streams: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        streams.setdefault((row["intersection_id"], row["direction"]), []).append(row)
    records = []
    for (intersection_id, direction), stream in streams.items():
        start = None
        worst = "HIGH"
        for row in stream:
            congested = row["congestion_level"] in ("HIGH", "SEVERE")
            if congested and start is None:
                start, worst = row["bucket_start"], row["congestion_level"]
            elif congested and row["congestion_level"] == "SEVERE":
                worst = "SEVERE"
            elif not congested and start is not None:
                if row["bucket_start"] - start >= timedelta(minutes=15):
                    records.append({
                        "id": short_id("CNG"), "intersection_id": intersection_id, "direction": direction,
                        "severity": worst, "detected_at": start, "resolved_at": row["bucket_start"],
                        "status": "RESOLVED", "recommended_action": "Seeded history record",
                    })
                start = None
    if records:
        session.execute(insert(CongestionEvent), records)


def _seed_sample_alerts(session: Session, now) -> None:
    samples = (
        ("SEVERE_CONGESTION", "CRITICAL", "SEVERE congestion on NORTH approach of Central Junction", "INT-002", 26),
        ("SENSOR_FAILURE", "WARNING", "SNS-014 (road occupancy) stopped reporting", "INT-001", 19),
        ("EMERGENCY_APPROACHING", "WARNING", "Ambulance approaching Market Crossing from WEST", "INT-005", 11),
        ("ACCIDENT_DETECTED", "CRITICAL", "Simulated incident on EAST approach of Harbour Cross", "INT-003", 7),
        ("UNUSUAL_TRAFFIC", "WARNING", "Unusual traffic on SOUTH approach of Riverside Gate", "INT-006", 4),
    )
    for category, severity, description, intersection_id, hours in samples:
        raised = now - timedelta(hours=hours)
        session.add(TrafficAlert(
            id=short_id("ALT"), category=category, severity=severity, description=description,
            intersection_id=intersection_id, raised_at=raised, acknowledged=True, acknowledged_at=raised,
            resolved=True, resolved_at=raised + timedelta(minutes=12), source="SEEDED",
        ))
    session.add(TrafficEvent(
        occurred_at=now, category="SYSTEM", severity="INFO", message="Demo network and history seeded",
    ))


def seed_if_empty(session: Session, history_days: int) -> bool:
    if session.scalar(select(func.count()).select_from(Intersection)):
        return False
    logger.info("Empty database: seeding demo city and %s days of history", history_days)
    seed_network(session)
    count = seed_history(session, history_days) if history_days else 0
    session.commit()
    logger.info("Seeded %s history rows", count)
    return True

