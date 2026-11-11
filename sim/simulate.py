"""Demo data for LiftPulse, through the real ride API.

    python -m sim.simulate seed --days 45      # build the demo dataset
    python -m sim.simulate live --url http://localhost:8000 --token demo
                                               # a new ride every few seconds

Every ride is a synthetic raw sensor trace posted to POST /api/rides, so the
whole pipeline (validation, features, scoring, alerts) runs as it does for a
real phone. `seed` runs the app in-process by default (no server needed);
pass --url to seed a running server instead.

The demo building has four lifts. Lifts A-C stay healthy; Lift D starts
wearing out after day 15 (guide rollers, brake tuning, levelling), so it
turns yellow and then red with alerts.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import numpy as np

from .ridegen import LiftProfile, PhoneProfile, generate_ride

BUILDING = {"name": "Riverside House", "address": "12 River Road"}


@dataclass(frozen=True)
class DemoLift:
    id: str
    name: str
    floors: int
    profile: LiftProfile
    degrades_from_day: float | None = None  # wear starts here and reaches 1.0 at the end


DEMO_LIFTS = (
    DemoLift("riverside-a", "Lift A", 10, LiftProfile(max_speed=1.6, accel=0.95, jerk=1.3, vib_freq_hz=4.5)),
    DemoLift("riverside-b", "Lift B", 8, LiftProfile(max_speed=1.0, accel=0.8, jerk=1.0, vib_freq_hz=5.5,
                                                     vib_horizontal=0.025)),
    DemoLift("riverside-c", "Lift C", 12, LiftProfile(max_speed=1.6, accel=0.9, jerk=1.2, vib_freq_hz=6.5,
                                                      vib_horizontal=0.035)),
    DemoLift("riverside-d", "Lift D", 8, LiftProfile(max_speed=1.2, accel=0.9, jerk=1.2, vib_freq_hz=5.0),
             degrades_from_day=15),
)
DISTURBANCES = ("handled", "walking", "too_short", "no_motion")
INVALID_SHARE = 0.05


def worn(profile: LiftProfile, wear: float) -> LiftProfile:
    """The lift's profile at a wear level from 0 (as new) to 1 (badly worn)."""
    wear = min(max(wear, 0.0), 1.0)
    return replace(
        profile,
        vib_horizontal=profile.vib_horizontal * (1 + 1.2 * wear),   # guide rollers wearing
        stop_jerk_factor=1 + 0.4 * wear,                            # brake / drive tuning drifting
        bounce_amp=profile.bounce_amp + 0.08 * wear,                # levelling getting worse
        bounce_decay_s=profile.bounce_decay_s + 0.35 * wear,
    )


def ride_variation(profile: LiftProfile, rng: random.Random) -> LiftProfile:
    """Ride-to-ride variation of one lift (car load, temperature, rope stretch)."""
    return replace(
        profile,
        accel=profile.accel * rng.gauss(1.0, 0.03),
        jerk=profile.jerk * rng.gauss(1.0, 0.06),
        stop_jerk_factor=profile.stop_jerk_factor * rng.gauss(1.0, 0.04),
        vib_horizontal=profile.vib_horizontal * rng.lognormvariate(0.0, 0.18),
        vib_vertical=profile.vib_vertical * rng.lognormvariate(0.0, 0.18),
        bounce_amp=profile.bounce_amp * rng.lognormvariate(0.0, 0.2),
    )


def wear_at(lift: DemoLift, day: float, total_days: float) -> float:
    if lift.degrades_from_day is None or day < lift.degrades_from_day:
        return 0.0
    return (day - lift.degrades_from_day) / max(total_days - lift.degrades_from_day, 1.0)


def random_phone(rng: random.Random) -> PhoneProfile:
    return PhoneProfile(
        rate_hz=rng.choice((60.0, 60.0, 100.0)),
        flat_on_floor=rng.random() < 0.6,
        inverted_sign=rng.random() < 0.3,
        has_rotation=rng.random() < 0.9,
        noise=rng.uniform(0.01, 0.02),
    )


