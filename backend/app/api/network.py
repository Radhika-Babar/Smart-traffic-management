"""Intersection, sensor and signal management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.algorithms.signal_fsm import SignalTiming
from app.api.deps import ApiError, RuntimeDep, SessionDep, not_found
from app.core.clock import iso_utc
from app.models import (
    Intersection,
    SensorReading,
    SignalDecision,
    SignalStateHistory,
    TrafficAlert,
    TrafficApproach,
    TrafficMetric,
    TrafficSensor,
    TrafficSignal,
)
from app.schemas.api import (
    ApproachUpdate,
    DecisionOut,
    IntersectionCreate,
    IntersectionOut,
    IntersectionUpdate,
    ManualControlRequest,
    Message,
    ModeRequest,
    NetworkChange,
    ReadingOut,
    SensorCreate,
    SensorOut,
    SensorUpdate,
    SignalHistoryOut,
    SignalOut,
    TimingRequest,
)
from app.schemas.messages import SENSOR_TYPES, Direction
from app.services.processing import STATUS_DISABLED, STATUS_ONLINE, SensorLive
from app.simulation.defaults import DIRECTIONS, REPORTING_INTERVALS
from app.simulation.sensors import SensorSpec

router = APIRouter(prefix="/api")

LOADERS = (
    selectinload(Intersection.approaches), selectinload(Intersection.signal), selectinload(Intersection.sensors),
)


def _intersection_out(row: Intersection) -> IntersectionOut:
    out = IntersectionOut.model_validate(row)
    out.sensor_count = len(row.sensors)
    return out


def _load(session, intersection_id: str) -> Intersection:
    row = session.scalar(select(Intersection).where(Intersection.id == intersection_id).options(*LOADERS))
    if row is None:
        raise not_found("Intersection", intersection_id)
    return row


def _next_serial(session, column, prefix: str) -> int:
    values = session.scalars(select(column)).all()
    numbers = [int(v.split("-")[1]) for v in values if v.startswith(prefix)]
    return max(numbers, default=0) + 1


@router.get("/intersections", response_model=list[IntersectionOut], tags=["intersections"])
def list_intersections(session: SessionDep) -> list[IntersectionOut]:
    rows = session.scalars(select(Intersection).options(*LOADERS).order_by(Intersection.id)).all()
    return [_intersection_out(r) for r in rows]


@router.post("/intersections", response_model=NetworkChange, status_code=201, tags=["intersections"])
def create_intersection(body: IntersectionCreate, runtime: RuntimeDep, session: SessionDep) -> NetworkChange:
    taken = session.scalar(
        select(Intersection).where(Intersection.grid_x == body.grid_x, Intersection.grid_y == body.grid_y)
    )
    if taken is not None:
        raise ApiError(409, "grid_occupied", f"{taken.name} already occupies that grid position")
    serial = _next_serial(session, Intersection.id, "INT")
    intersection_id = f"INT-{serial:03d}"
    session.add(Intersection(
        id=intersection_id, name=body.name, description=body.description, grid_x=body.grid_x,
        grid_y=body.grid_y, speed_limit_ms=body.speed_limit_ms,
    ))
    session.add(TrafficSignal(id=f"SIG-{serial:03d}", intersection_id=intersection_id))
    session.flush()
    sensor_serial = _next_serial(session, TrafficSensor.id, "SNS")
    for direction in DIRECTIONS:
        enabled = direction in body.directions
        session.add(TrafficApproach(
            intersection_id=intersection_id, direction=direction, enabled=enabled,
            base_demand_vph=body.base_demand_vph,
        ))
        if not enabled:
            continue
        for sensor_type in SENSOR_TYPES[:-1]:
            session.add(TrafficSensor(
                id=f"SNS-{sensor_serial:03d}", sensor_type=sensor_type, intersection_id=intersection_id,
                direction=direction, reporting_interval_s=REPORTING_INTERVALS[sensor_type],
            ))
            sensor_serial += 1
    session.add(TrafficSensor(
        id=f"SNS-{sensor_serial:03d}", sensor_type="ENVIRONMENTAL", intersection_id=intersection_id,
        reporting_interval_s=REPORTING_INTERVALS["ENVIRONMENTAL"],
    ))
    session.commit()
    runtime.reset_simulation()
    return NetworkChange(intersection=_intersection_out(_load(session, intersection_id)), simulation_reset=True)


@router.get("/intersections/{intersection_id}", response_model=IntersectionOut, tags=["intersections"])
def get_intersection(intersection_id: str, session: SessionDep) -> IntersectionOut:
    return _intersection_out(_load(session, intersection_id))


@router.patch("/intersections/{intersection_id}", response_model=NetworkChange, tags=["intersections"])
def update_intersection(
    intersection_id: str, body: IntersectionUpdate, runtime: RuntimeDep, session: SessionDep
) -> NetworkChange:
    row = _load(session, intersection_id)
    changes = body.model_dump(exclude_unset=True)
    needs_reset = "speed_limit_ms" in changes and changes["speed_limit_ms"] != row.speed_limit_ms
    for key, value in changes.items():
        setattr(row, key, value)
    session.commit()
    if needs_reset:
        runtime.reset_simulation()
    else:
        live = runtime.processor.intersections.get(intersection_id)
        if live is not None:
            live.name = row.name
    return NetworkChange(intersection=_intersection_out(row), simulation_reset=needs_reset)


@router.patch(
    "/intersections/{intersection_id}/approaches/{direction}", response_model=NetworkChange, tags=["intersections"]
)
def update_approach(
    intersection_id: str, direction: Direction, body: ApproachUpdate, runtime: RuntimeDep, session: SessionDep
) -> NetworkChange:
    row = _load(session, intersection_id)
    approach = next(a for a in row.approaches if a.direction == direction)
    needs_reset = body.enabled is not None and body.enabled != approach.enabled
    if needs_reset and not body.enabled and sum(a.enabled for a in row.approaches) <= 1:
        raise ApiError(409, "last_approach", "At least one approach must stay enabled")
    if body.enabled is not None:
        approach.enabled = body.enabled
    if body.base_demand_vph is not None:
        approach.base_demand_vph = body.base_demand_vph
        runtime.engine.set_demand(intersection_id, direction, body.base_demand_vph)
    session.commit()
    if needs_reset:
        runtime.reset_simulation()
    return NetworkChange(intersection=_intersection_out(row), simulation_reset=needs_reset)


@router.get("/intersections/{intersection_id}/traffic", tags=["intersections"])
def intersection_traffic(intersection_id: str, runtime: RuntimeDep) -> dict:
    with runtime.processor.lock:
        live = runtime.processor.intersections.get(intersection_id)
        if live is None:
            raise not_found("Intersection", intersection_id)
        return runtime.processor.intersection_view(live)


@router.get("/intersections/{intersection_id}/history", tags=["intersections"])
def intersection_history(
    intersection_id: str, runtime: RuntimeDep, session: SessionDep, hours: int = Query(24, ge=1, le=336)
) -> list[dict]:
    _load(session, intersection_id)
    since = datetime.fromtimestamp(runtime.engine.sim_epoch_now, tz=UTC).replace(tzinfo=None) - timedelta(hours=hours)
    rows = session.execute(
        select(
            TrafficMetric.bucket_start, func.sum(TrafficMetric.volume), func.avg(TrafficMetric.avg_wait_s),
            func.avg(TrafficMetric.density), func.avg(TrafficMetric.avg_speed),
        ).where(TrafficMetric.intersection_id == intersection_id, TrafficMetric.bucket_start >= since)
        .group_by(TrafficMetric.bucket_start).order_by(TrafficMetric.bucket_start)
    ).all()
    step = max(1, len(rows) // 200)
    return [
        {"t": iso_utc(r[0]), "volume": int(r[1]), "wait": round(r[2], 1), "density": round(r[3], 1),
         "speed": round(r[4], 2) if r[4] is not None else None}
        for r in rows[::step]
    ]


@router.get("/intersections/{intersection_id}/health", tags=["intersections"])
def intersection_health(intersection_id: str, runtime: RuntimeDep, session: SessionDep) -> dict:
    live = runtime.processor.intersections.get(intersection_id)
    if live is None:
        raise not_found("Intersection", intersection_id)
    sensors = [s for s in runtime.processor.sensors.values() if s.intersection_id == intersection_id]
    open_alerts = session.scalar(
        select(func.count()).select_from(TrafficAlert)
        .where(TrafficAlert.intersection_id == intersection_id, TrafficAlert.resolved.is_(False))
    )
    online = sum(s.status == STATUS_ONLINE for s in sensors)
    return {
        "intersection_id": intersection_id,
        "controller": live.controller,
        "sensors_online": online,
        "sensors_total": len(sensors),
        "open_alerts": open_alerts or 0,
        "status": "HEALTHY" if live.controller == "ONLINE" and online == len(sensors) else "DEGRADED",
    }


# ---- sensors -------------------------------------------------------------


def _sensor_out(row: TrafficSensor, live: SensorLive | None) -> SensorOut:
    last_seen = None
    if live is not None and live.last_seen:
        last_seen = iso_utc(datetime.fromtimestamp(live.last_seen, tz=UTC))
    return SensorOut(
        id=row.id, sensor_type=row.sensor_type, intersection_id=row.intersection_id, direction=row.direction,
        reporting_interval_s=row.reporting_interval_s, enabled=row.enabled,
        status=live.status if live else STATUS_DISABLED, last_seen=last_seen,
        readings_received=live.received if live else 0, latest=live.latest if live else {},
    )


def _require_sensor(session, sensor_id: str) -> TrafficSensor:
    row = session.get(TrafficSensor, sensor_id)
    if row is None:
        raise not_found("Sensor", sensor_id)
    return row


@router.get("/sensors", response_model=list[SensorOut], tags=["sensors"])
def list_sensors(
    runtime: RuntimeDep, session: SessionDep, intersection_id: str | None = None, sensor_type: str | None = None,
    status: str | None = None,
) -> list[SensorOut]:
    query = select(TrafficSensor).order_by(TrafficSensor.id)
    if intersection_id:
        query = query.where(TrafficSensor.intersection_id == intersection_id)
    if sensor_type:
        query = query.where(TrafficSensor.sensor_type == sensor_type)
    items = [_sensor_out(r, runtime.processor.sensors.get(r.id)) for r in session.scalars(query)]
    return [i for i in items if status is None or i.status == status]


@router.post("/sensors", response_model=SensorOut, status_code=201, tags=["sensors"])
def register_sensor(body: SensorCreate, runtime: RuntimeDep, session: SessionDep) -> SensorOut:
    _load(session, body.intersection_id)
    sensor_id = f"SNS-{_next_serial(session, TrafficSensor.id, 'SNS'):03d}"
    row = TrafficSensor(id=sensor_id, **body.model_dump())
    session.add(row)
    session.commit()
    spec = SensorSpec(sensor_id, row.sensor_type, row.intersection_id, row.direction, row.reporting_interval_s)
    runtime.engine.add_sensor(spec)
    live = SensorLive(sensor_id, row.sensor_type, row.intersection_id, row.direction, row.reporting_interval_s)
    runtime.processor.sensors[sensor_id] = live
    return _sensor_out(row, live)


@router.get("/sensors/{sensor_id}", response_model=SensorOut, tags=["sensors"])
def get_sensor(sensor_id: str, runtime: RuntimeDep, session: SessionDep) -> SensorOut:
    return _sensor_out(_require_sensor(session, sensor_id), runtime.processor.sensors.get(sensor_id))


@router.patch("/sensors/{sensor_id}", response_model=SensorOut, tags=["sensors"])
def update_sensor(sensor_id: str, body: SensorUpdate, runtime: RuntimeDep, session: SessionDep) -> SensorOut:
    row = _require_sensor(session, sensor_id)
    live = runtime.processor.sensors.get(sensor_id)
    if body.enabled is not None:
        row.enabled = body.enabled
        if live is not None:
            live.enabled = body.enabled
            live.status = STATUS_ONLINE if body.enabled else STATUS_DISABLED
            live.last_seen = None
            runtime.processor.resolve_alert(f"sensor:{sensor_id}")
            runtime.engine.set_sensor_enabled(sensor_id, body.enabled)
    if body.reporting_interval_s is not None:
        row.reporting_interval_s = body.reporting_interval_s
        if live is not None:
            live.interval_s = body.reporting_interval_s
            runtime.engine.set_sensor_interval(sensor_id, body.reporting_interval_s)
    session.commit()
    return _sensor_out(row, live)


@router.get("/sensors/{sensor_id}/readings", response_model=list[ReadingOut], tags=["sensors"])
def sensor_readings(sensor_id: str, session: SessionDep, limit: int = Query(60, ge=1, le=1000)) -> list:
    _require_sensor(session, sensor_id)
    rows = session.scalars(
        select(SensorReading).where(SensorReading.sensor_id == sensor_id)
        .order_by(SensorReading.recorded_at.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


@router.post("/sensors/{sensor_id}/restart", response_model=Message, tags=["sensors"])
def restart_sensor(sensor_id: str, runtime: RuntimeDep, session: SessionDep) -> Message:
    row = _require_sensor(session, sensor_id)
    if not row.enabled:
        raise ApiError(409, "sensor_disabled", "Enable the sensor before restarting it")
    if sensor_id not in runtime.engine.sensors.sensors:
        raise ApiError(409, "sensor_inactive", "This sensor's approach is disabled")
    runtime.engine.restart_sensor(sensor_id)
    runtime.processor.event("SENSOR", f"{sensor_id} restarted by operator", "INFO", row.intersection_id)
    return Message(message=f"{sensor_id} restarting")


# ---- signals -------------------------------------------------------------


def _require_signal(session, signal_id: str) -> TrafficSignal:
    row = session.get(TrafficSignal, signal_id)
    if row is None:
        raise not_found("Signal", signal_id)
    return row


@router.get("/signals", response_model=list[SignalOut], tags=["signals"])
def list_signals(session: SessionDep) -> list:
    return list(session.scalars(select(TrafficSignal).order_by(TrafficSignal.id)))


@router.get("/signals/{signal_id}", response_model=SignalOut, tags=["signals"])
def get_signal(signal_id: str, session: SessionDep) -> TrafficSignal:
    return _require_signal(session, signal_id)


@router.post("/signals/{signal_id}/mode", response_model=SignalOut, tags=["signals"])
def set_mode(signal_id: str, body: ModeRequest, runtime: RuntimeDep, session: SessionDep) -> TrafficSignal:
    row = _require_signal(session, signal_id)
    row.mode = body.mode
    session.commit()
    runtime.send_command(row.intersection_id, {"action": "SET_MODE", "mode": body.mode})
    runtime.processor.event("SIGNAL", f"{signal_id} set to {body.mode}", "INFO", row.intersection_id)
    return row


@router.post("/signals/{signal_id}/manual-control", response_model=Message, status_code=202, tags=["signals"])
def manual_control(signal_id: str, body: ManualControlRequest, runtime: RuntimeDep, session: SessionDep) -> Message:
    row = _require_signal(session, signal_id)
    if row.mode != "MANUAL":
        raise ApiError(409, "not_manual", "Switch this signal to MANUAL before commanding a direction")
    live = runtime.processor.intersections.get(row.intersection_id)
    if live is None or body.direction not in live.directions:
        raise ApiError(409, "direction_disabled", f"{body.direction} is not enabled at this intersection")
    runtime.send_command(row.intersection_id, {
        "action": "SWITCH", "direction": body.direction, "seconds": body.green_seconds, "reason": "operator request",
    })
    runtime.processor.event(
        "SIGNAL", f"Operator requested {body.direction} green at {row.intersection_id}", "INFO", row.intersection_id,
    )
    return Message(message=f"{body.direction} requested; the controller will switch after min green and clearance")


@router.put("/signals/{signal_id}/timing", response_model=SignalOut, tags=["signals"])
def set_timing(signal_id: str, body: TimingRequest, runtime: RuntimeDep, session: SessionDep) -> TrafficSignal:
    row = _require_signal(session, signal_id)
    try:
        timing = SignalTiming(
            body.min_green_s, body.max_green_s, body.yellow_s, body.all_red_s,
            body.default_green_s, body.preempt_max_hold_s,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_timing", str(error)) from error
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ApiError(422, "invalid_timing", "Timing violates a database constraint") from error
    runtime.engine.set_timing(row.intersection_id, timing)
    return row


@router.get("/signals/{signal_id}/decisions", response_model=list[DecisionOut], tags=["signals"])
def signal_decisions(signal_id: str, session: SessionDep, limit: int = Query(30, ge=1, le=200)) -> list:
    _require_signal(session, signal_id)
    return list(session.scalars(
        select(SignalDecision).where(SignalDecision.signal_id == signal_id)
        .order_by(SignalDecision.decided_at.desc()).limit(limit)
    ))


@router.get("/signals/{signal_id}/history", response_model=list[SignalHistoryOut], tags=["signals"])
def signal_history(signal_id: str, session: SessionDep, limit: int = Query(40, ge=1, le=500)) -> list:
    _require_signal(session, signal_id)
    return list(session.scalars(
        select(SignalStateHistory).where(SignalStateHistory.signal_id == signal_id)
        .order_by(SignalStateHistory.started_at.desc()).limit(limit)
    ))
