from app.services.conversation import extract_session_key


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
