"""前端静态资源托管：强缓存、预压缩产物直出、以及只压缩文本响应。

线上没有 Nginx/CDN，静态资源与 API 都由 uvicorn 直接对外，因此缓存与压缩策略
必须在应用内完成：

- `/assets/*` 的文件名带内容哈希，可以长期强缓存，回访不再重复下载；
- 构建期生成 `.br` / `.gz` 产物，命中时直接发送，避免每请求压缩的 CPU 开销；
- API 的 JSON 与 HTML 由中间件按需 gzip，SSE 与图片/字体原样透传。
"""

from __future__ import annotations

import mimetypes
import stat
import zlib
from typing import Any

import anyio
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 内容哈希产物可以永久缓存，文件改名即失效
HASHED_ASSET_CACHE = "public, max-age=31536000, immutable"
# 非哈希文件（图标等）保持短缓存，改动当天仍能生效
MUTABLE_ASSET_CACHE = "public, max-age=86400"
# 自托管字体：文件名不随构建变化，给中间档期
FONT_CACHE = "public, max-age=2592000"

# 预压缩产物优先级：brotli 体积最小，其次 gzip
PRECOMPRESSED_SUFFIXES: tuple[tuple[str, str], ...] = ((".br", "br"), (".gz", "gzip"))

# 只有文本类响应值得压缩；图片、字体本身已是压缩格式
COMPRESSIBLE_TYPES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/manifest+json",
    "image/svg+xml",
)
# 流式响应必须逐块下发，不参与压缩
STREAMING_TYPES = ("text/event-stream",)

NO_COMPRESSION_STATUS = frozenset({204, 304})


def is_compressible(content_type: str) -> bool:
    lowered = content_type.split(";")[0].strip().lower()
    if not lowered or lowered.startswith(STREAMING_TYPES):
        return False
    return lowered.startswith(COMPRESSIBLE_TYPES)


def parse_accept_encoding(value: str) -> dict[str, float]:
    """解析 Accept-Encoding，带出 q 值；`gzip;q=0` 表示客户端不接受 gzip。"""
    parsed: dict[str, float] = {}
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, params = part.partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, raw = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(raw.strip())
                except ValueError:
                    quality = 1.0
        name = name.strip().lower()
        if name:
            parsed[name] = quality
    return parsed


def accepts_encoding(header: str, encoding: str) -> bool:
    """客户端是否接受该编码；未声明且没有通配符时不压缩。"""
    parsed = parse_accept_encoding(header)
    if encoding in parsed:
        return parsed[encoding] > 0
    return parsed.get("*", 0.0) > 0


class CachedStaticFiles(StaticFiles):
    """带 Cache-Control 的静态文件服务，并在存在预压缩产物时直接发送。"""

    def __init__(self, *, directory: Any, cache_control: str = HASHED_ASSET_CACHE, **kwargs: Any) -> None:
        super().__init__(directory=directory, **kwargs)
        self.cache_control = cache_control

    async def get_response(self, path: str, scope: Scope) -> Response:
        accepted = Headers(scope=scope).get("accept-encoding", "")
        if accepted:
            for suffix, encoding in PRECOMPRESSED_SUFFIXES:
                if not accepts_encoding(accepted, encoding):
                    continue
                try:
                    full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, path + suffix)
                except OSError:
                    continue
                if stat_result and stat.S_ISREG(stat_result.st_mode):
                    return self.file_response(
                        full_path,
                        stat_result,
                        scope,
                        content_encoding=encoding,
                        media_type=mimetypes.guess_type(path)[0],
                    )
        return await super().get_response(path, scope)

    def file_response(  # type: ignore[override]
        self,
        full_path: Any,
        stat_result: Any,
        scope: Scope,
        status_code: int = 200,
        content_encoding: str | None = None,
        media_type: str | None = None,
    ) -> Response:
        if content_encoding is None:
            response = super().file_response(full_path, stat_result, scope, status_code)
        else:
            # 预压缩文件的后缀是 .br/.gz，类型必须按原始文件名判断
            response = FileResponse(
                full_path,
                status_code=status_code,
                stat_result=stat_result,
                media_type=media_type or "application/octet-stream",
            )
            if self.is_not_modified(response.headers, Headers(scope=scope)):
                response = NotModifiedResponse(response.headers)
        response.headers["cache-control"] = self.cache_control
        response.headers.add_vary_header("Accept-Encoding")
        if content_encoding is not None:
            response.headers["content-encoding"] = content_encoding
        return response


class CompressTextMiddleware:
    """按内容类型压缩响应，SSE 与二进制资源原样透传。

    与 Starlette 的 GZipMiddleware 相比，这里多了一层内容类型白名单：
    线上会发送 261 KB 的 PNG 图标，对它做 gzip 只是白白消耗 CPU。
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 5) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        responder = _CompressionResponder(self.app, self.minimum_size, self.compresslevel, scope)
        await responder(scope, receive, send)


class _CompressionResponder:
    def __init__(self, app: ASGIApp, minimum_size: int, compresslevel: int, scope: Scope) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.client_accepts_gzip = accepts_encoding(
            Headers(scope=scope).get("accept-encoding", ""), "gzip"
        )
        self.send: Send = self._unattached
        self.initial_message: Message | None = None
        self.started = False
        self.compressing = False
        self.compressor: zlib.compressobj | None = None

    async def _unattached(self, message: Message) -> None:  # pragma: no cover - 仅在框架误用时触发
        raise RuntimeError("response send 尚未就绪")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self._send_wrapper)
        # 兜底：应用没有下发任何 body 时也要把响应头送出去，避免请求悬挂
        if not self.started and self.initial_message is not None:
            await self._send_start()

    async def _send_wrapper(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.initial_message = message
            headers = Headers(raw=message["headers"])
            content_type = headers.get("content-type", "")
            self.compressing = (
                self.client_accepts_gzip
                and "content-encoding" not in headers
                and message["status"] not in NO_COMPRESSION_STATUS
                and is_compressible(content_type)
            )
            return

        if message["type"] != "http.response.body" or self.initial_message is None:
            await self.send(message)
            return

        body = message.get("body", b"")
        more_body = message.get("more_body", False)

        if not self.compressing:
            await self._send_start()
            await self.send(message)
            return

        if not self.started and not more_body and len(body) < self.minimum_size:
            await self._send_start()
            await self.send(message)
            return

        if self.compressor is None:
            self.compressor = zlib.compressobj(self.compresslevel, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
        compressed = self.compressor.compress(body)
        if not more_body:
            compressed += self.compressor.flush(zlib.Z_FINISH)
        else:
            compressed += self.compressor.flush(zlib.Z_SYNC_FLUSH)
        message = {**message, "body": compressed}
        await self._send_start(compressed_headers=True)
        await self.send(message)

    async def _send_start(self, *, compressed_headers: bool = False) -> None:
        if self.started:
            return
        self.started = True
        assert self.initial_message is not None
        if compressed_headers:
            headers = MutableHeaders(raw=self.initial_message["headers"])
            headers["content-encoding"] = "gzip"
            headers.add_vary_header("Accept-Encoding")
            if "content-length" in headers:
                del headers["content-length"]
        await self.send(self.initial_message)
