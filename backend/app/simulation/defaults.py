"""The demo city: six intersections on a 3 x 2 grid. Used by the seeder, tests and benchmarks."""

from __future__ import annotations

from app.schemas.messages import SENSOR_TYPES
from app.simulation.network import IntersectionSpec
from app.simulation.sensors import SensorSpec

DIRECTIONS = ("NORTH", "EAST", "SOUTH", "WEST")

CITY: tuple[tuple[str, str, int, int], ...] = (
    ("INT-001", "Northgate Circle", 0, 0),
    ("INT-002", "Central Junction", 1, 0),
    ("INT-003", "Harbour Cross", 2, 0),
    ("INT-004", "Westfield Square", 0, 1),
    ("INT-005", "Market Crossing", 1, 1),
    ("INT-006", "Riverside Gate", 2, 1),
)

BASE_DEMAND_VPH = 240.0

REPORTING_INTERVALS: dict[str, float] = {
    "VEHICLE_DETECTION": 2.0,
    "TRAFFIC_DENSITY": 5.0,
    "VEHICLE_COUNT": 5.0,
    "ROAD_OCCUPANCY": 5.0,
    "AVERAGE_SPEED": 5.0,
    "ENVIRONMENTAL": 30.0,
}


def default_specs() -> list[IntersectionSpec]:
    demand = dict.fromkeys(DIRECTIONS, BASE_DEMAND_VPH)
    return [IntersectionSpec(i, name, x, y, DIRECTIONS, demand) for i, name, x, y in CITY]


def default_sensor_specs(intersection_ids: list[str] | None = None) -> list[SensorSpec]:
    """Five road sensors on each approach plus one environmental sensor per intersection."""
    specs: list[SensorSpec] = []
    serial = 0
    for intersection_id in intersection_ids or [c[0] for c in CITY]:
        for direction in DIRECTIONS:
            for sensor_type in SENSOR_TYPES[:-1]:
                serial += 1
                specs.append(SensorSpec(
                    f"SNS-{serial:03d}", sensor_type, intersection_id, direction, REPORTING_INTERVALS[sensor_type],
                ))
        serial += 1
        specs.append(SensorSpec(
            f"SNS-{serial:03d}", "ENVIRONMENTAL", intersection_id, None, REPORTING_INTERVALS["ENVIRONMENTAL"],
        ))
    return specs
