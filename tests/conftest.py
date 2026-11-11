"""Shared fixtures: an app instance backed by a throwaway SQLite file."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_TOKEN = "test-token"
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        admin_token=ADMIN_TOKEN,
        public_url="https://lifts.example.org",
    )
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture()
def lift(client):
    """One building with one 8-floor lift; returns the lift JSON."""
    b = client.post("/api/admin/buildings", json={"name": "Test House", "address": "1 Test St"}, headers=ADMIN)
    assert b.status_code == 201, b.text
    r = client.post(
        "/api/admin/lifts",
        json={"name": "Lift A", "building_id": b.json()["id"], "floors": 8, "id": "test-a"},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    return r.json()
