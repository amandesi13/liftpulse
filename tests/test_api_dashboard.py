"""Dashboard API and pages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import ADMIN, BASELINE, post_rides


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_pages_are_served(small):
    for path, text in (("/dashboard", "Open alerts"), ("/lifts/l2", "Ride quality"), ("/admin", "QR stickers")):
        r = small.get(path)
        assert r.status_code == 200 and text in r.text
    assert small.get("/", follow_redirects=False).headers["location"] == "/dashboard"


def test_dashboard_routes_need_token(small):
    for path in ("/api/lifts", "/api/lifts/l2", "/api/lifts/l2/rides", "/api/alerts"):
        assert small.get(path).status_code == 401, path


def test_lift_overview(small):
    post_rides(small, 2 * BASELINE + 6, now() - timedelta(days=3), hours=3)
    [row] = small.get("/api/lifts", headers=ADMIN).json()
    assert row["id"] == "l2" and row["building_name"] == "B"
    assert row["status"] == "green" and row["health_score"] >= 80
    assert row["rides_7d"] == 2 * BASELINE + 6
    assert row["trend"] in ("up", "down", "flat")
    assert row["open_alerts"] == 0 and row["last_ride_at"].endswith("Z")


def test_lift_detail_and_ride_trace(small):
    post_rides(small, 2 * BASELINE + 4, now() - timedelta(days=3), hours=3)
    d = small.get("/api/lifts/l2", headers=ADMIN).json()
    assert d["status"] == "green"
    assert set(d["baselines"]) == {"up", "down"}
    jerk = d["features"]["peak_jerk_stop"]
    low, high, median = jerk["baseline_band"]
    assert low < median < high
    assert len(jerk["points"]) == 2 * BASELINE + 4
    assert d["health_history"] and d["health_history"][-1]["status"] == "green"

    rides = small.get("/api/lifts/l2/rides?limit=5", headers=ADMIN).json()
    assert len(rides) == 5 and rides[0]["started_at"] >= rides[-1]["started_at"]
    ride = small.get(f"/api/rides/{d['latest_ride_id']}", headers=ADMIN).json()
    trace = ride["trace"]
    assert len(trace["t"]) == len(trace["vertical"]) == len(trace["horizontal"]) > 50
    p = trace["phases"]
    assert 0 < p["start_begin"] < p["start_end"] <= p["stop_begin"] < p["stop_end"] <= trace["t"][-1] + 0.05
    assert small.get("/api/rides/99999", headers=ADMIN).status_code == 404
