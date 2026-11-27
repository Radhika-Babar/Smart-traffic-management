"""Historical analytics. Every figure is computed from stored rows."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import iso_utc
from app.models import (
    CongestionEvent,
    EmergencyVehicle,
    Intersection,
    SensorReading,
    SignalStateHistory,
    TrafficMetric,
    TrafficSensor,
    TrafficSignal,
)

COLUMNS = [
    "bucket_start", "bin_seconds", "intersection_id", "direction", "volume", "avg_queue",
    "avg_wait_s", "avg_speed", "density", "occupancy", "congestion_level", "source",
]


def load_metrics(
    session: Session,
    start: datetime,
    end: datetime,
    intersection_id: str | None = None,
    direction: str | None = None,
    condition: str | None = None,
) -> pd.DataFrame:
    filters = [TrafficMetric.bucket_start >= start, TrafficMetric.bucket_start <= end]
    if intersection_id:
        filters.append(TrafficMetric.intersection_id == intersection_id)
    if direction:
        filters.append(TrafficMetric.direction == direction)
    if condition:
        filters.append(TrafficMetric.congestion_level == condition)
    query = select(*[getattr(TrafficMetric, c) for c in COLUMNS]).where(*filters).order_by(TrafficMetric.bucket_start)
    return pd.DataFrame(session.execute(query).all(), columns=COLUMNS)


AVERAGED = {"wait": "avg_wait_s", "speed": "avg_speed", "density": "density", "queue": "avg_queue"}
DIGITS = {"wait": 1, "speed": 2, "density": 1, "queue": 1}


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Add weighted columns once, so every grouping below is a single vectorised sum."""
    frame = frame.copy()
    weight = frame["bin_seconds"].astype(float)
    for name, column in AVERAGED.items():
        values = pd.to_numeric(frame[column], errors="coerce")
        frame[f"_sum_{name}"] = (values * weight).fillna(0.0)
        frame[f"_weight_{name}"] = weight.where(values.notna(), 0.0)
    frame["_congested"] = weight.where(frame["congestion_level"].isin(["HIGH", "SEVERE"]), 0.0)
    return frame


def _summaries(frame: pd.DataFrame, key) -> list[tuple[object, dict]]:
    """Weighted summary for each group of `key`, in one pass."""
    columns = ["volume", "bin_seconds", "_congested"]
    for name in AVERAGED:
        columns += [f"_sum_{name}", f"_weight_{name}"]
    totals = frame.groupby(key, sort=True)[columns].sum()
    results = []
    for group, row in zip(totals.index, totals.to_dict("records"), strict=True):
        summary: dict = {"volume": int(row["volume"])}
        for name in AVERAGED:
            weight = row[f"_weight_{name}"]
            summary[name] = round(row[f"_sum_{name}"] / weight, DIGITS[name]) if weight else None
        summary["congested_pct"] = round(100.0 * row["_congested"] / row["bin_seconds"], 1) if row["bin_seconds"] else 0.0
        results.append((group, summary))
    return results


def _local_offset() -> timedelta:
    return datetime.now().astimezone().utcoffset() or timedelta(0)


def traffic_analytics(
    session: Session,
    start: datetime,
    end: datetime,
    intersection_id: str | None,
    direction: str | None,
    condition: str | None,
    uptime_window_s: float,
) -> dict:
    frame = load_metrics(session, start, end, intersection_id, direction, condition)
    names = dict(session.execute(select(Intersection.id, Intersection.name)).all())
    result: dict = {
        "range": {"start": iso_utc(start), "end": iso_utc(end)},
        "rows": len(frame),
        "sources": {},
        "summary": None,
        "series": [],
        "hourly_profile": [],
        "daily": [],
        "by_intersection": [],
        "by_direction": [],
        "congestion_levels": [],
    }
    if not frame.empty:
        frame["bucket_start"] = pd.to_datetime(frame["bucket_start"])
        frame["local"] = frame["bucket_start"] + _local_offset()
        frame = _prepare(frame)
        result["sources"] = {k: int(v) for k, v in frame["source"].value_counts().items()}
        result["summary"] = _summaries(frame, lambda _: 0)[0][1]

        span_hours = (frame["bucket_start"].max() - frame["bucket_start"].min()).total_seconds() / 3600
        freq = "5min" if span_hours <= 3 else "1h" if span_hours <= 200 else "6h"
        for moment, summary in _summaries(frame, frame["bucket_start"].dt.floor(freq)):
            result["series"].append({"t": moment.isoformat() + "Z", **summary})

        days = max(1, frame["local"].dt.date.nunique())
        for hour, summary in _summaries(frame, frame["local"].dt.hour):
            result["hourly_profile"].append({"hour": int(hour), **summary, "volume": round(summary["volume"] / days, 1)})
        for day, summary in _summaries(frame, frame["local"].dt.date):
            result["daily"].append({"date": day.isoformat(), **summary})
        for key, summary in _summaries(frame, "intersection_id"):
            result["by_intersection"].append({"id": key, "name": names.get(key, key), **summary})
        for key, summary in _summaries(frame, "direction"):
            result["by_direction"].append({"direction": key, **summary})
        levels = frame.groupby("congestion_level")["bin_seconds"].sum()
        total = float(levels.sum())
        result["congestion_levels"] = [
            {"level": level, "share": round(100.0 * float(levels.get(level, 0)) / total, 1)}
            for level in ("LOW", "MODERATE", "HIGH", "SEVERE")
        ]

    result["congestion_events"] = _congestion_frequency(session, start, end, intersection_id, direction, names)
    result["signal_utilization"] = _signal_utilization(session, start, end, intersection_id, direction)
    result["sensor_uptime"] = _sensor_uptime(session, end, uptime_window_s, intersection_id, names)
    result["emergency"] = _emergency_response(session, start, end)
    return result


