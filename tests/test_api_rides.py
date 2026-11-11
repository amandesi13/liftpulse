"""Public API used by the resident page: status, ride upload, reports."""

from __future__ import annotations

import numpy as np

from sim.ridegen import generate_ride

from .conftest import ADMIN


def _flat_samples(seconds: float = 5.0, hz: float = 60.0) -> list[list[float]]:
    n = int(seconds * hz)
    return [[i * 1000.0 / hz, 0.0, 0.0, 9.81, 0.0, 0.0, 0.0] for i in range(n)]


def test_ride_page_is_served(client, lift):
    r = client.get("/r/test-a")
    assert r.status_code == 200
    assert "Start ride" in r.text


def test_status_is_public(client, lift):
    r = client.get("/api/lifts/test-a/status")
    assert r.status_code == 200
    body = r.json()
    assert body["lift_name"] == "Lift A"
    assert body["building_name"] == "Test House"
    assert body["status"] == "grey"
    assert client.get("/api/lifts/missing/status").status_code == 404


def test_upload_is_stored(client, lift):
    r = client.post("/api/rides", json={"lift_id": "test-a", "device_id": "dev-1", "samples": _flat_samples()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ride_id"] >= 1
    assert body["lift_status"]["lift_id"] == "test-a"


def test_upload_validation(client, lift):
    bad_row = {"lift_id": "test-a", "samples": [[0, 1, 2]]}
    assert client.post("/api/rides", json=bad_row).status_code == 422
    null_accel = {"lift_id": "test-a", "samples": [[0, None, 0, 9.8]]}
    assert client.post("/api/rides", json=null_accel).status_code == 422
    unknown = {"lift_id": "nope", "samples": _flat_samples()}
    assert client.post("/api/rides", json=unknown).status_code == 404


def test_rotation_may_be_null(client, lift):
    samples = [[t, 0.0, 0.0, 9.81, None, None, None] for t in range(0, 5000, 16)]
    assert client.post("/api/rides", json={"lift_id": "test-a", "samples": samples}).status_code == 200


def test_report(client, lift):
    r = client.post("/api/reports", json={"lift_id": "test-a", "category": "noise", "comment": "squeaks"})
    assert r.status_code == 201
    assert client.post("/api/reports", json={"lift_id": "test-a", "category": "fire"}).status_code == 422


def test_valid_ride_is_analysed(client, lift):
    samples, truth = generate_ride(5, "down", rng=np.random.default_rng(4))
    body = client.post("/api/rides", json={"lift_id": "test-a", "samples": samples}).json()
    assert body["valid"] is True
    assert body["reject_reason"] is None
    assert body["features"]["direction"] == "down"
    assert body["features"]["floors_travelled_est"] == 5


def test_invalid_ride_gets_a_readable_reason(client, lift):
    samples, _ = generate_ride(5, disturbance="handled", rng=np.random.default_rng(4))
    body = client.post("/api/rides", json={"lift_id": "test-a", "samples": samples}).json()
    assert body["valid"] is False
    assert body["reject_reason"] == "phone_moved"
    assert "moved" in body["reject_message"]
