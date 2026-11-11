"""Read models for the dashboard: lift overview, lift detail and ride detail."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models, pipeline, scoring
from .config import Settings
from .signal import analyse_ride

# Features plotted on the lift page, with display units.
CHART_FEATURES: dict[str, str] = {
    "vib_horizontal_rms": "m/s²",
    "peak_jerk_stop": "m/s³",
    "peak_jerk_start": "m/s³",
    "vib_vertical_rms": "m/s²",
    "stop_settling_s": "s",
}
TREND_ARROW_DAYS = 7
TREND_ARROW_POINTS = 5.0
HISTORY_DAYS = 45
TRACE_HZ = 25


def iso(dt: datetime | None) -> str | None:
    """Naive-UTC datetime as ISO 8601 with a Z suffix."""
    return None if dt is None else dt.isoformat(timespec="seconds") + "Z"


SEVERITY_RANK = {s: i for i, s in enumerate(scoring.SEVERITIES)}


def _trend_arrow(db: Session, lift_id: str, now: datetime, current: float | None) -> str:
    past, _ = pipeline.lift_health(db, lift_id, now - timedelta(days=TREND_ARROW_DAYS))
    if current is None or past is None:
        return "flat"
    if current - past >= TREND_ARROW_POINTS:
        return "up"      # improving
    if past - current >= TREND_ARROW_POINTS:
        return "down"    # getting worse
    return "flat"


def lift_overview(db: Session, now: datetime) -> list[dict]:
    """One row per lift for the dashboard."""
    lifts = db.scalars(select(models.Lift).order_by(models.Lift.building_id, models.Lift.name)).all()
    week_ago = now - timedelta(days=7)
    rides_7d = dict(db.execute(
        select(models.Ride.lift_id, func.count()).where(models.Ride.started_at >= week_ago,
                                                        models.Ride.started_at <= now)
        .group_by(models.Ride.lift_id)).all())
    last_ride = dict(db.execute(select(models.Ride.lift_id, func.max(models.Ride.started_at))
                                .group_by(models.Ride.lift_id)).all())
    open_alerts: dict[str, list[str]] = {}
    for lift_id, severity in db.execute(select(models.Alert.lift_id, models.Alert.severity)
                                        .where(models.Alert.resolved.is_(False))):
        open_alerts.setdefault(lift_id, []).append(severity)

    rows = []
    for lift in lifts:
        score, status = pipeline.lift_health(db, lift.id, now)
        severities = open_alerts.get(lift.id, [])
        rows.append({
            "id": lift.id,
            "name": lift.name,
            "building_id": lift.building_id,
            "building_name": lift.building.name,
            "floors": lift.floors,
            "health_score": score,
            "status": status,
            "trend": _trend_arrow(db, lift.id, now, score),
            "rides_7d": rides_7d.get(lift.id, 0),
            "open_alerts": len(severities),
            "worst_severity": max(severities, key=SEVERITY_RANK.get) if severities else None,
            "last_ride_at": iso(last_ride.get(lift.id)),
        })
    return rows


def alert_json(alert: models.Alert, lift_name: str) -> dict:
    return {
        "id": alert.id, "lift_id": alert.lift_id, "lift_name": lift_name,
        "created_at": iso(alert.created_at), "updated_at": iso(alert.updated_at),
        "severity": alert.severity, "feature": alert.feature, "message": alert.message,
        "resolved": alert.resolved, "resolved_at": iso(alert.resolved_at),
    }


def _health_history(db: Session, lift_id: str, now: datetime) -> list[dict]:
    """Health score at the end of each day, for the last HISTORY_DAYS days."""
    first = db.scalar(select(func.min(models.Ride.started_at)).where(models.Ride.lift_id == lift_id))
    if first is None:
        return []
    start = max(first.date(), (now - timedelta(days=HISTORY_DAYS)).date())
    out = []
    day = start
    while day <= now.date():
        end = min(datetime.combine(day, datetime.max.time()), now)
        score, status = pipeline.lift_health(db, lift_id, end)
        out.append({"date": day.isoformat(), "score": score, "status": status})
        day += timedelta(days=1)
    return out


def lift_detail(db: Session, lift: models.Lift, settings: Settings, now: datetime) -> dict:
    """Everything the per-lift page shows."""
    score, status = pipeline.lift_health(db, lift.id, now)
    baselines = pipeline.lift_baselines(db, lift.id, settings.baseline_rides)
    since = now - timedelta(days=HISTORY_DAYS)
    rides = db.scalars(select(models.Ride).where(
        models.Ride.lift_id == lift.id, models.Ride.valid.is_(True), models.Ride.started_at >= since)
        .order_by(models.Ride.started_at)).all()

    features = {}
    for feature, unit in CHART_FEATURES.items():
        band = None
        bases = [b[feature] for b in baselines.values() if feature in b]
        if bases:
            band = [round(min(b.median - scoring.Z_FREE * b.spread for b in bases), 4),
                    round(max(b.median + scoring.Z_FREE * b.spread for b in bases), 4),
                    round(float(np.mean([b.median for b in bases])), 4)]
        daily: dict[str, list[float]] = {}
        points = []
        for r in rides:
            v = getattr(r, feature)
            if v is None:
                continue
            points.append({"t": iso(r.started_at), "v": v, "dir": r.direction})
            daily.setdefault(r.started_at.date().isoformat(), []).append(v)
        features[feature] = {
            "label": scoring.FEATURE_LABELS[feature],
            "unit": unit,
            "baseline_band": band,  # [low, high, median], low/high = median ± 2 spreads
            "points": points,
            "daily_median": [{"date": d, "v": round(float(np.median(vs)), 4)} for d, vs in sorted(daily.items())],
        }

    reports = db.scalars(select(models.Report).where(models.Report.lift_id == lift.id)
                         .order_by(models.Report.created_at.desc()).limit(20)).all()
    alerts = db.scalars(select(models.Alert).where(models.Alert.lift_id == lift.id)
                        .order_by(models.Alert.resolved, models.Alert.updated_at.desc()).limit(30)).all()
    latest = db.scalar(select(models.Ride.id).where(models.Ride.lift_id == lift.id, models.Ride.valid.is_(True))
                       .order_by(models.Ride.started_at.desc()).limit(1))
    counts = dict(db.execute(select(models.Ride.valid, func.count()).where(models.Ride.lift_id == lift.id)
                             .group_by(models.Ride.valid)).all())
    return {
        "id": lift.id, "name": lift.name, "building_name": lift.building.name,
        "floors": lift.floors, "floor_height_m": lift.floor_height_m,
        "health_score": score, "status": status,
        "sentence": pipeline.STATUS_SENTENCES[status].format(name=lift.name),
        "rides_total": counts.get(True, 0) + counts.get(False, 0), "rides_invalid": counts.get(False, 0),
        "baselines": {d: {f: {"median": round(b.median, 4), "spread": round(b.spread, 4)} for f, b in bs.items()}
                      for d, bs in baselines.items()},
        "baseline_rides": settings.baseline_rides,
        "health_history": _health_history(db, lift.id, now),
        "features": features,
        "reports": [{"id": r.id, "created_at": iso(r.created_at), "category": r.category, "comment": r.comment}
                    for r in reports],
        "alerts": [alert_json(a, lift.name) for a in alerts],
        "latest_ride_id": latest,
    }


def ride_row(r: models.Ride) -> dict:
    return {
        "id": r.id, "started_at": iso(r.started_at), "valid": r.valid, "reject_reason": r.reject_reason,
        "direction": r.direction, "floors": r.floors_travelled_est, "duration_s": r.duration_s,
        "ride_score": r.ride_score,
    }


def ride_detail(ride: models.Ride, lift: models.Lift) -> dict:
    """A ride with its features and, if raw samples were kept, the processed trace."""
    out = ride_row(ride)
    out.update({
        "lift_id": ride.lift_id, "lift_name": lift.name, "device_id": ride.device_id,
        "features": {f: getattr(ride, f) for f in scoring.SCORE_WEIGHTS} | {
            "max_speed": ride.max_speed, "travel_distance_m": ride.travel_distance_m,
            "dominant_vib_freq_hz": ride.dominant_vib_freq_hz},
        "z_scores": json.loads(ride.z_scores) if ride.z_scores else {},
        "trace": None,
    })
    if ride.raw_samples:
        a = analyse_ride(pipeline.decompress_samples(ride.raw_samples), floor_height_m=lift.floor_height_m)
        if a.t is not None:
            step = max(1, int(100 / TRACE_HZ))
            trace = {
                "t": np.round(a.t[::step], 2).tolist(),
                "vertical": np.round(a.vertical[::step], 3).tolist(),
                "horizontal": np.round(a.horizontal[::step], 3).tolist(),
                "phases": None,
            }
            if a.phases is not None:
                p = a.phases
                trace["phases"] = {k: round(getattr(p, k) / 100.0, 2)
                                   for k in ("start_begin", "start_end", "stop_begin", "stop_end")}
            out["trace"] = trace
    return out
