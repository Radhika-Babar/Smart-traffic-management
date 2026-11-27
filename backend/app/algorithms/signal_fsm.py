"""Traffic signal finite-state machine.

This is the safety kernel of the project. It has no I/O and no knowledge of
traffic: it only guarantees the signal sequence

    RED -> GREEN -> YELLOW -> ALL_RED -> GREEN

Every external influence (the adaptive algorithm, an operator, an emergency
preemption) is a *request*. A request can change which direction is served next
or how long green lasts, but it can never skip yellow or the all-red clearance,
shorten green below the minimum, or produce two greens at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

DIRECTIONS: tuple[str, ...] = ("NORTH", "EAST", "SOUTH", "WEST")

# Split phasing: each approach carries through, left and right movements on a
# single lane, so every pair of approaches conflicts.
CONFLICTS: dict[str, frozenset[str]] = {
    d: frozenset(o for o in DIRECTIONS if o != d) for d in DIRECTIONS
}


class Phase(str, Enum):
    RED = "RED"
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ALL_RED = "ALL_RED"


class ControlMode(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"


class InvalidTransition(RuntimeError):
    """Raised when code attempts a phase change outside the allowed sequence."""


class RejectedCommand(ValueError):
    """Raised when a request is well formed but not allowed right now."""


ALLOWED_TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    Phase.RED: frozenset({Phase.GREEN}),
    Phase.GREEN: frozenset({Phase.YELLOW}),
    Phase.YELLOW: frozenset({Phase.ALL_RED}),
    Phase.ALL_RED: frozenset({Phase.GREEN}),
}


@dataclass(frozen=True)
class SignalTiming:
    min_green: float = 8.0
    max_green: float = 60.0
    yellow: float = 3.0
    all_red: float = 2.0
    default_green: float = 20.0
    preempt_max_hold: float = 45.0

    def __post_init__(self) -> None:
        if self.min_green < 3:
            raise ValueError("min_green must be at least 3 seconds")
        if self.max_green < self.min_green:
            raise ValueError("max_green must not be below min_green")
        if not self.min_green <= self.default_green <= self.max_green:
            raise ValueError("default_green must lie between min_green and max_green")
        if self.yellow < 2:
            raise ValueError("yellow must be at least 2 seconds")
        if self.all_red < 1:
            raise ValueError("all_red must be at least 1 second")
        if self.preempt_max_hold < self.min_green:
            raise ValueError("preempt_max_hold must not be below min_green")


@dataclass
class Preemption:
    vehicle_id: str
    direction: str
    requested_at: float
    served_at: float | None = None


@dataclass(frozen=True)
class Transition:
    previous: Phase
    phase: Phase
    direction: str | None
    reason: str
    green_duration: float | None = None


@dataclass
class SignalFSM:
    directions: tuple[str, ...] = DIRECTIONS
    timing: SignalTiming = field(default_factory=SignalTiming)
    mode: ControlMode = ControlMode.AUTOMATIC

    phase: Phase = Phase.RED
    active: str | None = None
    elapsed: float = 0.0
    clock: float = 0.0
    planned_green: float = 0.0
    next_direction: str | None = None
    next_green: float | None = None
    next_reason: str | None = None
    end_requested: bool = False
    preempts: list[Preemption] = field(default_factory=list)
    last_served: str | None = None

    def __post_init__(self) -> None:
        self.directions = tuple(d for d in DIRECTIONS if d in self.directions)
        if not self.directions:
            raise ValueError("an intersection needs at least one direction")

    # ---- observation -----------------------------------------------------

    def heads(self) -> dict[str, str]:
        """Aspect shown to each approach."""
        aspects = {d: "RED" for d in self.directions}
        if self.active is not None and self.phase is Phase.GREEN:
            aspects[self.active] = "GREEN"
        elif self.active is not None and self.phase is Phase.YELLOW:
            aspects[self.active] = "YELLOW"
        return aspects

    @property
    def remaining(self) -> float:
        return max(0.0, self.planned_green - self.elapsed) if self.phase is Phase.GREEN else 0.0

    @property
    def preempt(self) -> Preemption | None:
        return self.preempts[0] if self.preempts else None

    # ---- requests --------------------------------------------------------

    def request_switch(self, direction: str, green: float, reason: str = "adaptive") -> None:
        """Serve `direction` next and end the current green once that is safe."""
        self._require_direction(direction)
        self.next_direction = direction
        self.next_green = self._clamp_green(green)
        self.next_reason = reason
        if self.phase is Phase.GREEN and direction != self.active:
            self.end_requested = True

    def request_extend(self, seconds: float) -> float:
        """Lengthen the running green. The result never exceeds max_green."""
        if self.phase is not Phase.GREEN:
            raise RejectedCommand(f"cannot extend green during {self.phase.value}")
        if seconds <= 0:
            raise RejectedCommand("extension must be positive")
        self.planned_green = min(self.timing.max_green, max(self.planned_green, self.elapsed + seconds))
        return self.planned_green

    def request_preempt(self, vehicle_id: str, direction: str) -> None:
        self._require_direction(direction)
        if any(p.vehicle_id == vehicle_id for p in self.preempts):
            return
        self.preempts.append(Preemption(vehicle_id, direction, self.clock))

    def release_preempt(self, vehicle_id: str) -> bool:
        before = len(self.preempts)
        self.preempts = [p for p in self.preempts if p.vehicle_id != vehicle_id]
        return len(self.preempts) != before

    def set_mode(self, mode: ControlMode) -> None:
        self.mode = mode
        if mode is ControlMode.MANUAL:
            self.next_direction = None
            self.next_green = None
            self.end_requested = False
        elif self.phase is Phase.GREEN:
            self.planned_green = min(self.planned_green, self.timing.max_green)

    def set_timing(self, timing: SignalTiming) -> None:
        self.timing = timing

    def set_directions(self, directions: tuple[str, ...]) -> None:
        enabled = tuple(d for d in DIRECTIONS if d in directions)
        if not enabled:
            raise RejectedCommand("at least one direction must remain enabled")
        self.directions = enabled
        if self.next_direction not in enabled:
            self.next_direction = None
        if self.active is not None and self.active not in enabled and self.phase is Phase.GREEN:
            self.end_requested = True
        self.preempts = [p for p in self.preempts if p.direction in enabled]

    # ---- time ------------------------------------------------------------

    def tick(self, dt: float) -> list[Transition]:
        self.clock += dt
        self.elapsed += dt
        self._expire_preempt()
        events: list[Transition] = []
        if self.phase in (Phase.RED, Phase.ALL_RED):
            if self.elapsed >= self.timing.all_red:
                events.append(self._begin_green())
        elif self.phase is Phase.GREEN:
            reason = self._green_end_reason()
            if reason is not None:
                events.append(self._transition(Phase.YELLOW, self.active, reason))
        elif self.phase is Phase.YELLOW and self.elapsed >= self.timing.yellow:
            events.append(self._transition(Phase.ALL_RED, None, "yellow complete"))
        return events

    # ---- internals -------------------------------------------------------

    def _require_direction(self, direction: str) -> None:
        if direction not in self.directions:
            raise RejectedCommand(f"direction {direction!r} is not enabled here")

    def _clamp_green(self, green: float) -> float:
        return min(self.timing.max_green, max(self.timing.min_green, green))

    def _expire_preempt(self) -> None:
        current = self.preempt
        if current is not None and current.served_at is not None:
            if self.clock - current.served_at >= self.timing.preempt_max_hold:
                self.preempts.pop(0)

    def _green_end_reason(self) -> str | None:
        current = self.preempt
        if current is not None:
            if current.direction == self.active:
                if current.served_at is None:
                    current.served_at = self.clock
                return None
            if self.elapsed >= self.timing.min_green:
                return f"preemption for {current.vehicle_id}"
            return None
        if self.elapsed < self.timing.min_green:
            return None
        if self.elapsed >= self.timing.max_green:
            return "maximum green reached"
        if self.end_requested:
            return self.next_reason or "switch requested"
        if self.mode is ControlMode.AUTOMATIC and self.elapsed >= self.planned_green:
            return "planned green complete"
        return None

    def _next_in_rotation(self) -> str:
        anchor = self.active or self.last_served
        if anchor not in self.directions:
            return self.directions[0]
        index = self.directions.index(anchor)
        return self.directions[(index + 1) % len(self.directions)]

    def _begin_green(self) -> Transition:
        current = self.preempt
        if current is not None:
            direction, green, reason = current.direction, self.timing.min_green, f"preemption for {current.vehicle_id}"
            current.served_at = self.clock
        elif self.next_direction is not None:
            direction = self.next_direction
            green = self.next_green or self.timing.default_green
            reason = self.next_reason or "requested"
        else:
            direction = self._next_in_rotation()
            green = self.timing.default_green
            reason = "local rotation"
        event = self._transition(Phase.GREEN, direction, reason, green)
        self.planned_green = green
        self.next_direction = None
        self.next_green = None
        self.next_reason = None
        self.end_requested = False
        return event

    def _transition(
        self, phase: Phase, direction: str | None, reason: str, green: float | None = None
    ) -> Transition:
        if phase not in ALLOWED_TRANSITIONS[self.phase]:
            raise InvalidTransition(f"{self.phase.value} -> {phase.value} is not allowed")
        previous = self.phase
        if self.active is not None:
            self.last_served = self.active
        self.phase = phase
        self.active = direction
        self.elapsed = 0.0
        return Transition(previous, phase, direction, reason, green)
