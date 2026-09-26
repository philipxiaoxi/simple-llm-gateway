from __future__ import annotations

import hmac
import mimetypes
import posixpath
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, SiteVersion

from . import sites, storage

GATE_COOKIE_MAX_AGE = 7 * 24 * 3600


def _gate_page(slug: str, message: str) -> HTMLResponse:
    safe_slug = slug.replace("&", "&amp;").replace("<", "&lt;")
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>需要访问令牌</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e8ee;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#171a21;padding:28px 24px;border-radius:12px;border:1px solid #2a2f3a;width:min(320px,86vw)}}
h1{{font-size:16px;margin:0 0 12px}} p{{color:#9aa3b2;font-size:13px;margin:0 0 16px}}
input{{width:100%;box-sizing:border-box;padding:9px 10px;border-radius:8px;border:1px solid #2a2f3a;background:#0f1115;color:#e6e8ee;margin-bottom:12px}}
button{{width:100%;padding:9px 10px;border:0;border-radius:8px;background:#5b8cff;color:#fff;font-weight:600;cursor:pointer}}</style>
</head><body><form method="get" action="/sites/{safe_slug}/">
<h1>站点受保护</h1><p>{message}</p>
<input type="password" name="token" placeholder="请输入访问令牌" autofocus/>
<button type="submit">进入</button></form></body></html>"""
    return HTMLResponse(html, status_code=401)


def _resolve_file(version_root: Path, path: str, site: Site) -> Path | None:
    rel = posixpath.normpath("/" + (path or ""))
    rel = rel.lstrip("/")
    if rel == ".":
        rel = ""
    root = version_root.resolve()
    if rel.startswith("../"):
        return None
    candidate = (version_root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    entry = site.entry_file or "index.html"
    if candidate.is_dir():
        candidate = (candidate / entry).resolve()
        if root not in candidate.parents and candidate != root:
            return None
    if candidate.is_file():
        return candidate
    segment = rel.rsplit("/", 1)[-1]
    if site.spa_fallback and "." not in segment:
        fallback = (version_root / entry).resolve()
        if root in fallback.parents and fallback.is_file():
            return fallback
    return None


def _file_response(site: Site, version: SiteVersion, file_path: Path) -> FileResponse:
    version_root = storage.version_dir(site.id, version.version_no)
    relative = file_path.relative_to(version_root).as_posix()
    media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    entry = version.entry_file or site.entry_file or "index.html"
    is_html = relative.lower().endswith((".html", ".htm")) or relative == entry
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache" if is_html else "public, max-age=31536000, immutable",
        },
    )


def _token_state(site: Site, request: Request) -> tuple[bool, bool]:
    if site.access_mode != "token" or not site.access_token_hash:
        return True, False
    cookie = request.cookies.get(sites.gate_cookie_name(site))
    if cookie and hmac.compare_digest(cookie, sites.gate_cookie_value(site)):
        return True, False
    token = request.query_params.get("token") or ""
    if token and sites.verify_token(site, token):
        return True, True
    return False, False


def redirect_slug(request: Request, slug: str) -> RedirectResponse:
    query = request.url.query
    target = f"/sites/{slug}/" + (f"?{query}" if query else "")
    return RedirectResponse(url=target, status_code=308)


def serve(db: Session, request: Request, slug: str, path: str) -> Response:
    site = db.scalar(select(Site).where(Site.slug == slug))
    if site is None or site.status != "active":
        raise HTTPException(status_code=404)
    version = db.get(SiteVersion, site.current_version_id) if site.current_version_id else None
    if version is None or version.status != "ready" or version.purged:
        raise HTTPException(status_code=503, detail="站点尚未部署完成")

    allowed, via_query = _token_state(site, request)
    if not allowed:
        return _gate_page(slug, "该站点已开启访问保护，请先输入令牌。")

    file_path = _resolve_file(storage.version_dir(site.id, version.version_no), path, site)
    if file_path is None:
        raise HTTPException(status_code=404)

    response = _file_response(site, version, file_path)
    if via_query:
        response.set_cookie(
            sites.gate_cookie_name(site),
            sites.gate_cookie_value(site),
            max_age=GATE_COOKIE_MAX_AGE,
            path=f"/sites/{slug}/",
            httponly=True,
            samesite="lax",
        )
    return response
