"""Rule-based health scoring: baselines, ride scores, lift health and alerts.

Pure functions over plain data, no database access. All tunable numbers live
in the constants below.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

# Features that affect the score, with their penalty weights. Vibration and
# jerk weigh most: they are what riders feel and what wear changes first.
SCORE_WEIGHTS: dict[str, float] = {
    "vib_horizontal_rms": 3.0,
    "vib_peak_to_peak_horizontal": 2.0,
    "vib_vertical_rms": 2.5,
    "peak_jerk_stop": 3.0,
    "peak_jerk_start": 2.5,
    "stop_settling_s": 2.0,
    "peak_accel_start": 1.0,
    "peak_accel_stop": 1.0,
}

# Smallest baseline spread per feature (in the feature's unit), so a lift
# whose baseline happens to be very consistent (e.g. settling always 0 s)
# does not turn tiny changes into huge z-scores.
MIN_SPREAD: dict[str, float] = {
    "vib_horizontal_rms": 0.004,
    "vib_peak_to_peak_horizontal": 0.015,
    "vib_vertical_rms": 0.004,
    "peak_jerk_stop": 0.05,
    "peak_jerk_start": 0.05,
    "stop_settling_s": 0.08,
    "peak_accel_start": 0.02,
    "peak_accel_stop": 0.02,
}
RELATIVE_MIN_SPREAD = 0.03  # and at least 3 % of the baseline median

Z_FREE = 2.0            # z-scores up to this cost nothing (normal ride-to-ride variation)
Z_CAP = 8.0             # one feature cannot cost more than its weight x (Z_CAP - Z_FREE) x ...
PENALTY_PER_Z = 2.0     # ... points per unit of z

GREEN_MIN, YELLOW_MIN = 80.0, 60.0
HEALTH_WINDOW = timedelta(days=7)
HEALTH_MIN_RIDES_IN_WINDOW = 10
HEALTH_FALLBACK_RIDES = 30
HEALTH_MIN_SCORED = 5

TREND_WINDOW = timedelta(days=21)
TREND_MIN_POINTS = 12
TREND_MIN_SPAN = timedelta(days=4)
TREND_WARN_INCREASE = 0.25      # +25 % vs baseline
TREND_MIN_Z = 2.0               # ... and the fitted value must also be this many spreads above it
TREND_CRITICAL_INCREASE = 0.60
OUTLIER_Z = 3.0
OUTLIER_LAST_N = 10
OUTLIER_WARN_COUNT = 5

SEVERITIES = ("info", "warn", "critical")

FEATURE_LABELS: dict[str, str] = {
    "vib_horizontal_rms": "horizontal vibration",
    "vib_peak_to_peak_horizontal": "horizontal sway",
    "vib_vertical_rms": "vertical vibration",
    "peak_jerk_stop": "stop jerk",
    "peak_jerk_start": "start jerk",
    "stop_settling_s": "bounce after stopping",
    "peak_accel_start": "start acceleration",
    "peak_accel_stop": "braking deceleration",
}

LIKELY_CAUSES: dict[str, str] = {
    "vib_horizontal_rms": "check guide rollers/shoes and rail alignment",
    "vib_peak_to_peak_horizontal": "check guide rollers/shoes and rail alignment",
    "vib_vertical_rms": "check ropes or belts, car suspension and the drive",
    "peak_jerk_stop": "harsh stopping, check brake and drive tuning",
    "peak_jerk_start": "harsh starting, check drive tuning and brake release",
    "stop_settling_s": "poor levelling or bounce, check rope tension and levelling control",
    "peak_accel_start": "check the drive's speed controller",
    "peak_accel_stop": "check the drive's speed controller and brake",
}


@dataclass(frozen=True)
class FeatureBaseline:
    median: float
    spread: float   # 1.4826 x MAD, floored
    n: int


@dataclass(frozen=True)
class RidePoint:
    """One valid ride as the scorer sees it."""

    started_at: datetime
    direction: str
    features: dict[str, float | None]
    score: float | None = None


@dataclass(frozen=True)
class TrendFinding:
    feature: str
    severity: str          # "warn" or "critical"
    increase: float | None  # fitted change vs baseline, e.g. 0.4 = +40 %
    outliers: int          # rides with z > OUTLIER_Z among the last OUTLIER_LAST_N
    window_days: int


# --------------------------------------------------------------- baselines


def robust_baseline(values: Sequence[float], feature: str) -> FeatureBaseline | None:
    """Median and robust spread (1.4826 x MAD) of the values, with a floor."""
    arr = np.array([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if len(arr) == 0:
        return None
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    spread = max(1.4826 * mad, MIN_SPREAD.get(feature, 1e-6), RELATIVE_MIN_SPREAD * abs(med))
    return FeatureBaseline(med, spread, len(arr))


def compute_baselines(rides: Sequence[RidePoint]) -> dict[str, FeatureBaseline]:
    """Baseline per scored feature from the given (baseline) rides of one direction."""
    out: dict[str, FeatureBaseline] = {}
    for feature in SCORE_WEIGHTS:
        b = robust_baseline([r.features.get(feature) for r in rides], feature)
        if b is not None:
            out[feature] = b
    return out


def robust_z(value: float, baseline: FeatureBaseline) -> float:
    return (value - baseline.median) / baseline.spread


# ------------------------------------------------------------ ride scores


def score_ride(features: dict[str, float | None],
               baselines: dict[str, FeatureBaseline]) -> tuple[float, dict[str, float]]:
    """Ride score 0-100 and the z-score of each scored feature.

    Starts at 100 and subtracts a penalty for every feature above its
    baseline beyond normal variation. Values below baseline (a smoother
    ride than usual) cost nothing.
    """
    zs: dict[str, float] = {}
    penalty = 0.0
    for feature, weight in SCORE_WEIGHTS.items():
        value, base = features.get(feature), baselines.get(feature)
        if value is None or base is None or not math.isfinite(value):
            continue
        z = robust_z(value, base)
        zs[feature] = round(z, 2)
        penalty += weight * PENALTY_PER_Z * min(max(z - Z_FREE, 0.0), Z_CAP - Z_FREE)
    return round(max(0.0, 100.0 - penalty), 1), zs


# ------------------------------------------------------------- lift health


def status_for(score: float | None) -> str:
    if score is None:
        return "grey"
    if score >= GREEN_MIN:
        return "green"
    return "yellow" if score >= YELLOW_MIN else "red"


def lift_health(scored: Sequence[tuple[datetime, float]], now: datetime) -> tuple[float | None, str]:
    """Rolling median of ride scores: the last 7 days, or the last 30 rides if the week is quiet.

    Returns (None, "grey") while fewer than HEALTH_MIN_SCORED rides are scored.
    """
    if len(scored) < HEALTH_MIN_SCORED:
        return None, "grey"
    ordered = sorted(scored, key=lambda p: p[0])
    recent = [s for t, s in ordered if now - HEALTH_WINDOW <= t <= now]
    if len(recent) < HEALTH_MIN_RIDES_IN_WINDOW:
        recent = [s for t, s in ordered if t <= now][-HEALTH_FALLBACK_RIDES:]
    if len(recent) < HEALTH_MIN_SCORED:
        return None, "grey"
    score = round(float(np.median(recent)), 1)
    return score, status_for(score)


# ----------------------------------------------------------------- trends


def detect_trends(rides: Sequence[RidePoint], baselines_by_direction: dict[str, dict[str, FeatureBaseline]],
                  now: datetime) -> list[TrendFinding]:
    """Find features that are drifting up or repeatedly far above baseline.

    Values are normalised by their direction's baseline, so up and down
    rides form one series. Two triggers:
      - trend: a linear fit over the last 21 days puts today's value more
        than 25 % above the baseline median (critical above 60 %), and more
        than TREND_MIN_Z robust spreads above it. The second condition keeps
        features with a near-zero baseline (e.g. settling time) from alerting
        on percentages that are only noise;
      - outliers: at least 5 of the last 10 rides have z > 3. This alone is a
        warning: tight features (jerk) put even a mildly worse ride above
        z = 3, so "critical" needs the size of the trend as well.
    """
    window = [r for r in rides if now - TREND_WINDOW <= r.started_at <= now]
    findings: list[TrendFinding] = []
    for feature in SCORE_WEIGHTS:
        ts, ratios, zs = [], [], []
        for r in window:
            value = r.features.get(feature)
            base = baselines_by_direction.get(r.direction, {}).get(feature)
            if value is None or base is None or base.median <= 0:
                continue
            ts.append((r.started_at - now).total_seconds() / 86400.0)
            ratios.append(value / base.median)
            zs.append(robust_z(value, base))

        increase = None
        if len(ts) >= TREND_MIN_POINTS and (max(ts) - min(ts)) * 86400 >= TREND_MIN_SPAN.total_seconds():
            slope, intercept = np.polyfit(ts, ratios, 1)
            z_now = float(np.polyval(np.polyfit(ts, zs, 1), 0.0))
            if slope > 0 and z_now > TREND_MIN_Z:
                increase = float(intercept) - 1.0  # fitted value at now (t = 0) vs baseline
        outliers = sum(1 for z in zs[-OUTLIER_LAST_N:] if z > OUTLIER_Z)
        trending = increase is not None and increase > TREND_WARN_INCREASE

        severity = None
        if trending and increase > TREND_CRITICAL_INCREASE:
            severity = "critical"
        elif trending or outliers >= OUTLIER_WARN_COUNT:
            severity = "warn"
        if severity:
            findings.append(TrendFinding(feature, severity, increase, outliers, TREND_WINDOW.days))
    return findings


def alert_message(lift_name: str, finding: TrendFinding) -> str:
    """Plain-language alert text, e.g. "Lift 2: stop jerk up 40% over 3 weeks; harsh stopping, ..."."""
    label = FEATURE_LABELS.get(finding.feature, finding.feature)
    cause = LIKELY_CAUSES.get(finding.feature, "inspect the lift")
    if finding.increase is not None and finding.increase > TREND_WARN_INCREASE:
        weeks = round(finding.window_days / 7)
        what = f"{label} up {round(finding.increase * 100)}% vs baseline over {weeks} weeks"
    else:
        what = f"{label} far above normal on {finding.outliers} of the last {OUTLIER_LAST_N} rides"
    return f"{lift_name}: {what}; {cause}."


def bump_severity(severity: str) -> str:
    """One level up (used when residents report a problem on a lift that is already trending)."""
    return SEVERITIES[min(SEVERITIES.index(severity) + 1, len(SEVERITIES) - 1)]
