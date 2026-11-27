"""Feature engineering from the traffic_metrics table.

All training data is generated locally. The prediction unit is one intersection
over one five-minute bin, and each target is the following bin.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TrafficMetric

TARGETS = ("volume", "wait", "density")
LAGS = (1, 2, 3, 6, 12)
BIN = "5min"


def load_bins(session: Session, since: datetime | None = None) -> pd.DataFrame:
    """Per-intersection five-minute series. Volumes sum, the others average."""
    query = select(
        TrafficMetric.bucket_start, TrafficMetric.intersection_id, TrafficMetric.direction,
        TrafficMetric.volume, TrafficMetric.avg_wait_s, TrafficMetric.density, TrafficMetric.bin_seconds,
    )
    if since is not None:
        query = query.where(TrafficMetric.bucket_start >= since)
    frame = pd.DataFrame(session.execute(query).all(), columns=[
        "bucket", "intersection_id", "direction", "volume", "wait", "density", "bin_seconds",
    ])
    if frame.empty:
        return frame
    frame["bucket"] = pd.to_datetime(frame["bucket"]).dt.floor(BIN)
    # Live rows are one-minute bins, so an incomplete bin must not look like a quiet one.
    grouped = frame.groupby(["intersection_id", "bucket"]).agg(
        volume=("volume", "sum"), wait=("wait", "mean"), density=("density", "mean"),
        covered=("bin_seconds", "sum"), approaches=("direction", "nunique"),
    ).reset_index()
    grouped["covered"] = grouped["covered"] / (grouped["approaches"].clip(lower=1) * 300.0)
    return grouped[grouped["covered"] >= 0.75].drop(columns=["covered", "approaches"])


def add_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    frame = frame.sort_values(["intersection_id", "bucket"]).copy()
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    local = frame["bucket"] + offset
    hour = local.dt.hour + local.dt.minute / 60.0
    frame["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    frame["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    frame["weekday"] = local.dt.weekday
    frame["is_weekend"] = (frame["weekday"] >= 5).astype(int)
    frame["site"] = frame["intersection_id"].str[-3:].astype(int)
    features = ["hour_sin", "hour_cos", "weekday", "is_weekend", "site"]
    groups = frame.groupby("intersection_id", sort=False)
    for name in TARGETS:
        frame[f"{name}_now"] = frame[name]
        features.append(f"{name}_now")
        for lag in LAGS:
            frame[f"{name}_lag{lag}"] = groups[name].shift(lag)
            features.append(f"{name}_lag{lag}")
    frame["volume_mean_1h"] = groups["volume"].transform(lambda s: s.rolling(12, min_periods=6).mean())
    features.append("volume_mean_1h")
    return frame, features


def training_table(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    table, features = add_features(frame)
    groups = table.groupby("intersection_id", sort=False)
    for name in TARGETS:
        table[f"y_{name}"] = groups[name].shift(-1)
    table = table.dropna(subset=features + [f"y_{name}" for name in TARGETS])
    return table.sort_values("bucket"), features
