from app.db import get_session_factory
from app.services.conversation import extract_session_key
from app.services.proxy import save_log
from app.services.reasoning import load_reasoning_map


def test_extract_claude_code_session_id() -> None:
    session_key = extract_session_key(
        {
            "metadata": {
                "user_id": '{"device_id":"abc","account_uuid":"","session_id":"979bc2f0-8dfc-4a7e-b131-65df53cbe394"}'
            }
        },
        None,
    )
    assert session_key == "979bc2f0-8dfc-4a7e-b131-65df53cbe394"


def test_extract_session_id_from_header() -> None:
    assert extract_session_key({}, {"x-session-id": "sess-header"}) == "sess-header"
    assert extract_session_key({}, {"x-session-affinity": "sess-opencode"}) == "sess-opencode"


def test_continuation_log_is_scoped_to_selected_account(client, auth_headers) -> None:
    first = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "账号一", "provider": "deepseek", "api_key": "sk-one"},
    ).json()
    second = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "账号二", "provider": "deepseek", "api_key": "sk-two"},
    ).json()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "多账号 Key", "account_ids": [first["id"], second["id"]]},
    ).json()
    request_body = {
        "metadata": {"user_id": '{"session_id":"same-session"}'},
        "messages": [{"role": "user", "content": "你好"}],
    }
    response_body = {"choices": [{"message": {"role": "assistant", "content": "好"}}]}
    session = get_session_factory()()
    try:
        first_log = save_log(
            session,
            account_id=first["id"],
            api_key_id=created["id"],
            protocol="openai",
            model="shared",
            stream=False,
            status="success",
            http_status=200,
            error_message=None,
            usage=(1, 1, 2),
            latency_ms=1,
            request_body=request_body,
            response_body=response_body,
        )
        second_log = save_log(
            session,
            account_id=second["id"],
            api_key_id=created["id"],
            protocol="openai",
            model="second/shared",
            stream=False,
            status="success",
            http_status=200,
            error_message=None,
            usage=(1, 1, 2),
            latency_ms=1,
            request_body=request_body,
            response_body=response_body,
        )
        session.commit()
        assert first_log.id != second_log.id
        assert {first_log.account_id, second_log.account_id} == {first["id"], second["id"]}
    finally:
        session.close()


def test_reasoning_map_is_scoped_to_selected_account(client, auth_headers) -> None:
    first = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "账号一", "provider": "deepseek", "api_key": "sk-one"},
    ).json()
    second = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "账号二", "provider": "deepseek", "api_key": "sk-two"},
    ).json()
    created = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "多账号 Key", "account_ids": [first["id"], second["id"]]},
    ).json()
    request_body = {"session_id": "shared-session", "messages": [{"role": "user", "content": "你好"}]}
    session = get_session_factory()()
    try:
        for account_id, reasoning in ((first["id"], "账号一推理"), (second["id"], "账号二推理")):
            save_log(
                session,
                account_id=account_id,
                api_key_id=created["id"],
                protocol="openai",
                model="shared",
                stream=False,
                status="success",
                http_status=200,
                error_message=None,
                usage=(1, 1, 2),
                latency_ms=1,
                request_body=request_body,
                response_body={"choices": [{"message": {"role": "assistant", "content": "好"}}]},
                reasoning_map={"call_shared": reasoning},
            )
        session.commit()
        assert load_reasoning_map(session, created["id"], first["id"], "shared-session") == {
            "call_shared": "账号一推理"
        }
        assert load_reasoning_map(session, created["id"], second["id"], "shared-session") == {
            "call_shared": "账号二推理"
        }
    finally:
        session.close()
