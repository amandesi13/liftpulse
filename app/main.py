"""FastAPI application: API routes and static pages."""

from __future__ import annotations

import io
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, pipeline
from .config import DEFAULT_ADMIN_TOKEN, Settings, load_settings
from .db import Base, get_db, make_engine, make_session_factory
from .schemas import BuildingIn, LiftIn, LiftStatusOut, ReportIn, RideIn, RideOut

log = logging.getLogger("liftpulse")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an app instance with its own database engine and settings."""
    settings = settings or load_settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        if settings.admin_token == DEFAULT_ADMIN_TOKEN:
            log.warning("LIFTPULSE_ADMIN_TOKEN is not set; using the default token %r", DEFAULT_ADMIN_TOKEN)
        yield
        engine.dispose()

    app = FastAPI(title="LiftPulse", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    _register_routes(app)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


# ---------------------------------------------------------------- helpers


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Dependency: the shared admin token, as X-Admin-Token header or ?token=."""
    expected = request.app.state.settings.admin_token
    given = x_admin_token or token or ""
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="admin token required")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:24] or "lift"


def _unique_lift_id(db: Session, base: str) -> str:
    candidate, n = base, 2
    while db.get(models.Lift, candidate) is not None:
        candidate = f"{base[:28]}-{n}"
        n += 1
    return candidate


def _public_base_url(request: Request) -> str:
    configured = request.app.state.settings.public_url
    return configured or str(request.base_url).rstrip("/")


def _is_admin(request: Request, header: str | None) -> bool:
    expected = request.app.state.settings.admin_token
    return header is not None and secrets.compare_digest(header.encode(), expected.encode())


def _get_lift(db: Session, lift_id: str) -> models.Lift:
    lift = db.get(models.Lift, lift_id)
    if lift is None:
        raise HTTPException(404, "lift not found")
    return lift


def iso(dt):
    """Naive-UTC datetime as ISO 8601 with a Z suffix."""
    return None if dt is None else dt.isoformat(timespec="seconds") + "Z"


def _alert_json(alert: models.Alert, lift: models.Lift | None) -> dict:
    return {
        "id": alert.id,
        "lift_id": alert.lift_id,
        "lift_name": lift.name if lift else alert.lift_id,
        "created_at": iso(alert.created_at),
        "updated_at": iso(alert.updated_at),
        "severity": alert.severity,
        "feature": alert.feature,
        "message": alert.message,
        "resolved": alert.resolved,
        "resolved_at": iso(alert.resolved_at),
    }


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


