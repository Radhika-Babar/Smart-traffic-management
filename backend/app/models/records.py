"""Time-series and operational records. These survive restarts and resets."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.models.base import Base, TimestampMixin

# SQLite only autoincrements INTEGER primary keys.
BigId = BigInteger().with_variant(Integer, "sqlite")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_sensor_readings_sensor_time", "sensor_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("traffic_sensors.id", ondelete="CASCADE"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    vehicle_count: Mapped[int | None] = mapped_column(Integer)
    traffic_density: Mapped[float | None] = mapped_column(Float)
    average_speed: Mapped[float | None] = mapped_column(Float)
    occupancy_percentage: Mapped[float | None] = mapped_column(Float)
    queue_length: Mapped[int | None] = mapped_column(Integer)
    approaching: Mapped[int | None] = mapped_column(Integer)
    avg_wait_s: Mapped[float | None] = mapped_column(Float)
    max_wait_s: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    rain_mm_h: Mapped[float | None] = mapped_column(Float)
    visibility_m: Mapped[float | None] = mapped_column(Float)


class SignalStateHistory(Base):
    __tablename__ = "signal_state_history"
    __table_args__ = (Index("ix_signal_state_signal_time", "signal_id", "started_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("traffic_signals.id", ondelete="CASCADE"), nullable=False)
    phase: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(5))
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_reason: Mapped[str | None] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(10), nullable=False)


class SignalDecision(Base):
    __tablename__ = "signal_decisions"
    __table_args__ = (Index("ix_signal_decisions_signal_time", "signal_id", "decided_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("traffic_signals.id", ondelete="CASCADE"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(5))
    green_s: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    scores: Mapped[list] = mapped_column(JSON, nullable=False)


class TrafficSimulation(Base):
    """One run of the simulator, from start or reset to the next reset or shutdown."""

    __tablename__ = "traffic_simulations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    scenario_key: Mapped[str] = mapped_column(String(24), default="NORMAL", nullable=False)
    transport: Mapped[str | None] = mapped_column(String(16))
    completed_trips: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Vehicle(Base):
    """A completed simulated trip."""

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    simulation_id: Mapped[int | None] = mapped_column(ForeignKey("traffic_simulations.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(ForeignKey("vehicle_categories.key"), nullable=False)
    vehicle_ref: Mapped[str] = mapped_column(String(24), nullable=False)
    travel_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    wait_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    free_flow_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    stops: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class TrafficEvent(Base):
    __tablename__ = "traffic_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), default="INFO", nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    intersection_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"))


class EmergencyVehicle(TimestampMixin, Base):
    __tablename__ = "emergency_vehicles"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    vehicle_type: Mapped[str] = mapped_column(String(12), nullable=False)
    origin_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"))
    destination_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"))
    route: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    travel_time_s: Mapped[float | None] = mapped_column(Float)
    wait_time_s: Mapped[float | None] = mapped_column(Float)
    stops: Mapped[int | None] = mapped_column(Integer)
    preemptions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TrafficIncident(TimestampMixin, Base):
    """A simulated incident. `origin` tells injected ground truth from unmatched anomalies."""

    __tablename__ = "traffic_incidents"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    incident_type: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    intersection_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"), index=True)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    affected_lanes: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    injected_at: Mapped[datetime | None] = mapped_column(DateTime)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    detection_score: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list | None] = mapped_column(JSON)
    recommended_response: Mapped[str | None] = mapped_column(String(200))


class CongestionEvent(TimestampMixin, Base):
    __tablename__ = "congestion_events"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    intersection_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"), index=True)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(200), nullable=False)


class TrafficAlert(TimestampMixin, Base):
    __tablename__ = "traffic_alerts"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    intersection_id: Mapped[str | None] = mapped_column(ForeignKey("intersections.id", ondelete="SET NULL"))
    raised_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(8), default="LIVE", nullable=False)


class TrafficMetric(Base):
    """Per-approach aggregate. LIVE rows come from sensors, SEEDED rows from the history generator."""

    __tablename__ = "traffic_metrics"
    __table_args__ = (
        UniqueConstraint("bucket_start", "intersection_id", "direction"),
        Index("ix_traffic_metrics_int_time", "intersection_id", "bucket_start"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    bin_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    intersection_id: Mapped[str] = mapped_column(ForeignKey("intersections.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_queue: Mapped[float] = mapped_column(Float, nullable=False)
    avg_wait_s: Mapped[float] = mapped_column(Float, nullable=False)
    avg_speed: Mapped[float | None] = mapped_column(Float)
    density: Mapped[float] = mapped_column(Float, nullable=False)
    occupancy: Mapped[float] = mapped_column(Float, nullable=False)
    congestion_level: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)


class TrafficPrediction(Base):
    __tablename__ = "traffic_predictions"
    __table_args__ = (Index("ix_traffic_predictions_int_target", "intersection_id", "target_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    intersection_id: Mapped[str] = mapped_column(ForeignKey("intersections.id", ondelete="CASCADE"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    target_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    model_name: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_volume: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_wait_s: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_density: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_level: Mapped[str] = mapped_column(String(8), nullable=False)
    actual_volume: Mapped[float | None] = mapped_column(Float)
    actual_wait_s: Mapped[float | None] = mapped_column(Float)
    actual_density: Mapped[float | None] = mapped_column(Float)


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    model_name: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    mae: Mapped[float] = mapped_column(Float, nullable=False)
    rmse: Mapped[float] = mapped_column(Float, nullable=False)
    r2: Mapped[float] = mapped_column(Float, nullable=False)
    train_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    test_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_importance: Mapped[dict | None] = mapped_column(JSON)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SystemConfiguration(Base):
    __tablename__ = "system_configuration"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
