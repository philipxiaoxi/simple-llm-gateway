from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings, validate_app_secret_key
from app.db import get_session_factory, init_db
from app.routers import (
    admin_accounts,
    admin_auth,
    admin_dashboard,
    admin_keys,
    admin_logs,
    health,
    oauth,
    proxy,
    share,
)
from app.seed import seed_admin
from app.services.grok_oauth import cleanup_expired_oauth_states


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_app_secret_key(get_settings().app_secret_key)
    init_db()
    seed_admin()
    session = get_session_factory()()
    try:
        cleanup_expired_oauth_states(session)
        session.commit()
    finally:
        session.close()
    yield


app = FastAPI(
    title="LLM Gateway",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(health.router)
app.include_router(admin_auth.router)
app.include_router(admin_accounts.router)
app.include_router(admin_keys.router)
app.include_router(admin_logs.router)
app.include_router(admin_dashboard.router)
app.include_router(oauth.router)
app.include_router(proxy.router)
app.include_router(share.router)


def _frontend_dist() -> Path:
    from app.config import get_settings

    configured = get_settings().frontend_dist
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/favicon.svg")
    def frontend_favicon() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "favicon.svg")

    @app.get("/icons.svg")
    def frontend_icons() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "icons.svg")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        # 前端路由一律回 index.html，不把用户路径拼到磁盘上。
        return FileResponse(FRONTEND_DIST / "index.html")