def random_ride(lift: DemoLift, profile: LiftProfile, rng: random.Random, nprng: np.random.Generator,
                allow_invalid: bool = True) -> list[list[float | None]]:
    """One ride between two random floors of the lift."""
    a, b = rng.sample(range(lift.floors), 2)
    profile = ride_variation(profile, rng)
    disturbance = rng.choice(DISTURBANCES) if allow_invalid and rng.random() < INVALID_SHARE else None
    samples, _ = generate_ride(abs(a - b), "up" if b > a else "down", lift=profile, phone=random_phone(rng),
                               rng=nprng, pre_still_s=rng.uniform(1.0, 2.0), disturbance=disturbance)
    return samples


# ------------------------------------------------------------- transports


class InProcess:
    """Calls the API of an app instance in this process (same code path as HTTP)."""

    def __init__(self, reset: bool):
        from fastapi.testclient import TestClient

        from app.config import load_settings
        from app.db import Base
        from app.main import create_app

        app = create_app(load_settings())
        if reset:
            Base.metadata.drop_all(app.state.engine)
        self.client = TestClient(app)
        self.client.__enter__()
        self.token = app.state.settings.admin_token

    def post(self, path: str, body: dict, admin: bool = False) -> dict:
        r = self.client.post(path, json=body, headers={"X-Admin-Token": self.token} if admin else {})
        r.raise_for_status()
        return r.json()

    def get(self, path: str) -> list | dict:
        r = self.client.get(path, headers={"X-Admin-Token": self.token})
        r.raise_for_status()
        return r.json()


class Http:
    """Calls a running LiftPulse server."""

    def __init__(self, url: str, token: str):
        import requests

        self.url, self.token, self.session = url.rstrip("/"), token, requests.Session()

    def post(self, path: str, body: dict, admin: bool = False) -> dict:
        r = self.session.post(self.url + path, json=body, timeout=30,
                              headers={"X-Admin-Token": self.token} if admin else {})
        r.raise_for_status()
        return r.json()

    def get(self, path: str) -> list | dict:
        r = self.session.get(self.url + path, headers={"X-Admin-Token": self.token}, timeout=30)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------- commands


def ensure_demo_lifts(api) -> None:
    existing = {l["id"] for l in api.get("/api/lifts")}
    if all(l.id in existing for l in DEMO_LIFTS):
        return
    if existing & {l.id for l in DEMO_LIFTS}:
        sys.exit("Some demo lifts exist already. Use --reset (in-process) or an empty database.")
    building = api.post("/api/admin/buildings", BUILDING, admin=True)
    for l in DEMO_LIFTS:
        api.post("/api/admin/lifts", {"id": l.id, "name": l.name, "building_id": building["id"],
                                      "floors": l.floors}, admin=True)


