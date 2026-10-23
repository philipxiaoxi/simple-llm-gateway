from __future__ import annotations

from fastapi.responses import JSONResponse


def _headers_for(status_code: int, error_type: str | None) -> dict[str, str] | None:
    if status_code == 401:
        return {"WWW-Authenticate": "Bearer"}
    return None


def openai_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "param": None, "code": None}},
        headers=headers or _headers_for(status_code, error_type),
    )


def anthropic_error(
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
        headers=headers or _headers_for(status_code, error_type),
    )


def protocol_error(protocol: str, status_code: int, message: str, error_type: str | None = None) -> JSONResponse:
    if protocol == "anthropic_messages":
        mapped = error_type or (
            "authentication_error"
            if status_code == 401
            else "permission_error"
            if status_code == 403
            else "api_error"
        )
        return anthropic_error(status_code, message, mapped)
    mapped = error_type or (
        "rate_limit_error"
        if status_code == 429
        else "invalid_request_error"
        if 400 <= status_code < 500
        else "server_error"
    )
    return openai_error(status_code, message, mapped)
