import json

from app.capabilities import deepseek_web


def test_deepseek_account_binding_and_mcp(client, auth_headers, monkeypatch):
    settings = "/api/admin/mcp/deepseek-web/settings"

    def create_account(provider):
        response = client.post("/api/admin/accounts", headers=auth_headers, json={
            "name": provider, "provider": provider, "api_key": "test-secret-" + provider,
        })
        assert response.is_success, response.text
        return response.json()["id"]

    deepseek = create_account("deepseek")
    other = create_account("openai_generic")
    assert client.get(settings, headers=auth_headers).json() == {
        "account_id": None, "accounts": [{"id": deepseek, "name": "deepseek", "available": True}],
    }
    assert client.put(settings, headers=auth_headers, json={"account_id": other}).status_code == 400
    key = client.post("/api/admin/mcp/keys", headers=auth_headers, json={
        "name": "web", "capability_ids": ["deepseek_web"],
    }).json()
    headers = {"Authorization": f"Bearer {key['key']}", "Accept": "application/json, text/event-stream"}
    path = "/v1/capabilities/deepseek_web"
    assert client.post(path + "/search", headers=headers, json={"query": "example"}).status_code == 503
    assert client.put(settings, headers=auth_headers, json={"account_id": deepseek}).status_code == 200

    calls = []

    async def upstream(operation, credential, payload):
        assert credential == "test-secret-deepseek"
        calls.append((operation, payload))
        if operation == "search":
            return {"results": [{"title": "Example", "url": "https://example.com", "content": "not included"}]}
        return {"title": "Example", "content": "x" * (deepseek_web.MAX_PAGE_CHARS + 1)}

    monkeypatch.setattr(deepseek_web, "call_deepseek", upstream)
    monkeypatch.setattr(deepseek_web, "assert_safe_url", lambda url: "example.com")
    rest = client.post(path + "/search", headers=headers, json={"query": " example "})
    assert rest.json() == {"results": [{"title": "Example", "url": "https://example.com"}]}
    assert calls == [("search", {"query": "example", "count": 5, "need_content": False})]
    assert client.post(path + "/fetch", headers=headers, json={"url": "http://127.0.0.1"}).status_code == 400
    assert len(calls) == 1
    mcp = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "deepseek_fetch", "arguments": {"url": "https://example.com"},
    }})
    result = json.loads(mcp.json()["result"]["content"][0]["text"])
    assert result["truncated"] and len(result["content"]) == deepseek_web.MAX_PAGE_CHARS
    entry = client.get(f"/api/admin/mcp/keys/{key['id']}/integration", headers=auth_headers).json()
    capability = next(row for row in entry["capabilities"] if row["capability_id"] == "deepseek_web")
    assert capability["authorized"] and len(capability["integration"]["rest_endpoints"]) == 2
    assert client.delete(f"/api/admin/accounts/{deepseek}", headers=auth_headers).is_success
    assert client.get(settings, headers=auth_headers).json()["account_id"] is None
