from app.services.conversation import (
    extract_log_messages,
    extract_session_key,
    is_continuation,
    normalize_messages,
)


def test_normalize_openai_and_anthropic_messages() -> None:
    openai_messages = normalize_messages(
        "openai_chat",
        {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]},
    )
    anthropic_messages = normalize_messages(
        "anthropic_messages",
        {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            ]
        },
    )
    assert openai_messages == [("user", "hi"), ("assistant", "ok")]
    assert anthropic_messages == [("user", "hi"), ("assistant", "ok")]


def test_same_first_message_is_new_conversation() -> None:
    first = {"messages": [{"role": "user", "content": "hi"}]}
    second = {"messages": [{"role": "user", "content": "hi"}]}
    assert is_continuation(first, second, "openai_chat") is False


def test_follow_up_with_history_is_same_conversation() -> None:
    first = {"messages": [{"role": "user", "content": "hi"}]}
    second = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "and then?"},
        ]
    }
    assert is_continuation(first, second, "openai_chat") is True


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


def test_extract_log_messages_appends_openai_reply() -> None:
    messages = extract_log_messages(
        "openai_chat",
        {"messages": [{"role": "user", "content": "hi"}]},
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
    )
    assert messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


def test_unrelated_prompt_is_new_conversation() -> None:
    first = {"messages": [{"role": "user", "content": "hi"}]}
    second = {"messages": [{"role": "user", "content": "unrelated"}]}
    assert is_continuation(first, second, "openai_chat") is False
