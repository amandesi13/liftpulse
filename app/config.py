"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ADMIN_TOKEN = "change-me"


@dataclass(frozen=True)
class Settings:
    """Settings for one app instance.

    Attributes:
        database_url: SQLAlchemy URL. SQLite by default; any SQLAlchemy URL
            (e.g. PostgreSQL) works without code changes.
        admin_token: Shared secret for the dashboard and admin API.
        public_url: Base URL printed in QR codes, e.g. https://lifts.example.com.
            When unset, the URL of the incoming request is used.
        store_raw_samples: Keep the raw sensor samples of every ride (debugging).
        baseline_rides: Valid rides per lift and direction that form the baseline.
    """

    database_url: str = "sqlite:///./data/liftpulse.db"
    admin_token: str = DEFAULT_ADMIN_TOKEN
    public_url: str | None = None
    store_raw_samples: bool = True
    baseline_rides: int = 20


def load_settings() -> Settings:
    """Build settings from LIFTPULSE_* environment variables."""
    return Settings(
        database_url=os.getenv("LIFTPULSE_DATABASE_URL", Settings.database_url),
        admin_token=os.getenv("LIFTPULSE_ADMIN_TOKEN", DEFAULT_ADMIN_TOKEN),
        public_url=(os.getenv("LIFTPULSE_PUBLIC_URL") or "").rstrip("/") or None,
        store_raw_samples=os.getenv("LIFTPULSE_STORE_RAW", "1") != "0",
        baseline_rides=int(os.getenv("LIFTPULSE_BASELINE_RIDES", str(Settings.baseline_rides))),
    )
