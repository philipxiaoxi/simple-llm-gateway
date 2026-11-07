from __future__ import annotations

from app.services.security_audit import is_xhr_request, judge_request

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"


def _tags(path: str, headers: dict[str, str]) -> list[str]:
    return judge_request(path, {"user-agent": UA, **headers}, "1.2.3.4").risk_tags


def test_image_subresource_does_not_require_marker() -> None:
    # 图片/子资源由浏览器直接加载，无法携带自定义头，也不该被要求
    tags = _tags(
        "/api/admin/generation/assets/abc.png",
        {"sec-fetch-dest": "image", "sec-fetch-mode": "no-cors", "accept": "image/avif,image/webp,*/*"},
    )
    assert "missing_marker" not in tags


def test_document_navigation_does_not_require_marker() -> None:
    tags = _tags(
        "/api/admin/dashboard",
        {"sec-fetch-dest": "document", "sec-fetch-mode": "navigate", "accept": "text/html"},
    )
    assert "missing_marker" not in tags


def test_fetch_request_requires_marker() -> None:
    tags = _tags(
        "/api/admin/dashboard/stats",
        {"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "accept": "*/*"},
    )
    assert tags == ["missing_marker"]


def test_xhr_requested_with_requires_marker() -> None:
    tags = _tags("/api/admin/keys", {"x-requested-with": "XMLHttpRequest"})
    assert "missing_marker" in tags


def test_marker_satisfies_requirement() -> None:
    tags = _tags(
        "/api/admin/keys",
        {"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "x-gw-client": "web"},
    )
    assert "missing_marker" not in tags


def test_legacy_client_without_fetch_metadata_uses_accept() -> None:
    # 没有 Fetch Metadata 时按 Accept 兜底：JSON 视为接口调用，HTML 视为导航
    assert is_xhr_request({"accept": "application/json"}) is True
    assert is_xhr_request({"accept": "text/html,application/xhtml+xml"}) is False
    assert is_xhr_request({"content-type": "application/json"}) is True


def test_non_console_path_is_not_judged() -> None:
    result = judge_request("/v1/chat/completions", {"user-agent": "curl/8"}, "1.2.3.4")
    assert result.is_console is False
    assert result.risk_tags == []


def _capture(monkeypatch) -> list:
    from app.services import security_audit as audit

    captured: list = []
    monkeypatch.setattr(audit, "_enqueue", captured.append)
    return captured


def test_subresource_404_does_not_raise_probe_path(monkeypatch) -> None:
    from app.services import security_audit as audit

    path = "/api/admin/generation/assets/missing.png"
    headers = {"sec-fetch-dest": "image", "sec-fetch-mode": "no-cors"}
    judge = audit.judge_request(path, {"user-agent": UA, **headers}, "1.2.3.4")
    captured = _capture(monkeypatch)
    audit.submit_request_event(judge, 404, "GET", path, headers=headers, peer="1.2.3.4")
    assert captured == []


def test_xhr_404_still_raises_probe_path(monkeypatch) -> None:
    from app.services import security_audit as audit

    path = "/api/admin/keys/999"
    headers = {"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "x-gw-client": "web"}
    judge = audit.judge_request(path, {"user-agent": UA, **headers}, "1.2.3.4")
    captured = _capture(monkeypatch)
    audit.submit_request_event(judge, 404, "GET", path, headers=headers, peer="1.2.3.4")
    assert len(captured) == 1
    assert captured[0].risk_tags == ["probe_path"]


def test_command_line_client_is_automation_not_scanner() -> None:
    tags = _tags("/api/admin/users", {"user-agent": "curl/8.5.0"})
    assert "automation_client" in tags
    assert "scanner_ua" not in tags


def test_attack_scanner_is_still_critical_source() -> None:
    tags = _tags("/api/admin/users", {"user-agent": "sqlmap/1.8"})
    assert "scanner_ua" in tags


def test_authenticated_automation_is_info(monkeypatch) -> None:
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)
    monkeypatch.setattr(audit, "_actor_from_headers", lambda _headers: "admin")
    judge = audit.judge_request("/api/admin/users", {"user-agent": "curl/8.5.0"}, "1.2.3.4")
    audit.submit_request_event(
        judge, 200, "GET", "/api/admin/users", headers={"user-agent": "curl/8.5.0"}, peer="1.2.3.4"
    )
    assert len(captured) == 1
    assert captured[0].severity == "info"
    assert "authenticated_automation" in captured[0].risk_tags


def test_anonymous_automation_remains_warning(monkeypatch) -> None:
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)
    monkeypatch.setattr(audit, "_actor_from_headers", lambda _headers: None)
    judge = audit.judge_request("/api/admin/users", {"user-agent": "curl/8.5.0"}, "1.2.3.4")
    audit.submit_request_event(
        judge, 200, "GET", "/api/admin/users", headers={"user-agent": "curl/8.5.0"}, peer="1.2.3.4"
    )
    assert len(captured) == 1
    assert captured[0].severity == "warn"
    assert "authenticated_automation" not in captured[0].risk_tags


def test_authenticated_scanner_stays_critical(monkeypatch) -> None:
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)
    monkeypatch.setattr(audit, "_actor_from_headers", lambda _headers: "admin")
    judge = audit.judge_request("/api/admin/users", {"user-agent": "sqlmap/1.8"}, "1.2.3.4")
    audit.submit_request_event(
        judge, 200, "GET", "/api/admin/users", headers={"user-agent": "sqlmap/1.8"}, peer="1.2.3.4"
    )
    assert len(captured) == 1
    assert captured[0].severity == "critical"


def test_internal_request_marker_is_unforgeable() -> None:
    from app.internal_requests import internal_audit_headers, is_internal_audit_request

    assert is_internal_audit_request(internal_audit_headers())
    assert not is_internal_audit_request({"x-internal-audit-token": "wrong"})


def test_middleware_skips_internal_requests(monkeypatch) -> None:
    import asyncio

    from app.internal_requests import internal_audit_headers
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    headers = [(key.encode(), value.encode()) for key, value in internal_audit_headers().items()]
    scope = {
        "type": "http",
        "path": "/api/admin/users",
        "method": "GET",
        "headers": headers,
        "client": ("1.2.3.4", 1234),
    }
    asyncio.run(audit.SecurityAuditMiddleware(app)(scope, receive, send))
    assert captured == []


def test_login_request_is_not_duplicated_as_suspicious(monkeypatch) -> None:
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)
    judge = audit.judge_request("/api/admin/login", {"user-agent": "curl/8.5.0"}, "1.2.3.4")
    audit.submit_request_event(
        judge, 401, "POST", "/api/admin/login", headers={"user-agent": "curl/8.5.0"}, peer="1.2.3.4"
    )
    assert captured == []


def test_login_scanner_still_generates_event(monkeypatch) -> None:
    from app.services import security_audit as audit

    captured = _capture(monkeypatch)
    judge = audit.judge_request("/api/admin/login", {"user-agent": "sqlmap/1.8"}, "1.2.3.4")
    audit.submit_request_event(
        judge, 401, "POST", "/api/admin/login", headers={"user-agent": "sqlmap/1.8"}, peer="1.2.3.4"
    )
    assert len(captured) == 1
    assert captured[0].severity == "critical"
