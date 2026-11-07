from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from app.assistant.api_catalog import ApiOperation
from app.internal_requests import internal_audit_headers

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "api_key_encrypted",
        "password",
        "password_hash",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
        "key",
        "key_encrypted",
    }
)


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS and item not in (None, ""):
                result[str(key)] = "***"
            else:
                result[str(key)] = redact_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [redact_value(item, depth + 1) for item in value]
    return value


def _error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
            if isinstance(detail.get("message"), str):
                return detail["message"]
        if isinstance(detail, str):
            return detail
        if isinstance(payload.get("message"), str):
            return payload["message"]
    if isinstance(payload, str):
        return payload[:500]
    return ""


def _render_path(operation: ApiOperation, path_params: dict[str, Any]) -> tuple[str, list[str]]:
    path = operation.path
    missing: list[str] = []
    for parameter in operation.parameters:
        if parameter.get("in") != "path":
            continue
        name = str(parameter.get("name") or "")
        if not name:
            continue
        value = path_params.get(name)
        if value is None or value == "":
            missing.append(name)
            continue
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    return path, missing


def _query_params(operation: ApiOperation, query: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        str(parameter.get("name"))
        for parameter in operation.parameters
        if parameter.get("in") == "query"
    }
    result: dict[str, Any] = {}
    for key, value in query.items():
        if value is None or value == "":
            continue
        if allowed and key not in allowed:
            continue
        if isinstance(value, str | int | float | bool):
            result[key] = value
        elif isinstance(value, list):
            result[key] = [item for item in value if isinstance(item, str | int | float | bool)]
        else:
            result[key] = json.dumps(value, ensure_ascii=False)
    return result


def actual_path(operation: ApiOperation, path_params: dict[str, Any]) -> str:
    path, _missing = _render_path(operation, path_params)
    return path


def preview_request(operation: ApiOperation, path_params: dict[str, Any], query: dict[str, Any], body: Any) -> dict[str, Any]:
    return {
        "method": operation.method,
        "path": actual_path(operation, path_params),
        "query": redact_value(_query_params(operation, query)),
        "body": redact_value(body) if body is not None else None,
    }


async def execute_api(
    app,
    token: str,
    operation: ApiOperation,
    *,
    path_params: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    path, missing = _render_path(operation, path_params or {})
    if missing:
        return {
            "ok": False,
            "status": 400,
            "error": f"缺少路径参数：{', '.join(missing)}",
            "data": None,
        }
    params = _query_params(operation, query or {})
    headers = {
        "Authorization": f"Bearer {token}",
        "X-GW-Client": "web",
        "X-Assistant-Call": "1",
        "Accept": "application/json",
        **internal_audit_headers(),
    }
    request_body = body if operation.method in {"POST", "PUT", "PATCH", "DELETE"} else None
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://assistant.internal",
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.request(
                operation.method,
                path,
                params=params,
                json=request_body,
                headers=headers,
            )
    except httpx.TimeoutException:
        return {"ok": False, "status": 0, "error": "接口执行超时", "data": None}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "status": 0, "error": str(error)[:500], "data": None}

    content_type = response.headers.get("content-type", "")
    payload: Any = None
    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:4000]}
    elif content_type.startswith("text/"):
        payload = {"text": response.text[:8000]}
    else:
        payload = {"text": f"接口返回 {content_type or '未知类型'}，已省略二进制内容"}

    redacted = redact_value(payload)
    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "error": _error_detail(redacted) or f"接口返回 {response.status_code}",
            "data": redacted,
        }
    return {"ok": True, "status": response.status_code, "error": "", "data": redacted}
