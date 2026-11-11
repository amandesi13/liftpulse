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
- **Gravity from the first still 0.5 s within the first 2 s** (|a| varying
  < 0.05 m/s²), not simply the first 0.5 s (the tap on "Start" shakes the
  phone) and not the stillest window: during a fast lift's constant-
  acceleration phase |a| is just as steady, and using that as "rest" made
  13 of 600 genuine rides look motionless. If no window is still enough
  and even the stillest varies by more than 0.25 m/s², the ride is rejected
  (`not_still_at_start`). After the fix: 0 of 800 genuine rides rejected.
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

## Scoring and alerts (`app/scoring.py`)

- **Weights and thresholds in one place:** `SCORE_WEIGHTS` and the constants
  at the top of `scoring.py`. A ride starts at 100; each feature costs
  `weight × 2 × (z − 2)` points for z between 2 and 8 (z ≤ 2 is normal
  variation and free; values below baseline never cost points). Calibrated
  on synthetic rides: healthy lifts score 96–100, +45 % stop jerk ≈ 64
  (yellow), a badly worn lift ≈ 0 (red).
- **Minimum spread per feature** (`MIN_SPREAD`, and ≥ 3 % of the median).
  Without it, a feature with a near-constant baseline (settling is often
  exactly 0 s) would turn any change into an enormous z-score.
- **Baseline completion rescoring:** a ride gets a score only once its
  direction has 20 valid rides. When the 20th arrives, the 20 baseline
  rides are scored too, so the history has no gap.
- **Trend alerts need significance as well as size.** "+25 % vs baseline"
  alone fired on healthy lifts for features with tiny baselines (+39 % of
  0.05 s settling is noise). A trend now also needs the fitted value to be
  more than 2 robust spreads above baseline. Up and down rides are pooled by
  normalising each by its own direction's baseline.
- **Severity:** warn for a trend > 25 % or 5 of the last 10 rides at z > 3;
  critical only for a trend > 60 %. Tight features like jerk put every
  slightly worse ride above z = 3, so outliers alone stay a warning; an
  earlier "8 outliers = critical" rule made alerts flip critical → warn
  once enough data for a trend fit arrived.
- **One open alert per lift and feature**, updated in place as the trend
  grows; it resolves itself when the condition clears. An alert resolved by
  maintenance is not re-raised for 24 h unless it gets more severe.
- **Resident reports:** with open sensor alerts, a report raises them one
  level and notes it in the message; without them, it creates an info alert
  so the report shows in the feed.
- **Lift health** uses the ride time as "now" while history is being
  backfilled, so a seeded dataset gets alerts dated when they would have
  appeared.

## Dashboard (`app/views.py`, `static/`)

- **Read models in `views.py`**, so the dashboard's queries and the shapes
  the pages need are in one module and the routes stay one-liners.
- **Feature charts in real units** (m/s², m/s³, s) with the baseline band
  (median ± 2 robust spreads, pooled over both directions) rather than "%
  of baseline": percentages are meaningless for features whose baseline is
  near zero (settling time).
- **Trend arrow** compares the health score now with 7 days ago (±5 points).
- **Health history** is recomputed per day from ride scores on request. Fine
  for tens of lifts; a daily snapshot table would be the next step at scale.
- **No build step, no framework:** vanilla JS with a 10-line `el()` helper;
  Chart.js 4.4.1 from jsDelivr for the lift page. The admin token is taken
  from `?token=` once (then removed from the URL) or asked for, and kept in
  sessionStorage with an in-memory fallback.
- **Auto-refresh every 5 s** on the dashboard, so simulator live mode shows
  up during a pitch without touching the laptop.

## Simulator and packaging

- **Seeding goes through `POST /api/rides`.** In-process by default (the
  app's own TestClient, so no server is needed and the code path is the HTTP
  one); `--url` seeds a running server. `started_at` backfill is honoured only
  with the admin token.
- **Ride-to-ride variation** (car load: ±3 % acceleration, ±6 % jerk, ±18 %
  vibration) makes baselines as wide as a real lift's. Without it the
  synthetic lift was so consistent that small wear dropped the score to 0
  within days instead of degrading over weeks.
- **Wear model for Lift D** (from day 15, linear to day 45): horizontal
  vibration ×2.2, stop jerk ×1.4, more and longer post-stop bounce. Tuned so
  the demo shows warnings around day 29, yellow for about a week, then red
  at about 40 rather than 0.
- **Docker:** `python:3.12-slim`, non-root user, data in a `/data` volume,
  uvicorn with `--proxy-headers` so QR codes use the public https URL behind
  a tunnel or ingress. `httpx` is a runtime dependency because in-process
  seeding uses the TestClient.
- **Kubernetes:** one replica with `Recreate` strategy, since SQLite allows a
  single writer; scale out by switching to PostgreSQL.
- **CI (GitHub Actions):** tests on 3.11 and 3.12, then builds the image,
  seeds a short history inside it and checks the API and ride page. The
  Docker files were written on a machine without Docker; CI is where they
  are verified.
