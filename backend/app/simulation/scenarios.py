"""Predefined traffic scenarios."""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScenarioAction:
    delay_s: float
    kind: str  # EMERGENCY | INCIDENT | SENSOR_FAILURE
    params: dict


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    demand_multiplier: float = 1.0
    follow_clock: bool = False
    direction_bias: dict[str, float] = field(default_factory=dict)
    weather: str = "CLEAR"
    actions: tuple[ScenarioAction, ...] = ()


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in (
        Scenario(
            "NORMAL", "Normal traffic",
            "Demand follows the time-of-day profile of the simulated city clock.",
            follow_clock=True,
        ),
        Scenario(
            "MORNING_RUSH", "Morning rush hour",
            "Heavy inbound flow from the north and west towards the city centre.",
            demand_multiplier=1.6, direction_bias={"NORTH": 1.4, "WEST": 1.4, "SOUTH": 0.7, "EAST": 0.7},
        ),
        Scenario(
            "EVENING_RUSH", "Evening rush hour",
            "Heavy outbound flow, with the south and east entries carrying the load.",
            demand_multiplier=1.6, direction_bias={"SOUTH": 1.4, "EAST": 1.4, "NORTH": 0.7, "WEST": 0.7},
        ),
        Scenario(
            "HEAVY_CONGESTION", "Heavy congestion",
            "Demand well above capacity on every entry road, so queues spill back.",
            demand_multiplier=2.2,
        ),
        Scenario(
            "EMERGENCY", "Emergency vehicle",
            "An ambulance crosses the network and a fire engine follows, requesting signal priority.",
            demand_multiplier=1.2,
            actions=(
                ScenarioAction(5, "EMERGENCY", {"vehicle_type": "AMBULANCE", "origin": "INT-001", "destination": "INT-006"}),
                ScenarioAction(45, "EMERGENCY", {"vehicle_type": "FIRE_ENGINE", "origin": "INT-004", "destination": "INT-003"}),
            ),
        ),
        Scenario(
            "ACCIDENT", "Road accident",
            "A major accident blocks an approach to Central Junction during busy traffic.",
            demand_multiplier=1.4,
            actions=(ScenarioAction(20, "INCIDENT", {
                "incident_type": "MAJOR_ACCIDENT", "intersection_id": "INT-002", "direction": "EAST", "duration_s": 300,
            }),),
        ),
        Scenario(
            "SENSOR_FAILURE", "Sensor failure",
            "All sensors on one approach and several elsewhere go offline, and the controller degrades gracefully.",
            demand_multiplier=1.2,
            actions=(ScenarioAction(5, "SENSOR_FAILURE", {
                "intersection_id": "INT-003", "direction": "NORTH", "count": 5, "duration_s": 240,
            }), ScenarioAction(15, "SENSOR_FAILURE", {
                "intersection_id": "INT-005", "direction": None, "count": 3, "duration_s": 180,
            })),
        ),
        Scenario(
            "MIXED", "Mixed traffic conditions",
            "Rain, elevated demand, a minor accident, a police response and a sensor fault together.",
            demand_multiplier=1.5, weather="RAIN",
            actions=(
                ScenarioAction(15, "INCIDENT", {
                    "incident_type": "MINOR_ACCIDENT", "intersection_id": "INT-005", "direction": "WEST", "duration_s": 180,
                }),
                ScenarioAction(40, "EMERGENCY", {"vehicle_type": "POLICE", "origin": "INT-003", "destination": "INT-004"}),
                ScenarioAction(60, "SENSOR_FAILURE", {
                    "intersection_id": "INT-001", "direction": None, "count": 2, "duration_s": 120,
                }),
            ),
        ),
    )
}


def random_scenario(rng: random.Random, intersections: list[str]) -> Scenario:
    """Generate a one-off scenario with random demand and events."""
    directions = ("NORTH", "EAST", "SOUTH", "WEST")
    actions: list[ScenarioAction] = []
    if rng.random() < 0.7:
        actions.append(ScenarioAction(rng.uniform(5, 40), "INCIDENT", {
            "incident_type": rng.choice(
                ["MINOR_ACCIDENT", "MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "SUDDEN_SLOWDOWN", "LANE_CLOSURE"]
            ),
            "intersection_id": rng.choice(intersections),
            "direction": rng.choice(directions),
            "duration_s": rng.uniform(120, 300),
        }))
    if rng.random() < 0.6 and len(intersections) >= 2:
        origin, destination = rng.sample(intersections, 2)
        actions.append(ScenarioAction(rng.uniform(20, 70), "EMERGENCY", {
            "vehicle_type": rng.choice(["AMBULANCE", "FIRE_ENGINE", "POLICE"]),
            "origin": origin, "destination": destination,
        }))
    if rng.random() < 0.4:
        actions.append(ScenarioAction(rng.uniform(10, 60), "SENSOR_FAILURE", {
            "intersection_id": rng.choice(intersections), "direction": None,
            "count": rng.randint(1, 4), "duration_s": rng.uniform(90, 240),
        }))
    multiplier = round(rng.uniform(0.8, 2.4), 2)
    weather = rng.choice(["CLEAR", "CLEAR", "RAIN"])
    bias = {d: round(rng.uniform(0.6, 1.5), 2) for d in directions}
    return Scenario(
        "RANDOM", "Random scenario",
        f"Generated: demand x{multiplier}, {weather.lower()} weather, {len(actions)} timed events.",
        demand_multiplier=multiplier, direction_bias=bias, weather=weather, actions=tuple(actions),
    )
