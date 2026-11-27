import pytest

from app.algorithms.congestion import (
    ApproachCongestion,
    CongestionInput,
    CongestionThresholds,
    classify,
    update,
)
from app.algorithms.incident_detection import ApproachDetector, Sample
from app.algorithms.preemption import PreemptionManager
from app.algorithms.priority import (
    AlgorithmConfig,
    ApproachInput,
    PriorityWeights,
    decide,
    plan_green,
    score_all,
    score_approach,
)

CONFIG = AlgorithmConfig()


def approach(direction: str, queue=0.0, waiting=0.0, density=0.0, approaching=0.0, speed=None) -> ApproachInput:
    return ApproachInput(direction, queue, waiting, density, approaching, speed)


class TestPriority:
    def test_score_is_bounded_0_to_100(self) -> None:
        huge = score_approach(approach("NORTH", 999, 9999, 100, 999, 0.0), CONFIG)
        assert huge.priority == pytest.approx(100)
        assert score_approach(approach("NORTH"), CONFIG).priority == 0

    def test_normalisation_clamps_each_input(self) -> None:
        score = score_approach(approach("NORTH", queue=60, waiting=500, density=150), CONFIG)
        assert score.queue_norm == 1 and score.waiting_norm == 1 and score.density_norm == 1

    def test_formula_matches_hand_calculation(self) -> None:
        weights = PriorityWeights(queue=0.4, waiting=0.3, density=0.2, demand=0.1, speed=0.0)
        config = AlgorithmConfig(weights=weights)
        score = score_approach(approach("NORTH", queue=15, waiting=60, density=50, approaching=5), config)
        expected = 100 * (0.4 * 0.5 + 0.3 * 0.5 + 0.2 * 0.5 + 0.1 * 0.5)
        assert score.priority == pytest.approx(expected)

    def test_score_grows_with_each_pressure_input(self) -> None:
        base = score_approach(approach("NORTH", 5, 10, 20, 2), CONFIG).priority
        assert score_approach(approach("NORTH", 10, 10, 20, 2), CONFIG).priority > base
        assert score_approach(approach("NORTH", 5, 40, 20, 2), CONFIG).priority > base
        assert score_approach(approach("NORTH", 5, 10, 60, 2), CONFIG).priority > base

    def test_empty_approach_scores_zero_despite_stale_density(self) -> None:
        assert score_approach(approach("NORTH", density=40, waiting=50), CONFIG).priority == 0

    def test_weights_must_be_valid(self) -> None:
        with pytest.raises(ValueError):
            PriorityWeights(queue=-1)
        with pytest.raises(ValueError):
            PriorityWeights(0, 0, 0, 0, 0)

    def test_spec_example_selects_north(self) -> None:
        scores = score_all(
            [
                approach("NORTH", 35, 45, 82, 5),
                approach("SOUTH", 12, 20, 40, 3),
                approach("EAST", 8, 10, 25, 2),
                approach("WEST", 20, 35, 60, 4),
            ],
            CONFIG,
        )
        decision = decide(scores, None, 0, 8, 60, CONFIG)
        assert decision.action == "SWITCH" and decision.direction == "NORTH"

    def test_green_time_tracks_queue_within_limits(self) -> None:
        small = score_approach(approach("NORTH", queue=1), CONFIG)
        medium = score_approach(approach("NORTH", queue=12), CONFIG)
        large = score_approach(approach("NORTH", queue=200), CONFIG)
        assert plan_green(small, CONFIG, 8, 60) == 8
        assert 8 < plan_green(medium, CONFIG, 8, 60) < 60
        assert plan_green(large, CONFIG, 8, 60) == 60


