"""End to end: rides through the API turn into scores, status and alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from sim.ridegen import LiftProfile, generate_ride

from .conftest import ADMIN, BASELINE, post_rides

def test_grey_until_baseline_then_green(small):
    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
    first = post_rides(small, 2, start)
    assert first["ride_score"] is None
    assert first["lift_status"]["status"] == "grey"
    last = post_rides(small, 2 * BASELINE + 8, start + timedelta(hours=8), seed=1, hours=3)
    assert last["ride_score"] is not None
    assert last["lift_status"]["status"] == "green"
    assert small.get("/api/alerts", headers=ADMIN).json() == []


def test_degradation_raises_alert_and_report_escalates(small):
    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=12)
    post_rides(small, 2 * BASELINE + 4, start, hours=6)
    worn = LiftProfile(stop_jerk_factor=1.7)
    last = post_rides(small, 24, start + timedelta(days=5), lift=worn, seed=2, hours=6)
    assert last["lift_status"]["status"] in ("yellow", "red")

    alerts = small.get("/api/alerts", headers=ADMIN).json()
    jerk = next(a for a in alerts if a["feature"] == "peak_jerk_stop")
    assert jerk["message"].startswith("Lift 2: stop jerk")
    before = jerk["severity"]

    assert small.post("/api/reports", json={"lift_id": "l2", "category": "shaking"}).status_code == 201
    after = next(a for a in small.get("/api/alerts", headers=ADMIN).json() if a["id"] == jerk["id"])
    assert after["message"].endswith("Residents have also reported problems.")
    assert after["severity"] == ("critical" if before in ("warn", "critical") else "warn")

    resolved = small.post(f"/api/alerts/{jerk['id']}/resolve", headers=ADMIN).json()
    assert resolved["resolved"] is True
    assert all(a["id"] != jerk["id"] for a in small.get("/api/alerts", headers=ADMIN).json())


def test_report_without_sensor_alert_creates_info_alert(small):
    small.post("/api/reports", json={"lift_id": "l2", "category": "door", "comment": "slow"})
    alerts = small.get("/api/alerts", headers=ADMIN).json()
    assert [(a["severity"], a["feature"]) for a in alerts] == [("info", "resident_report")]
    assert "door problem" in alerts[0]["message"]


def test_backdating_needs_admin_token(small):
    samples, _ = generate_ride(3, rng=np.random.default_rng(0))
    t = "2020-01-01T00:00:00"
    small.post("/api/rides", json={"lift_id": "l2", "samples": samples, "started_at": t})
    small.post("/api/rides", json={"lift_id": "l2", "samples": samples, "started_at": t}, headers=ADMIN)
    r = small.post("/api/admin/recompute", headers=ADMIN)
    assert r.status_code == 200
    assert small.post("/api/admin/recompute").status_code == 401
