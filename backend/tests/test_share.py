from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


def _make_key(client: TestClient, auth_headers: dict[str, str], name: str = "同事A") -> dict:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": name, "account_id": account["id"]},
    ).json()
    client.post(f"/api/admin/accounts/{account['id']}/models", headers=auth_headers)
    return created


def test_share_lookup_requires_full_key(client: TestClient) -> None:
    response = client.post("/api/share/lookup", json={"api_key": "sk"})
    assert response.status_code == 400


def test_share_lookup_unknown_key(client: TestClient) -> None:
    response = client.post("/api/share/lookup", json={"api_key": "sk-not-exist-xxxxx"})
    assert response.status_code == 404


def test_share_lookup_and_cc_switch_without_admin(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    from unittest.mock import AsyncMock, patch

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

    with patch("app.providers.base.httpx.AsyncClient") as client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=FakeResponse())
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        client_cls.return_value = instance
        created = _make_key(client, auth_headers)

    lookup = client.post("/api/share/lookup", json={"api_key": created["key"]})
    assert lookup.status_code == 200
    body = lookup.json()
    assert body["name"] == "同事A"
    assert body["account_name"] == "DS"
    assert body["provider"] == "deepseek"
    assert body["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert body["gateway"]["anthropic_base_url"] == "http://testserver"
    assert body["gateway"]["openai_base_url"] == "http://testserver/v1"
    assert "api_key" not in body
    assert {item["app"] for item in body["targets"]} == {"claude", "opencode", "codex", "grokbuild"}

    built = client.post(
        "/api/share/cc-switch",
        json={"api_key": created["key"], "app": "claude", "model": "deepseek-reasoner"},
    )
    assert built.status_code == 200
    query = parse_qs(urlparse(built.json()["url"]).query)
    assert query["app"] == ["claude"]
    assert query["apiKey"] == [created["key"]]
    assert query["model"] == ["deepseek-reasoner"]
    assert not query["endpoint"][0].endswith("/v1")


def test_share_cc_switch_rejects_disabled_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _make_key(client, auth_headers)
    client.patch(
        f"/api/admin/keys/{created['id']}",
        headers=auth_headers,
        json={"status": "disabled"},
    )
    lookup = client.post("/api/share/lookup", json={"api_key": created["key"]})
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "disabled"
    built = client.post(
        "/api/share/cc-switch",
        json={"api_key": created["key"], "app": "opencode", "model": "deepseek-chat"},
    )
    assert built.status_code == 403
