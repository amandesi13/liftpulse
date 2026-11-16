"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ADMIN_TOKEN = "change-me"
DEFAULT_DATABASE_URL = "sqlite:///./data/liftpulse.db"


@dataclass(frozen=True)
class Settings:
    """Settings for one app instance.

    Attributes:
        database_url: SQLAlchemy URL. SQLite by default; PostgreSQL (e.g. Neon)
            works without code changes.
        admin_token: Shared secret for the dashboard and admin API.
        public_url: Base URL printed in QR codes, e.g. https://lifts.example.com.
            When unset, the URL of the incoming request is used.
        store_raw_samples: Keep the raw sensor samples of every ride (debugging).
        baseline_rides: Valid rides per lift and direction that form the baseline.
    """

    database_url: str = DEFAULT_DATABASE_URL
    admin_token: str = DEFAULT_ADMIN_TOKEN
    public_url: str | None = None
    store_raw_samples: bool = True
    baseline_rides: int = 20


def normalize_database_url(url: str) -> str:
    """Make provider URLs usable by SQLAlchemy.

    Neon, Render and Heroku hand out postgres:// or postgresql:// URLs, which
    SQLAlchemy maps to psycopg2. LiftPulse ships psycopg 3, so select it.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def load_env_files(*names: str) -> None:
    """Load KEY=value files (e.g. Neon's .env.local) without overriding real env vars."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a dependency
        return
    for name in names:
        load_dotenv(name, override=False)


def load_settings() -> Settings:
    """Build settings from the environment (and .env.local / .env, if present).

    Database: LIFTPULSE_DATABASE_URL, else DATABASE_URL (what Neon's
    `neon init` and most hosts set), else a local SQLite file.
    Public URL: LIFTPULSE_PUBLIC_URL, else RENDER_EXTERNAL_URL (set by Render).
    """
    load_env_files(".env.local", ".env")
    database_url = os.getenv("LIFTPULSE_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    public_url = os.getenv("LIFTPULSE_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
    return Settings(
        database_url=normalize_database_url(database_url),
        admin_token=os.getenv("LIFTPULSE_ADMIN_TOKEN", DEFAULT_ADMIN_TOKEN),
        public_url=public_url.rstrip("/") or None,
        store_raw_samples=os.getenv("LIFTPULSE_STORE_RAW", "1") != "0",
        baseline_rides=int(os.getenv("LIFTPULSE_BASELINE_RIDES", str(Settings.baseline_rides))),
    )
