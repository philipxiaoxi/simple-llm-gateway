from __future__ import annotations

from fastapi.responses import JSONResponse


def openai_error(status_code: int, message: str, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "param": None, "code": None}},
    )


def anthropic_error(status_code: int, message: str, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
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
        "invalid_request_error" if status_code in {400, 401, 403} else "server_error"
    )
    return openai_error(status_code, message, mapped)
