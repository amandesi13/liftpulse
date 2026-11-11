# Decisions

Defaults chosen while building the MVP, and why. Each can be revisited.

## Architecture

- **Pure core, thin I/O shell.** `app/signal.py` and `app/scoring.py` are pure
  functions (NumPy/SciPy in, numbers out) with no database or HTTP code, so they
  are tested in isolation. `app/pipeline.py` connects them to the database; it
  is not in the original layout, but keeps the routes in `main.py` short.
- **App factory.** `create_app(settings)` builds an app with its own engine, so
  tests run against a throwaway SQLite file and nothing global leaks between
  tests. `app.main:app` is the default instance for uvicorn.
- **Naive UTC timestamps** in the database; the API returns ISO strings with `Z`.
- **SQLite with WAL mode** for the MVP. Every query goes through SQLAlchemy, so
  `LIFTPULSE_DATABASE_URL=postgresql+psycopg://...` is the only change needed
  for PostgreSQL (plus the driver).
- **Admin token** from `LIFTPULSE_ADMIN_TOKEN`, accepted as `X-Admin-Token`
  header or `?token=` query parameter (the latter so QR images and dashboard
  links work in plain `<img>`/`<a>` tags). A warning is logged when the default
  `change-me` is in use.

## Signal processing (`app/signal.py`)

- **Server-side for the MVP.** Raw samples are uploaded and analysed on the
  server, so the algorithm can change without touching phones and old rides
  can be re-analysed. The functions are pure NumPy, so they could later run
  on-device (e.g. Pyodide/WASM, or a port) to upload features only.
- **Gravity from the stillest 0.5 s within the first 2 s**, not the first
  0.5 s: the tap on "Start" shakes the phone. If even the stillest window
  varies by more than 0.25 m/s², the ride is rejected (`not_still_at_start`).
- **Orientation- and sign-independent.** Vertical acceleration is the
  projection onto the measured gravity direction minus |g|. Old iOS reports
  `accelerationIncludingGravity` with the opposite sign; the projection is
  the same either way, so no browser detection is needed.
- **Jerk as a local slope.** Acceleration is low-passed at 5 Hz and jerk is
  its Savitzky-Golay slope over 0.31 s. A plain derivative turned phone noise
  (σ ≈ 0.015 m/s²) into jerk peaks ~20 % above the true value; the slope
  estimate is within ±10 % on 120 random synthetic rides. Real jerk ramps
  last ~0.5–1 s, so the window does not flatten them.
- **Ride phases:** the start pulse is the first excursion beyond
  max(0.15 m/s², 30 % of the strongest pulse); the stop pulse is the largest
  opposite-sign excursion after it. The sign order gives the direction.
- **Speed** is integrated over the motion window with linear drift removal
  (zero speed at both ends); distance is the integral of speed.
- **Horizontal vibration** uses the principal horizontal axis (PCA), so
  peak-to-peak and the dominant frequency do not depend on how the phone
  is rotated on the floor.
- **Stop settling** is the time after the stop pulse until the 0.3 s RMS
  envelope of vertical acceleration stays below 0.04 m/s². Healthy lifts
  often measure 0 s; scoring therefore uses a minimum spread (see scoring).
- **Validity thresholds** (in `Limits`): rotation > 60 °/s or a > 20 °
  change in phone tilt → `phone_moved`; horizontal RMS > 0.8 m/s² →
  `walking_or_handled`; < 3 s → `too_short`; no start/stop pulse pair →
  `no_lift_motion`; < 1 m travelled or implausible speed →
  `implausible_motion`. They are set from synthetic data and need checking
  against real phones.
- **Synthetic rides** (`sim/ridegen.py`) come first because the tests need
  traces with known answers: jerk-limited S-curve motion, speed-dependent
  car vibration, post-stop bounce, and a phone model with random orientation,
  noise, bias, irregular timing and both sign conventions.