class TestDecision:
    def test_extends_while_discharging_and_clearly_ahead(self) -> None:
        scores = score_all([approach("NORTH", 25, 30, 70, 5), approach("EAST", 3, 10, 15, 1)], CONFIG)
        decision = decide(scores, "NORTH", 15, 8, 60, CONFIG)
        assert decision.action == "EXTEND" and decision.direction == "NORTH"

    def test_switches_when_rival_is_stronger(self) -> None:
        scores = score_all([approach("NORTH", 2, 3, 10, 1), approach("EAST", 20, 60, 70, 3)], CONFIG)
        decision = decide(scores, "NORTH", 15, 8, 60, CONFIG)
        assert decision.action == "SWITCH" and decision.direction == "EAST"

    def test_hysteresis_prefers_switching_only_past_margin(self) -> None:
        scores = score_all([approach("NORTH", 10, 30, 40, 2), approach("EAST", 10, 30, 40, 2)], CONFIG)
        assert decide(scores, "NORTH", 15, 8, 60, CONFIG).action == "SWITCH"

    def test_starving_approach_beats_higher_score(self) -> None:
        scores = score_all([approach("NORTH", 28, 40, 90, 8), approach("EAST", 1, 125, 5, 0)], CONFIG)
        decision = decide(scores, "SOUTH", 20, 8, 60, CONFIG)
        assert decision.direction == "EAST"
        assert "starvation" in decision.reason

    def test_no_rival_rests_on_green(self) -> None:
        scores = score_all([approach("NORTH", 0, 0, 2, 1), approach("EAST")], CONFIG)
        assert decide(scores, "NORTH", 20, 8, 60, CONFIG).action == "EXTEND"

    def test_no_extension_past_max_green(self) -> None:
        scores = score_all([approach("NORTH", 25, 30, 70, 5), approach("EAST", 3, 10, 15, 1)], CONFIG)
        decision = decide(scores, "NORTH", 58, 8, 60, CONFIG)
        assert decision.action == "SWITCH" and decision.direction == "EAST"

    def test_nothing_to_do(self) -> None:
        scores = score_all([approach("NORTH"), approach("EAST")], CONFIG)
        assert decide(scores, "NORTH", 59, 8, 60, CONFIG).action == "HOLD"


class TestCongestion:
    T = CongestionThresholds()

    @pytest.mark.parametrize(
        ("queue", "occupancy", "wait", "speed", "level"),
        [
            (1, 5, 5, 13, "LOW"),
            (10, 40, 20, 12, "MODERATE"),
            (18, 65, 50, 8, "HIGH"),
            (30, 90, 130, 2, "SEVERE"),
        ],
    )
    def test_levels(self, queue, occupancy, wait, speed, level) -> None:
        assert classify(CongestionInput(queue, occupancy, wait, speed), self.T) == level

    def test_single_indicator_cannot_raise_level(self) -> None:
        assert classify(CongestionInput(40, 5, 5, 13), self.T) == "LOW"

    def test_missing_speed_is_tolerated(self) -> None:
        assert classify(CongestionInput(30, 90, 130, None), self.T) == "SEVERE"

    def test_thresholds_are_validated(self) -> None:
        with pytest.raises(ValueError):
            CongestionThresholds(queue=(20, 10, 30))
        with pytest.raises(ValueError):
            CongestionThresholds(speed=(1, 2, 3))

    def test_alert_needs_sustained_condition_and_clears_with_hysteresis(self) -> None:
        state = ApproachCongestion()
        assert update(state, "HIGH", 0, self.T) is None
        assert update(state, "HIGH", 15, self.T) is None
        opened = update(state, "HIGH", 46, self.T)
        assert opened and opened.kind == "OPENED"
        escalated = update(state, "SEVERE", 50, self.T)
        assert escalated and escalated.kind == "ESCALATED"
        assert update(state, "LOW", 60, self.T) is None
        assert update(state, "HIGH", 70, self.T) is None, "dip does not close it"
        assert update(state, "LOW", 80, self.T) is None
        resolved = update(state, "LOW", 201, self.T)
        assert resolved and resolved.kind == "RESOLVED" and resolved.level == "SEVERE"

    def test_short_spike_never_alerts(self) -> None:
        state = ApproachCongestion()
        assert update(state, "SEVERE", 0, self.T) is None
        assert update(state, "LOW", 10, self.T) is None
        assert update(state, "SEVERE", 20, self.T) is None
        assert update(state, "SEVERE", 60, self.T) is None


