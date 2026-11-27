"""Sensor-based incident detection.

Works from per-approach sensor time series only. It never sees the injected
ground truth, so a simulated accident must really disturb the readings to be
caught. Normal stop-and-go at a red light must not trigger it: the speed
sensor sits mid-block, upstream of the usual queue.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectorConfig:
    speed_drop_ratio: float = 0.6
    severe_speed_ratio: float = 0.5
    # Above this queue a slow mid-block reading may just be the signal queue backing up.
    queue_spillback: float = 9.0
    detached_queue: float = 5.0
    detached_occupancy: float = 20.0
    queue_growth_per_min: float = 8.0
    wait_jump_ratio: float = 1.6
    min_baseline_samples: int = 6
    confirm_samples: int = 3
    clear_samples: int = 8
    baseline_alpha: float = 0.08
    min_score: float = 0.6
    history_seconds: float = 60.0


@dataclass(frozen=True)
class Sample:
    t: float
    speed: float | None
    queue: float
    wait: float
    served: float  # vehicles discharged since the previous sample
    had_green: bool
    occupancy: float = 100.0  # stop-line detector


@dataclass(frozen=True)
class Detection:
    kind: str  # DETECTED | CLEARED
    severity: str
    score: float
    evidence: tuple[str, ...]


@dataclass
class ApproachDetector:
    config: DetectorConfig = field(default_factory=DetectorConfig)
    speed_baseline: float | None = None
    wait_baseline: float | None = None
    samples: int = 0
    history: deque = field(default_factory=deque)
    streak: int = 0
    calm: int = 0
    active: bool = False

    def _queue_growth(self, sample: Sample) -> float:
        oldest = self.history[0] if self.history else None
        if oldest is None or sample.t - oldest.t < 20:
            return 0.0
        return (sample.queue - oldest.queue) / (sample.t - oldest.t) * 60.0

    def _score(self, sample: Sample) -> tuple[float, tuple[str, ...]]:
        """Score how strongly this sample looks like an obstruction.

        A signal queue always sits on the stop-line detector. An obstruction
        leaves the queue detached from it: many stopped vehicles, little
        stop-line occupancy. That separates incidents from ordinary spillback,
        where mid-block speed also collapses but the queue reaches the line.
        """
        cfg = self.config
        detached = sample.queue >= cfg.detached_queue and sample.occupancy < cfg.detached_occupancy
        if sample.queue >= cfg.queue_spillback and not detached:
            return 0.0, ()
        evidence: list[str] = []
        score = 0.0
        speed_low = False
        if sample.speed is not None and self.speed_baseline:
            ratio = sample.speed / self.speed_baseline
            if ratio < cfg.speed_drop_ratio:
                speed_low = True
                score += 0.65 if ratio < cfg.severe_speed_ratio else 0.5
                evidence.append(f"Average speed fell to {ratio * 100:.0f}% of baseline")
        if detached:
            score += 0.5
            evidence.append(
                f"{sample.queue:.0f} vehicles stopped upstream while stop-line occupancy is {sample.occupancy:.0f}%"
            )
        growth = self._queue_growth(sample)
        if growth >= cfg.queue_growth_per_min:
            score += 0.25
            evidence.append(f"Queue growing {growth:.0f} vehicles/min")
        if self.wait_baseline and sample.wait > max(self.wait_baseline * cfg.wait_jump_ratio, 30):
            score += 0.15
            evidence.append(f"Waiting time {sample.wait:.0f}s against {self.wait_baseline:.0f}s baseline")
        if sample.had_green and sample.served < 1 and sample.queue >= 5:
            score += 0.25
            evidence.append("Flow disrupted: green given but queue did not discharge")
        return (score if speed_low or detached else 0.0), tuple(evidence)

    def update(self, sample: Sample) -> Detection | None:
        cfg = self.config
        while self.history and sample.t - self.history[0].t > cfg.history_seconds:
            self.history.popleft()
        if sample.speed is None and (self.streak or self.active):
            # Nothing passed the sensor, which says nothing either way.
            self.history.append(sample)
            return None
        ready = self.samples >= cfg.min_baseline_samples
        score, evidence = self._score(sample) if ready else (0.0, ())
        self.history.append(sample)
        self.samples += 1
        anomalous = score >= cfg.min_score

        if not anomalous and not self.active:
            alpha = cfg.baseline_alpha
            if sample.speed is not None:
                self.speed_baseline = sample.speed if self.speed_baseline is None else (
                    (1 - alpha) * self.speed_baseline + alpha * sample.speed
                )
            self.wait_baseline = sample.wait if self.wait_baseline is None else (
                (1 - alpha) * self.wait_baseline + alpha * sample.wait
            )

        if anomalous:
            self.streak += 1
            self.calm = 0
            if not self.active and self.streak >= cfg.confirm_samples:
                self.active = True
                severity = "MAJOR" if score >= 0.9 else "MINOR"
                return Detection("DETECTED", severity, min(score, 1.0), evidence)
            return None
        self.streak = 0
        if self.active:
            self.calm += 1
            if self.calm >= cfg.clear_samples:
                self.active = False
                self.calm = 0
                return Detection("CLEARED", "MINOR", 0.0, ())
        return None
