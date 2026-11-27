"""Central emergency-preemption bookkeeping.

Decides *when* to ask a field controller for priority and when to release it.
How the signal gets there safely is the FSM's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PreemptionConfig:
    activation_eta_s: float = 25.0
    activation_distance_m: float = 260.0


@dataclass(frozen=True)
class PreemptAction:
    action: str  # PREEMPT | RELEASE
    intersection_id: str
    vehicle_id: str
    direction: str | None = None


@dataclass
class PreemptionManager:
    config: PreemptionConfig = field(default_factory=PreemptionConfig)
    # vehicle_id -> {intersection_id: direction}
    active: dict[str, dict[str, str]] = field(default_factory=dict)

    def on_telemetry(
        self,
        vehicle_id: str,
        status: str,
        next_intersection: str | None,
        approach: str | None,
        distance_m: float | None,
        eta_s: float | None,
    ) -> list[PreemptAction]:
        held = self.active.setdefault(vehicle_id, {})
        actions: list[PreemptAction] = []
        finished = status != "EN_ROUTE"
        for intersection in list(held):
            if finished or intersection != next_intersection:
                actions.append(PreemptAction("RELEASE", intersection, vehicle_id))
                del held[intersection]
        if finished:
            self.active.pop(vehicle_id, None)
            return actions
        if next_intersection is None or approach is None or next_intersection in held:
            return actions
        close = (eta_s is not None and eta_s <= self.config.activation_eta_s) or (
            distance_m is not None and distance_m <= self.config.activation_distance_m
        )
        if close:
            held[next_intersection] = approach
            actions.append(PreemptAction("PREEMPT", next_intersection, vehicle_id, approach))
        return actions

    def clear(self) -> None:
        self.active.clear()
