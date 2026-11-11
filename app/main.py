"""FastAPI application: API routes and static pages."""

from __future__ import annotations

import io
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .config import DEFAULT_ADMIN_TOKEN, Settings, load_settings
from .db import Base, get_db, make_engine, make_session_factory
from .schemas import BuildingIn, LiftIn

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
