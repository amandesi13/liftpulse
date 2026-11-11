"""Synthetic lift rides as a phone would record them.

Physics: a jerk-limited S-curve motion profile (constant-jerk segments), with
car vibration that grows with speed, an optional bounce after the stop, and
a phone sensor model: random orientation, noise, bias, irregular sample
times and either sign convention for accelerationIncludingGravity.

Used by the simulator and by the tests, which need traces with known answers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

G = 9.80665
FINE_HZ = 1000.0  # internal integration rate


@dataclass
class LiftProfile:
    """How a particular lift moves. Units: m, s, m/s, m/s^2, m/s^3, Hz."""

    max_speed: float = 1.2
    accel: float = 0.9
    jerk: float = 1.2
    floor_height: float = 2.8
    vib_freq_hz: float = 5.0
    vib_horizontal: float = 0.03   # amplitude at full speed
    vib_vertical: float = 0.02
    stop_jerk_factor: float = 1.0  # >1: harsher stopping (degradation)
    bounce_amp: float = 0.08       # vertical oscillation after the stop
    bounce_freq_hz: float = 2.2
    bounce_decay_s: float = 0.25


@dataclass
class PhoneProfile:
    """How the recording phone behaves."""

    rate_hz: float = 60.0
    jitter: float = 0.15          # relative jitter of sample intervals
    noise: float = 0.015          # m/s^2 white noise per axis
    bias: float = 0.05            # max per-axis bias, m/s^2
    flat_on_floor: bool = True    # else hand-held and tilted
    inverted_sign: bool = False   # old iOS convention: gravity reads negative
    rotation_noise_dps: float = 0.5
    has_rotation: bool = True


@dataclass
class RideTruth:
    """Known answers for a generated ride."""

    direction: str
    distance_m: float
    floors: int
    peak_speed: float
    peak_jerk_start: float
    peak_jerk_stop: float
    peak_accel: float
    duration_s: float


@dataclass
class _Segments:
    jerks: list[tuple[float, float]] = field(default_factory=list)  # (duration, jerk)


def _phase_times(v: float, a_max: float, j: float) -> tuple[float, float, float]:
    """Constant-jerk ramp time, constant-accel time, and peak accel to reach speed v."""
    if v >= a_max * a_max / j:
        tj = a_max / j
        return tj, v / a_max - tj, a_max
    a_peak = math.sqrt(v * j)
    return a_peak / j, 0.0, a_peak


def motion_profile(distance: float, lift: LiftProfile) -> tuple[np.ndarray, RideTruth]:
    """Vertical acceleration (positive = in the direction of travel) at FINE_HZ."""
    j_start = lift.jerk
    j_stop = lift.jerk * lift.stop_jerk_factor

    def travel_without_cruise(v: float) -> float:
        tj1, ta1, _ = _phase_times(v, lift.accel, j_start)
        tj2, ta2, _ = _phase_times(v, lift.accel, j_stop)
        # each phase is symmetric, so its mean speed is v/2
        return v * (2 * tj1 + ta1) / 2 + v * (2 * tj2 + ta2) / 2

    v = lift.max_speed
    if travel_without_cruise(v) > distance:
        lo, hi = 0.0, v
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if travel_without_cruise(mid) < distance else (lo, mid)
        v = lo
    tj1, ta1, a1 = _phase_times(v, lift.accel, j_start)
    tj2, ta2, a2 = _phase_times(v, lift.accel, j_stop)
    cruise = max(0.0, (distance - travel_without_cruise(v)) / v)

    segments = [(tj1, j_start), (ta1, 0.0), (tj1, -j_start), (cruise, 0.0),
                (tj2, -j_stop), (ta2, 0.0), (tj2, j_stop)]
    jerk = np.concatenate([np.full(int(round(d * FINE_HZ)), jj) for d, jj in segments if d > 0])
    accel = np.cumsum(jerk) / FINE_HZ
    truth = RideTruth(
        direction="up", distance_m=distance, floors=0, peak_speed=v,
        peak_jerk_start=j_start, peak_jerk_stop=j_stop, peak_accel=max(a1, a2),
        duration_s=len(accel) / FINE_HZ,
    )
    return accel, truth


def _rotation(yaw: float, tilt_x: float, tilt_y: float) -> np.ndarray:
    cz, sz = math.cos(yaw), math.sin(yaw)
    cx, sx = math.cos(tilt_x), math.sin(tilt_x)
    cy, sy = math.cos(tilt_y), math.sin(tilt_y)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    return rz @ rx @ ry


def generate_ride(
    floors: int,
    direction: str = "up",
    lift: LiftProfile | None = None,
    phone: PhoneProfile | None = None,
    rng: np.random.Generator | None = None,
    pre_still_s: float = 1.5,
    post_still_s: float = 3.2,
    disturbance: str | None = None,
) -> tuple[list[list[float | None]], RideTruth]:
    """Generate one ride as uploaded by the resident page.

    Args:
        floors: storeys travelled (>= 1).
        direction: "up" or "down".
        disturbance: None, "handled" (phone picked up mid-ride), "walking",
            "too_short" or "no_motion", to produce rides that must be rejected.

    Returns:
        (samples, truth): samples as [[t_ms, ax, ay, az, rx, ry, rz], ...].
    """
    lift = lift or LiftProfile()
    phone = phone or PhoneProfile()
    rng = rng or np.random.default_rng()
    distance = floors * lift.floor_height

    if disturbance == "no_motion":
        vert = np.zeros(int(8 * FINE_HZ))
        truth = RideTruth(direction, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 8.0)
    else:
        vert, truth = motion_profile(distance, lift)
    truth.direction, truth.floors = direction, floors if disturbance != "no_motion" else 0

    n_pre, n_post = int(pre_still_s * FINE_HZ), int(post_still_s * FINE_HZ)
    n_motion = len(vert)
    speed = np.cumsum(vert) / FINE_HZ

    # bounce after the stop (levelling / rope stretch)
    t_post = np.arange(n_post) / FINE_HZ
    bounce = lift.bounce_amp * np.exp(-t_post / max(lift.bounce_decay_s, 1e-3)) * np.sin(
        2 * math.pi * lift.bounce_freq_hz * t_post)

    sign = 1.0 if direction == "up" else -1.0
    a_vert = np.concatenate([np.zeros(n_pre), sign * vert, bounce])
    v_frac = np.concatenate([np.zeros(n_pre), np.abs(speed) / max(lift.max_speed, 1e-6), np.zeros(n_post)])
    n = len(a_vert)
    t = np.arange(n) / FINE_HZ

    # car vibration, proportional to speed, along a random horizontal direction
    theta = rng.uniform(0, 2 * math.pi)
    ph = rng.uniform(0, 2 * math.pi, size=3)
    f = lift.vib_freq_hz
    h_main = lift.vib_horizontal * v_frac * (np.sin(2 * math.pi * f * t + ph[0])
                                             + 0.3 * np.sin(2 * math.pi * 1.7 * f * t + ph[1]))
    hx, hy = h_main * math.cos(theta), h_main * math.sin(theta)
    vz = lift.vib_vertical * v_frac * np.sin(2 * math.pi * 1.3 * f * t + ph[2])

    proper = np.stack([hx, hy, a_vert + vz + G], axis=1)  # world frame, z up
    rot_dps = np.zeros((n, 3))

    if disturbance == "walking":
        # carried around after a still start (e.g. in a pocket while walking)
        walk = (t >= pre_still_s).astype(float)
        steps = 1.8 * np.sin(2 * math.pi * 1.9 * t) * walk
        proper[:, 0] += steps
        proper[:, 2] += 1.2 * np.abs(np.sin(2 * math.pi * 1.9 * t)) * walk
        rot_dps += (25 * np.sin(2 * math.pi * 1.9 * t) * walk)[:, None]

    if phone.flat_on_floor:
        r0 = _rotation(rng.uniform(0, 2 * math.pi), math.radians(rng.uniform(-3, 3)),
                       math.radians(rng.uniform(-3, 3)))
    else:
        r0 = _rotation(rng.uniform(0, 2 * math.pi), math.radians(rng.uniform(30, 70)),
                       math.radians(rng.uniform(-15, 15)))
    rotations = np.repeat(r0[None], n, axis=0)

    if disturbance == "handled":
        # phone picked up and turned mid-ride: rotation burst, orientation changes
        k0 = n_pre + n_motion // 3
        k1 = k0 + int(1.0 * FINE_HZ)
        r1 = _rotation(rng.uniform(0, 2 * math.pi), math.radians(55), math.radians(10))
        rotations[k1:] = r1
        rot_dps[k0:k1] += 120.0
        proper[k0:k1, 0] += 2.0 * np.sin(np.linspace(0, 4 * math.pi, k1 - k0))

    # world -> phone frame: m = R^T a
    meas = np.einsum("nji,nj->ni", rotations, proper)
    if phone.inverted_sign:
        meas = -meas

    # irregular phone sample times
    dt_ms = 1000.0 / phone.rate_hz
    times = [0.0]
    end_ms = (n - 1) / FINE_HZ * 1000.0
    if disturbance == "too_short":
        end_ms = min(end_ms, 2000.0)
    while times[-1] + dt_ms < end_ms:
        times.append(times[-1] + dt_ms * (1 + rng.uniform(-phone.jitter, phone.jitter)))
    times_arr = np.array(times)
    idx = np.clip(np.round(times_arr / 1000.0 * FINE_HZ).astype(int), 0, n - 1)

    bias = rng.uniform(-phone.bias, phone.bias, size=3)
    acc = meas[idx] + bias + rng.normal(0, phone.noise, size=(len(idx), 3))
    rot = rot_dps[idx] + rng.normal(0, phone.rotation_noise_dps, size=(len(idx), 3))

    samples: list[list[float | None]] = []
    for k in range(len(idx)):
        row: list[float | None] = [round(float(times_arr[k]), 1),
                                   *(round(float(x), 4) for x in acc[k])]
        row += [round(float(x), 2) for x in rot[k]] if phone.has_rotation else [None, None, None]
        samples.append(row)
    truth.duration_s = n_motion / FINE_HZ
    return samples, truth
