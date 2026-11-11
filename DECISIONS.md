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
