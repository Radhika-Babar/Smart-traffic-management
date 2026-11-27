"""Virtual roadside signal cabinet.

Owns the safety FSM for one intersection. Commands from the central system
arrive over the bus and are applied on the simulation thread. A command that
the FSM refuses is logged and dropped: the signal keeps running safely.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable

from app.algorithms.signal_fsm import (
    ControlMode,
    RejectedCommand,
    SignalFSM,
    SignalTiming,
    Transition,
)
from app.mqtt import topics

logger = logging.getLogger(__name__)

HEARTBEAT_S = 1.0
# With no central command for this long the cabinet reports FAILSAFE and runs
# its own fixed rotation, which is what the FSM does when left alone.
COMMAND_TIMEOUT_S = 90.0


class FieldController:
    def __init__(
        self,
        intersection_id: str,
        directions: tuple[str, ...],
        timing: SignalTiming,
        publish: Callable[[str, dict], None],
        timestamp: Callable[[], str],
        mode: ControlMode = ControlMode.AUTOMATIC,
    ) -> None:
        self.intersection_id = intersection_id
        self.fsm = SignalFSM(directions=directions, timing=timing, mode=mode)
        self._publish = publish
        self._timestamp = timestamp
        self._inbox: deque[dict] = deque()
        self._next_heartbeat = 0.0
        self.last_command_at = 0.0
        self.failed_until: float | None = None
        self.rejected_commands = 0
        self.expect_commands = True

    # Called from the bus thread.
    def enqueue(self, command: dict) -> None:
        self._inbox.append(command)

    @property
    def status(self) -> str:
        if self.failed_until is not None:
            return "FAILED"
        if self.expect_commands and self.fsm.clock - self.last_command_at > COMMAND_TIMEOUT_S:
            return "FAILSAFE"
        return "ONLINE"

    def fail(self, duration: float) -> None:
        """Simulated cabinet fault: rotation continues locally but reports stop."""
        self.failed_until = self.fsm.clock + duration

    def recover(self) -> None:
        self.failed_until = None
        self.last_command_at = self.fsm.clock

    def tick(self, dt: float) -> None:
        if self.failed_until is not None and self.fsm.clock >= self.failed_until:
            self.recover()
        while self._inbox:
            command = self._inbox.popleft()
            if self.failed_until is None:
                self._apply(command)
        transitions = self.fsm.tick(dt)
        if self.failed_until is not None:
            return
        for transition in transitions:
            self._send_state(transition)
        if self.fsm.clock >= self._next_heartbeat:
            self._next_heartbeat = self.fsm.clock + HEARTBEAT_S
            if not transitions:
                self._send_state(None)

    def _apply(self, command: dict) -> None:
        action = command.get("action")
        self.last_command_at = self.fsm.clock
        try:
            if action == "SWITCH":
                self.fsm.request_switch(
                    command["direction"], command.get("seconds") or self.fsm.timing.default_green,
                    command.get("reason") or "central request",
                )
            elif action == "EXTEND":
                self.fsm.request_extend(command["seconds"])
            elif action == "PREEMPT":
                self.fsm.request_preempt(command["vehicle_id"], command["direction"])
            elif action == "RELEASE":
                self.fsm.release_preempt(command["vehicle_id"])
            elif action == "SET_MODE":
                self.fsm.set_mode(ControlMode(command["mode"]))
            else:
                raise RejectedCommand(f"unknown action {action!r}")
        except (RejectedCommand, KeyError) as error:
            self.rejected_commands += 1
            # A late EXTEND at high speed is normal, so this is not a warning.
            logger.info("%s rejected command %s: %s", self.intersection_id, command, error)

    def _send_state(self, transition: Transition | None) -> None:
        fsm = self.fsm
        preempt = fsm.preempt
        self._publish(topics.signal_state(self.intersection_id), {
            "intersection_id": self.intersection_id,
            "phase": fsm.phase.value,
            "active_direction": fsm.active,
            "heads": fsm.heads(),
            "elapsed": round(fsm.elapsed, 2),
            "planned_green": round(fsm.planned_green, 2),
            "remaining": round(fsm.remaining, 2),
            "mode": fsm.mode.value,
            "controller": self.status,
            "preempt_vehicle": preempt.vehicle_id if preempt else None,
            "preempt_direction": preempt.direction if preempt else None,
            "transition": transition is not None,
            "previous_phase": transition.previous.value if transition else None,
            "reason": transition.reason if transition else None,
            "green_duration": transition.green_duration if transition else None,
            "min_green": fsm.timing.min_green,
            "max_green": fsm.timing.max_green,
            "timestamp": self._timestamp(),
            "simulated": True,
        })