class TestIncidentDetector:
    @staticmethod
    def feed_normal(detector: ApproachDetector, seconds=120, start=0.0) -> float:
        t = start
        for i in range(int(seconds / 5)):
            sample = Sample(t, 12.5 + (i % 3) * 0.4, 4 + (i % 4), 18 + (i % 5), 3, i % 3 == 0, 45.0)
            assert detector.update(sample) is None
            t += 5
        return t

    def test_normal_traffic_never_alarms(self) -> None:
        self.feed_normal(ApproachDetector(), 3600)

    def test_speed_collapse_with_growing_queue_is_detected_then_clears(self) -> None:
        detector = ApproachDetector()
        t = self.feed_normal(detector)
        detections = []
        for i in range(10):
            result = detector.update(Sample(t, 3.0, 6 + 3 * i, 30 + 5 * i, 0, True, 4.0))
            if result:
                detections.append(result)
            t += 5
        assert detections and detections[0].kind == "DETECTED"
        assert any("speed" in e.lower() for e in detections[0].evidence)
        cleared = [r for r in (detector.update(Sample(t + 5 * i, 12.5, 4, 18, 3, True, 40.0)) for i in range(10)) if r]
        assert cleared and cleared[0].kind == "CLEARED"

    def test_signal_queue_spillback_is_not_an_incident(self) -> None:
        detector = ApproachDetector()
        t = self.feed_normal(detector)
        for i in range(20):
            sample = Sample(t + 5 * i, 2.0, 16 + i, 80, 0, True, 92.0)
            assert detector.update(sample) is None

    def test_quiet_interval_does_not_reset_confirmation(self) -> None:
        detector = ApproachDetector()
        t = self.feed_normal(detector)
        results = [
            detector.update(Sample(t + 5 * i, speed, 2, 10, 1, False, 10.0))
            for i, speed in enumerate([3.0, None, 3.0, None, 3.0])
        ]
        assert results[-1] is not None

    def test_one_bad_sample_is_ignored(self) -> None:
        detector = ApproachDetector()
        t = self.feed_normal(detector)
        assert detector.update(Sample(t, 2.0, 30, 90, 0, True)) is None
        self.feed_normal(detector, 60, t + 5)

    def test_no_detection_before_a_baseline_exists(self) -> None:
        detector = ApproachDetector()
        for i in range(5):
            assert detector.update(Sample(i * 5.0, 1.0, 30, 100, 0, True)) is None


class TestPreemptionManager:
    def test_requests_near_intersection_and_releases_after_passing(self) -> None:
        manager = PreemptionManager()
        assert manager.on_telemetry("EV-1", "EN_ROUTE", "INT-001", "WEST", 500, 40) == []
        first = manager.on_telemetry("EV-1", "EN_ROUTE", "INT-001", "WEST", 200, 15)
        assert [(a.action, a.intersection_id, a.direction) for a in first] == [("PREEMPT", "INT-001", "WEST")]
        assert manager.on_telemetry("EV-1", "EN_ROUTE", "INT-001", "WEST", 100, 8) == [], "no duplicates"
        passed = manager.on_telemetry("EV-1", "EN_ROUTE", "INT-002", "WEST", 380, 30)
        assert [(a.action, a.intersection_id) for a in passed] == [("RELEASE", "INT-001")]

    def test_arrival_releases_everything(self) -> None:
        manager = PreemptionManager()
        manager.on_telemetry("EV-1", "EN_ROUTE", "INT-001", "WEST", 100, 8)
        done = manager.on_telemetry("EV-1", "ARRIVED", None, None, None, None)
        assert [a.action for a in done] == ["RELEASE"]
        assert manager.active == {}
