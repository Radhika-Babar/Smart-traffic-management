"""Application runtime: builds every long-lived component and owns their lifecycle."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.algorithms.signal_fsm import ControlMode, SignalTiming
from app.core.clock import iso_utc, to_naive_utc, utcnow
from app.core.config import Settings
from app.core.database import Database
from app.ml.service import PredictionService
from app.models import (
    CongestionEvent,
    EmergencyVehicle,
    Intersection,
    SystemConfiguration,
    TrafficAlert,
    TrafficIncident,
    TrafficSimulation,
)
from app.mqtt import topics
from app.mqtt.bus import MODE_FALLBACK, MODE_MQTT, LocalHub
from app.mqtt.publisher import create_field_gateway
from app.mqtt.subscriber import BackendSubscriber
from app.services.persistence import BatchWriter
from app.services.processing import STATUS_DISABLED, SensorLive, TrafficProcessor
from app.services.runtime_config import SystemSettings, to_runtime
from app.services.seed import seed_if_empty
from app.simulation.engine import TrafficEngine
from app.simulation.network import IntersectionSpec
from app.simulation.runner import RUNNING, SimulationRunner
from app.simulation.sensors import SensorSpec
from app.websocket.manager import ConnectionManager

logger = logging.getLogger(__name__)

FRAME_INTERVAL_S = 0.2
CLOCK_KEY = "sim_clock"
FALLBACK_GRACE_S = 4.0
WARM_UP_S = 240.0


class Runtime:
    def __init__(self, settings: Settings, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database or Database(settings.database_url)
        self.started_at = time.time()
        self.ws = ConnectionManager()
        self.writer = BatchWriter(self.database, self._on_commit)
        self.predictions = PredictionService(self.database)
        self.system_settings = SystemSettings()
        self.simulation_id: int | None = None
        self.hub = LocalHub()
        self.field = None
        self.subscriber = None
        self.engine: TrafficEngine | None = None
        self.processor: TrafficProcessor | None = None
        self.runner: SimulationRunner | None = None
        self.tasks: list[asyncio.Task] = []
        self._stop_forecasts = threading.Event()
        self._fallback_logged = False

    def _on_commit(self, kinds: list[str]) -> None:
        if kinds:
            self.ws.broadcast_threadsafe("persisted", kinds)

    # ---- network description --------------------------------------------

    def load_network(self) -> tuple[list[IntersectionSpec], list[SensorSpec], dict, dict, list]:
        with self.database.session() as session:
            rows = session.scalars(
                select(Intersection).options(
                    selectinload(Intersection.approaches), selectinload(Intersection.signal),
                    selectinload(Intersection.sensors),
                ).order_by(Intersection.id)
            ).all()
            specs, sensors, timings, modes, identities = [], [], {}, {}, []
            for row in rows:
                enabled = [a for a in row.approaches if a.enabled]
                directions = tuple(a.direction for a in enabled)
                specs.append(IntersectionSpec(
                    row.id, row.name, row.grid_x, row.grid_y, directions,
                    {a.direction: a.base_demand_vph for a in enabled}, row.speed_limit_ms,
                ))
                signal = row.signal
                timings[row.id] = SignalTiming(
                    signal.min_green_s, signal.max_green_s, signal.yellow_s, signal.all_red_s,
                    signal.default_green_s, signal.preempt_max_hold_s,
                )
                modes[row.id] = ControlMode(signal.mode)
                identities.append((row.id, row.name, directions, signal.id))
                for sensor in sorted(row.sensors, key=lambda s: s.id):
                    if sensor.direction is None or sensor.direction in directions:
                        sensors.append(SensorSpec(
                            sensor.id, sensor.sensor_type, sensor.intersection_id, sensor.direction,
                            sensor.reporting_interval_s, sensor.enabled,
                        ))
            return specs, sensors, timings, modes, identities

    @staticmethod
    def _live_sensors(specs: list[SensorSpec]) -> list[SensorLive]:
        return [
            SensorLive(
                s.id, s.sensor_type, s.intersection_id, s.direction, s.interval_s, s.enabled,
                status="ONLINE" if s.enabled else STATUS_DISABLED,
            )
            for s in specs
        ]

    # ---- startup ---------------------------------------------------------

    def startup(self) -> None:
        self.database.migrate()
        with self.database.session() as session:
            seed_if_empty(session, self.settings.seed_history_days)
            self._recover(session)
            stored = session.get(SystemConfiguration, "system")
            if stored is not None:
                self.system_settings = SystemSettings.model_validate(stored.value)
            clock = session.get(SystemConfiguration, CLOCK_KEY)
            epoch = clock.value["epoch"] if clock else None
            session.commit()

        specs, sensors, timings, modes, identities = self.load_network()
        config = to_runtime(self.system_settings)
        host = self.settings.mqtt_broker_host if self.settings.mqtt_enabled else None
        port = self.settings.mqtt_broker_port

        holder: dict = {}
        self.subscriber = BackendSubscriber(
            self.hub, lambda t, p: holder["processor"].handle(t, p), host, port, self._on_connection,
        )
        self.field = create_field_gateway(
            self.hub, lambda t, p: holder["engine"].on_command(t, p), host, port, self._on_connection,
        )
        self.processor = TrafficProcessor(
            identities, self._live_sensors(sensors), config, self.subscriber.endpoint.publish,
            self.writer, self.ws.broadcast_threadsafe,
        )
        holder["processor"] = self.processor
        self.engine = TrafficEngine(
            specs, sensors, timings, self.field.publish, modes, seed=self.settings.simulation_seed,
            dt=self.settings.simulation_interval,
            epoch=max(epoch, datetime.now(UTC).timestamp()) if epoch else None,
            trip_sink=self._record_trip,
        )
        self.engine.set_expect_commands(config.strategy == "ADAPTIVE")
        self.engine.sensors.mtbf_hours = self.system_settings.sensor_mtbf_hours
        holder["engine"] = self.engine
        if self.settings.simulation_autostart:
            self.engine.warm_up(WARM_UP_S)
        self.runner = SimulationRunner(self.engine, self._on_sim_status)

        self.writer.start()
        self.hub.start()
        self.subscriber.start()
        self.field.start()
        self._open_run()
        if not self.predictions.load():
            self.predictions.train_in_background()
        threading.Thread(target=self._forecast_loop, name="forecasts", daemon=True).start()
        if self.settings.simulation_autostart:
            self.runner.start()

    def _recover(self, session) -> None:
        """Close anything left open by a previous process. History itself stays."""
        now = utcnow()
        session.execute(
            update(TrafficSimulation).where(TrafficSimulation.ended_at.is_(None))
            .values(ended_at=now, status="STOPPED")
        )
        session.execute(
            update(TrafficAlert).where(TrafficAlert.resolved.is_(False)).values(resolved=True, resolved_at=now)
        )
        session.execute(
            update(TrafficIncident).where(TrafficIncident.status.in_(("INJECTED", "DETECTED")))
            .values(status="RESOLVED", resolved_at=now)
        )
        session.execute(
            update(EmergencyVehicle).where(EmergencyVehicle.status == "EN_ROUTE")
            .values(status="CANCELLED", finished_at=now)
        )
        session.execute(
            update(CongestionEvent).where(CongestionEvent.status == "ACTIVE").values(status="RESOLVED", resolved_at=now)
        )

    def _open_run(self) -> None:
        with self.database.session() as session, session.begin():
            run = TrafficSimulation(
                status="RUNNING", scenario_key=self.engine.scenario.key, transport=self.transport,
            )
            session.add(run)
            session.flush()
            self.simulation_id = run.id

    def _close_run(self) -> None:
        if self.simulation_id is None:
            return
        with self.database.session() as session, session.begin():
            session.execute(update(TrafficSimulation).where(TrafficSimulation.id == self.simulation_id).values(
                ended_at=utcnow(), status="STOPPED", completed_trips=self.engine.completed_trips,
            ))

    def _record_trip(self, trip: dict) -> None:
        self.writer.add("trip", {
            "simulation_id": self.simulation_id,
            "category": trip["category"],
            "vehicle_ref": trip["vehicle_ref"],
            "travel_time_s": trip["travel_time_s"],
            "wait_time_s": trip["wait_time_s"],
            "free_flow_time_s": trip["free_flow_time_s"],
            "stops": trip["stops"],
            "completed_at": to_naive_utc(datetime.fromisoformat(trip["completed_at"])),
        })

    # ---- transport -------------------------------------------------------

    @property
    def transport(self) -> str:
        if self.field is not None and self.subscriber is not None:
            if self.field.connected and self.subscriber.endpoint.connected:
                return MODE_MQTT
        return MODE_FALLBACK

    def _on_connection(self, name: str, connected: bool, detail: str) -> None:
        processor = self.processor
        if processor is None:
            return
        if connected:
            processor.resolve_alert("mqtt")
            processor.event("MQTT", f"{name} connected to broker")
        else:
            processor.event("MQTT", f"{name} lost the broker: {detail}", "WARNING")
            processor.raise_alert(
                "MQTT_FAILURE", "CRITICAL",
                "MQTT broker connection lost. Running on labelled local fallback, reconnecting automatically",
                None, "mqtt",
            )
        self.ws.broadcast_threadsafe("transport", self.transport_status())

    def transport_status(self) -> dict:
        endpoint = self.subscriber.endpoint
        return {
            "mode": self.transport,
            "enabled": self.settings.mqtt_enabled,
            "broker": f"{self.settings.mqtt_broker_host}:{self.settings.mqtt_broker_port}",
            "field_connected": self.field.connected,
            "backend_connected": endpoint.connected,
            "reconnects": self.field.reconnects + endpoint.reconnects,
            "published": dict(self.field.published + endpoint.published),
            "received": dict(endpoint.received + self.field.received),
            "malformed": self.field.malformed + endpoint.malformed,
            "invalid_payloads": self.processor.stats["invalid_payloads"],
            "backlog": self.subscriber.backlog,
        }

    # ---- simulation control ---------------------------------------------

    def _on_sim_status(self, status: str) -> None:
        self.ws.broadcast_threadsafe("simulation", self.simulation_status())
        if self.processor is not None:
            self.processor.event("SIMULATION", f"Simulation {status.lower()}")

    def simulation_status(self) -> dict:
        performance = self.engine.performance()
        return {
            "state": self.runner.state,
            "speed": self.runner.speed,
            "clock": iso_utc(datetime.fromtimestamp(self.engine.sim_epoch_now, tz=UTC)),
            "sim_seconds": round(self.engine.time, 1),
            "run_id": self.simulation_id,
            "scenario": performance["scenario"],
            "scenario_name": performance["scenario_name"],
            "weather": performance["weather"],
            "volume_factor": self.engine.params.volume_factor,
            "spawn_rate_vph": self.engine.params.spawn_rate_vph,
            "intersection_factors": self.engine.params.intersection_factors,
            "control_strategy": self.processor.config.strategy,
            "tick_ms": round(self.runner.tick_ms, 2),
            "worker_alive": self.runner.worker_alive,
            "simulated": True,
        }

    def reset_simulation(self) -> None:
        was_running = self.runner.state == RUNNING
        self.runner.pause()
        self._close_run()
        specs, sensors, timings, modes, identities = self.load_network()
        self.engine.reset(specs, sensors, timings, modes)
        self.engine.sensors.mtbf_hours = self.system_settings.sensor_mtbf_hours
        self.processor.reset()
        self._close_emergencies()
        self._sync_processor_network(identities, sensors)
        self._open_run()
        self.processor.event("SIMULATION", "Simulation reset. Stored history is preserved")
        if was_running:
            self.runner.resume()
        self.ws.broadcast_threadsafe("topology", self.engine.topology())
        self.ws.broadcast_threadsafe("simulation", self.simulation_status())

    def _close_emergencies(self) -> None:
        """Vehicles removed by a reset never arrive, so their records must not stay open."""
        self.writer.flush()
        with self.database.session() as session, session.begin():
            session.execute(
                update(EmergencyVehicle).where(EmergencyVehicle.status == "EN_ROUTE")
                .values(status="CANCELLED", finished_at=utcnow())
            )

    def _sync_processor_network(self, identities: list, sensors: list[SensorSpec]) -> None:
        if {i[0] for i in identities} != set(self.processor.intersections) or {
            s.id for s in sensors
        } != set(self.processor.sensors) or any(
            self.processor.intersections[i[0]].directions != i[2] for i in identities
        ):
            self.processor.load_network(identities, self._live_sensors(sensors))

    def apply_settings(self, settings: SystemSettings) -> None:
        self.system_settings = settings
        config = to_runtime(settings)
        self.processor.apply_config(config)
        self.engine.set_expect_commands(config.strategy == "ADAPTIVE")
        self.engine.sensors.mtbf_hours = settings.sensor_mtbf_hours
        self.predictions.thresholds = config.congestion
        with self.database.session() as session, session.begin():
            row = session.get(SystemConfiguration, "system")
            row.value = settings.model_dump(mode="json")

    def send_command(self, intersection_id: str, command: dict) -> None:
        """Route an operator command through the same path as any other."""
        self.subscriber.endpoint.publish(topics.signal_command(intersection_id), {
            "intersection_id": intersection_id, "source": "OPERATOR", **command,
        })

    # ---- background loops ------------------------------------------------

    def _forecast_loop(self) -> None:
        while not self._stop_forecasts.wait(5.0):
            try:
                self.predictions.forecast(
                    datetime.fromtimestamp(self.engine.sim_epoch_now, tz=UTC).replace(tzinfo=None)
                )
                self._save_clock()
            except Exception:
                logger.exception("Forecast cycle failed")

    def _save_clock(self) -> None:
        with self.database.session() as session, session.begin():
            row = session.get(SystemConfiguration, CLOCK_KEY)
            value = {"epoch": self.engine.sim_epoch_now}
            if row is None:
                session.add(SystemConfiguration(key=CLOCK_KEY, value=value, description="Simulated city clock"))
            else:
                row.value = value

    def overview(self) -> dict:
        snapshot = self.processor.snapshot()
        performance = self.engine.performance()
        network = snapshot["network"]
        intersections = snapshot["intersections"]
        alerts = snapshot["active_alerts"]
        total = snapshot["sensors_total"] or 1
        controllers_ok = sum(i["controller"] == "ONLINE" for i in intersections) / max(1, len(intersections))
        sensor_ratio = snapshot["sensors_online"] / total
        transport_score = 1.0 if self.transport == MODE_MQTT else 0.6
        score = round(100 * (0.4 * sensor_ratio + 0.3 * controllers_ok + 0.3 * transport_score), 1)
        return {
            "total_intersections": len(intersections),
            "active_sensors": snapshot["sensors_online"],
            "total_sensors": snapshot["sensors_total"],
            "offline_sensors": snapshot["sensors_offline"],
            "total_vehicles": performance["vehicles"],
            "average_density": network["density"],
            "average_speed": network["speed"],
            "total_queue": network["queue"],
            "flow_vph": network["volume"],
            "active_congestion_alerts": sum(a["category"] == "SEVERE_CONGESTION" for a in alerts),
            "active_alerts": len(alerts),
            "active_emergencies": performance["active_emergencies"],
            "active_incidents": performance["active_incidents"],
            "average_wait_s": network["wait"],
            "average_trip_wait_s": performance["average_trip_wait_s"],
            "flow_efficiency": performance["flow_efficiency"],
            "completed_trips": performance["completed_trips"],
            "auto_signals": sum(i["mode"] == "AUTOMATIC" for i in intersections),
            "network_health": {
                "score": score,
                "status": "HEALTHY" if score >= 90 else "DEGRADED" if score >= 70 else "CRITICAL",
                "sensors": round(100 * sensor_ratio, 1),
                "controllers": round(100 * controllers_ok, 1),
                "transport": self.transport,
            },
            "simulated": True,
        }

    async def broadcast_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(FRAME_INTERVAL_S)
            tick += 1
            try:
                if self.ws.count:
                    frame = await asyncio.to_thread(self.engine.frame)
                    self.ws.broadcast("frame", frame)
                if tick % 5 == 0:
                    if self.ws.count:
                        live = await asyncio.to_thread(self._live_payload)
                        self.ws.broadcast("live", live)
                if tick % 10 == 0 and self.ws.count:
                    self.ws.broadcast("sensors", await asyncio.to_thread(self.processor.sensor_view))
                if tick % 25 == 0:
                    await asyncio.to_thread(self._publish_health)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Broadcast cycle failed")

    def _live_payload(self) -> dict:
        snapshot = self.processor.snapshot()
        return {
            "overview": self.overview(),
            "intersections": snapshot["intersections"],
            "alerts": snapshot["active_alerts"],
            "emergencies": snapshot["emergencies"],
            "simulation": self.simulation_status(),
            "transport": self.transport_status(),
            "point": self.processor.series[-1] if self.processor.series else None,
        }

    def _publish_health(self) -> None:
        if self.transport != MODE_MQTT and self.settings.mqtt_enabled and not self._fallback_logged:
            if time.time() - self.started_at > FALLBACK_GRACE_S:
                self._fallback_logged = True
                self.processor.raise_alert(
                    "MQTT_FAILURE", "CRITICAL",
                    f"MQTT broker not reachable at {self.settings.mqtt_broker_host}:{self.settings.mqtt_broker_port}. "
                    "Running on labelled local fallback",
                    None, "mqtt",
                )
        overview = self.overview()
        self.subscriber.endpoint.publish(topics.SYSTEM_HEALTH, {
            **overview["network_health"], "timestamp": iso_utc(utcnow()), "simulated": True,
        })

    # ---- shutdown --------------------------------------------------------

    def shutdown(self) -> None:
        self._stop_forecasts.set()
        if self.runner is not None:
            self.runner.shutdown()
        if self.engine is not None:
            self._save_clock()
            self._close_run()
        if self.field is not None:
            self.field.stop()
        if self.subscriber is not None:
            self.subscriber.stop()
        self.hub.stop()
        self.writer.stop()
        self.database.dispose()


