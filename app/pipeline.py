"""Connects the pure signal/scoring code to the database.

Routes call these functions; they read and write ORM objects. All maths lives
in signal.py and scoring.py.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, scoring
from .config import Settings
from .schemas import RideIn
from .signal import FEATURE_NAMES, REJECT_MESSAGES, analyse_ride

STATUS_SENTENCES = {
    "green": "{name} is running normally.",
    "yellow": "{name} shows some unusual readings. Maintenance has been notified.",
    "red": "{name} needs attention. Maintenance has been alerted.",
    "grey": "We're still learning how {name} normally rides. Every ride helps.",
}

REPORT_LABELS = {
    "stuck": "the lift getting stuck",
    "noise": "a strange noise",
    "shaking": "shaking",
    "door": "a door problem",
    "not_level": "the lift not stopping level",
    "other": "a problem",
}
REPORT_ALERT_FEATURE = "resident_report"
REPORT_NOTE = " Residents have also reported problems."
REPORT_BOOST_WINDOW = timedelta(days=7)
RESOLVE_COOLDOWN = timedelta(hours=24)


def compress_samples(samples: list[list[float | None]]) -> bytes:
    """Store samples compactly (zlib-compressed JSON)."""
    return zlib.compress(json.dumps(samples, separators=(",", ":")).encode(), 6)


def decompress_samples(blob: bytes) -> list[list[float | None]]:
    return json.loads(zlib.decompress(blob))


# ----------------------------------------------------------- scoring glue


def ride_point(ride: models.Ride) -> scoring.RidePoint:
    return scoring.RidePoint(
        started_at=ride.started_at,
        direction=ride.direction or "",
        features={f: getattr(ride, f) for f in scoring.SCORE_WEIGHTS},
        score=ride.ride_score,
    )


def baseline_rides(db: Session, lift_id: str, direction: str, n: int) -> list[models.Ride]:
    """The first n valid rides of a lift in one direction."""
    stmt = (select(models.Ride)
            .where(models.Ride.lift_id == lift_id, models.Ride.valid.is_(True),
                   models.Ride.direction == direction)
            .order_by(models.Ride.started_at, models.Ride.id).limit(n))
    return list(db.scalars(stmt))


def lift_baselines(db: Session, lift_id: str, n: int) -> dict[str, dict[str, scoring.FeatureBaseline]]:
    """Baselines per direction, only for directions that already have n valid rides."""
    out = {}
    for direction in ("up", "down"):
        rides = baseline_rides(db, lift_id, direction, n)
        if len(rides) >= n:
            out[direction] = scoring.compute_baselines([ride_point(r) for r in rides])
    return out


def _apply_score(ride: models.Ride, baselines: dict[str, scoring.FeatureBaseline]) -> None:
    score, zs = scoring.score_ride({f: getattr(ride, f) for f in scoring.SCORE_WEIGHTS}, baselines)
    ride.ride_score = score
    ride.z_scores = json.dumps(zs)


def lift_health(db: Session, lift_id: str, now: datetime) -> tuple[float | None, str]:
    """Health score and status of a lift as of `now`."""
    base = select(models.Ride.started_at, models.Ride.ride_score).where(
        models.Ride.lift_id == lift_id, models.Ride.ride_score.is_not(None), models.Ride.started_at <= now)
    rows = db.execute(base.where(models.Ride.started_at >= now - scoring.HEALTH_WINDOW)).all()
    if len(rows) < scoring.HEALTH_MIN_RIDES_IN_WINDOW:
        rows = db.execute(base.order_by(models.Ride.started_at.desc())
                          .limit(scoring.HEALTH_FALLBACK_RIDES)).all()
    return scoring.lift_health([(t, s) for t, s in rows], now)


def lift_status(db: Session, lift: models.Lift, now: datetime | None = None) -> dict:
    """Small public status payload for the resident page."""
    score, status = lift_health(db, lift.id, now or models.utcnow())
    return {
        "lift_id": lift.id,
        "lift_name": lift.name,
        "building_name": lift.building.name,
        "status": status,
        "health_score": score,
        "sentence": STATUS_SENTENCES[status].format(name=lift.name),
    }


def update_alerts(db: Session, lift: models.Lift, settings: Settings, now: datetime) -> None:
    """Create, update or auto-resolve the sensor alerts of a lift as of `now`."""
    baselines = lift_baselines(db, lift.id, settings.baseline_rides)
    if not baselines:
        return
    stmt = (select(models.Ride)
            .where(models.Ride.lift_id == lift.id, models.Ride.valid.is_(True),
                   models.Ride.started_at >= now - scoring.TREND_WINDOW, models.Ride.started_at <= now)
            .order_by(models.Ride.started_at, models.Ride.id))
    findings = scoring.detect_trends([ride_point(r) for r in db.scalars(stmt)], baselines, now)
    reported = db.scalar(select(models.Report.id).where(
        models.Report.lift_id == lift.id, models.Report.created_at >= now - REPORT_BOOST_WINDOW,
        models.Report.created_at <= now).limit(1)) is not None

    open_alerts = {a.feature: a for a in db.scalars(select(models.Alert).where(
        models.Alert.lift_id == lift.id, models.Alert.resolved.is_(False),
        models.Alert.feature != REPORT_ALERT_FEATURE))}
    found = set()
    for f in findings:
        found.add(f.feature)
        severity, message = f.severity, scoring.alert_message(lift.name, f)
        if reported:
            severity = scoring.bump_severity(severity)
            message += REPORT_NOTE
        alert = open_alerts.get(f.feature)
        if alert is not None:
            if (alert.severity, alert.message) != (severity, message):
                alert.severity, alert.message, alert.updated_at = severity, message, now
            continue
        recent = db.scalar(select(models.Alert).where(
            models.Alert.lift_id == lift.id, models.Alert.feature == f.feature,
            models.Alert.resolved.is_(True), models.Alert.resolved_at >= now - RESOLVE_COOLDOWN)
            .order_by(models.Alert.resolved_at.desc()).limit(1))
        if recent is not None and scoring.SEVERITIES.index(severity) <= scoring.SEVERITIES.index(recent.severity):
            continue  # just resolved by maintenance: only come back if it got worse
        db.add(models.Alert(lift_id=lift.id, created_at=now, updated_at=now, severity=severity,
                            feature=f.feature, message=message))
    for feature, alert in open_alerts.items():
        if feature not in found:  # the condition has cleared
            alert.resolved, alert.resolved_at, alert.updated_at = True, now, now


def recompute_lift(db: Session, lift: models.Lift, settings: Settings, now: datetime) -> int:
    """Re-score every valid ride of a lift against the current baselines, then refresh alerts."""
    baselines = lift_baselines(db, lift.id, settings.baseline_rides)
    rides = db.scalars(select(models.Ride).where(models.Ride.lift_id == lift.id, models.Ride.valid.is_(True)))
    n = 0
    for ride in rides:
        if ride.direction in baselines:
            _apply_score(ride, baselines[ride.direction])
            n += 1
        else:
            ride.ride_score, ride.z_scores = None, None
    update_alerts(db, lift, settings, now)
    db.commit()
    return n


# ---------------------------------------------------------------- ingest


def ingest_ride(
    db: Session, settings: Settings, lift: models.Lift, body: RideIn, started_at: datetime
) -> dict:
    """Analyse, store and score an uploaded ride; return the response for the resident."""
    samples = body.samples
    analysis = analyse_ride(samples, floor_height_m=lift.floor_height_m)
    ride = models.Ride(
        lift_id=lift.id,
        device_id=body.device_id,
        started_at=started_at,
        duration_s=(samples[-1][0] - samples[0][0]) / 1000.0 if len(samples) > 1 else 0.0,
        sample_rate_hz=body.sample_rate_hint,
        raw_samples=compress_samples(samples) if settings.store_raw_samples else None,
        valid=analysis.valid,
        reject_reason=analysis.reject_reason,
    )
    for name in FEATURE_NAMES:
        if name in analysis.features:
            setattr(ride, name, analysis.features[name])
    db.add(ride)
    db.flush()

    if ride.valid:
        n = settings.baseline_rides
        base_rides = baseline_rides(db, lift.id, ride.direction, n)
        if len(base_rides) >= n:
            baselines = scoring.compute_baselines([ride_point(r) for r in base_rides])
            _apply_score(ride, baselines)
            if ride in base_rides:  # this ride completed the baseline: score the others too
                for r in base_rides:
                    _apply_score(r, baselines)
        update_alerts(db, lift, settings, started_at)
    db.commit()
    return {
        "ride_id": ride.id,
        "valid": ride.valid,
        "reject_reason": ride.reject_reason,
        "reject_message": REJECT_MESSAGES.get(ride.reject_reason) if ride.reject_reason else None,
        "features": analysis.features,
        "ride_score": ride.ride_score,
        "lift_status": lift_status(db, lift, max(started_at, models.utcnow())),
    }


def add_report(db: Session, lift: models.Lift, category: str, comment: str, now: datetime) -> models.Report:
    """Store a resident report and reflect it in the alerts.

    If the lift already has sensor alerts, they go up one severity level
    (residents confirm what the data shows). Otherwise an info alert records
    the report, so it shows in the alert feed.
    """
    report = models.Report(lift_id=lift.id, category=category, comment=comment.strip(), created_at=now)
    db.add(report)
    open_alerts = list(db.scalars(select(models.Alert).where(
        models.Alert.lift_id == lift.id, models.Alert.resolved.is_(False))))
    sensor = [a for a in open_alerts if a.feature != REPORT_ALERT_FEATURE]
    for alert in sensor:
        if not alert.message.endswith(REPORT_NOTE):
            alert.severity = scoring.bump_severity(alert.severity)
            alert.message += REPORT_NOTE
            alert.updated_at = now
    if not sensor:
        message = f"{lift.name}: a resident reported {REPORT_LABELS.get(category, 'a problem')}."
        existing = next((a for a in open_alerts if a.feature == REPORT_ALERT_FEATURE), None)
        if existing is not None:
            existing.message, existing.updated_at = message, now
        else:
            db.add(models.Alert(lift_id=lift.id, created_at=now, updated_at=now, severity="info",
                                feature=REPORT_ALERT_FEATURE, message=message))
    db.commit()
    return report