def seed(api, days: int, rides_per_day: int, seed_value: int) -> None:
    rng, nprng = random.Random(seed_value), np.random.default_rng(seed_value)
    existing = {l["id"]: l for l in api.get("/api/lifts")}
    if any(existing.get(l.id, {}).get("last_ride_at") for l in DEMO_LIFTS):
        sys.exit("The demo lifts already have rides. Use --reset to start over.")
    ensure_demo_lifts(api)

    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=days)
    schedule = []
    for d in range(days):
        day0 = start + timedelta(days=d)
        for lift in DEMO_LIFTS:
            for _ in range(max(1, int(rng.gauss(rides_per_day, rides_per_day * 0.15)))):
                t = day0 + timedelta(hours=rng.uniform(6.0, 23.0))
                if t < end:
                    schedule.append((t, lift))
    schedule.sort(key=lambda x: x[0])

    counts = {l.id: [0, 0] for l in DEMO_LIFTS}
    t0 = time.time()
    for i, (t, lift) in enumerate(schedule, 1):
        day = (t - start).total_seconds() / 86400.0
        profile = worn(lift.profile, wear_at(lift, day, days))
        body = {"lift_id": lift.id, "device_id": f"sim-{rng.randrange(400)}",
                "samples": random_ride(lift, profile, rng, nprng), "started_at": t.isoformat()}
        res = api.post("/api/rides", body, admin=True)
        counts[lift.id][0 if res["valid"] else 1] += 1
        if i % 200 == 0 or i == len(schedule):
            print(f"  {i}/{len(schedule)} rides ({time.time() - t0:.0f} s)", flush=True)

    api.post("/api/reports", {"lift_id": "riverside-d", "category": "noise",
                              "comment": "Rattling noise between floors 3 and 5"})
    print("\nDone:")
    for row in api.get("/api/lifts"):
        valid, invalid = counts.get(row["id"], [0, 0])
        score = "-" if row["health_score"] is None else f"{row['health_score']:.0f}"
        print(f"  {row['name']}: {row['status']:6} health {score:>3}  ({valid} valid, {invalid} rejected rides)")
    alerts = api.get("/api/alerts")
    print(f"\nOpen alerts: {len(alerts)}")
    for a in alerts:
        print(f"  [{a['severity']}] {a['message']}")


def live(api, interval: float, seed_value: int | None) -> None:
    rng = random.Random(seed_value)
    nprng = np.random.default_rng(seed_value)
    print(f"Posting a ride every {interval:g} s. Ctrl-C to stop.")
    try:
        while True:
            lift = rng.choice(DEMO_LIFTS)
            profile = worn(lift.profile, 1.0 if lift.degrades_from_day is not None else 0.0)
            body = {"lift_id": lift.id, "device_id": f"live-{rng.randrange(400)}",
                    "samples": random_ride(lift, profile, rng, nprng, allow_invalid=rng.random() < 0.5)}
            res = api.post("/api/rides", body)
            f, st = res["features"], res["lift_status"]
            n = f.get("floors_travelled_est")
            ride = (f"{f['direction']} {n} floor{'' if n == 1 else 's'}, score "
                    f"{'-' if res['ride_score'] is None else round(res['ride_score'])}"
                    if res["valid"] else f"rejected ({res['reject_reason']})")
            print(f"{datetime.now():%H:%M:%S}  {lift.name}: {ride}  ->  {st['status']}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--live":  # also accept "python -m sim.simulate --live ..."
        argv = ["live", *argv[1:]]
    p = argparse.ArgumentParser(prog="python -m sim.simulate", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="create the demo dataset")
    s.add_argument("--days", type=int, default=45)
    s.add_argument("--rides-per-day", type=int, default=15, help="per lift")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--url", help="seed a running server instead of an in-process app")
    s.add_argument("--token", default=None, help="admin token for --url (default: LIFTPULSE_ADMIN_TOKEN)")
    s.add_argument("--reset", action="store_true", help="in-process only: wipe the database first")

    lv = sub.add_parser("live", help="post a new ride every few seconds to a running server")
    lv.add_argument("--url", default="http://localhost:8000")
    lv.add_argument("--token", default=None, help="admin token (default: LIFTPULSE_ADMIN_TOKEN)")
    lv.add_argument("--interval", type=float, default=4.0)
    lv.add_argument("--seed", type=int, default=None)
    args = p.parse_args(argv)

    import os
    token = args.token or os.getenv("LIFTPULSE_ADMIN_TOKEN", "change-me")
    if args.cmd == "seed":
        if args.url and args.reset:
            sys.exit("--reset only works in-process; empty the server's database instead.")
        api = Http(args.url, token) if args.url else InProcess(reset=args.reset)
        seed(api, args.days, args.rides_per_day, args.seed)
    else:
        live(Http(args.url, token), args.interval, args.seed)


if __name__ == "__main__":
    main()
