"""ORM models: buildings, lifts, rides, resident reports and alerts.

All timestamps are stored as naive UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Current time as naive UTC (the storage convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(240), default="")

    lifts: Mapped[list[Lift]] = relationship(back_populates="building")


class Lift(Base):
    __tablename__ = "lifts"

    # Short slug, used in the QR URL: /r/<id>
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    name: Mapped[str] = mapped_column(String(120))
    floors: Mapped[int] = mapped_column(Integer, default=5)
    floor_height_m: Mapped[float] = mapped_column(Float, default=2.8)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    building: Mapped[Building] = relationship(back_populates="lifts")


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (Index("ix_rides_lift_started", "lift_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lift_id: Mapped[str] = mapped_column(ForeignKey("lifts.id"))
    device_id: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_rate_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    # zlib-compressed JSON of the samples as uploaded (MVP: kept for debugging)
    raw_samples: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=False)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Extracted features (NULL for invalid rides or when not measurable)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    peak_accel_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_accel_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_jerk_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_jerk_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    vib_vertical_rms: Mapped[float | None] = mapped_column(Float, nullable=True)
    vib_horizontal_rms: Mapped[float | None] = mapped_column(Float, nullable=True)
    vib_peak_to_peak_horizontal: Mapped[float | None] = mapped_column(Float, nullable=True)
    dominant_vib_freq_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    travel_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    floors_travelled_est: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_settling_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Scoring results
    ride_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_scores: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    lift: Mapped[Lift] = relationship()


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lift_id: Mapped[str] = mapped_column(ForeignKey("lifts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    category: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(String(500), default="")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lift_id: Mapped[str] = mapped_column(ForeignKey("lifts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    severity: Mapped[str] = mapped_column(String(8))  # info / warn / critical
    feature: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(String(400))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
