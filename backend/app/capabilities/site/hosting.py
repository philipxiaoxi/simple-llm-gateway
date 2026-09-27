from __future__ import annotations

import hmac
import mimetypes
import posixpath
import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Site, SiteVersion
from app.static_assets import MUTABLE_ASSET_CACHE

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


# 根绝对 URL（以单个 / 开头）：Vite/CRA 默认产物形如 /assets/index-xxxx.js。
# <base href> 只影响相对 URL，改不了根绝对路径，必须显式改写到站点前缀。
_HTML_ROOT_URL_ATTR = re.compile(
    r'(?P<prefix>\b(?:src|href|poster|action)\s*=\s*)(?P<quote>["\'])(?P<url>/(?!/)[^"\']*)(?P=quote)',
    re.IGNORECASE,
)
_HTML_HEAD_TAG = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HTML_BASE_TAG = re.compile(r"<base(?![\w-])", re.IGNORECASE)
_HTML_COMMENT_OPEN = re.compile(r"<!--", re.IGNORECASE)
_HTML_COMMENT_CLOSE = re.compile(r"-->", re.IGNORECASE)
_CSS_URL = re.compile(r"url\(\s*(?P<quote>[\"']?)(?P<url>/(?!/)[^)\"']*)(?P=quote)\s*\)", re.IGNORECASE)
_CSS_IMPORT = re.compile(
    r'@import\s+(?P<quote>["\'])(?P<url>/(?!/)[^"\']*)(?P=quote)', re.IGNORECASE
)


def _prefix_root_url(url: str, site_prefix: str) -> str:
    if url.startswith(site_prefix):
        return url
    return site_prefix + url.lstrip("/")


def _comment_end(text: str, pos: int) -> int | None:
    """pos 处若为注释起始，返回注释结束下标；否则 None。"""
    match = _HTML_COMMENT_OPEN.match(text, pos)
    if match is None:
        return None
    close = _HTML_COMMENT_CLOSE.search(text, match.end())
    return close.end() if close else len(text)


def _has_base_tag(text: str) -> bool:
    """是否已存在真正的 <base> 标签。

    必须跳过注释：源码注释里提到 `<base href>` 是常见写法，若用纯正则匹配 <base\\b，
    就会误判为已有 base 标签而跳过注入，站点里所有相对链接会退回文档基准 URL 变 404。
    """
    pos = 0
    while True:
        index = text.find("<", pos)
        if index < 0:
            return False
        end = _comment_end(text, index)
        if end is not None:
            pos = end
            continue
        match = _HTML_BASE_TAG.match(text, index)
        if match is not None:
            return True
        pos = index + 1


def _rewrite_html(text: str, site_prefix: str, base_href: str) -> str:
    if not _has_base_tag(text):
        head = _HTML_HEAD_TAG.search(text)
        base_tag = f'<base href="{base_href}">'
        text = text[: head.end()] + base_tag + text[head.end():] if head else base_tag + text
    return _HTML_ROOT_URL_ATTR.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}"
        f"{_prefix_root_url(match.group('url'), site_prefix)}{match.group('quote')}",
        text,
    )


def _rewrite_css(text: str, site_prefix: str) -> str:
    text = _CSS_URL.sub(
        lambda match: f"url({match.group('quote')}"
        f"{_prefix_root_url(match.group('url'), site_prefix)}{match.group('quote')})",
        text,
    )
    return _CSS_IMPORT.sub(
        lambda match: f"@import {match.group('quote')}"
        f"{_prefix_root_url(match.group('url'), site_prefix)}{match.group('quote')}",
        text,
    )


# Vite/CRA 构建产物形如 index-a1b2c3d4.js：文件名带内容哈希，改名即失效。
_HASHED_NAME = re.compile(r"[-.][0-9A-Za-z_-]{8,}\.[0-9A-Za-z]+$")
# 站点强缓存上限：不复用管理端的 HASHED_ASSET_CACHE（一年）。站点是可以反复部署的，
# 文件名带哈希只是"通常安全"，用户也可能用同名文件覆盖旧资源，故封顶三天。
_SITE_IMMUTABLE_CACHE = "public, max-age=259200, immutable"


def _cache_control(relative: str, is_html: bool) -> str:
    if is_html:
        # 入口 HTML 必须每次回源：no-store 让浏览器连副本都不留，
        # 避免部署新版本后仍拿旧 index.html 引用已删除的哈希资源。
        return "no-store"
    if _HASHED_NAME.search(relative):
        return _SITE_IMMUTABLE_CACHE
    # 非哈希静态文件（logo.png、app.js 等）文件名不随内容变化，沿用全站统一的短缓存
    return MUTABLE_ASSET_CACHE


def _file_response(site: Site, version: SiteVersion, file_path: Path, slug: str) -> Response:
    version_root = storage.version_dir(site.id, version.version_no)
    relative = file_path.relative_to(version_root).as_posix()
    media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    entry = version.entry_file or site.entry_file or "index.html"
    is_html = relative.lower().endswith((".html", ".htm")) or relative == entry
    is_css = relative.lower().endswith(".css")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": _cache_control(relative, is_html),
    }
    if is_html or is_css:
        site_prefix = f"/sites/{slug}/"
        text = file_path.read_bytes().decode("utf-8", "replace")
        if is_html:
            parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
            base_href = site_prefix + (f"{parent}/" if parent else "")
            text = _rewrite_html(text, site_prefix, base_href)
        else:
            text = _rewrite_css(text, site_prefix)
        content_type = "text/html; charset=utf-8" if is_html else "text/css; charset=utf-8"
        return Response(content=text.encode("utf-8"), media_type=content_type, headers=headers)
    return FileResponse(file_path, media_type=media_type, headers=headers)


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

    response = _file_response(site, version, file_path, slug)
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
