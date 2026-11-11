"""Signal processing against synthetic rides with known answers."""

from __future__ import annotations

import numpy as np
import pytest

from app import signal
from sim.ridegen import LiftProfile, PhoneProfile, generate_ride

PHONES = {
    "flat": PhoneProfile(flat_on_floor=True),
    "handheld-ios-sign": PhoneProfile(flat_on_floor=False, inverted_sign=True),
    "100hz-no-gyro": PhoneProfile(rate_hz=100, has_rotation=False),
}


def ride(floors=4, direction="up", lift=None, phone="flat", seed=0, **kw):
    samples, truth = generate_ride(floors, direction, lift=lift, phone=PHONES[phone],
                                   rng=np.random.default_rng(seed), **kw)
    return signal.analyse_ride(samples), truth


@pytest.mark.parametrize("phone", PHONES)
@pytest.mark.parametrize("direction", ["up", "down"])
@pytest.mark.parametrize("floors", [1, 3, 9])
def test_direction_distance_and_floors(floors, direction, phone):
    result, truth = ride(floors, direction, phone=phone, seed=floors)
    assert result.valid, result.reject_reason
    f = result.features
    assert f["direction"] == direction
    assert f["travel_distance_m"] == pytest.approx(truth.distance_m, rel=0.10)
    assert f["floors_travelled_est"] == floors
    assert f["max_speed"] == pytest.approx(truth.peak_speed, rel=0.10)


@pytest.mark.parametrize("stop_factor", [1.0, 1.5])
@pytest.mark.parametrize("seed", range(5))
def test_peak_jerk_within_15_percent(stop_factor, seed):
    result, truth = ride(5, "down" if seed % 2 else "up", lift=LiftProfile(stop_jerk_factor=stop_factor),
                         phone="handheld-ios-sign" if seed % 2 else "flat", seed=seed)
    assert result.valid
    assert result.features["peak_jerk_start"] == pytest.approx(truth.peak_jerk_start, rel=0.15)
    assert result.features["peak_jerk_stop"] == pytest.approx(truth.peak_jerk_stop, rel=0.15)


def test_peak_acceleration():
    result, truth = ride(6, lift=LiftProfile(accel=0.8))
    assert result.features["peak_accel_start"] == pytest.approx(0.8, rel=0.10)
    assert result.features["peak_accel_stop"] == pytest.approx(0.8, rel=0.10)


def test_vibration_rms_tracks_amplitude_and_frequency():
    calm, _ = ride(8, lift=LiftProfile(vib_horizontal=0.03, vib_freq_hz=4.0), seed=1)
    rough, _ = ride(8, lift=LiftProfile(vib_horizontal=0.09, vib_freq_hz=4.0), seed=1)
    ratio = rough.features["vib_horizontal_rms"] / calm.features["vib_horizontal_rms"]
    assert 2.4 < ratio < 3.6
    assert rough.features["vib_peak_to_peak_horizontal"] > 2 * calm.features["vib_peak_to_peak_horizontal"]
    assert rough.features["dominant_vib_freq_hz"] == pytest.approx(4.0, abs=0.5)


def test_one_floor_ride_has_no_cruise_vibration():
    result, _ = ride(1)
    assert result.valid
    assert result.features["vib_horizontal_rms"] is None


def test_settling_time_grows_with_bounce():
    healthy, _ = ride(4, seed=3)
    worn, _ = ride(4, lift=LiftProfile(bounce_amp=0.18, bounce_decay_s=0.7), seed=3)
    assert worn.features["stop_settling_s"] > healthy.features["stop_settling_s"] + 0.5


@pytest.mark.parametrize("disturbance, reason", [
    ("handled", "phone_moved"),
    ("walking", "walking_or_handled"),
    ("too_short", "too_short"),
    ("no_motion", "no_lift_motion"),
])
def test_invalid_rides_rejected(disturbance, reason):
    result, _ = ride(4, disturbance=disturbance, seed=7)
    assert not result.valid
    assert result.reject_reason == reason
    assert reason in signal.REJECT_MESSAGES


def test_shaky_start_rejected():
    samples, _ = generate_ride(4, rng=np.random.default_rng(1))
    rng = np.random.default_rng(2)
    shaky = [[r[0], r[1] + rng.normal(0, 1.5), r[2] + rng.normal(0, 1.5), r[3] + rng.normal(0, 1.5), *r[4:]]
             if r[0] < 2500 else r for r in samples]
    assert signal.analyse_ride(shaky).reject_reason == "not_still_at_start"


def test_too_few_samples():
    assert signal.analyse_ride([[0, 0, 0, 9.8]] * 3).reject_reason == "too_short"


def test_resample_handles_irregular_and_duplicate_times():
    t = np.array([0.0, 0.01, 0.01, 0.035, 0.02, 0.05])
    v = np.array([[0.0], [1.0], [9.0], [3.5], [2.0], [5.0]])
    grid, out = signal.resample_uniform(t, v, fs=100.0)
    assert np.allclose(grid, [0.0, 0.01, 0.02, 0.03, 0.04, 0.05])
    assert np.allclose(out[:, 0], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])


def test_decompose_is_sign_invariant():
    g = np.array([0.0, 0.0, 9.81])
    acc = np.array([[0.0, 0.0, 9.81 + 0.5], [0.2, 0.0, 9.81]])
    v1, hx1, hy1 = signal.decompose(acc, g)
    v2, _, _ = signal.decompose(-acc, -g)
    assert np.allclose(v1, [0.5, 0.0])
    assert np.allclose(v1, v2)
    assert np.hypot(hx1[1], hy1[1]) == pytest.approx(0.2)
