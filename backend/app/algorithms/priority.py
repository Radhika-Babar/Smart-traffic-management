"""Weighted priority scoring and adaptive green-time selection.

Pure functions only. The inputs are what the sensors reported, never simulator
ground truth, and the output carries enough detail to explain each decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PriorityWeights:
    queue: float = 0.35
    waiting: float = 0.30
    density: float = 0.15
    demand: float = 0.15
    speed: float = 0.05

    def __post_init__(self) -> None:
        values = (self.queue, self.waiting, self.density, self.demand, self.speed)
        if any(v < 0 for v in values):
            raise ValueError("weights must not be negative")
        if sum(values) <= 0:
            raise ValueError("at least one weight must be positive")


@dataclass(frozen=True)
class Normalization:
    queue_vehicles: float = 30.0
    waiting_seconds: float = 120.0
    demand_vehicles: float = 40.0
    free_flow_speed: float = 13.9


@dataclass(frozen=True)
class AlgorithmConfig:
    weights: PriorityWeights = field(default_factory=PriorityWeights)
    norm: Normalization = field(default_factory=Normalization)
    max_wait_limit: float = 120.0
    saturation_headway: float = 2.1
    startup_lost_time: float = 2.5
    hysteresis: float = 1.15
    min_extension: float = 4.0
    decision_lookahead: float = 1.5


@dataclass(frozen=True)
class ApproachInput:
    direction: str
    queue: float = 0.0
    waiting: float = 0.0
    density: float = 0.0
    approaching: float = 0.0
    speed: float | None = None
    degraded: bool = False
    red_seconds: float = 0.0  # since this approach last had green


@dataclass(frozen=True)
class ApproachScore:
    direction: str
    queue: float
    waiting: float
    density: float
    approaching: float
    speed: float | None
    queue_norm: float
    waiting_norm: float
    density_norm: float
    demand_norm: float
    speed_norm: float
    priority: float
    has_demand: bool
    starving: bool
    degraded: bool
    red_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class Decision:
    action: str  # SWITCH | EXTEND | HOLD
    direction: str | None
    green_seconds: float
    reason: str
    scores: tuple[ApproachScore, ...]


def _unit(value: float, scale: float) -> float:
    return max(0.0, min(1.0, value / scale)) if scale > 0 else 0.0


def score_approach(item: ApproachInput, config: AlgorithmConfig) -> ApproachScore:
    """Priority = 100 * weighted mean of inputs normalised to [0, 1]."""
    weights, norm = config.weights, config.norm
    queue_n = _unit(item.queue, norm.queue_vehicles)
    waiting_n = _unit(item.waiting, norm.waiting_seconds)
    density_n = _unit(item.density, 100.0)
    demand_n = _unit(item.queue + item.approaching, norm.demand_vehicles)
    has_demand = item.queue >= 1 or item.approaching >= 1
    # Low speed only signals pressure when vehicles are actually there.
    speed_n = 0.0
    if has_demand and item.speed is not None:
        speed_n = 1.0 - _unit(item.speed, norm.free_flow_speed)
    total = weights.queue + weights.waiting + weights.density + weights.demand + weights.speed
    weighted = (
        weights.queue * queue_n
        + weights.waiting * waiting_n
        + weights.density * density_n
        + weights.demand * demand_n
        + weights.speed * speed_n
    )
    priority = 100.0 * weighted / total if has_demand else 0.0
    return ApproachScore(
        direction=item.direction,
        queue=item.queue,
        waiting=item.waiting,
        density=item.density,
        approaching=item.approaching,
        speed=item.speed,
        queue_norm=queue_n,
        waiting_norm=waiting_n,
        density_norm=density_n,
        demand_norm=demand_n,
        speed_norm=speed_n,
        priority=priority,
        has_demand=has_demand,
        starving=has_demand and max(item.waiting, item.red_seconds) >= config.max_wait_limit,
        degraded=item.degraded,
        red_seconds=item.red_seconds,
    )


def score_all(items: list[ApproachInput], config: AlgorithmConfig) -> tuple[ApproachScore, ...]:
    return tuple(score_approach(i, config) for i in items)


def plan_green(score: ApproachScore, config: AlgorithmConfig, min_green: float, max_green: float) -> float:
    """Green long enough to discharge the queue plus part of the approaching platoon."""
    vehicles = score.queue + 0.5 * score.approaching
    wanted = config.startup_lost_time + vehicles * config.saturation_headway
    return max(min_green, min(max_green, wanted))


def decide(
    scores: tuple[ApproachScore, ...],
    current: str | None,
    elapsed: float,
    min_green: float,
    max_green: float,
    config: AlgorithmConfig,
) -> Decision:
    """Choose what happens when the running green is due to end.

    `elapsed` is how long `current` has been green. Starving approaches are
    served first, longest red first, so with four approaches none can wait more
    than three maximum greens plus clearances once it passes the limit.
    Otherwise the highest score wins. The current direction keeps
    green only while it is still discharging and beats its best rival by the
    hysteresis margin, which stops the controller thrashing between near-equal
    approaches.
    """
    by_direction = {s.direction: s for s in scores}
    rivals = [s for s in scores if s.direction != current and s.has_demand]
    starving = sorted((s for s in rivals if s.starving), key=lambda s: max(s.waiting, s.red_seconds), reverse=True)
    best_rival = max(rivals, key=lambda s: s.priority, default=None)
    own = by_direction.get(current) if current else None

    def switch(target: ApproachScore, reason: str) -> Decision:
        return Decision("SWITCH", target.direction, plan_green(target, config, min_green, max_green), reason, scores)

    if starving:
        target = starving[0]
        waited = max(target.waiting, target.red_seconds)
        return switch(target, f"{target.direction} waited {waited:.0f}s, over the starvation limit")

    room = max_green - elapsed
    if own is not None and own.has_demand and room >= config.min_extension:
        beats_rival = best_rival is None or own.priority >= best_rival.priority * config.hysteresis
        if own.queue >= 1 and beats_rival:
            seconds = max(config.min_extension, min(room, own.queue * config.saturation_headway))
            rival_text = f"{best_rival.direction} {best_rival.priority:.0f}" if best_rival else "no rival demand"
            return Decision(
                "EXTEND", current, seconds,
                f"{current} still discharging (priority {own.priority:.0f} vs {rival_text})", scores,
            )

    if best_rival is not None:
        own_text = f" over {current} {own.priority:.0f}" if own is not None else ""
        return switch(best_rival, f"{best_rival.direction} has highest priority {best_rival.priority:.0f}{own_text}")

    if own is not None and room >= config.min_extension:
        return Decision("EXTEND", current, config.min_extension, "no conflicting demand, resting on current green", scores)
    return Decision("HOLD", current, 0.0, "no demand on any approach", scores)
