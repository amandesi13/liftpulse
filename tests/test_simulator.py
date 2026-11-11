"""The demo simulator: wear model, ride realism, and seeding through the API."""

from __future__ import annotations

import random

import numpy as np

from app.signal import analyse_ride
from sim import simulate

from .conftest import ADMIN_TOKEN


class ClientApi:
    """simulate's transport interface over a TestClient."""

    def __init__(self, client):
        self.client = client

    def post(self, path, body, admin=False):
        r = self.client.post(path, json=body, headers={"X-Admin-Token": ADMIN_TOKEN} if admin else {})
        r.raise_for_status()
        return r.json()

    def get(self, path):
        r = self.client.get(path, headers={"X-Admin-Token": ADMIN_TOKEN})
        r.raise_for_status()
        return r.json()


def test_wear_makes_the_lift_rougher():
    base = simulate.DEMO_LIFTS[3].profile
    new, old = simulate.worn(base, 0.0), simulate.worn(base, 1.0)
    assert new == simulate.worn(base, -1.0)
    assert old.vib_horizontal > new.vib_horizontal
    assert old.stop_jerk_factor > new.stop_jerk_factor
    assert old.bounce_amp > new.bounce_amp


def test_wear_timeline():
    healthy, degrading = simulate.DEMO_LIFTS[0], simulate.DEMO_LIFTS[3]
    assert simulate.wear_at(healthy, 44, 45) == 0.0
    assert simulate.wear_at(degrading, 10, 45) == 0.0
    assert simulate.wear_at(degrading, 30, 45) == 0.5
    assert simulate.wear_at(degrading, 45, 45) == 1.0


def test_simulated_rides_are_valid_unless_disturbed():
    rng, nprng = random.Random(0), np.random.default_rng(0)
    results = [analyse_ride(simulate.random_ride(lift, lift.profile, rng, nprng, allow_invalid=False))
               for lift in simulate.DEMO_LIFTS for _ in range(15)]
    assert all(r.valid for r in results), [r.reject_reason for r in results if not r.valid]


def test_seed_runs_through_the_api(client, capsys):
    api = ClientApi(client)
    simulate.seed(api, days=2, rides_per_day=4, seed_value=1)
    lifts = {l["id"]: l for l in api.get("/api/lifts")}
    assert set(lifts) >= {l.id for l in simulate.DEMO_LIFTS}
    assert all(lifts[l.id]["last_ride_at"] for l in simulate.DEMO_LIFTS)
    assert "Done:" in capsys.readouterr().out
