from unittest.mock import AsyncMock, patch
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session_factory
from app.models import UpstreamAccount


def _make_key(client: TestClient, auth_headers: dict[str, str]) -> str:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    session = get_session_factory()()
    try:
        row = session.get(UpstreamAccount, account["id"])
        row.models_json = '["deepseek-chat", "deepseek-reasoner"]'
        session.commit()
        account_id = row.id
    finally:
        session.close()
    return client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account_id},
    ).json()["key"]


def _set_account_models(models: list[str]) -> None:
    session = get_session_factory()()
    try:
        account = session.scalar(select(UpstreamAccount))
        assert account is not None
        account.models_json = json.dumps(models)
        session.commit()
    finally:
        session.close()


def _save_alias(client: TestClient, key: str, alias: str, model: str):
    return client.post(
        "/api/share/aliases/save",
        json={"api_key": key, "alias": alias, "model": model},
    )


def test_save_list_and_switch_alias(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    saved = _save_alias(client, key, "fast", "deepseek-chat")
    assert saved.status_code == 200, saved.text
    assert saved.json()["aliases"] == [{"alias": "fast", "model": "deepseek-chat"}]

    lookup = client.post("/api/share/lookup", json={"api_key": key}).json()
    assert lookup["aliases"] == [{"alias": "fast", "model": "deepseek-chat"}]

    # 同名保存即切换指向的模型
    switched = _save_alias(client, key, "fast", "deepseek-reasoner")
    assert switched.status_code == 200
    assert switched.json()["aliases"] == [{"alias": "fast", "model": "deepseek-reasoner"}]

    # 删除
    deleted = client.post("/api/share/aliases/delete", json={"api_key": key, "alias": "fast"})
    assert deleted.status_code == 200
    assert deleted.json()["aliases"] == []

    missing = client.post("/api/share/aliases/delete", json={"api_key": key, "alias": "fast"})
    assert missing.status_code == 404


def test_alias_rejected_on_conflicts(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)

    same_as_model = _save_alias(client, key, "deepseek-chat", "deepseek-chat")
    assert same_as_model.status_code == 400

    unknown_model = _save_alias(client, key, "fast", "not-a-model")
    assert unknown_model.status_code == 400

    bad_name = _save_alias(client, key, "-bad name", "deepseek-chat")
    assert bad_name.status_code == 400

    wrong_key = _save_alias(client, key + "0", "fast", "deepseek-chat")
    assert wrong_key.status_code == 404


def test_proxy_rewrites_alias_to_bound_model(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    assert _save_alias(client, key, "fast", "deepseek-chat").status_code == 200

    captured: dict = {}

    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        captured["model"] = model
        return FakeResponse()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_call_chat)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert captured["model"] == "deepseek-chat"

    # 切换别名后，同一别名转发到新模型
    assert _save_alias(client, key, "fast", "deepseek-reasoner").status_code == 200
    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_call_chat)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert captured["model"] == "deepseek-reasoner"

    # 真实模型名不受别名影响
    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_call_chat)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert captured["model"] == "deepseek-chat"


def test_alias_resolves_across_protocols_and_models_list(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    assert _save_alias(client, key, "think", "deepseek-reasoner").status_code == 200

    models = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).json()
    model_ids = [item["id"] for item in models["data"]]
    assert "think" in model_ids
    assert "deepseek-chat" in model_ids

    # Anthropic 协议同样按别名转发
    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "model": "deepseek-reasoner",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    captured: dict = {}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        captured["model"] = model
        return FakeResponse()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_call_chat)):
        response = client.post(
            "/v1/messages",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "think", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert captured["model"] == "deepseek-reasoner"


def test_alias_only_works_for_its_own_key(client: TestClient, auth_headers: dict[str, str]) -> None:
    key_one = _make_key(client, auth_headers)
    account_id = client.get("/api/admin/accounts", headers=auth_headers).json()[0]["id"]
    key_two = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k2", "account_id": account_id},
    ).json()["key"]
    assert _save_alias(client, key_one, "fast", "deepseek-chat").status_code == 200

    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=FakeResponse())):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key_two}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 400
    assert "fast" in response.json()["error"]["message"]


def _fake_upstream_response() -> type:
    class FakeResponse:
        def model_dump(self) -> dict:
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    return FakeResponse


def test_alias_errors_when_target_model_removed(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    assert _save_alias(client, key, "fast", "deepseek-chat").status_code == 200

    # 管理员把别名指向的模型从账号模型列表移除
    _set_account_models(["deepseek-reasoner"])

    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=_fake_upstream_response()())):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "fast" in message
    assert "不存在" in message

    # 其它模型不受影响，也不回落到默认模型
    with patch("app.services.proxy.call_chat", new=AsyncMock(return_value=_fake_upstream_response()())):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "deepseek-reasoner", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200


def test_alias_errors_when_all_models_removed(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    assert _save_alias(client, key, "fast", "deepseek-chat").status_code == 200

    # 模型列表被清空：别名请求必须报"不存在"，不能透传别名或回落到默认模型
    _set_account_models([])

    captured: dict = {}

    async def fake_call_chat(account, messages, model, stream, extra, api_key):
        captured["model"] = model
        return _fake_upstream_response()()

    with patch("app.services.proxy.call_chat", new=AsyncMock(side_effect=fake_call_chat)):
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 400
    assert "不存在" in response.json()["error"]["message"]
    assert captured == {}


def test_admin_key_alias_endpoints(client: TestClient, auth_headers: dict[str, str]) -> None:
    key = _make_key(client, auth_headers)
    key_id = int(client.get("/api/admin/keys", headers=auth_headers).json()[0]["id"])

    listed = client.get(f"/api/admin/keys/{key_id}/aliases", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json() == {"aliases": [], "models": ["deepseek-chat", "deepseek-reasoner"]}

    saved = client.post(
        f"/api/admin/keys/{key_id}/aliases",
        headers=auth_headers,
        json={"alias": "fast", "model": "deepseek-chat"},
    )
    assert saved.status_code == 200
    assert saved.json()["aliases"] == [{"alias": "fast", "model": "deepseek-chat"}]

    switched = client.post(
        f"/api/admin/keys/{key_id}/aliases",
        headers=auth_headers,
        json={"alias": "fast", "model": "deepseek-reasoner"},
    )
    assert switched.status_code == 200
    assert switched.json()["aliases"] == [{"alias": "fast", "model": "deepseek-reasoner"}]

    conflict = client.post(
        f"/api/admin/keys/{key_id}/aliases",
        headers=auth_headers,
        json={"alias": "deepseek-chat", "model": "deepseek-chat"},
    )
    assert conflict.status_code == 400

    missing_key = client.post(
        "/api/admin/keys/99999/aliases",
        headers=auth_headers,
        json={"alias": "fast", "model": "deepseek-chat"},
    )
    assert missing_key.status_code == 404

    # 管理端设置后，自助页和转发立即同步生效
    lookup = client.post("/api/share/lookup", json={"api_key": key}).json()
    assert lookup["aliases"] == [{"alias": "fast", "model": "deepseek-reasoner"}]

    deleted = client.delete(f"/api/admin/keys/{key_id}/aliases/fast", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["aliases"] == []

    gone = client.delete(f"/api/admin/keys/{key_id}/aliases/fast", headers=auth_headers)
    assert gone.status_code == 404
