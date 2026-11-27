"""Safety tests for the signal state machine.

The fuzz test is the most important test in the project: it throws random
requests at the controller and checks the safety invariants on every tick.
"""

import random

import pytest

from app.algorithms.signal_fsm import (
    ALLOWED_TRANSITIONS,
    CONFLICTS,
    DIRECTIONS,
    ControlMode,
    InvalidTransition,
    Phase,
    RejectedCommand,
    SignalFSM,
    SignalTiming,
)

DT = 0.5


def run(fsm: SignalFSM, seconds: float) -> list:
    events = []
    for _ in range(round(seconds / DT)):
        events.extend(fsm.tick(DT))
    return events


def run_until(fsm: SignalFSM, phase: Phase, limit: float = 300) -> None:
    for _ in range(round(limit / DT)):
        if fsm.phase is phase:
            return
        fsm.tick(DT)
    raise AssertionError(f"never reached {phase}")


def assert_no_conflict(fsm: SignalFSM) -> None:
    heads = fsm.heads()
    moving = [d for d, aspect in heads.items() if aspect != "RED"]
    assert len(moving) <= 1, heads
    for direction in moving:
        assert not (CONFLICTS[direction] & set(moving))


def test_starts_all_red_then_serves_first_direction() -> None:
    fsm = SignalFSM()
    assert fsm.phase is Phase.RED
    assert set(fsm.heads().values()) == {"RED"}
    run_until(fsm, Phase.GREEN)
    assert fsm.active in DIRECTIONS


def test_cycle_always_follows_green_yellow_all_red_green() -> None:
    fsm = SignalFSM()
    events = run(fsm, 400)
    sequence = [e.phase for e in events]
    assert len(sequence) > 12
    for previous, current in zip(sequence, sequence[1:], strict=False):
        assert current in ALLOWED_TRANSITIONS[previous]


def test_transition_outside_sequence_is_rejected() -> None:
    fsm = SignalFSM()
    run_until(fsm, Phase.GREEN)
    with pytest.raises(InvalidTransition):
        fsm._transition(Phase.ALL_RED, None, "skip yellow")
    with pytest.raises(InvalidTransition):
        fsm._transition(Phase.GREEN, "NORTH", "green to green")


def test_yellow_and_all_red_durations_are_respected() -> None:
    timing = SignalTiming(min_green=5, max_green=20, yellow=4, all_red=3, default_green=5)
    fsm = SignalFSM(timing=timing)
    run_until(fsm, Phase.YELLOW)
    ticks = 0
    while fsm.phase is Phase.YELLOW:
        fsm.tick(DT)
        ticks += 1
    assert ticks * DT == pytest.approx(4)
    assert fsm.phase is Phase.ALL_RED
    ticks = 0
    while fsm.phase is Phase.ALL_RED:
        fsm.tick(DT)
        ticks += 1
    assert ticks * DT == pytest.approx(3)
    assert fsm.phase is Phase.GREEN


def test_minimum_green_cannot_be_cut_short() -> None:
    fsm = SignalFSM(timing=SignalTiming(min_green=10, max_green=30, default_green=15))
    run_until(fsm, Phase.GREEN)
    other = next(d for d in DIRECTIONS if d != fsm.active)
    fsm.request_switch(other, 10)
    run(fsm, 9)
    assert fsm.phase is Phase.GREEN
    run(fsm, 1.5)
    assert fsm.phase is Phase.YELLOW


def test_maximum_green_cannot_be_exceeded_by_extensions() -> None:
    fsm = SignalFSM(timing=SignalTiming(min_green=5, max_green=25, default_green=10))
    run_until(fsm, Phase.GREEN)
    longest = 0.0
    for _ in range(100):
        if fsm.phase is Phase.GREEN:
            fsm.request_extend(50)
            longest = max(longest, fsm.elapsed)
        fsm.tick(DT)
    assert fsm.planned_green <= 25
    assert longest <= 25


def test_manual_mode_holds_until_operator_then_respects_max_green() -> None:
    fsm = SignalFSM(timing=SignalTiming(min_green=5, max_green=30, default_green=10))
    run_until(fsm, Phase.GREEN)
    fsm.set_mode(ControlMode.MANUAL)
    held = fsm.active
    run(fsm, 20)
    assert fsm.active == held and fsm.phase is Phase.GREEN
    run(fsm, 11)
    assert fsm.phase is not Phase.GREEN or fsm.active != held


def test_manual_switch_still_passes_through_clearance() -> None:
    fsm = SignalFSM()
    run_until(fsm, Phase.GREEN)
    fsm.set_mode(ControlMode.MANUAL)
    target = next(d for d in DIRECTIONS if d != fsm.active)
    fsm.request_switch(target, 15, "operator")
    events = run(fsm, 40)
    phases = [e.phase for e in events]
    assert phases[:3] == [Phase.YELLOW, Phase.ALL_RED, Phase.GREEN]
    assert events[2].direction == target


def test_disabled_direction_is_rejected() -> None:
    fsm = SignalFSM(directions=("NORTH", "SOUTH", "EAST"))
    with pytest.raises(RejectedCommand):
        fsm.request_switch("WEST", 10)
    with pytest.raises(RejectedCommand):
        fsm.request_preempt("EV-1", "WEST")


