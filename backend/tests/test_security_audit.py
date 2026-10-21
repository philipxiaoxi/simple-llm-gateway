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
