"""Connects the pure signal/scoring code to the database.

Routes call these functions; they read and write ORM objects. All maths lives
in signal.py and scoring.py.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime

from sqlalchemy.orm import Session

from . import models
from .config import Settings
from .schemas import RideIn

STATUS_SENTENCES = {
    "green": "{name} is running normally.",
    "yellow": "{name} shows some unusual readings. Maintenance has been notified.",
    "red": "{name} needs attention. Maintenance has been alerted.",
    "grey": "We're still learning how {name} normally rides. Every ride helps.",
}


def compress_samples(samples: list[list[float | None]]) -> bytes:
    """Store samples compactly (zlib-compressed JSON)."""
    return zlib.compress(json.dumps(samples, separators=(",", ":")).encode(), 6)


def decompress_samples(blob: bytes) -> list[list[float | None]]:
    return json.loads(zlib.decompress(blob))


def lift_status(db: Session, lift: models.Lift) -> dict:
    """Small public status payload for the resident page."""
    status, score = "grey", None
    return {
        "lift_id": lift.id,
        "lift_name": lift.name,
        "building_name": lift.building.name,
        "status": status,
        "health_score": score,
        "sentence": STATUS_SENTENCES[status].format(name=lift.name),
    }


def ingest_ride(
    db: Session, settings: Settings, lift: models.Lift, body: RideIn, started_at: datetime
) -> dict:
    """Store an uploaded ride and return the response for the resident."""
    samples = body.samples
    duration = (samples[-1][0] - samples[0][0]) / 1000.0 if len(samples) > 1 else 0.0
    ride = models.Ride(
        lift_id=lift.id,
        device_id=body.device_id,
        started_at=started_at,
        duration_s=duration,
        sample_rate_hz=body.sample_rate_hint,
        raw_samples=compress_samples(samples) if settings.store_raw_samples else None,
        valid=False,
        reject_reason="not_analysed",
    )
    db.add(ride)
    db.commit()
    return {
        "ride_id": ride.id,
        "valid": ride.valid,
        "reject_reason": ride.reject_reason,
        "reject_message": "Ride stored. Analysis is not available yet.",
        "features": {},
        "ride_score": None,
        "lift_status": lift_status(db, lift),
    }


def add_report(db: Session, lift: models.Lift, category: str, comment: str, now: datetime) -> models.Report:
    report = models.Report(lift_id=lift.id, category=category, comment=comment.strip(), created_at=now)
    db.add(report)
    db.commit()
    return report
