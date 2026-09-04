from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings, validate_app_secret_key
from app.db import get_session_factory, init_db
from app.routers import (
    admin_accounts,
    admin_auth,
    admin_benchmark,
    admin_benchmark_history,
    admin_content_audit,
    admin_dashboard,
    admin_jobs,
    admin_keys,
    admin_leaderboard,
    admin_logs,
    admin_skills,
    admin_tools,
    admin_voice,
    health,
    local_agent,
    oauth,
    proxy,
    share,
    voice,
)
from app.seed import seed_admin, seed_desktop_tools, seed_skill_categories
from app.services.desktop_tools import reconcile_stuck_downloads
from app.services.grok_oauth import cleanup_expired_oauth_states
from app.services.jobs import start_job_loops


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_app_secret_key(get_settings().app_secret_key)
    init_db()
    seed_admin()
    seed_skill_categories()
    seed_desktop_tools()
    session = get_session_factory()()
    try:
        reconcile_stuck_downloads(session)
        cleanup_expired_oauth_states(session)
        session.commit()
    finally:
        session.close()
    background_tasks = start_job_loops()
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="AI一体化服务平台",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(health.router)
app.include_router(local_agent.router)
app.include_router(admin_auth.router)
app.include_router(admin_accounts.router)
app.include_router(admin_benchmark.router)
app.include_router(admin_benchmark_history.router)
app.include_router(admin_keys.router)
app.include_router(admin_leaderboard.router)
app.include_router(admin_jobs.router)
app.include_router(admin_content_audit.router)
app.include_router(admin_logs.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_skills.router)
app.include_router(admin_tools.router)
app.include_router(admin_tools.download_router)
app.include_router(admin_voice.router)
app.include_router(oauth.router)
app.include_router(proxy.router)
app.include_router(share.router)
app.include_router(voice.router)


class DisableApiCacheMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(DisableApiCacheMiddleware)


def _frontend_dist() -> Path:
    from app.config import get_settings

    configured = get_settings().frontend_dist
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
if FRONTEND_DIST.exists():
    no_cache_headers = {"Cache-Control": "no-cache"}

    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/favicon.svg")
    @app.get("/favicon.ico")
    @app.get("/favicon.png")
    def frontend_favicon(request: Request) -> FileResponse:
        return FileResponse(FRONTEND_DIST / request.url.path.lstrip("/"))

    @app.get("/apple-touch-icon.png")
    @app.get("/pwa-192x192.png")
    @app.get("/pwa-512x512.png")
    def frontend_pwa_icon(request: Request) -> FileResponse:
        return FileResponse(FRONTEND_DIST / request.url.path.lstrip("/"))

    @app.get("/manifest.webmanifest")
    @app.get("/sw.js")
    def frontend_pwa_file(request: Request) -> FileResponse:
        return FileResponse(FRONTEND_DIST / request.url.path.lstrip("/"), headers=no_cache_headers)

    @app.get("/workbox-{filename}.js")
    def frontend_workbox_file(filename: str) -> FileResponse:
        return FileResponse(FRONTEND_DIST / f"workbox-{filename}.js", headers=no_cache_headers)

    @app.get("/icons.svg")
    def frontend_icons() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "icons.svg")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def spa(full_path: str, request: Request) -> FileResponse:
        # API 路径不能回落到 SPA，否则未注册的 POST 会变成 405 而不是 404。
        is_api = full_path == "health" or full_path.startswith(
            ("api/", "v1/", "anthropic/", "chat", "responses", "models")
        )
        if is_api or request.method not in {"GET", "HEAD"}:
            raise HTTPException(status_code=404, detail="Not Found")
        # 前端路由一律回 index.html，不把用户路径拼到磁盘上。
        return FileResponse(FRONTEND_DIST / "index.html", headers=no_cache_headers)
