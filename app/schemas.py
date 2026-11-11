"""Request and response models of the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

REPORT_CATEGORIES = ("stuck", "noise", "shaking", "door", "not_level", "other")
ReportCategory = Literal["stuck", "noise", "shaking", "door", "not_level", "other"]

# 120 s at 200 Hz: more than any phone delivers within the recording limit.
MAX_SAMPLES = 24_000


class BuildingIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=240)


class LiftIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    building_id: int
    floors: int = Field(default=5, ge=2, le=200)
    floor_height_m: float = Field(default=2.8, gt=1.5, lt=10)
    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,31}$")


class RideIn(BaseModel):
    """One recorded ride.

    samples: rows of [t_ms, ax, ay, az, rx, ry, rz], with
    accelerationIncludingGravity in m/s^2 and rotationRate in deg/s. The
    rotation values may be null when the device does not report them.
    started_at is only honoured with the admin token (the simulator uses it
    to backfill history); otherwise the server time is used.
    """

    lift_id: str = Field(min_length=1, max_length=32)
    device_id: str = Field(default="", max_length=64)
    sample_rate_hint: float | None = Field(default=None, gt=0, lt=1000)
    samples: list[list[float | None]] = Field(min_length=1, max_length=MAX_SAMPLES)
    started_at: datetime | None = None

    @field_validator("samples")
    @classmethod
    def _rows_well_formed(cls, rows: list[list[float | None]]) -> list[list[float | None]]:
        for row in rows:
            if len(row) not in (4, 7):
                raise ValueError("each sample must be [t_ms, ax, ay, az] or [t_ms, ax, ay, az, rx, ry, rz]")
            if any(v is None for v in row[:4]):
                raise ValueError("t_ms, ax, ay and az must not be null")
        return rows


class LiftStatusOut(BaseModel):
    lift_id: str
    lift_name: str
    building_name: str
    status: Literal["green", "yellow", "red", "grey"]
    health_score: float | None
    sentence: str


class RideOut(BaseModel):
    ride_id: int
    valid: bool
    reject_reason: str | None
    reject_message: str | None
    features: dict[str, float | int | str | None]
    ride_score: float | None
    lift_status: LiftStatusOut


class ReportIn(BaseModel):
    lift_id: str = Field(min_length=1, max_length=32)
    category: ReportCategory
    comment: str = Field(default="", max_length=500)
