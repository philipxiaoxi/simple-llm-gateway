from app.db import get_session_factory
from app.services.conversation import (
    common_prefix_len,
    extract_session_key,
    find_continuation_log,
    new_messages_to_store,
)
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


def test_common_prefix_len_counts_matching_head() -> None:
    stored = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    inbound = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert common_prefix_len(stored, inbound) == 2


def test_new_messages_to_store_appends_only_new() -> None:
    head = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    tail = [{"role": "assistant", "content": "b"}]
    inbound = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assistant = {"role": "assistant", "content": "c-reply"}
    assert new_messages_to_store(head, tail, inbound, assistant) == [
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "c-reply"},
    ]


def test_new_messages_to_store_skips_duplicate_assistant() -> None:
    # 已存尾部与本次回复一致（重复请求），不追加任何消息
    head = [{"role": "user", "content": "a"}]
    tail = [{"role": "assistant", "content": "reply"}]
    inbound = [{"role": "user", "content": "a"}]
    assistant = {"role": "assistant", "content": "reply"}
    assert new_messages_to_store(head, tail, inbound, assistant) == []


def test_new_messages_to_store_without_assistant() -> None:
    head = [{"role": "user", "content": "a"}]
    tail = [{"role": "assistant", "content": "b"}]
    inbound = [{"role": "user", "content": "a"}, {"role": "user", "content": "c"}]
    assert new_messages_to_store(head, tail, inbound, None) == [{"role": "user", "content": "c"}]


def test_find_continuation_log_without_session_is_none() -> None:
    session = get_session_factory()()
    try:
        assert (
            find_continuation_log(
                session,
                account_id=1,
                api_key_id=1,
                protocol="openai",
                session_key=None,
            )
            is None
        )
    finally:
        session.close()


def test_load_reasoning_map_without_session_is_empty() -> None:
    session = get_session_factory()()
    try:
        assert load_reasoning_map(session, api_key_id=1, account_id=1, session_key=None) == {}
    finally:
        session.close()
