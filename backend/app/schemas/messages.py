"""Validated MQTT payloads. Nothing from the bus is trusted before passing these."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Direction = Literal["NORTH", "EAST", "SOUTH", "WEST"]
SensorType = Literal[
    "VEHICLE_DETECTION", "TRAFFIC_DENSITY", "VEHICLE_COUNT", "ROAD_OCCUPANCY", "AVERAGE_SPEED", "ENVIRONMENTAL"
]
SENSOR_TYPES: tuple[str, ...] = (
    "VEHICLE_DETECTION", "TRAFFIC_DENSITY", "VEHICLE_COUNT", "ROAD_OCCUPANCY", "AVERAGE_SPEED", "ENVIRONMENTAL",
)

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "VEHICLE_DETECTION": ("queue_length", "approaching", "avg_wait_s", "max_wait_s"),
    "TRAFFIC_DENSITY": ("traffic_density",),
    "VEHICLE_COUNT": ("vehicle_count",),
    "ROAD_OCCUPANCY": ("occupancy_percentage",),
    # average_speed may be null when nothing passed the sensor in the interval.
    "AVERAGE_SPEED": (),
    "ENVIRONMENTAL": ("temperature_c", "rain_mm_h", "visibility_m"),
}


class SensorReadingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_id: str = Field(pattern=r"^SNS-\d{3,}$")
    sensor_type: SensorType
    intersection_id: str = Field(pattern=r"^INT-\d{3,}$")
    direction: Direction | None = None
    status: Literal["ACTIVE"]
    timestamp: datetime
    seq: int = Field(ge=0)
    simulated: Literal[True] = True

    vehicle_count: int | None = Field(default=None, ge=0, le=10000)
    traffic_density: float | None = Field(default=None, ge=0, le=100)
    average_speed: float | None = Field(default=None, ge=0, le=60)
    occupancy_percentage: float | None = Field(default=None, ge=0, le=100)
    queue_length: int | None = Field(default=None, ge=0, le=500)
    approaching: int | None = Field(default=None, ge=0, le=500)
    avg_wait_s: float | None = Field(default=None, ge=0)
    max_wait_s: float | None = Field(default=None, ge=0)
    temperature_c: float | None = Field(default=None, ge=-40, le=60)
    rain_mm_h: float | None = Field(default=None, ge=0, le=200)
    visibility_m: float | None = Field(default=None, ge=0, le=20000)

    @model_validator(mode="after")
    def _check_type_fields(self) -> SensorReadingMessage:
        missing = [name for name in REQUIRED_FIELDS[self.sensor_type] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{self.sensor_type} reading is missing {', '.join(missing)}")
        if self.sensor_type != "ENVIRONMENTAL" and self.direction is None:
            raise ValueError("road sensors must report a direction")
        return self


class SignalStateMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intersection_id: str = Field(pattern=r"^INT-\d{3,}$")
    phase: Literal["RED", "GREEN", "YELLOW", "ALL_RED"]
    active_direction: Direction | None
    heads: dict[Direction, Literal["RED", "YELLOW", "GREEN"]]
    elapsed: float = Field(ge=0)
    planned_green: float = Field(ge=0)
    remaining: float = Field(ge=0)
    mode: Literal["AUTOMATIC", "MANUAL"]
    controller: Literal["ONLINE", "FAILSAFE", "FAILED"]
    preempt_vehicle: str | None = None
    preempt_direction: Direction | None = None
    transition: bool = False
    previous_phase: str | None = None
    reason: str | None = None
    green_duration: float | None = None
    min_green: float = Field(gt=0)
    max_green: float = Field(gt=0)
    timestamp: datetime
    simulated: Literal[True] = True

    @model_validator(mode="after")
    def _single_green(self) -> SignalStateMessage:
        moving = [d for d, aspect in self.heads.items() if aspect != "RED"]
        if len(moving) > 1:
            raise ValueError(f"conflicting aspects reported: {moving}")
        return self


class IntersectionStatusMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intersection_id: str = Field(pattern=r"^INT-\d{3,}$")
    vehicles_on_approaches: int = Field(ge=0)
    active_sensors: int = Field(ge=0)
    total_sensors: int = Field(ge=0)
    controller: Literal["ONLINE", "FAILSAFE", "FAILED"]
    timestamp: datetime
    simulated: Literal[True] = True


class EmergencyMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    vehicle_type: Literal["AMBULANCE", "FIRE_ENGINE", "POLICE"]
    status: Literal["EN_ROUTE", "ARRIVED", "CANCELLED"]
    x: float
    y: float
    speed: float = Field(ge=0)
    next_intersection: str | None
    approach: Direction | None
    distance_m: float | None = Field(default=None, ge=0)
    eta_s: float | None = Field(default=None, ge=0)
    route: list[str]
    route_index: int = Field(ge=0)
    timestamp: datetime
    simulated: Literal[True] = True


class SignalCommand(BaseModel):
    """Central system to field controller."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["SWITCH", "EXTEND", "PREEMPT", "RELEASE", "SET_MODE"]
    intersection_id: str
    direction: Direction | None = None
    seconds: float | None = Field(default=None, gt=0, le=600)
    vehicle_id: str | None = None
    mode: Literal["AUTOMATIC", "MANUAL"] | None = None
    reason: str | None = None
    source: Literal["ADAPTIVE", "OPERATOR", "EMERGENCY"] = "ADAPTIVE"
    timestamp: datetime | None = None
