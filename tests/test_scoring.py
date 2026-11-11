"""Scoring: baselines, ride scores, lift health and trend alerts (pure functions)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from app import scoring

NOW = datetime(2026, 3, 1, 12, 0)

# Typical healthy values and ride-to-ride spread (from the synthetic generator).
HEALTHY = {
    "vib_horizontal_rms": (0.024, 0.0025),
    "vib_peak_to_peak_horizontal": (0.10, 0.010),
    "vib_vertical_rms": (0.015, 0.0015),
    "peak_jerk_stop": (1.25, 0.04),
    "peak_jerk_start": (1.25, 0.04),
    "stop_settling_s": (0.05, 0.05),
    "peak_accel_start": (0.92, 0.015),
    "peak_accel_stop": (0.92, 0.015),
}


def make_rides(days=21, per_day=15, degrade=None, seed=0, end=NOW):
    """Rides over `days` ending at `end`. degrade: {feature: total relative increase at the end}."""
    rng = np.random.default_rng(seed)
    n = days * per_day
    rides = []
    for i in range(n):
        frac = i / max(n - 1, 1)
        t = end - timedelta(days=days) + timedelta(days=days * frac)
        features = {}
        for f, (mean, sd) in HEALTHY.items():
            v = max(0.0, rng.normal(mean, sd))
            if degrade and f in degrade:
                v *= 1 + degrade[f] * frac
            features[f] = v
        rides.append(scoring.RidePoint(t, "up" if i % 2 else "down", features))
    return rides


def baselines_from(rides, n=20):
    return {d: scoring.compute_baselines([r for r in rides if r.direction == d][:n]) for d in ("up", "down")}


def scored(rides, baselines):
    return [(r.started_at, scoring.score_ride(r.features, baselines[r.direction])[0]) for r in rides]


def test_robust_baseline_and_floor():
    b = scoring.robust_baseline([1.0, 1.1, 0.9, 1.0, 5.0], "peak_jerk_stop")
    assert b.median == pytest.approx(1.0)
    assert b.spread == pytest.approx(1.4826 * 0.1)
    flat = scoring.robust_baseline([0.0] * 20, "stop_settling_s")
    assert flat.spread == scoring.MIN_SPREAD["stop_settling_s"]
    assert scoring.robust_baseline([None, float("nan")], "peak_jerk_stop") is None


def test_ride_at_baseline_scores_100_and_smoother_ride_is_not_penalised():
    rides = make_rides(days=3)
    b = baselines_from(rides)["up"]
    medians = {f: base.median for f, base in b.items()}
    assert scoring.score_ride(medians, b)[0] == 100.0
    smoother = {f: v * 0.5 for f, v in medians.items()}
    assert scoring.score_ride(smoother, b)[0] == 100.0


def test_rougher_ride_loses_points_and_reports_z_scores():
    b = baselines_from(make_rides(days=3))["up"]
    features = {f: base.median for f, base in b.items()}
    features["vib_horizontal_rms"] = b["vib_horizontal_rms"].median + 6 * b["vib_horizontal_rms"].spread
    score, zs = scoring.score_ride(features, b)
    assert zs["vib_horizontal_rms"] == pytest.approx(6.0)
    expected = 100 - scoring.SCORE_WEIGHTS["vib_horizontal_rms"] * scoring.PENALTY_PER_Z * (6 - scoring.Z_FREE)
    assert score == pytest.approx(expected)


def test_healthy_lift_stays_green_without_alerts():
    rides = make_rides(days=30)
    b = baselines_from(rides)
    score, status = scoring.lift_health(scored(rides, b), NOW)
    assert status == "green" and score >= 95
    assert scoring.detect_trends(rides, b, NOW) == []


def test_degrading_lift_triggers_alerts_and_turns_red():
    healthy = make_rides(days=10, end=NOW - timedelta(days=21))
    degrading = make_rides(days=21, degrade={"peak_jerk_stop": 0.8, "vib_horizontal_rms": 1.2}, seed=1)
    rides = healthy + degrading
    b = baselines_from(rides)

    findings = {f.feature: f for f in scoring.detect_trends(rides, b, NOW)}
    assert {"peak_jerk_stop", "vib_horizontal_rms"} <= set(findings)
    jerk = findings["peak_jerk_stop"]
    assert jerk.severity == "critical"
    assert jerk.increase == pytest.approx(0.8, abs=0.15)
    msg = scoring.alert_message("Lift 2", jerk)
    assert msg.startswith("Lift 2: stop jerk up ")
    assert "% vs baseline over 3 weeks; harsh stopping, check brake and drive tuning." in msg

    score, status = scoring.lift_health(scored(rides, b), NOW)
    assert status == "red"
    # a week before the end the lift was still only part-way degraded
    week_ago = NOW - timedelta(days=9)
    earlier = [p for p in scored(rides, b) if p[0] <= week_ago]
    assert scoring.lift_health(earlier, week_ago)[1] in ("green", "yellow")


def test_mild_trend_is_a_warning():
    rides = make_rides(days=10, end=NOW - timedelta(days=21)) + make_rides(days=21, degrade={"peak_jerk_start": 0.4})
    findings = {f.feature: f for f in scoring.detect_trends(rides, baselines_from(rides), NOW)}
    assert findings["peak_jerk_start"].severity == "warn"


def test_repeated_outliers_trigger_without_a_trend():
    rides = make_rides(days=21)
    b = baselines_from(rides)
    spike = b["down"]["stop_settling_s"].median + 5 * b["down"]["stop_settling_s"].spread
    last = rides[-10:]
    for r in last[:6]:
        r.features["stop_settling_s"] = spike * (1 if r.direction == "down" else 1.0)
    findings = {f.feature: f for f in scoring.detect_trends(rides, b, NOW)}
    assert findings["stop_settling_s"].outliers >= scoring.OUTLIER_WARN_COUNT
    assert "far above normal on" in scoring.alert_message("Lift 1", findings["stop_settling_s"])


def test_too_few_rides_is_grey():
    assert scoring.lift_health([(NOW, 100.0)] * 4, NOW) == (None, "grey")


def test_quiet_week_falls_back_to_last_30_rides():
    old = [(NOW - timedelta(days=20, minutes=i), 70.0) for i in range(30)]
    assert scoring.lift_health(old, NOW) == (70.0, "yellow")


@pytest.mark.parametrize("score, status", [(100, "green"), (80, "green"), (79.9, "yellow"),
                                           (60, "yellow"), (59.9, "red"), (None, "grey")])
def test_status_thresholds(score, status):
    assert scoring.status_for(score) == status


def test_bump_severity():
    assert scoring.bump_severity("info") == "warn"
    assert scoring.bump_severity("warn") == "critical"
    assert scoring.bump_severity("critical") == "critical"
