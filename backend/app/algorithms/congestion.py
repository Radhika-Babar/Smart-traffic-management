"""Congestion classification with configurable thresholds and hysteresis."""

from __future__ import annotations

from dataclasses import dataclass

LEVELS: tuple[str, ...] = ("LOW", "MODERATE", "HIGH", "SEVERE")


@dataclass(frozen=True)
class CongestionThresholds:
    """Each tuple holds the bounds for MODERATE, HIGH and SEVERE."""

    queue: tuple[float, float, float] = (8, 16, 26)
    occupancy: tuple[float, float, float] = (35, 60, 80)
    wait: tuple[float, float, float] = (40, 75, 110)
    # Speed falls as congestion rises, so these are upper bounds.
    speed: tuple[float, float, float] = (9.0, 5.5, 3.0)
    alert_level: str = "HIGH"
    raise_after_s: float = 45.0
    clear_after_s: float = 120.0

    def __post_init__(self) -> None:
        for name in ("queue", "occupancy", "wait"):
            bounds = tuple(getattr(self, name))
            if list(bounds) != sorted(bounds):
                raise ValueError(f"{name} thresholds must increase")
        if list(self.speed) != sorted(self.speed, reverse=True):
            raise ValueError("speed thresholds must decrease")
        if self.alert_level not in LEVELS:
            raise ValueError("alert_level must be a congestion level")


@dataclass(frozen=True)
class CongestionInput:
    queue: float
    occupancy: float
    wait: float
    speed: float | None


def _rising(value: float, bounds: tuple[float, float, float]) -> int:
    return sum(value >= b for b in bounds)


def _falling(value: float, bounds: tuple[float, float, float]) -> int:
    return sum(value <= b for b in bounds)


def classify(item: CongestionInput, thresholds: CongestionThresholds) -> str:
    """Level confirmed by at least two independent indicators.

    Using the second-highest indicator means one noisy or stuck metric, such as
    a short red-phase queue, cannot raise the classification on its own.
    """
    ranks = [
        _rising(item.queue, thresholds.queue),
        _rising(item.occupancy, thresholds.occupancy),
        _rising(item.wait, thresholds.wait),
    ]
    if item.speed is not None:
        ranks.append(_falling(item.speed, thresholds.speed))
    ranks.sort(reverse=True)
    return LEVELS[ranks[1]]


def level_rank(level: str) -> int:
    return LEVELS.index(level)


def recommend(level: str, direction: str) -> str:
    if level == "SEVERE":
        return f"Extend green on {direction}, divert upstream traffic and inspect the approach"
    if level == "HIGH":
        return f"Monitor {direction} and allow the adaptive controller to raise its green share"
    return "No action needed"


@dataclass
class ApproachCongestion:
    """Debounced state of one approach."""

    level: str = "LOW"
    above_since: float | None = None
    below_since: float | None = None
    open_level: str | None = None
    peak_level: str | None = None
    record_id: str | None = None


@dataclass(frozen=True)
class CongestionChange:
    kind: str  # OPENED | ESCALATED | RESOLVED
    level: str


def update(
    state: ApproachCongestion, level: str, now: float, thresholds: CongestionThresholds
) -> CongestionChange | None:
    """Advance one approach and report when an alert should open, grow or close."""
    state.level = level
    alerting = level_rank(level) >= level_rank(thresholds.alert_level)
    if alerting:
        state.below_since = None
        if state.above_since is None:
            state.above_since = now
        if state.open_level is None:
            if now - state.above_since >= thresholds.raise_after_s:
                state.open_level = state.peak_level = level
                return CongestionChange("OPENED", level)
        elif level_rank(level) > level_rank(state.peak_level or "LOW"):
            state.open_level = state.peak_level = level
            return CongestionChange("ESCALATED", level)
        return None
    state.above_since = None
    if state.open_level is None:
        return None
    if state.below_since is None:
        state.below_since = now
    if now - state.below_since >= thresholds.clear_after_s:
        peak = state.peak_level or level
        state.open_level = state.peak_level = None
        state.below_since = None
        return CongestionChange("RESOLVED", peak)
    return None
