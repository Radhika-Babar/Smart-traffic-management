"""Time-of-day demand profile, shared by the micro-simulation and the history generator."""

from __future__ import annotations

import math

# Fraction of base demand for each hour of a weekday.
_WEEKDAY = (
    0.12, 0.08, 0.06, 0.06, 0.10, 0.25, 0.60, 1.15, 1.55, 1.25, 0.90, 0.85,
    0.92, 0.95, 0.85, 0.90, 1.05, 1.40, 1.60, 1.25, 0.85, 0.60, 0.40, 0.22,
)
# Weekends rise later and have no sharp commuter peaks.
_WEEKEND = (
    0.20, 0.14, 0.10, 0.08, 0.08, 0.12, 0.22, 0.38, 0.58, 0.78, 0.92, 1.02,
    1.08, 1.05, 1.00, 0.98, 1.00, 1.05, 1.02, 0.92, 0.78, 0.62, 0.45, 0.30,
)


def demand_factor(hour: float, weekday: int) -> float:
    """Smoothly interpolated demand multiplier. `weekday` is 0 for Monday."""
    table = _WEEKEND if weekday >= 5 else _WEEKDAY
    base = math.floor(hour) % 24
    fraction = hour - math.floor(hour)
    return table[base] * (1 - fraction) + table[(base + 1) % 24] * fraction
