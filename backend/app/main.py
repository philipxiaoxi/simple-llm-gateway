from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db import init_db
from app.routers import (
    admin_accounts,
    admin_auth,
    admin_dashboard,
    admin_keys,
    admin_logs,
    health,
    oauth,
    proxy,
)
from app.seed import seed_admin


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    seed_admin()
    yield


app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(admin_auth.router)
app.include_router(admin_accounts.router)
app.include_router(admin_keys.router)
app.include_router(admin_logs.router)
app.include_router(admin_dashboard.router)
app.include_router(oauth.router)
app.include_router(proxy.router)


def _frontend_dist() -> Path:
    from app.config import get_settings

    configured = get_settings().frontend_dist
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
