from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.config import get_settings, validate_app_secret_key
from app.db import get_engine, get_session_factory, init_db
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
    health,
    local_agent,
    oauth,
    proxy,
    share,
    voice_rooms,
)
from app.seed import seed_admin, seed_desktop_tools, seed_skill_categories
from app.services.desktop_tools import reconcile_stuck_downloads
from app.services.grok_oauth import cleanup_expired_oauth_states
from app.services.jobs import start_job_loops
from app.services.voice_retention import voice_cleanup_loop
from app.static_assets import (
    FONT_CACHE,
    HASHED_ASSET_CACHE,
    MUTABLE_ASSET_CACHE,
    CachedStaticFiles,
    CompressTextMiddleware,
)


def _warm_up() -> None:
    """预热数据库连接与热点查询，避免重启后第一个用户承担冷启动开销。

    实测（正式服规模数据）：进程刚起时 /api/admin/dashboard 首次 121 ms，
    预热后 9 ms。这里把这份代价提前到启动阶段。
    """
    started = time.perf_counter()
    try:
        with get_engine().connect() as connection:
            # 覆盖列表、统计与内容审计三条主要读取路径
            connection.execute(text("SELECT count(*) FROM request_logs"))
            connection.execute(text("SELECT coalesce(sum(total_tokens), 0) FROM request_logs"))
            connection.execute(text("SELECT count(*) FROM content_audit_findings"))
            connection.execute(
                text("SELECT id FROM leaderboard_snapshots ORDER BY id DESC LIMIT 1")
            )
        from app.services.model_caps import load_catalog_index

        load_catalog_index()
    except Exception:  # 预热失败不能影响启动
        pass
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > 50:
        print(f"[startup] 预热完成，用时 {elapsed_ms:.0f} ms")


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
    await asyncio.to_thread(_warm_up)
    background_tasks = start_job_loops()
    # 语音日志（段落/事件）会持续增长，挂一个每天跑一次的清理任务
    background_tasks.append(asyncio.create_task(voice_cleanup_loop()))
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
app.include_router(admin_skills.download_router)
app.include_router(admin_tools.router)
app.include_router(admin_tools.download_router)
app.include_router(admin_tools.download_router)
app.include_router(oauth.router)
app.include_router(proxy.router)
app.include_router(share.router)
app.include_router(voice_rooms.admin_router)
app.include_router(voice_rooms.public_router)
app.include_router(voice_rooms.router)


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
# 最后添加，保证压缩在最外层：API JSON 与静态文本都会用到
app.add_middleware(CompressTextMiddleware, minimum_size=1024, compresslevel=5)


def _frontend_dist() -> Path:
    from app.config import get_settings

    configured = get_settings().frontend_dist
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
if FRONTEND_DIST.exists():
    no_cache_headers = {"Cache-Control": "no-cache"}
    icon_headers = {"Cache-Control": MUTABLE_ASSET_CACHE}
    # 这些文件构建后可能不存在（例如图标改版），缺失时返回 404 而不是 500
    ICON_FILES = frozenset(
        {
            "favicon.svg",
            "favicon.ico",
            "favicon.png",
            "apple-touch-icon.png",
            "pwa-192x192.png",
            "pwa-512x512.png",
            "icons.svg",
        }
    )
    PWA_FILES = frozenset({"manifest.webmanifest", "sw.js"})
    # dist 根目录下的独立脚本（如语音采集的 AudioWorklet）。
    # 不显式放行就会被 SPA 兜底成 index.html，浏览器拿它当模块加载会直接失败。
    ROOT_SCRIPT_FILES = frozenset(
        path.name
        for path in FRONTEND_DIST.glob("*.js")
        if path.is_file() and not path.name.startswith("workbox-")
    )

    # 文件名带内容哈希，可以永久强缓存；预压缩产物命中时直接发送
    app.mount(
        "/assets",
        CachedStaticFiles(directory=FRONTEND_DIST / "assets", cache_control=HASHED_ASSET_CACHE),
        name="assets",
    )
    # 自托管字体：不挂载就会被 SPA 兜底成 index.html，浏览器拿不到字体
    if (FRONTEND_DIST / "fonts").is_dir():
        app.mount(
            "/fonts",
            CachedStaticFiles(directory=FRONTEND_DIST / "fonts", cache_control=FONT_CACHE),
            name="fonts",
        )

    def _frontend_file(name: str, headers: dict[str, str]) -> FileResponse:
        path = (FRONTEND_DIST / name).resolve()
        if path.parent != FRONTEND_DIST.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(path, headers=headers)

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    @app.api_route("/favicon.png", methods=["GET", "HEAD"])
    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"])
    @app.api_route("/pwa-192x192.png", methods=["GET", "HEAD"])
    @app.api_route("/pwa-512x512.png", methods=["GET", "HEAD"])
    @app.api_route("/icons.svg", methods=["GET", "HEAD"])
    def frontend_icon(request: Request) -> FileResponse:
        name = request.url.path.lstrip("/")
        if name not in ICON_FILES:
            raise HTTPException(status_code=404, detail="Not Found")
        return _frontend_file(name, icon_headers)

    @app.api_route("/manifest.webmanifest", methods=["GET", "HEAD"])
    @app.api_route("/sw.js", methods=["GET", "HEAD"])
    def frontend_pwa_file(request: Request) -> FileResponse:
        name = request.url.path.lstrip("/")
        if name not in PWA_FILES:
            raise HTTPException(status_code=404, detail="Not Found")
        return _frontend_file(name, no_cache_headers)

    @app.api_route("/workbox-{filename}.js", methods=["GET", "HEAD"])
    def frontend_workbox_file(filename: str) -> FileResponse:
        if not filename.replace("-", "").isalnum():
            raise HTTPException(status_code=404, detail="Not Found")
        return _frontend_file(f"workbox-{filename}.js", no_cache_headers)

    @app.api_route("/voice-worklet.js", methods=["GET", "HEAD"])
    def frontend_root_script(request: Request) -> FileResponse:
        # AudioWorklet 必须拿到 application/javascript，返回 index.html 会让 addModule 直接失败
        name = request.url.path.lstrip("/")
        if name not in ROOT_SCRIPT_FILES:
            raise HTTPException(status_code=404, detail="Not Found")
        return _frontend_file(name, no_cache_headers)

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
