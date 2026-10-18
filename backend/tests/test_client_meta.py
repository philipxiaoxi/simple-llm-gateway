import asyncio

import app.services.client_meta as client_meta_module
from app.services.client_meta import client_ip, client_meta, lookup_ip_location, parse_client_name


def test_forwarded_ip_ignored_unless_peer_is_trusted() -> None:
    headers = {"X-Forwarded-For": "219.137.186.167", "X-Real-IP": "1.1.1.1"}
    assert client_ip(headers, peer="8.8.8.8") == "8.8.8.8"
    assert client_ip(headers, peer="172.20.0.8") == "219.137.186.167"


def test_client_ip_prefers_forwarded_chain() -> None:
    headers = {
        "X-Forwarded-For": "219.137.186.167, 10.0.0.8",
        "X-Real-IP": "1.1.1.1",
    }
    assert client_ip(headers, peer="172.20.0.8") == "219.137.186.167"


def test_client_meta_reads_api_client_headers() -> None:
    meta = client_meta(
        {
            "User-Agent": "OpenAI/Python 1.30.0",
            "X-Real-IP": "219.137.186.167",
            "Referer": "https://weixin110.qq.com/",
        },
        peer="172.20.0.8",
    )
    assert meta["client_ip"] == "219.137.186.167"
    assert meta["user_agent"] == "OpenAI/Python 1.30.0"
    assert meta["client_name"] == "OpenAI SDK"
    assert "referrer" not in meta


def test_parse_client_name_recognizes_tools() -> None:
    assert parse_client_name("curl/8.4.0") == "curl"
    assert parse_client_name("OpenAI/Python 1.30.0") == "OpenAI SDK"
    assert parse_client_name("python-requests/2.31.0") == "python-requests"
    assert parse_client_name("Go-http-client/2.0") == "Go HTTP"
    assert parse_client_name("SomethingWeird/1.0") is None
    assert parse_client_name("") is None


def test_lookup_ip_location_caches(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "success", "country": "中国", "regionName": "广东", "city": "深圳"}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(client_meta_module.httpx, "AsyncClient", FakeClient)
    client_meta_module._location_cache.clear()

    async def run() -> tuple[str | None, str | None, str | None]:
        first = await lookup_ip_location("8.8.8.8")
        second = await lookup_ip_location("8.8.8.8")
        private = await lookup_ip_location("127.0.0.1")
        return first, second, private

    first, second, private = asyncio.run(run())
    assert first == "中国 广东 深圳"
    assert second == first
    assert private is None
    assert len(calls) == 1
