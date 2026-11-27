"""Tunable parameters, stored in the system_configuration table."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.algorithms.congestion import CongestionThresholds
from app.algorithms.incident_detection import DetectorConfig
from app.algorithms.preemption import PreemptionConfig
from app.algorithms.priority import AlgorithmConfig, PriorityWeights


class WeightsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: float = Field(0.35, ge=0, le=1)
    waiting: float = Field(0.30, ge=0, le=1)
    density: float = Field(0.15, ge=0, le=1)
    demand: float = Field(0.15, ge=0, le=1)
    speed: float = Field(0.05, ge=0, le=1)

    @model_validator(mode="after")
    def _not_all_zero(self) -> WeightsConfig:
        if self.queue + self.waiting + self.density + self.demand + self.speed <= 0:
            raise ValueError("at least one weight must be positive")
        return self


class AlgorithmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: WeightsConfig = WeightsConfig()
    max_wait_limit_s: float = Field(120, ge=30, le=600)
    hysteresis: float = Field(1.15, ge=1, le=2)
    control_strategy: str = Field("ADAPTIVE", pattern="^(ADAPTIVE|FIXED_TIME)$")


class CongestionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: tuple[float, float, float] = (8, 16, 26)
    occupancy: tuple[float, float, float] = (35, 60, 80)
    wait: tuple[float, float, float] = (40, 75, 110)
    speed: tuple[float, float, float] = (9.0, 5.5, 3.0)
    alert_level: str = Field("HIGH", pattern="^(MODERATE|HIGH|SEVERE)$")
    raise_after_s: float = Field(45, ge=0, le=600)
    clear_after_s: float = Field(120, ge=0, le=600)

    @model_validator(mode="after")
    def _ordered(self) -> CongestionSettings:
        CongestionThresholds(**self.model_dump())
        return self


class SystemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: AlgorithmSettings = AlgorithmSettings()
    congestion: CongestionSettings = CongestionSettings()
    sensor_mtbf_hours: float = Field(0, ge=0, le=1000)


@dataclass
class RuntimeConfig:
    algorithm: AlgorithmConfig = field(default_factory=AlgorithmConfig)
    congestion: CongestionThresholds = field(default_factory=CongestionThresholds)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    preemption: PreemptionConfig = field(default_factory=PreemptionConfig)
    strategy: str = "ADAPTIVE"


def to_runtime(settings: SystemSettings) -> RuntimeConfig:
    algorithm = settings.algorithm
    return RuntimeConfig(
        algorithm=AlgorithmConfig(
            weights=PriorityWeights(**algorithm.weights.model_dump()),
            max_wait_limit=algorithm.max_wait_limit_s,
            hysteresis=algorithm.hysteresis,
        ),
        congestion=CongestionThresholds(**settings.congestion.model_dump()),
        strategy=algorithm.control_strategy,
    )