def _congestion_frequency(session, start, end, intersection_id, direction, names) -> list[dict]:
    filters = [CongestionEvent.detected_at >= start, CongestionEvent.detected_at <= end]
    if intersection_id:
        filters.append(CongestionEvent.intersection_id == intersection_id)
    if direction:
        filters.append(CongestionEvent.direction == direction)
    rows = session.execute(
        select(CongestionEvent.intersection_id, CongestionEvent.severity, func.count())
        .where(*filters).group_by(CongestionEvent.intersection_id, CongestionEvent.severity)
    ).all()
    merged: dict[str, dict] = {}
    for key, severity, count in rows:
        entry = merged.setdefault(key, {"id": key, "name": names.get(key, key), "HIGH": 0, "SEVERE": 0})
        entry[severity] = entry.get(severity, 0) + count
    return sorted(merged.values(), key=lambda e: e["id"] or "")


def _signal_utilization(session, start, end, intersection_id, direction) -> list[dict]:
    filters = [
        SignalStateHistory.started_at >= start, SignalStateHistory.started_at <= end,
        SignalStateHistory.phase == "GREEN",
    ]
    if direction:
        filters.append(SignalStateHistory.direction == direction)
    query = select(
        TrafficSignal.intersection_id, SignalStateHistory.direction,
        func.sum(SignalStateHistory.duration_s), func.count(),
    ).join(TrafficSignal, TrafficSignal.id == SignalStateHistory.signal_id).where(*filters).group_by(
        TrafficSignal.intersection_id, SignalStateHistory.direction,
    )
    if intersection_id:
        query = query.where(TrafficSignal.intersection_id == intersection_id)
    rows = session.execute(query).all()
    totals: dict[str, float] = {}
    for key, _, seconds, _ in rows:
        totals[key] = totals.get(key, 0.0) + seconds
    return [
        {"intersection_id": key, "direction": d, "green_s": round(seconds, 1), "phases": count,
         "share": round(100.0 * seconds / totals[key], 1), "avg_green_s": round(seconds / count, 1)}
        for key, d, seconds, count in rows
    ]


def _sensor_uptime(session, end, window_s: float, intersection_id, names) -> list[dict]:
    """Delivered readings against readings expected in the recent window."""
    window = max(60.0, min(window_s, 3600.0))
    since = end - timedelta(seconds=window)
    counts = dict(session.execute(
        select(SensorReading.sensor_id, func.count()).where(SensorReading.recorded_at >= since)
        .group_by(SensorReading.sensor_id)
    ).all())
    query = select(TrafficSensor).where(TrafficSensor.enabled.is_(True))
    if intersection_id:
        query = query.where(TrafficSensor.intersection_id == intersection_id)
    merged: dict[str, list[float]] = {}
    for sensor in session.scalars(query):
        expected = window / sensor.reporting_interval_s
        ratio = min(1.0, counts.get(sensor.id, 0) / expected) if expected else 1.0
        merged.setdefault(sensor.intersection_id, []).append(ratio)
    return [
        {"id": key, "name": names.get(key, key), "uptime": round(100.0 * sum(v) / len(v), 1), "sensors": len(v),
         "window_s": round(window)}
        for key, v in sorted(merged.items())
    ]


def _emergency_response(session, start, end) -> dict:
    rows = session.scalars(
        select(EmergencyVehicle).where(
            EmergencyVehicle.dispatched_at >= start, EmergencyVehicle.dispatched_at <= end,
            EmergencyVehicle.status == "ARRIVED",
        )
    ).all()
    if not rows:
        return {"completed": 0, "avg_travel_s": None, "avg_wait_s": None, "avg_preemptions": None, "by_type": []}
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row.vehicle_type, []).append(row)
    return {
        "completed": len(rows),
        "avg_travel_s": round(sum(r.travel_time_s or 0 for r in rows) / len(rows), 1),
        "avg_wait_s": round(sum(r.wait_time_s or 0 for r in rows) / len(rows), 1),
        "avg_preemptions": round(sum(r.preemptions for r in rows) / len(rows), 1),
        "by_type": [
            {"type": k, "count": len(v), "avg_travel_s": round(sum(r.travel_time_s or 0 for r in v) / len(v), 1)}
            for k, v in grouped.items()
        ],
    }
