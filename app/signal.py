"""Feature extraction for one lift ride recorded with a phone. Pure functions.

Pipeline (see analyse_ride):
  1. resample the irregular phone samples to a uniform 100 Hz grid
  2. estimate gravity from the stillest 0.5 s before the lift moves
  3. split acceleration into a vertical part (along gravity, minus g) and a
     horizontal part; this works for any phone orientation and either sign
     convention of accelerationIncludingGravity
  4. low-pass filter: 10 Hz for acceleration, 5 Hz before differentiating
     to jerk (ISO 18738-1 style ride-quality filtering); jerk is the local
     slope over 0.31 s (Savitzky-Golay), because a plain derivative turns
     phone sensor noise into jerk peaks larger than the real ones
  5. find the start pulse, the constant-speed (cruise) phase and the stop pulse
  6. reject recordings that are not a clean lift ride
  7. compute the ride features
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.integrate import cumulative_trapezoid
from scipy.signal import butter, savgol_filter, sosfiltfilt

FS_HZ = 100.0
ACCEL_CUTOFF_HZ = 10.0
JERK_CUTOFF_HZ = 5.0
JERK_SLOPE_WINDOW_S = 0.31  # jerk = local slope of acceleration over this window
GRAVITY_WINDOW_S = 0.5
DEFAULT_FLOOR_HEIGHT_M = 2.8

# Features stored per ride; the numeric ones (all but direction) are scored.
FEATURE_NAMES: tuple[str, ...] = (
    "direction",
    "duration_s",
    "peak_accel_start",
    "peak_accel_stop",
    "peak_jerk_start",
    "peak_jerk_stop",
    "vib_vertical_rms",
    "vib_horizontal_rms",
    "vib_peak_to_peak_horizontal",
    "dominant_vib_freq_hz",
    "max_speed",
    "travel_distance_m",
    "floors_travelled_est",
    "stop_settling_s",
)

REJECT_MESSAGES = {
    "too_short": "The recording was too short. Tap Start before the doors close.",
    "not_still_at_start": "The phone wasn't held still at the start. Hold it still or place it on the floor.",
    "phone_moved": "The phone moved during the ride, so the reading isn't reliable.",
    "walking_or_handled": "It looks like the phone was carried around. Hold it still during the ride.",
    "no_lift_motion": "We couldn't detect the lift moving. Was the ride over before you tapped Start?",
    "implausible_motion": "The motion didn't look like a lift ride.",
}


@dataclass(frozen=True)
class Limits:
    """Thresholds for phase detection and validity checks."""

    min_duration_s: float = 3.0
    min_pulse_accel: float = 0.15        # m/s^2: weaker pulses are not a lift start/stop
    pulse_fraction: float = 0.3          # pulse threshold relative to the strongest pulse
    pulse_edge: float = 0.03             # m/s^2: where a pulse is considered to begin/end
    max_start_std: float = 0.25          # m/s^2: |a| variation allowed in the gravity window
    max_rotation_dps: float = 60.0
    max_tilt_change_deg: float = 20.0
    max_horizontal_rms: float = 0.8      # m/s^2 over the whole ride
    min_cruise_s: float = 1.0
    settle_threshold: float = 0.04       # m/s^2 RMS envelope counted as settled
    settle_window_s: float = 0.3
    settle_search_s: float = 5.0
    min_distance_m: float = 1.0
    max_speed: float = 12.0


@dataclass
class Phases:
    """Sample indices on the 100 Hz grid."""

    start_begin: int
    start_end: int
    stop_begin: int
    stop_end: int
    direction: str  # "up" or "down"


@dataclass
class RideAnalysis:
    valid: bool
    reject_reason: str | None
    features: dict[str, float | int | str | None] = field(default_factory=dict)
    t: np.ndarray | None = None
    vertical: np.ndarray | None = None      # filtered, m/s^2
    horizontal: np.ndarray | None = None    # filtered magnitude, m/s^2
    phases: Phases | None = None


# ------------------------------------------------------------- basic steps


def parse_samples(samples: list[list[float | None]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split upload rows into time (s), acceleration (N,3) and rotation (N,3, NaN if absent)."""
    arr = np.array([[np.nan if v is None else v for v in (row + [None] * (7 - len(row)))] for row in samples],
                   dtype=float)
    return arr[:, 0] / 1000.0, arr[:, 1:4], arr[:, 4:7]


