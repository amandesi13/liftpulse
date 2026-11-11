"""Shared fixtures: an app instance backed by a throwaway SQLite file."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from sim.ridegen import generate_ride

ADMIN_TOKEN = "test-token"
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}
BASELINE = 6


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


@pytest.fixture()
def small(tmp_path):
    """App with a 6-ride baseline so the tests stay fast, and one lift "l2"."""
    settings = Settings(database_url=f"sqlite:///{tmp_path / 's.db'}", admin_token=ADMIN_TOKEN,
                        baseline_rides=BASELINE)
    with TestClient(create_app(settings)) as c:
        b = c.post("/api/admin/buildings", json={"name": "B"}, headers=ADMIN).json()
        c.post("/api/admin/lifts", json={"name": "Lift 2", "building_id": b["id"], "id": "l2"}, headers=ADMIN)
        yield c


def post_rides(client, n, start, lift=None, seed=0, hours=4.0, lift_id="l2"):
    """Upload n synthetic rides, alternating direction, `hours` apart from `start`."""
    rng = np.random.default_rng(seed)
    last = None
    for i in range(n):
        samples, _ = generate_ride(int(rng.integers(2, 7)), "up" if i % 2 else "down", lift=lift, rng=rng)
        t = start + timedelta(hours=hours * i)
        last = client.post("/api/rides", headers=ADMIN, json={
            "lift_id": lift_id, "samples": samples, "started_at": t.isoformat()}).json()
    return last
