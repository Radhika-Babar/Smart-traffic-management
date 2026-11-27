"""Static description of the road network and its devices."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

DIRECTION_CHECK = "direction IN ('NORTH','EAST','SOUTH','WEST')"


class Intersection(TimestampMixin, Base):
    __tablename__ = "intersections"
    __table_args__ = (UniqueConstraint("grid_x", "grid_y"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    grid_x: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_y: Mapped[int] = mapped_column(Integer, nullable=False)
    speed_limit_ms: Mapped[float] = mapped_column(Float, default=13.9, nullable=False)

    approaches: Mapped[list[TrafficApproach]] = relationship(
        back_populates="intersection", cascade="all, delete-orphan", order_by="TrafficApproach.id"
    )
    sensors: Mapped[list[TrafficSensor]] = relationship(back_populates="intersection", cascade="all, delete-orphan")
    signal: Mapped[TrafficSignal] = relationship(
        back_populates="intersection", cascade="all, delete-orphan", uselist=False
    )


class TrafficApproach(TimestampMixin, Base):
    __tablename__ = "traffic_approaches"
    __table_args__ = (
        UniqueConstraint("intersection_id", "direction"),
        CheckConstraint(DIRECTION_CHECK, name="direction"),
        CheckConstraint("base_demand_vph >= 0", name="demand"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intersection_id: Mapped[str] = mapped_column(ForeignKey("intersections.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lanes: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    base_demand_vph: Mapped[float] = mapped_column(Float, default=240.0, nullable=False)

    intersection: Mapped[Intersection] = relationship(back_populates="approaches")


class TrafficSensor(TimestampMixin, Base):
    __tablename__ = "traffic_sensors"
    __table_args__ = (CheckConstraint("reporting_interval_s >= 1", name="interval"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    sensor_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    intersection_id: Mapped[str] = mapped_column(
        ForeignKey("intersections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str | None] = mapped_column(String(5))
    reporting_interval_s: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    intersection: Mapped[Intersection] = relationship(back_populates="sensors")


class TrafficSignal(TimestampMixin, Base):
    __tablename__ = "traffic_signals"
    __table_args__ = (
        CheckConstraint("max_green_s >= min_green_s", name="green_range"),
        CheckConstraint("mode IN ('AUTOMATIC','MANUAL')", name="mode"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    intersection_id: Mapped[str] = mapped_column(
        ForeignKey("intersections.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    mode: Mapped[str] = mapped_column(String(10), default="AUTOMATIC", nullable=False)
    min_green_s: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    max_green_s: Mapped[float] = mapped_column(Float, default=60.0, nullable=False)
    default_green_s: Mapped[float] = mapped_column(Float, default=20.0, nullable=False)
    yellow_s: Mapped[float] = mapped_column(Float, default=3.0, nullable=False)
    all_red_s: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    preempt_max_hold_s: Mapped[float] = mapped_column(Float, default=45.0, nullable=False)

    intersection: Mapped[Intersection] = relationship(back_populates="signal")


class VehicleCategory(Base):
    __tablename__ = "vehicle_categories"

    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    speed_factor: Mapped[float] = mapped_column(Float, nullable=False)
    share: Mapped[float] = mapped_column(Float, nullable=False)
