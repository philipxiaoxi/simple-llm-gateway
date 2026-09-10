"""静态资源托管与响应压缩的行为约束。

线上没有 Nginx/CDN，缓存头与压缩都由应用负责，因此这些行为需要被测试固定下来。
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.static_assets import CachedStaticFiles, CompressTextMiddleware, is_compressible


@pytest.fixture()
def assets_dir(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("console.log('" + "x" * 2000 + "')")
    (assets / "app-abc123.js.br").write_bytes(b"BRVARIANT")
    (assets / "app-abc123.js.gz").write_bytes(gzip.compress(b"GZVARIANT"))
    (assets / "small-def456.js").write_text("ok")
    return assets


@pytest.fixture()
def static_client(assets_dir: Path) -> TestClient:
    app = FastAPI()
    app.mount("/assets", CachedStaticFiles(directory=assets_dir), name="assets")
    return TestClient(app)


def test_hashed_asset_is_immutable(static_client: TestClient) -> None:
    response = static_client.get("/assets/small-def456.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_brotli_variant_is_served_when_accepted(static_client: TestClient) -> None:
    response = static_client.get(
        "/assets/app-abc123.js", headers={"Accept-Encoding": "gzip, deflate, br"}
    )
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "br"
    assert response.content == b"BRVARIANT"
    # 类型必须按原始文件名判断，不能变成 application/octet-stream
    assert response.headers["content-type"].startswith("text/javascript")
    assert "accept-encoding" in response.headers["vary"].lower()


def test_gzip_variant_is_served_when_brotli_not_accepted(static_client: TestClient) -> None:
    response = static_client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    # httpx 会自动解压，这里断言解压后的内容
    assert response.content == b"GZVARIANT"


def test_identity_variant_is_served_without_accept_encoding(static_client: TestClient) -> None:
    response = static_client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "identity"})
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.text.startswith("console.log(")


def test_unknown_asset_returns_404(static_client: TestClient) -> None:
    assert static_client.get("/assets/missing-000.js").status_code == 404


class _EchoApp:
    """按路径返回指定类型的响应，用于验证压缩白名单。"""

    def __init__(self) -> None:
        self.app = FastAPI()

        @self.app.get("/json")
        def json_route():  # type: ignore[no-untyped-def]
            from fastapi.responses import JSONResponse

            return JSONResponse({"payload": "y" * 4000})

        @self.app.get("/small")
        def small_route():  # type: ignore[no-untyped-def]
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": True})

        @self.app.get("/png")
        def png_route():  # type: ignore[no-untyped-def]
            from starlette.responses import Response

            return Response(b"\x89PNG" + b"z" * 4000, media_type="image/png")

        @self.app.get("/already")
        def already_route():  # type: ignore[no-untyped-def]
            from starlette.responses import Response

            return Response(
                b"raw-bytes",
                media_type="text/plain",
                headers={"Content-Encoding": "br"},
            )


def _compressed_client() -> TestClient:
    app = _EchoApp().app
    app.add_middleware(CompressTextMiddleware, minimum_size=1024, compresslevel=5)
    return TestClient(app)


def test_json_response_is_gzipped() -> None:
    response = _compressed_client().get("/json", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["payload"] == "y" * 4000
    assert "accept-encoding" in response.headers["vary"].lower()


def test_small_response_is_not_compressed() -> None:
    response = _compressed_client().get("/small", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers


def test_binary_response_is_not_compressed() -> None:
    response = _compressed_client().get("/png", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers
    assert len(response.content) == 4004


def test_existing_content_encoding_is_left_alone() -> None:
    response = _compressed_client().get("/already", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "br"
    assert response.content == b"raw-bytes"


def test_streaming_responses_are_not_compressed() -> None:
    app = FastAPI()

    @app.get("/stream")
    def stream_route():  # type: ignore[no-untyped-def]
        from starlette.responses import StreamingResponse

        def chunks():  # type: ignore[no-untyped-def]
            yield b"data: " + b"a" * 2000 + b"\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app.add_middleware(CompressTextMiddleware, minimum_size=1024)
    response = TestClient(app).get("/stream", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers
    assert response.content.startswith(b"data: ")


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/json", True),
        ("application/json; charset=utf-8", True),
        ("text/html; charset=utf-8", True),
        ("text/javascript", True),
        ("image/svg+xml", True),
        ("text/event-stream", False),
        ("image/png", False),
        ("font/woff2", False),
        ("application/zip", False),
    ],
)
def test_compressible_content_types(content_type: str, expected: bool) -> None:
    assert is_compressible(content_type) is expected


def test_identity_request_is_not_compressed_by_middleware() -> None:
    """客户端没要求 gzip 时不能自作主张压缩。"""
    response = _compressed_client().get("/json", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in response.headers
    assert response.json()["payload"] == "y" * 4000


def test_missing_accept_encoding_is_not_compressed() -> None:
    response = _compressed_client().get("/json", headers={"Accept-Encoding": ""})
    assert "content-encoding" not in response.headers


def test_wildcard_accept_encoding_is_compressed() -> None:
    response = _compressed_client().get("/json", headers={"Accept-Encoding": "*"})
    assert response.headers["content-encoding"] == "gzip"


def test_zero_quality_is_not_compressed() -> None:
    response = _compressed_client().get("/json", headers={"Accept-Encoding": "gzip;q=0"})
    assert "content-encoding" not in response.headers


def test_precompressed_variant_respects_zero_quality(assets_dir) -> None:
    app = FastAPI()
    app.mount("/assets", CachedStaticFiles(directory=assets_dir), name="assets")
    client = TestClient(app)
    response = client.get("/assets/app-abc123.js", headers={"Accept-Encoding": "br;q=0, gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert response.content == b"GZVARIANT"