# ----------------------------------------------------------------- routes


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return _page("admin.html")

    @app.get("/r/{lift_id}", include_in_schema=False)
    def ride_page(lift_id: str) -> FileResponse:
        return _page("ride.html")

    # ---- public: resident page

    @app.get("/api/lifts/{lift_id}/status", response_model=LiftStatusOut)
    def public_status(lift_id: str, db: Session = Depends(get_db)) -> dict:
        return pipeline.lift_status(db, _get_lift(db, lift_id))

    @app.post("/api/rides", response_model=RideOut)
    def upload_ride(
        body: RideIn,
        request: Request,
        db: Session = Depends(get_db),
        x_admin_token: str | None = Header(default=None),
    ) -> dict:
        lift = _get_lift(db, body.lift_id)
        now = models.utcnow()
        started_at = now
        # Backdating is reserved for the simulator, which holds the admin token.
        if body.started_at is not None and _is_admin(request, x_admin_token):
            started_at = body.started_at.replace(tzinfo=None) if body.started_at.tzinfo is None else (
                body.started_at.astimezone(timezone.utc).replace(tzinfo=None)
            )
        return pipeline.ingest_ride(db, request.app.state.settings, lift, body, started_at)

    @app.post("/api/reports", status_code=201)
    def create_report(body: ReportIn, db: Session = Depends(get_db)) -> dict:
        lift = _get_lift(db, body.lift_id)
        report = pipeline.add_report(db, lift, body.category, body.comment, models.utcnow())
        return {"id": report.id, "lift_id": lift.id, "category": report.category}

    # ---- alerts (dashboard)

    @app.get("/api/alerts", dependencies=[Depends(require_admin)])
    def list_alerts(include_resolved: bool = False, limit: int = Query(default=100, le=1000),
                    db: Session = Depends(get_db)) -> list[dict]:
        stmt = select(models.Alert, models.Lift).join(models.Lift, models.Alert.lift_id == models.Lift.id)
        if not include_resolved:
            stmt = stmt.where(models.Alert.resolved.is_(False))
        stmt = stmt.order_by(models.Alert.updated_at.desc()).limit(limit)
        return [_alert_json(a, lift) for a, lift in db.execute(stmt).all()]

    @app.post("/api/alerts/{alert_id}/resolve", dependencies=[Depends(require_admin)])
    def resolve_alert(alert_id: int, db: Session = Depends(get_db)) -> dict:
        alert = db.get(models.Alert, alert_id)
        if alert is None:
            raise HTTPException(404, "alert not found")
        if not alert.resolved:
            now = models.utcnow()
            alert.resolved, alert.resolved_at, alert.updated_at = True, now, now
            db.commit()
        return _alert_json(alert, db.get(models.Lift, alert.lift_id))

    @app.post("/api/admin/recompute", dependencies=[Depends(require_admin)])
    def recompute(request: Request, lift_id: str | None = None, db: Session = Depends(get_db)) -> dict:
        lifts = [_get_lift(db, lift_id)] if lift_id else list(db.scalars(select(models.Lift)))
        settings, now = request.app.state.settings, models.utcnow()
        scored = {lift.id: pipeline.recompute_lift(db, lift, settings, now) for lift in lifts}
        return {"rescored_rides": scored}

    # ---- admin: buildings and lifts

    @app.get("/api/admin/buildings", dependencies=[Depends(require_admin)])
    def list_buildings(db: Session = Depends(get_db)) -> list[dict]:
        rows = db.scalars(select(models.Building).order_by(models.Building.id)).all()
        return [{"id": b.id, "name": b.name, "address": b.address} for b in rows]

    @app.post("/api/admin/buildings", status_code=201, dependencies=[Depends(require_admin)])
    def create_building(body: BuildingIn, db: Session = Depends(get_db)) -> dict:
        building = models.Building(name=body.name, address=body.address)
        db.add(building)
        db.commit()
        return {"id": building.id, "name": building.name, "address": building.address}

    @app.post("/api/admin/lifts", status_code=201, dependencies=[Depends(require_admin)])
    def create_lift(body: LiftIn, request: Request, db: Session = Depends(get_db)) -> dict:
        building = db.get(models.Building, body.building_id)
        if building is None:
            raise HTTPException(404, "building not found")
        if body.id is not None and db.get(models.Lift, body.id) is not None:
            raise HTTPException(409, "lift id already exists")
        lift_id = body.id or _unique_lift_id(db, _slugify(f"{building.name}-{body.name}"))
        lift = models.Lift(
            id=lift_id,
            building_id=building.id,
            name=body.name,
            floors=body.floors,
            floor_height_m=body.floor_height_m,
        )
        db.add(lift)
        db.commit()
        return {
            "id": lift.id,
            "name": lift.name,
            "building_id": building.id,
            "floors": lift.floors,
            "floor_height_m": lift.floor_height_m,
            "ride_url": f"{_public_base_url(request)}/r/{lift.id}",
        }

    @app.get("/api/admin/lifts/{lift_id}/qr.png", dependencies=[Depends(require_admin)])
    def lift_qr(lift_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
        if db.get(models.Lift, lift_id) is None:
            raise HTTPException(404, "lift not found")
        img = qrcode.make(f"{_public_base_url(request)}/r/{lift_id}", box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")


app = create_app()
