"""资讯上报的 REST 入口（MCP Key 鉴权）。

挂在 `/v1/info/report` 独立路由，而非 `/v1/capabilities/info/report`：通用能力入口
`/v1/capabilities/{capability_id}/{operation}` 先注册，会遮蔽同形 3 段路径（详见
`capabilities_public.dispatch_capability` 与设计文档「路由冲突说明」）。MCP 侧仍走能力平面。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.capabilities import ensure_defaults
from app.config import get_settings
from app.db import get_db
from app.info import report
from app.info.errors import InfoError
from app.services.mcp_auth import assert_capability_allowed, get_mcp_key_from_headers
from app.services.mcp_logs import begin_mcp_call

router = APIRouter(prefix="/v1/info", tags=["info-report"])

CAPABILITY_ID = "info"


def _error(status: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"type": error_type, "message": message}})


def _from_http(error: HTTPException) -> JSONResponse:
    detail: Any = error.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=error.status_code, content=detail)
    return _error(error.status_code, "invalid_request", str(detail))


def _mcp_key_dep(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    return get_mcp_key_from_headers(db, authorization, x_api_key)


def _authorize(mcp_key) -> None:
    ensure_defaults()
    assert_capability_allowed(mcp_key, CAPABILITY_ID)


async def _read_multipart(request: Request) -> report.ReportInput:
    form = await request.form()
    fields: dict[str, Any] = {}
    media: list[report.MediaFile] = []
    for key, value in form.multi_items():
        if hasattr(value, "read") and hasattr(value, "filename"):
            raw = await value.read()
            await value.close()
            media.append(
                report.MediaFile(
                    filename=str(getattr(value, "filename", "") or ""),
                    content_type=str(getattr(value, "content_type", "") or ""),
                    data=raw,
                )
            )
        else:
            fields[key] = value
    return report.build_input(fields, media)


async def _read_json(request: Request, *, max_bytes: int) -> report.ReportInput:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON 统一按空对象处理，交由内容校验拒绝
        body = {}
    if not isinstance(body, dict):
        raise InfoError("请求体必须为 JSON 对象", status_code=400, error_type="invalid_request")
    media = report.parse_json_media(body.get("media"), max_bytes=max_bytes)
    return report.build_input(body, media)


@router.get("/report/config")
def report_config(db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    try:
        _authorize(mcp_key)
    except HTTPException as error:
        return _from_http(error)
    return report.limits()


@router.post("/report")
async def report_one(request: Request, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    try:
        _authorize(mcp_key)
    except HTTPException as error:
        return _from_http(error)

    rec = begin_mcp_call(db, mcp_key, CAPABILITY_ID, "report")
    try:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            item = await _read_multipart(request)
        else:
            item = await _read_json(request, max_bytes=get_settings().info_report_max_file_bytes)
        result = report.save_report(db, mcp_key=mcp_key, item=item)
        rec.success()
        db.commit()
        return result
    except InfoError as error:
        db.rollback()
        rec.failure(error.message)
        db.commit()
        return _error(error.status_code, error.error_type, error.message)
    except HTTPException as error:
        db.rollback()
        rec.failure(str(error.detail))
        db.commit()
        return _from_http(error)
    except Exception as error:  # noqa: BLE001
        db.rollback()
        rec.failure(str(error))
        db.commit()
        return _error(500, "internal_error", "内部错误")


@router.post("/report/batch")
async def report_batch(request: Request, db: Session = Depends(get_db), mcp_key=Depends(_mcp_key_dep)):
    try:
        _authorize(mcp_key)
    except HTTPException as error:
        return _from_http(error)

    rec = begin_mcp_call(db, mcp_key, CAPABILITY_ID, "report_batch")
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise InfoError("items 必须为数组", status_code=400, error_type="invalid_request")
        limit = get_settings().info_report_max_file_bytes
        items: list[report.ReportInput] = []
        for entry in body["items"]:
            if not isinstance(entry, dict):
                raise InfoError("items 项必须为对象", status_code=400, error_type="invalid_request")
            media = report.parse_json_media(entry.get("media"), max_bytes=limit)
            items.append(report.build_input(entry, media))
        result = report.save_reports(db, mcp_key=mcp_key, items=items)
        rec.success()
        db.commit()
        return result
    except InfoError as error:
        db.rollback()
        rec.failure(error.message)
        db.commit()
        return _error(error.status_code, error.error_type, error.message)
    except Exception as error:  # noqa: BLE001
        db.rollback()
        rec.failure(str(error))
        db.commit()
        return _error(500, "internal_error", "内部错误")
