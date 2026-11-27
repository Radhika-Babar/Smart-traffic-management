"""Analytics, predictions and system administration."""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.api.deps import ApiError, RuntimeDep, SessionDep
from app.core.clock import to_naive_utc
from app.core.runtime import Runtime
from app.models import (
    ModelEvaluation,
    SensorReading,
    SignalDecision,
    SignalStateHistory,
    TrafficEvent,
    TrafficMetric,
    TrafficPrediction,
)
from app.schemas.api import CleanupRequest, EvaluationOut, Message, PredictionOut
from app.services.analytics import load_metrics, traffic_analytics
from app.services.runtime_config import SystemSettings

router = APIRouter(prefix="/api")


def _sim_now(runtime: Runtime) -> datetime:
    return datetime.fromtimestamp(runtime.engine.sim_epoch_now, tz=UTC).replace(tzinfo=None)


def _range(runtime: Runtime, start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    stop = to_naive_utc(end) if end else _sim_now(runtime)
    begin = to_naive_utc(start) if start else stop - timedelta(days=7)
    if begin >= stop:
        raise ApiError(422, "invalid_range", "start must be before end")
    return begin, stop


@router.get("/analytics/traffic", tags=["analytics"])
def analytics_traffic(
    runtime: RuntimeDep,
    session: SessionDep,
    start: datetime | None = None,
    end: datetime | None = None,
    intersection_id: str | None = None,
    direction: str | None = None,
    condition: str | None = None,
) -> dict:
    begin, stop = _range(runtime, start, end)
    return traffic_analytics(session, begin, stop, intersection_id, direction, condition, runtime.engine.time)


@router.get("/analytics/export", tags=["analytics"])
def analytics_export(
    runtime: RuntimeDep,
    session: SessionDep,
    start: datetime | None = None,
    end: datetime | None = None,
    intersection_id: str | None = None,
    direction: str | None = None,
    condition: str | None = None,
) -> StreamingResponse:
    begin, stop = _range(runtime, start, end)
    frame = load_metrics(session, begin, stop, intersection_id, direction, condition)
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    name = f"traffic-report-{begin:%Y%m%d}-{stop:%Y%m%d}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/predictions", response_model=list[PredictionOut], tags=["predictions"])
def predictions(session: SessionDep, intersection_id: str | None = None, limit: int = Query(60, ge=1, le=500)) -> list:
    query = select(TrafficPrediction).order_by(TrafficPrediction.target_at.desc()).limit(limit)
    if intersection_id:
        query = query.where(TrafficPrediction.intersection_id == intersection_id)
    return list(session.scalars(query))


@router.get("/predictions/evaluation", tags=["predictions"])
def prediction_evaluation(runtime: RuntimeDep, session: SessionDep) -> dict:
    service = runtime.predictions
    bundle = service.bundle
    rows = []
    if bundle is not None:
        rows = session.scalars(select(ModelEvaluation).where(ModelEvaluation.run_id == bundle["run_id"])).all()
    return {
        "trained": bundle is not None,
        "training": service.training,
        "last_error": service.last_error,
        "run_id": bundle["run_id"] if bundle else None,
        "trained_at": bundle["trained_at"] if bundle else None,
        "split_at": bundle["split_at"] if bundle else None,
        "train_rows": bundle["train_rows"] if bundle else 0,
        "test_rows": bundle["test_rows"] if bundle else 0,
        "selected": {k: v["name"] for k, v in bundle["models"].items()} if bundle else {},
        "metrics": [EvaluationOut.model_validate(r).model_dump(mode="json") for r in rows],
    }


@router.get("/predictions/backtest", tags=["predictions"])
def prediction_backtest(intersection_id: str, runtime: RuntimeDep) -> list[dict]:
    bundle = runtime.predictions.bundle
    return bundle["backtest"].get(intersection_id, []) if bundle else []


@router.post("/predictions/train", response_model=Message, status_code=202, tags=["predictions"])
def train(runtime: RuntimeDep) -> Message:
    if not runtime.predictions.train_in_background():
        raise ApiError(409, "already_training", "Training is already running")
    return Message(message="Training started")


@router.get("/system/health", tags=["system"])
def system_health(runtime: RuntimeDep) -> dict:
    return {
        "status": runtime.overview()["network_health"]["status"],
        "uptime_s": round(time.time() - runtime.started_at),
        "database": {
            "backend": runtime.database.backend, "rows_written": runtime.writer.rows_written,
            "pending": runtime.writer.pending, "dropped": runtime.writer.dropped,
        },
        "transport": runtime.transport_status(),
        "simulation": runtime.simulation_status(),
        "websocket_clients": runtime.ws.count,
        "network_health": runtime.overview()["network_health"],
    }


@router.get("/system/config", response_model=SystemSettings, tags=["system"])
def get_config(runtime: RuntimeDep) -> SystemSettings:
    return runtime.system_settings


@router.put("/system/config", response_model=SystemSettings, tags=["system"])
def put_config(body: SystemSettings, runtime: RuntimeDep) -> SystemSettings:
    try:
        runtime.apply_settings(body)
    except (ValueError, ValidationError) as error:
        raise ApiError(422, "invalid_config", str(error)) from error
    runtime.processor.event("SYSTEM", "System configuration updated")
    return runtime.system_settings


@router.post("/system/cleanup", tags=["system"])
def cleanup(body: CleanupRequest, runtime: RuntimeDep, session: SessionDep) -> dict:
    """Explicit history deletion. This is the only path that removes records."""
    cutoff = _sim_now(runtime) - timedelta(days=body.older_than_days)
    removed = {}
    for label, column in (
        ("sensor_readings", SensorReading.recorded_at),
        ("traffic_metrics", TrafficMetric.bucket_start),
        ("signal_state_history", SignalStateHistory.started_at),
        ("signal_decisions", SignalDecision.decided_at),
        ("traffic_events", TrafficEvent.occurred_at),
    ):
        result = session.execute(delete(column.class_).where(column < cutoff))
        removed[label] = result.rowcount
    session.commit()
    runtime.processor.event("SYSTEM", f"History older than {body.older_than_days} days removed", "WARNING")
    return {"removed": removed, "cutoff": cutoff.isoformat() + "Z"}
