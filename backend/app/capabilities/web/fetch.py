from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from app.config import get_settings
from app.info.errors import InfoError
from app.info.urlguard import assert_safe_url

from .errors import WebError

REDIRECT_STATUS = {301, 302, 303, 307, 308}
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.1"


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    encoding: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def _guard(url: str) -> None:
    try:
        assert_safe_url(url)
    except InfoError as error:
        raise WebError(error.message, status_code=error.status_code, error_type=error.error_type) from error


def sniff_content_type(body: bytes, declared: str) -> str:
    declared = (declared or "").lower()
    if declared and declared != "application/octet-stream":
        return declared
    head = body[:512].lstrip().lower()
    if head.startswith(b"%pdf"):
        return "application/pdf"
    if head.startswith((b"<!doctype html", b"<html")) or b"<html" in head:
        return "text/html"
    return declared or "application/octet-stream"


async def fetch(
    url: str,
    *,
    max_bytes: int | None = None,
    timeout: float | None = None,
    max_redirects: int | None = None,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    settings = get_settings()
    limit = max_bytes if max_bytes is not None else settings.web_fetch_max_bytes
    wait = timeout if timeout is not None else settings.web_fetch_timeout_seconds
    hops = max_redirects if max_redirects is not None else settings.web_fetch_max_redirects
    request_headers = {"User-Agent": settings.resolved_web_user_agent, "Accept": ACCEPT}
    if headers:
        request_headers.update(headers)

    current = url
    async with httpx.AsyncClient(
        timeout=wait,
        follow_redirects=False,
        headers=request_headers,
    ) as client:
        for _ in range(max(1, hops) + 1):
            _guard(current)
            try:
                async with client.stream("GET", current) as response:
                    if response.status_code in REDIRECT_STATUS:
                        location = response.headers.get("location")
                        if not location:
                            raise WebError("重定向缺少 Location", status_code=502, error_type="unsafe_redirect")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise WebError(
                            f"上游返回 HTTP {response.status_code}",
                            status_code=502,
                            error_type="upstream_error",
                        )
                    declared = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > limit:
                        raise WebError(
                            f"响应超过 {limit} 字节上限",
                            status_code=413,
                            error_type="too_large",
                        )
                    buffer = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            raise WebError(
                                f"响应超过 {limit} 字节上限",
                                status_code=413,
                                error_type="too_large",
                            )
                    return FetchResult(
                        url=url,
                        final_url=str(response.url),
                        status=response.status_code,
                        content_type=sniff_content_type(bytes(buffer), declared),
                        body=bytes(buffer),
                        encoding=response.encoding or "",
                        headers=dict(response.headers),
                    )
            except httpx.HTTPError as error:
                raise WebError(
                    f"抓取失败: {error}",
                    status_code=502,
                    error_type="upstream_error",
                ) from error
    raise WebError("重定向次数过多", status_code=400, error_type="unsafe_redirect")
