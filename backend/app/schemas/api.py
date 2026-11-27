"""REST request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.core.clock import iso_utc
from app.schemas.messages import Direction, SensorType

UtcDatetime = Annotated[datetime, PlainSerializer(iso_utc, return_type=str | None)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    message: str


# ---- intersections -------------------------------------------------------


class ApproachOut(ORMModel):
    direction: Direction
    enabled: bool
    lanes: int
    base_demand_vph: float


class SignalOut(ORMModel):
    id: str
    intersection_id: str
    mode: Literal["AUTOMATIC", "MANUAL"]
    min_green_s: float
    max_green_s: float
    default_green_s: float
    yellow_s: float
    all_red_s: float
    preempt_max_hold_s: float


class IntersectionOut(ORMModel):
    id: str
    name: str
    description: str | None
    grid_x: int
    grid_y: int
    speed_limit_ms: float
    approaches: list[ApproachOut]
    signal: SignalOut
    sensor_count: int = 0


class IntersectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    grid_x: int = Field(ge=-5, le=10)
    grid_y: int = Field(ge=-5, le=10)
    directions: list[Direction] = Field(default=["NORTH", "EAST", "SOUTH", "WEST"], min_length=1, max_length=4)
    speed_limit_ms: float = Field(default=13.9, ge=3, le=30)
    base_demand_vph: float = Field(default=240, ge=0, le=1500)


class IntersectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    speed_limit_ms: float | None = Field(default=None, ge=3, le=30)


class ApproachUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    base_demand_vph: float | None = Field(default=None, ge=0, le=1500)


class NetworkChange(BaseModel):
    intersection: IntersectionOut
    simulation_reset: bool


# ---- sensors -------------------------------------------------------------


class SensorOut(BaseModel):
    id: str
    sensor_type: SensorType
    intersection_id: str
    direction: Direction | None
    reporting_interval_s: float
    enabled: bool
    status: str
    last_seen: str | None
    readings_received: int
    latest: dict[str, Any]
    simulated: bool = True


class SensorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_type: SensorType
    intersection_id: str
    direction: Direction | None = None
    reporting_interval_s: float = Field(default=5, ge=1, le=300)

    @model_validator(mode="after")
    def _direction_matches_type(self) -> SensorCreate:
        if self.sensor_type != "ENVIRONMENTAL" and self.direction is None:
            raise ValueError("road sensors need a direction")
        return self


class SensorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    reporting_interval_s: float | None = Field(default=None, ge=1, le=300)


class ReadingOut(ORMModel):
    recorded_at: UtcDatetime
    vehicle_count: int | None
    traffic_density: float | None
    average_speed: float | None
    occupancy_percentage: float | None
    queue_length: int | None
    approaching: int | None
    avg_wait_s: float | None
    max_wait_s: float | None
    temperature_c: float | None
    rain_mm_h: float | None
    visibility_m: float | None


# ---- signals -------------------------------------------------------------


class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["AUTOMATIC", "MANUAL"]


class ManualControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction
    green_seconds: float = Field(default=30, ge=5, le=300)


class TimingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_green_s: float = Field(ge=3, le=120)
    max_green_s: float = Field(ge=3, le=300)
    default_green_s: float = Field(ge=3, le=300)
    yellow_s: float = Field(ge=2, le=10)
    all_red_s: float = Field(ge=1, le=10)
    preempt_max_hold_s: float = Field(default=45, ge=3, le=300)


class DecisionOut(ORMModel):
    id: int
    decided_at: UtcDatetime
    action: str
    direction: str | None
    green_s: float
    reason: str
    scores: list[dict[str, Any]]


class SignalHistoryOut(ORMModel):
    id: int
    phase: str
    direction: str | None
    started_at: UtcDatetime
    ended_at: UtcDatetime
    duration_s: float
    end_reason: str | None
    mode: str


# ---- alerts --------------------------------------------------------------


class AlertOut(ORMModel):
    id: str
    category: str
    severity: str
    description: str
    intersection_id: str | None
    raised_at: UtcDatetime
    acknowledged: bool
    acknowledged_at: UtcDatetime | None
    resolved: bool
    resolved_at: UtcDatetime | None
    source: str


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int


# ---- simulation ----------------------------------------------------------


class SpeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speed: Literal[1, 2, 5, 10]


class ParametersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_factor: float | None = Field(default=None, ge=0, le=4)
    spawn_rate_vph: float | None = Field(default=None, ge=0, le=1500)
    clear_spawn_rate: bool = False
    intersection_id: str | None = None
    intersection_factor: float | None = Field(default=None, ge=0, le=4)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str


class WeatherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: Literal["CLEAR", "RAIN"]


class SensorFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intersection_id: str | None = None
    direction: Direction | None = None
    count: int = Field(default=1, ge=1, le=30)
    duration_s: float = Field(default=180, ge=10, le=3600)


class ControllerFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intersection_id: str
    duration_s: float = Field(default=60, ge=10, le=600)


# ---- emergency and incidents ---------------------------------------------


class EmergencyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_type: Literal["AMBULANCE", "FIRE_ENGINE", "POLICE"]
    origin: str
    destination: str


class EmergencyOut(ORMModel):
    id: str
    vehicle_type: str
    origin_id: str | None
    destination_id: str | None
    route: list[str]
    status: str
    dispatched_at: UtcDatetime
    finished_at: UtcDatetime | None
    travel_time_s: float | None
    wait_time_s: float | None
    stops: int | None
    preemptions: int


class IncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_type: Literal["MINOR_ACCIDENT", "MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "SUDDEN_SLOWDOWN", "LANE_CLOSURE"]
    intersection_id: str
    direction: Direction
    duration_s: float | None = Field(default=None, ge=20, le=3600)


class IncidentOut(ORMModel):
    id: str
    incident_type: str
    origin: str
    intersection_id: str | None
    direction: str
    severity: str
    affected_lanes: str | None
    status: str
    injected_at: UtcDatetime | None
    detected_at: UtcDatetime | None
    resolved_at: UtcDatetime | None
    detection_score: float | None
    evidence: list[str] | None
    recommended_response: str | None
    simulated: bool = True


class CongestionOut(ORMModel):
    id: str
    intersection_id: str | None
    direction: str
    severity: str
    detected_at: UtcDatetime
    resolved_at: UtcDatetime | None
    status: str
    recommended_action: str


# ---- predictions and system ----------------------------------------------


class PredictionOut(ORMModel):
    intersection_id: str
    generated_at: UtcDatetime
    target_at: UtcDatetime
    model_name: str
    predicted_volume: float
    predicted_wait_s: float
    predicted_density: float
    predicted_level: str
    actual_volume: float | None
    actual_wait_s: float | None
    actual_density: float | None


class EvaluationOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    run_id: str
    trained_at: UtcDatetime
    model_name: str
    target: str
    mae: float
    rmse: float
    r2: float
    train_rows: int
    test_rows: int
    feature_importance: dict[str, float] | None
    selected: bool


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    older_than_days: int = Field(ge=1, le=3650)
    confirm: Literal[True]
