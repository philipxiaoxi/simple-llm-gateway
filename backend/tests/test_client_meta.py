import app.services.client_meta as client_meta_module
from app.services.client_meta import client_ip, client_meta, lookup_ip_location, parse_user_agent


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


def test_client_meta_reads_nginx_headers() -> None:
    meta = client_meta(
        {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Mobile/15E148 MicroMessenger/8.0.49",
            "Referer": "https://weixin110.qq.com/",
            "X-Real-IP": "219.137.186.167",
        },
        peer="172.20.0.8",
    )
    assert meta["client_ip"] == "219.137.186.167"
    assert meta["device"] == "Mobile · iOS"
    assert meta["browser"] == "微信"
    assert meta["referrer"] == "https://weixin110.qq.com/"


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

    import asyncio

    first, second, private = asyncio.run(run())
    assert first == "中国 广东 深圳"
    assert second == first
    assert private is None
    assert len(calls) == 1


def test_parse_user_agent_desktop_browser() -> None:
    device, browser = parse_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    assert device == "Desktop · Windows"
    assert browser == "Chrome"
