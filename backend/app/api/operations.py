"""Simulation control, emergencies, incidents, alerts and the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import ApiError, RuntimeDep, SessionDep, not_found
from app.core.clock import iso_utc, utcnow
from app.models import (
    CongestionEvent,
    EmergencyVehicle,
    Intersection,
    TrafficAlert,
    TrafficEvent,
    TrafficIncident,
    VehicleCategory,
)
from app.schemas.api import (
    AlertOut,
    AlertPage,
    CongestionOut,
    ControllerFailureRequest,
    EmergencyOut,
    EmergencyRequest,
    IncidentOut,
    IncidentRequest,
    Message,
    ParametersRequest,
    ScenarioRequest,
    SensorFailureRequest,
    SpeedRequest,
    WeatherRequest,
)
from app.simulation.scenarios import SCENARIOS, random_scenario

router = APIRouter(prefix="/api")


# ---- dashboard -----------------------------------------------------------


@router.get("/dashboard/overview", tags=["dashboard"])
def dashboard_overview(runtime: RuntimeDep) -> dict:
    return runtime.overview()


@router.get("/dashboard/live", tags=["dashboard"])
def dashboard_live(runtime: RuntimeDep) -> dict:
    return runtime._live_payload()


@router.get("/dashboard/series", tags=["dashboard"])
def dashboard_series(runtime: RuntimeDep) -> list[dict]:
    return list(runtime.processor.series)


@router.get("/dashboard/topology", tags=["dashboard"])
def dashboard_topology(runtime: RuntimeDep) -> dict:
    return runtime.engine.topology()


@router.get("/dashboard/events", tags=["dashboard"])
def dashboard_events(session: SessionDep, limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    rows = session.scalars(select(TrafficEvent).order_by(TrafficEvent.occurred_at.desc()).limit(limit))
    return [
        {"occurred_at": iso_utc(r.occurred_at), "category": r.category, "severity": r.severity,
         "message": r.message, "intersection_id": r.intersection_id}
        for r in rows
    ]


# ---- simulation ----------------------------------------------------------


@router.get("/simulation/status", tags=["simulation"])
def simulation_status(runtime: RuntimeDep) -> dict:
    return runtime.simulation_status()


@router.post("/simulation/start", tags=["simulation"])
def simulation_start(runtime: RuntimeDep) -> dict:
    runtime.runner.start()
    return runtime.simulation_status()


@router.post("/simulation/pause", tags=["simulation"])
def simulation_pause(runtime: RuntimeDep) -> dict:
    runtime.runner.pause()
    return runtime.simulation_status()


@router.post("/simulation/resume", tags=["simulation"])
def simulation_resume(runtime: RuntimeDep) -> dict:
    runtime.runner.resume()
    return runtime.simulation_status()


@router.post("/simulation/reset", tags=["simulation"])
def simulation_reset(runtime: RuntimeDep) -> dict:
    runtime.reset_simulation()
    return runtime.simulation_status()


@router.post("/simulation/speed", tags=["simulation"])
def simulation_speed(body: SpeedRequest, runtime: RuntimeDep) -> dict:
    runtime.runner.set_speed(body.speed)
    return runtime.simulation_status()


@router.post("/simulation/parameters", tags=["simulation"])
def simulation_parameters(body: ParametersRequest, runtime: RuntimeDep) -> dict:
    params = runtime.engine.params
    if body.intersection_id is not None and body.intersection_id not in runtime.processor.intersections:
        raise not_found("Intersection", body.intersection_id)
    with runtime.engine.lock:
        if body.volume_factor is not None:
            params.volume_factor = body.volume_factor
        if body.clear_spawn_rate:
            params.spawn_rate_vph = None
        elif body.spawn_rate_vph is not None:
            params.spawn_rate_vph = body.spawn_rate_vph
        if body.intersection_id is not None and body.intersection_factor is not None:
            params.intersection_factors[body.intersection_id] = body.intersection_factor
    return runtime.simulation_status()


@router.get("/simulation/scenarios", tags=["simulation"])
def simulation_scenarios() -> list[dict]:
    return [
        {"key": s.key, "name": s.name, "description": s.description, "demand_multiplier": s.demand_multiplier,
         "follows_clock": s.follow_clock, "weather": s.weather, "events": len(s.actions)}
        for s in SCENARIOS.values()
    ]


@router.post("/simulation/scenario", tags=["simulation"])
def simulation_scenario(body: ScenarioRequest, runtime: RuntimeDep) -> dict:
    scenario = SCENARIOS.get(body.key)
    if body.key == "RANDOM":
        scenario = random_scenario(runtime.engine.rng, list(runtime.processor.intersections))
    if scenario is None:
        raise ApiError(404, "not_found", f"Scenario {body.key} does not exist")
    runtime.engine.apply_scenario(scenario)
    return {**runtime.simulation_status(), "scenario_description": scenario.description}


@router.post("/simulation/weather", tags=["simulation"])
def simulation_weather(body: WeatherRequest, runtime: RuntimeDep) -> dict:
    runtime.engine.set_weather(body.condition)
    runtime.processor.event("SIMULATION", f"Weather set to {body.condition.lower()}")
    return runtime.simulation_status()


@router.post("/simulation/sensor-failure", tags=["simulation"])
def simulation_sensor_failure(body: SensorFailureRequest, runtime: RuntimeDep) -> dict:
    failed = runtime.engine.fail_sensors(body.intersection_id, body.direction, body.count, body.duration_s)
    if not failed:
        raise ApiError(409, "no_candidates", "No active sensors match that selection")
    return {"failed": failed}


@router.post("/simulation/controller-failure", tags=["simulation"])
def simulation_controller_failure(body: ControllerFailureRequest, runtime: RuntimeDep) -> dict:
    if body.intersection_id not in runtime.engine.controllers:
        raise not_found("Intersection", body.intersection_id)
    runtime.engine.fail_controller(body.intersection_id, body.duration_s)
    return {"failed": body.intersection_id, "duration_s": body.duration_s}


@router.get("/simulation/vehicle-categories", tags=["simulation"])
def vehicle_categories(session: SessionDep) -> list[dict]:
    return [
        {"key": c.key, "label": c.label, "length_m": c.length_m, "speed_factor": c.speed_factor, "share": c.share}
        for c in session.scalars(select(VehicleCategory))
    ]


# ---- emergency -----------------------------------------------------------


@router.get("/emergency-vehicles", response_model=list[EmergencyOut], tags=["emergency"])
def list_emergency(session: SessionDep, limit: int = Query(50, ge=1, le=200)) -> list:
    return list(session.scalars(
        select(EmergencyVehicle).order_by(EmergencyVehicle.dispatched_at.desc()).limit(limit)
    ))


@router.get("/emergency-vehicles/route", tags=["emergency"])
def preview_route(origin: str, destination: str, runtime: RuntimeDep) -> dict:
    if origin not in runtime.engine.network.intersections:
        raise not_found("Intersection", origin)
    if destination not in runtime.engine.network.intersections:
        raise not_found("Intersection", destination)
    return {"route": runtime.engine.network.shortest_path(origin, destination)}


@router.post("/emergency-vehicles", status_code=201, tags=["emergency"])
def dispatch_emergency(body: EmergencyRequest, runtime: RuntimeDep) -> dict:
    try:
        return runtime.engine.dispatch_emergency(body.vehicle_type, body.origin, body.destination)
    except ValueError as error:
        raise ApiError(422, "invalid_route", str(error)) from error


@router.delete("/emergency-vehicles/{vehicle_id}", response_model=Message, tags=["emergency"])
def cancel_emergency(vehicle_id: str, runtime: RuntimeDep) -> Message:
    try:
        runtime.engine.cancel_emergency(vehicle_id)
    except KeyError as error:
        raise not_found("Active emergency vehicle", vehicle_id) from error
    return Message(message=f"{vehicle_id} cancelled")


# ---- incidents and congestion -------------------------------------------


@router.get("/incidents", response_model=list[IncidentOut], tags=["incidents"])
def list_incidents(session: SessionDep, status: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list:
    query = select(TrafficIncident).order_by(TrafficIncident.created_at.desc()).limit(limit)
    if status:
        query = query.where(TrafficIncident.status == status)
    return list(session.scalars(query))


@router.post("/incidents/simulate", status_code=201, tags=["incidents"])
def simulate_incident(body: IncidentRequest, runtime: RuntimeDep) -> dict:
    try:
        return runtime.engine.inject_incident(
            body.incident_type, body.intersection_id, body.direction, body.duration_s,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_incident", str(error)) from error


@router.post("/incidents/{incident_id}/resolve", response_model=Message, tags=["incidents"])
def resolve_incident(incident_id: str, runtime: RuntimeDep) -> Message:
    try:
        runtime.engine.clear_incident(incident_id)
    except KeyError as error:
        raise not_found("Active simulated incident", incident_id) from error
    return Message(message=f"{incident_id} cleared")


@router.get("/congestion", response_model=list[CongestionOut], tags=["incidents"])
def list_congestion(
    session: SessionDep, status: str | None = None, severity: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> list:
    query = select(CongestionEvent).order_by(CongestionEvent.detected_at.desc()).limit(limit)
    if status:
        query = query.where(CongestionEvent.status == status)
    if severity:
        query = query.where(CongestionEvent.severity == severity)
    return list(session.scalars(query))


# ---- alerts --------------------------------------------------------------


@router.get("/alerts", response_model=AlertPage, tags=["alerts"])
def list_alerts(
    session: SessionDep,
    category: str | None = None,
    severity: str | None = None,
    intersection_id: str | None = None,
    acknowledged: bool | None = None,
    resolved: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AlertPage:
    filters = []
    if category:
        filters.append(TrafficAlert.category == category)
    if severity:
        filters.append(TrafficAlert.severity == severity)
    if intersection_id:
        filters.append(TrafficAlert.intersection_id == intersection_id)
    if acknowledged is not None:
        filters.append(TrafficAlert.acknowledged.is_(acknowledged))
    if resolved is not None:
        filters.append(TrafficAlert.resolved.is_(resolved))
    total = session.scalar(select(func.count()).select_from(TrafficAlert).where(*filters)) or 0
    rows = session.scalars(
        select(TrafficAlert).where(*filters).order_by(TrafficAlert.raised_at.desc()).limit(limit).offset(offset)
    ).all()
    return AlertPage(items=[AlertOut.model_validate(r) for r in rows], total=total)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut, tags=["alerts"])
def acknowledge_alert(alert_id: str, runtime: RuntimeDep, session: SessionDep) -> TrafficAlert:
    row = session.get(TrafficAlert, alert_id)
    if row is None:
        raise not_found("Alert", alert_id)
    row.acknowledged = True
    row.acknowledged_at = utcnow()
    session.commit()
    for alert in runtime.processor.alerts.values():
        if alert["id"] == alert_id:
            alert["acknowledged"] = True
    return row


@router.post("/alerts/acknowledge-all", response_model=Message, tags=["alerts"])
def acknowledge_all(runtime: RuntimeDep, session: SessionDep) -> Message:
    rows = session.scalars(select(TrafficAlert).where(TrafficAlert.acknowledged.is_(False))).all()
    now = utcnow()
    for row in rows:
        row.acknowledged = True
        row.acknowledged_at = now
    session.commit()
    for alert in runtime.processor.alerts.values():
        alert["acknowledged"] = True
    return Message(message=f"{len(rows)} alerts acknowledged")


def intersection_names(session) -> dict[str, str]:
    return dict(session.execute(select(Intersection.id, Intersection.name)).all())
