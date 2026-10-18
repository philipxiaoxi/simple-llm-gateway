import json

from app.services.headers_redact import dump_headers, is_sensitive_header, redact_headers


def test_sensitive_headers_are_redacted() -> None:
    redacted = redact_headers(
        {
            "Authorization": "Bearer sk-live-secret",
            "x-api-key": "sk-live-secret",
            "Cookie": "session=abc",
            "x-goog-api-key": "AIza-secret",
            "User-Agent": "curl/8.4.0",
        }
    )
    assert redacted is not None
    assert redacted["Authorization"] == "<redacted>"
    assert redacted["x-api-key"] == "<redacted>"
    assert redacted["Cookie"] == "<redacted>"
    assert redacted["x-goog-api-key"] == "<redacted>"
    assert redacted["User-Agent"] == "curl/8.4.0"


def test_is_sensitive_header_matches_substrings() -> None:
    assert is_sensitive_header("Authorization")
    assert is_sensitive_header("x-api-key")
    assert is_sensitive_header("x-goog-api-key")
    assert is_sensitive_header("Cookie")
    assert not is_sensitive_header("User-Agent")
    assert not is_sensitive_header("Content-Type")


def test_redact_headers_handles_empty() -> None:
    assert redact_headers(None) is None
    assert redact_headers({}) is None
    assert dump_headers(None) is None
    assert dump_headers({}) is None


def test_long_values_are_clipped() -> None:
    redacted = redact_headers({"X-Trace": "a" * 5000})
    assert redacted is not None
    assert len(redacted["X-Trace"]) == 2048


def test_dump_headers_keeps_json_under_total_limit() -> None:
    headers = {f"X-Pad-{index}": "v" * 4000 for index in range(10)}
    raw = dump_headers(headers)
    assert raw is not None
    parsed = json.loads(raw)
    assert len(raw.encode("utf-8")) <= 16384
    assert 0 < len(parsed) < len(headers)
    assert "<redacted>" not in raw
