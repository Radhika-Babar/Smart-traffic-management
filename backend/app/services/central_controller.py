"""Central adaptive controller.

Runs on the backend, fed only by sensor-derived approach data. Sends requests to
field controllers; it holds no authority over the signal sequence itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.algorithms.priority import AlgorithmConfig, ApproachInput, Decision, decide, score_all
from app.schemas.messages import SignalStateMessage

ADAPTIVE = "ADAPTIVE"
FIXED_TIME = "FIXED_TIME"


@dataclass
class CentralController:
    config: AlgorithmConfig = field(default_factory=AlgorithmConfig)
    strategy: str = ADAPTIVE
    # Serial of the green the last SWITCH applied to, so one green gets one switch.
    _switched: dict[str, int] = field(default_factory=dict)

    def evaluate(self, state: SignalStateMessage, serial: int, inputs: list[ApproachInput]) -> Decision | None:
        """Decide on a running green if it is due. Returns None when nothing is needed."""
        if self.strategy != ADAPTIVE or state.mode != "AUTOMATIC" or state.preempt_vehicle:
            return None
        if state.phase != "GREEN" or state.controller == "FAILED" or not inputs:
            return None
        if self._switched.get(state.intersection_id) == serial:
            return None
        current = state.active_direction
        scores = score_all(inputs, self.config)
        by_direction = {s.direction: s for s in scores}
        own = by_direction.get(current)
        rival_waiting = any(s.has_demand and s.direction != current for s in scores)
        gap_out = own is not None and not own.has_demand and rival_waiting
        due = state.remaining <= self.config.decision_lookahead or gap_out
        if not due:
            return None
        decision = decide(scores, current, state.elapsed, state.min_green, state.max_green, self.config)
        if decision.action == "SWITCH":
            self._switched[state.intersection_id] = serial
        elif decision.action == "HOLD":
            return None
        return decision

    def reset(self) -> None:
        self._switched.clear()