def resample_uniform(t: np.ndarray, values: np.ndarray, fs: float = FS_HZ) -> tuple[np.ndarray, np.ndarray]:
    """Linear interpolation onto a uniform grid. Columns that are all NaN stay NaN."""
    order = np.argsort(t, kind="stable")
    t, values = t[order], values[order]
    t, first = np.unique(t, return_index=True)
    values = values[first]
    grid = np.arange(t[0], t[-1] + 1e-9, 1.0 / fs)
    out = np.full((len(grid), values.shape[1]), np.nan)
    for c in range(values.shape[1]):
        ok = np.isfinite(values[:, c])
        if ok.sum() >= 2:
            out[:, c] = np.interp(grid, t[ok], values[ok, c])
    return grid, out


def lowpass(x: np.ndarray, cutoff_hz: float, fs: float = FS_HZ, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth low-pass. Signals too short to filter are returned unchanged."""
    sos = butter(order, cutoff_hz, fs=fs, output="sos")
    if len(x) <= 3 * (2 * len(sos) + 1):
        return x.copy()
    return sosfiltfilt(sos, x, axis=0)


def find_still_window(acc: np.ndarray, fs: float = FS_HZ, window_s: float = GRAVITY_WINDOW_S,
                      search_s: float = 2.0, still_std: float = 0.05) -> tuple[int, float]:
    """Start index and |a| standard deviation of the window used to estimate gravity.

    The first window (in 0.1 s steps, within the first ~2 s) whose |a| varies
    by less than `still_std` - the phone resting before the lift starts. Not
    simply the stillest window: during a long constant-acceleration phase |a|
    is just as steady, and taking that as "rest" would hide the ride. If no
    window is still enough (the tap on Start kept shaking the phone), the
    stillest one is returned and the caller decides.
    """
    n = max(1, int(window_s * fs))
    mag = np.linalg.norm(acc, axis=1)
    last_start = max(0, min(int(search_s * fs), len(mag) - n))
    best_i, best_sd = 0, math.inf
    for i in range(0, last_start + 1, max(1, int(0.1 * fs))):
        sd = float(np.std(mag[i:i + n]))
        if sd < still_std:
            return i, sd
        if sd < best_sd:
            best_i, best_sd = i, sd
    return best_i, best_sd


def decompose(acc: np.ndarray, gravity: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vertical acceleration (positive = upward) and two horizontal components.

    Sign-invariant: if the phone reports gravity negative, the projection on
    the measured gravity direction still grows when the car accelerates up.
    """
    g = float(np.linalg.norm(gravity))
    u = gravity / g
    proj = acc @ u
    vertical = proj - g
    horiz = acc - np.outer(proj, u)
    ref = np.array([1.0, 0.0, 0.0]) if abs(u[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(u, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(u, e1)
    return vertical, horiz @ e1, horiz @ e2


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True values."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(int)))
    return list(zip(edges[::2], edges[1::2]))


def detect_phases(vert: np.ndarray, limits: Limits = Limits()) -> Phases | None:
    """Find the start and stop pulses in filtered vertical acceleration.

    The start pulse is the first excursion beyond the threshold; the stop
    pulse is the largest excursion of the opposite sign after it. Their
    order gives the direction: an upward ride starts with positive
    acceleration (felt heavier), a downward ride with negative.
    """
    peak = float(np.max(np.abs(vert))) if len(vert) else 0.0
    if peak < limits.min_pulse_accel:
        return None
    th = max(limits.min_pulse_accel, limits.pulse_fraction * peak)
    first = int(np.flatnonzero(np.abs(vert) > th)[0])
    s = 1.0 if vert[first] > 0 else -1.0

    start_runs = [r for r in _runs(vert * s > th) if r[0] <= first < r[1] or r[0] >= first]
    if not start_runs:
        return None
    s0, s1 = start_runs[0]
    stop_runs = [r for r in _runs(vert * -s > th) if r[0] >= s1]
    if not stop_runs:
        return None
    p0, p1 = max(stop_runs, key=lambda r: float(np.sum(np.abs(vert[r[0]:r[1]]))))

    def extend(i0: int, i1: int, sign: float) -> tuple[int, int]:
        while i0 > 0 and vert[i0 - 1] * sign > limits.pulse_edge:
            i0 -= 1
        while i1 < len(vert) and vert[i1] * sign > limits.pulse_edge:
            i1 += 1
        return i0, i1

    s0, s1 = extend(s0, s1, s)
    p0, p1 = extend(p0, p1, -s)
    if p0 < s1:
        p0 = s1
    return Phases(int(s0), int(s1), int(p0), int(p1), "up" if s > 0 else "down")


def integrate_speed(vert: np.ndarray, begin: int, end: int, fs: float = FS_HZ) -> np.ndarray:
    """Speed between begin and end, forcing zero speed at both ends (linear drift removal)."""
    seg = vert[begin:end]
    v = cumulative_trapezoid(seg, dx=1.0 / fs, initial=0.0)
    return v - np.linspace(0.0, v[-1], len(v))


def settling_time(vert: np.ndarray, stop_end: int, limits: Limits = Limits(), fs: float = FS_HZ) -> float:
    """Seconds after the stop pulse until the vertical RMS envelope stays below the threshold."""
    seg = vert[stop_end:stop_end + int(limits.settle_search_s * fs)]
    if len(seg) == 0:
        return 0.0
    w = max(1, int(limits.settle_window_s * fs))
    env = np.sqrt(np.convolve(seg ** 2, np.ones(w) / w, mode="same"))
    above = np.flatnonzero(env >= limits.settle_threshold)
    return 0.0 if len(above) == 0 else float((above[-1] + 1) / fs)


def vibration_features(vert: np.ndarray, hx: np.ndarray, hy: np.ndarray,
                       fs: float = FS_HZ) -> dict[str, float]:
    """RMS, peak-to-peak and dominant frequency during the cruise phase."""
    v = vert - vert.mean()
    h = np.stack([hx - hx.mean(), hy - hy.mean()], axis=1)
    # principal horizontal axis, so peak-to-peak does not depend on phone yaw
    w, vecs = np.linalg.eigh(np.cov(h.T))
    main = h @ vecs[:, int(np.argmax(w))]
    spectrum = np.abs(np.fft.rfft(main * np.hanning(len(main))))
    freqs = np.fft.rfftfreq(len(main), 1.0 / fs)
    band = (freqs >= 0.5) & (freqs <= ACCEL_CUTOFF_HZ)
    dominant = float(freqs[band][np.argmax(spectrum[band])]) if band.any() else float("nan")
    return {
        "vib_vertical_rms": float(np.sqrt(np.mean(v ** 2))),
        "vib_horizontal_rms": float(np.sqrt(np.mean(np.sum(h ** 2, axis=1)))),
        "vib_peak_to_peak_horizontal": float(np.ptp(main)),
        "dominant_vib_freq_hz": dominant,
    }


def jerk_signal(vert_raw: np.ndarray, fs: float = FS_HZ) -> np.ndarray:
    """Jerk (m/s^3): slope of the 5 Hz-filtered vertical acceleration."""
    smooth = lowpass(vert_raw, JERK_CUTOFF_HZ, fs)
    window = int(JERK_SLOPE_WINDOW_S * fs) | 1  # odd
    if len(smooth) <= window:
        return np.gradient(smooth) * fs
    return savgol_filter(smooth, window, polyorder=2, deriv=1, delta=1.0 / fs)


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    c = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# ----------------------------------------------------------------- driver


def analyse_ride(
    samples: list[list[float | None]],
    floor_height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    limits: Limits = Limits(),
) -> RideAnalysis:
    """Validate one uploaded ride and extract its features."""
    if len(samples) < 10:
        return RideAnalysis(False, "too_short")
    t_raw, acc_raw, rot_raw = parse_samples(samples)
    duration = float(t_raw.max() - t_raw.min())
    if duration < limits.min_duration_s:
        return RideAnalysis(False, "too_short")

    t, uni = resample_uniform(t_raw, np.hstack([acc_raw, rot_raw]))
    acc, rot = uni[:, :3], uni[:, 3:]
    fs = FS_HZ

    i0, sd = find_still_window(acc, fs)
    n_win = int(GRAVITY_WINDOW_S * fs)
    if sd > limits.max_start_std:
        return RideAnalysis(False, "not_still_at_start")
    gravity = acc[i0:i0 + n_win].mean(axis=0)

    vert_raw, hx_raw, hy_raw = decompose(acc, gravity)
    vert = lowpass(vert_raw, ACCEL_CUTOFF_HZ, fs)
    hx, hy = lowpass(hx_raw, ACCEL_CUTOFF_HZ, fs), lowpass(hy_raw, ACCEL_CUTOFF_HZ, fs)
    horiz_mag = np.hypot(hx, hy)
    base = RideAnalysis(False, None, t=t - t[0], vertical=vert, horizontal=horiz_mag)

    # --- validity: phone handling
    if np.isfinite(rot).all(axis=1).any():
        rot_mag = np.linalg.norm(np.nan_to_num(rot), axis=1)
        if float(np.max(rot_mag)) > limits.max_rotation_dps:
            base.reject_reason = "phone_moved"
            return base
    tail = acc[max(0, len(acc) - int(1.0 * fs)):max(1, len(acc) - int(0.3 * fs))]
    if len(tail) and _angle_deg(gravity, tail.mean(axis=0)) > limits.max_tilt_change_deg:
        base.reject_reason = "phone_moved"
        return base
    h_rms = float(np.sqrt(np.mean((hx - hx.mean()) ** 2 + (hy - hy.mean()) ** 2)))
    if h_rms > limits.max_horizontal_rms:
        base.reject_reason = "walking_or_handled"
        return base

    # --- phases
    phases = detect_phases(vert, limits)
    if phases is None:
        base.reject_reason = "no_lift_motion"
        return base
    base.phases = phases

    pad = int(0.3 * fs)
    m0, m1 = max(0, phases.start_begin - pad), min(len(vert), phases.stop_end + pad)
    speed = integrate_speed(vert, m0, m1, fs)
    max_speed = float(np.max(np.abs(speed)))
    distance = float(abs(np.trapezoid(speed, dx=1.0 / fs)))
    if distance < limits.min_distance_m or not (0.2 <= max_speed <= limits.max_speed):
        base.reject_reason = "implausible_motion"
        return base

    jerk = jerk_signal(vert_raw, fs)
    jp = int(0.2 * fs)

    def window_max(x: np.ndarray, a: int, b: int) -> float:
        return float(np.max(np.abs(x[max(0, a - jp):min(len(x), b + jp)])))

    features: dict[str, float | int | str | None] = {
        "direction": phases.direction,
        "duration_s": round(duration, 2),
        "peak_accel_start": float(np.max(np.abs(vert[phases.start_begin:phases.start_end]))),
        "peak_accel_stop": float(np.max(np.abs(vert[phases.stop_begin:phases.stop_end]))),
        "peak_jerk_start": window_max(jerk, phases.start_begin, phases.start_end),
        "peak_jerk_stop": window_max(jerk, phases.stop_begin, phases.stop_end),
        "max_speed": max_speed,
        "travel_distance_m": distance,
        "floors_travelled_est": max(1, int(round(distance / floor_height_m))),
        "stop_settling_s": settling_time(vert, phases.stop_end, limits, fs),
        "vib_vertical_rms": None,
        "vib_horizontal_rms": None,
        "vib_peak_to_peak_horizontal": None,
        "dominant_vib_freq_hz": None,
    }
    trim = int(0.2 * fs)
    c0, c1 = phases.start_end + trim, phases.stop_begin - trim
    if c1 - c0 >= int(limits.min_cruise_s * fs):
        features.update(vibration_features(vert[c0:c1], hx[c0:c1], hy[c0:c1], fs))

    for k, v in features.items():
        if isinstance(v, float):
            features[k] = None if not math.isfinite(v) else round(v, 4)
    base.valid, base.features = True, features
    return base