def test_extend_outside_green_is_rejected() -> None:
    fsm = SignalFSM()
    with pytest.raises(RejectedCommand):
        fsm.request_extend(5)


def test_invalid_timing_rejected() -> None:
    with pytest.raises(ValueError):
        SignalTiming(min_green=30, max_green=20, default_green=25)
    with pytest.raises(ValueError):
        SignalTiming(yellow=1)
    with pytest.raises(ValueError):
        SignalTiming(all_red=0)


def test_preemption_waits_for_min_green_and_uses_clearance() -> None:
    timing = SignalTiming(min_green=10, max_green=60, yellow=3, all_red=2, default_green=30)
    fsm = SignalFSM(timing=timing)
    run_until(fsm, Phase.GREEN)
    target = next(d for d in DIRECTIONS if d != fsm.active)
    fsm.request_preempt("EV-1", target)
    events = run(fsm, 9)
    assert not events, "green must not end before min_green"
    events = run(fsm, 8)
    assert [e.phase for e in events] == [Phase.YELLOW, Phase.ALL_RED, Phase.GREEN]
    assert fsm.active == target


def test_preemption_holds_green_then_returns_to_normal() -> None:
    fsm = SignalFSM(timing=SignalTiming(min_green=5, max_green=20, default_green=10))
    run_until(fsm, Phase.GREEN)
    fsm.request_preempt("EV-1", fsm.active)
    run(fsm, 30)
    assert fsm.phase is Phase.GREEN, "the served direction is held for the vehicle"
    assert fsm.release_preempt("EV-1")
    run(fsm, 1)
    assert fsm.phase is not Phase.GREEN, "held past max, so it ends right after release"


def test_preemption_hold_is_bounded() -> None:
    fsm = SignalFSM(timing=SignalTiming(min_green=5, max_green=20, default_green=10, preempt_max_hold=30))
    run_until(fsm, Phase.GREEN)
    fsm.request_preempt("EV-STUCK", fsm.active)
    run(fsm, 31)
    assert fsm.preempt is None
    assert fsm.phase is not Phase.GREEN or fsm.elapsed < 30


def test_second_preemption_waits_for_the_first() -> None:
    fsm = SignalFSM()
    run_until(fsm, Phase.GREEN)
    first, second = [d for d in DIRECTIONS if d != fsm.active][:2]
    fsm.request_preempt("EV-1", first)
    fsm.request_preempt("EV-2", second)
    run(fsm, 30)
    assert fsm.active == first
    fsm.release_preempt("EV-1")
    run(fsm, 30)
    assert fsm.active == second


def test_disabling_active_direction_ends_green_safely() -> None:
    fsm = SignalFSM()
    run_until(fsm, Phase.GREEN)
    active = fsm.active
    fsm.set_directions(tuple(d for d in DIRECTIONS if d != active))
    events = run(fsm, 15)
    assert events[0].phase is Phase.YELLOW
    assert fsm.active != active


def test_rotation_visits_every_direction_without_commands() -> None:
    fsm = SignalFSM()
    served = {e.direction for e in run(fsm, 200) if e.phase is Phase.GREEN}
    assert served == set(DIRECTIONS)


@pytest.mark.parametrize("seed", range(25))
def test_fuzz_safety_invariants_hold_under_random_requests(seed: int) -> None:
    rng = random.Random(seed)
    timing = SignalTiming(
        min_green=rng.uniform(5, 12), max_green=rng.uniform(25, 60),
        yellow=rng.uniform(2, 5), all_red=rng.uniform(1, 4), default_green=rng.uniform(12, 25),
        preempt_max_hold=30,
    )
    fsm = SignalFSM(timing=timing)
    phase_started = 0.0
    now = 0.0
    previous_phase = fsm.phase
    preempted_green = False
    for _ in range(12000):
        roll = rng.random()
        direction = rng.choice(DIRECTIONS)
        try:
            if roll < 0.05:
                fsm.request_switch(direction, rng.uniform(0, 200))
            elif roll < 0.08:
                fsm.request_extend(rng.uniform(0.1, 100))
            elif roll < 0.09:
                fsm.request_preempt(f"EV-{rng.randint(1, 3)}", direction)
            elif roll < 0.12:
                fsm.release_preempt(f"EV-{rng.randint(1, 3)}")
            elif roll < 0.125:
                fsm.set_mode(rng.choice(list(ControlMode)))
        except RejectedCommand:
            pass

        events = fsm.tick(DT)
        now += DT
        assert_no_conflict(fsm)
        preempted_green = preempted_green or fsm.preempt is not None
        for event in events:
            assert event.phase in ALLOWED_TRANSITIONS[previous_phase]
            duration = now - phase_started
            if previous_phase is Phase.GREEN:
                assert duration >= timing.min_green - DT / 2
                if not preempted_green:
                    assert duration <= timing.max_green + DT
            if previous_phase is Phase.YELLOW:
                assert duration >= timing.yellow - DT / 2
            if previous_phase is Phase.ALL_RED:
                assert duration >= timing.all_red - DT / 2
            if event.phase is Phase.GREEN:
                preempted_green = fsm.preempt is not None
            previous_phase = event.phase
            phase_started = now
