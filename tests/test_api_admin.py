"""Admin API: buildings, lifts, QR codes and the admin token."""

from __future__ import annotations

from .conftest import ADMIN


def test_admin_routes_need_token(client):
    assert client.get("/api/admin/buildings").status_code == 401
    assert client.get("/api/admin/buildings", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/api/admin/buildings", headers=ADMIN).status_code == 200
    assert client.get("/api/admin/buildings?token=test-token").status_code == 200


def test_create_lift_returns_ride_url(client, lift):
    assert lift["id"] == "test-a"
    assert lift["ride_url"] == "https://lifts.example.org/r/test-a"


def test_lift_id_is_generated_and_unique(client, lift):
    body = {"name": "Lift A", "building_id": lift["building_id"]}
    first = client.post("/api/admin/lifts", json=body, headers=ADMIN).json()
    second = client.post("/api/admin/lifts", json=body, headers=ADMIN).json()
    assert first["id"] == "test-house-lift-a"
    assert second["id"] == "test-house-lift-a-2"


def test_duplicate_lift_id_rejected(client, lift):
    body = {"name": "Other", "building_id": lift["building_id"], "id": "test-a"}
    assert client.post("/api/admin/lifts", json=body, headers=ADMIN).status_code == 409


def test_unknown_building_rejected(client):
    body = {"name": "Lift", "building_id": 999}
    assert client.post("/api/admin/lifts", json=body, headers=ADMIN).status_code == 404


def test_qr_code_is_png(client, lift):
    r = client.get("/api/admin/lifts/test-a/qr.png", headers=ADMIN)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/api/admin/lifts/nope/qr.png", headers=ADMIN).status_code == 404
