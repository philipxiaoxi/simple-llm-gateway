from __future__ import annotations

import base64
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.services.apps import ocr as ocr_service
from app.services.apps import static_deploy as static_service
from app.services.apps.service import list_apps, require_enabled_app


def tool_definitions(db: Session) -> list[dict[str, Any]]:
    """根据已启用应用生成 MCP / function-calling 工具定义。"""
    enabled = {item["id"] for item in list_apps(db) if item.get("enabled")}
    tools: list[dict[str, Any]] = []

    if "ocr" in enabled:
        tools.append(
            {
                "name": "app_ocr_recognize",
                "description": (
                    "对图片做 OCR 文字识别。传入图片的 base64 内容（可带 data URL 前缀）或纯 base64，"
                    "返回识别出的文本。应用须在网关应用中心启用并绑定支持视觉的上游账号。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "image_base64": {
                            "type": "string",
                            "description": "图片 base64，或 data:image/...;base64,... 形式",
                        },
                        "filename": {
                            "type": "string",
                            "description": "可选文件名，用于推断 MIME，如 photo.png",
                        },
                        "mime_type": {
                            "type": "string",
                            "description": "可选 MIME，如 image/png、image/jpeg",
                        },
                    },
                    "required": ["image_base64"],
                },
            }
        )

    if "static-deploy" in enabled:
        tools.extend(
            [
                {
                    "name": "app_static_list_sites",
                    "description": "列出网关应用中心已创建的静态站点及其公开 URL（/a/{slug}/）。",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "app_static_create_site",
                    "description": "创建静态站点。slug 将作为公开路径 /a/{slug}/。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "站点显示名称"},
                            "slug": {
                                "type": "string",
                                "description": "小写字母数字与短横线，公开路径标识",
                            },
                            "description": {"type": "string", "description": "可选备注"},
                        },
                        "required": ["name", "slug"],
                    },
                },
                {
                    "name": "app_static_deploy_files",
                    "description": (
                        "向已有站点部署静态文件。files 为数组，每项含 path 与 content_base64。"
                        "也可传 zip_base64 部署整个压缩包。部署后可通过 public_url 访问。"
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "site_id": {"type": "integer", "description": "站点 ID"},
                            "zip_base64": {
                                "type": "string",
                                "description": "可选：整个站点 zip 的 base64",
                            },
                            "files": {
                                "type": "array",
                                "description": "可选：文件列表",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "content_base64": {"type": "string"},
                                    },
                                    "required": ["path", "content_base64"],
                                },
                            },
                        },
                        "required": ["site_id"],
                    },
                },
                {
                    "name": "app_static_delete_site",
                    "description": "删除静态站点及其文件。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "site_id": {"type": "integer", "description": "站点 ID"},
                        },
                        "required": ["site_id"],
                    },
                },
            ]
        )

    tools.append(
        {
            "name": "app_list",
            "description": "列出应用中心内置应用及启停、绑定状态，便于确认 OCR/静态部署是否可用。",
            "inputSchema": {"type": "object", "properties": {}},
        }
    )
    return tools


def _decode_base64_payload(raw: str) -> bytes:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(400, "image_base64 / content_base64 不能为空")
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=False)
    except Exception as exc:
        raise HTTPException(400, "base64 解码失败") from exc


async def call_tool(db: Session, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    tool_name = (name or "").strip()

    if tool_name == "app_list":
        return {"apps": list_apps(db)}

    if tool_name == "app_ocr_recognize":
        manifest, installation = require_enabled_app(db, "ocr")
        image_bytes = _decode_base64_payload(str(args.get("image_base64") or ""))
        result = await ocr_service.recognize_image(
            db,
            installation=installation,
            manifest=manifest,
            image_bytes=image_bytes,
            filename=str(args.get("filename") or "") or None,
            content_type=str(args.get("mime_type") or "") or None,
        )
        if not result.ok:
            raise HTTPException(400, result.error or "识别失败")
        return {
            "text": result.text,
            "model": result.model,
            "account_id": result.account_id,
            "ms": result.ms,
        }

    if tool_name == "app_static_list_sites":
        require_enabled_app(db, "static-deploy")
        return {"sites": static_service.list_sites(db)}

    if tool_name == "app_static_create_site":
        site = static_service.create_site(
            db,
            name=str(args.get("name") or ""),
            slug=str(args.get("slug") or ""),
            description=str(args.get("description") or "") or None,
        )
        db.commit()
        return {"site": site}

    if tool_name == "app_static_deploy_files":
        site_id = int(args.get("site_id") or 0)
        if not site_id:
            raise HTTPException(400, "site_id 必填")
        zip_b64 = args.get("zip_base64")
        if zip_b64:
            payload = _decode_base64_payload(str(zip_b64))
            site = static_service.deploy_zip(db, site_id, payload, "upload.zip")
            db.commit()
            return {"site": site}
        files_arg = args.get("files") or []
        if not isinstance(files_arg, list) or not files_arg:
            raise HTTPException(400, "请提供 zip_base64 或 files")
        pairs: list[tuple[str, bytes]] = []
        for item in files_arg:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            content = _decode_base64_payload(str(item.get("content_base64") or ""))
            if path:
                pairs.append((path, content))
        site = static_service.deploy_files(db, site_id, pairs)
        db.commit()
        return {"site": site}

    if tool_name == "app_static_delete_site":
        site_id = int(args.get("site_id") or 0)
        if not site_id:
            raise HTTPException(400, "site_id 必填")
        static_service.delete_site(db, site_id)
        db.commit()
        return {"ok": True, "site_id": site_id}

    raise HTTPException(404, f"未知工具：{tool_name}")


def openai_tools(db: Session) -> list[dict[str, Any]]:
    """OpenAI function tools 形态，便于部分 Agent 直接挂 tools。"""
    result = []
    for tool in tool_definitions(db):
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
        )
    return result


async def recognize_upload(db: Session, file: UploadFile) -> dict[str, Any]:
    manifest, installation = require_enabled_app(db, "ocr")
    payload = await file.read()
    result = await ocr_service.recognize_image(
        db,
        installation=installation,
        manifest=manifest,
        image_bytes=payload,
        filename=file.filename,
        content_type=file.content_type,
    )
    if not result.ok:
        raise HTTPException(400, result.error or "识别失败")
    return {
        "ok": True,
        "text": result.text,
        "model": result.model,
        "account_id": result.account_id,
        "ms": result.ms,
    }


def enabled_app_summary(db: Session) -> list[dict[str, Any]]:
    items = []
    for app in list_apps(db):
        if not app.get("enabled"):
            continue
        items.append(
            {
                "id": app["id"],
                "name": app["name"],
                "description": app["description"],
                "entry_path": app["entry_path"],
                "capabilities": app["capabilities"],
                "bound_account_id": app.get("bound_account_id"),
                "bound_model": app.get("bound_model"),
            }
        )
    return items


def mcp_server_info() -> dict[str, Any]:
    return {
        "name": "gateway-app-center",
        "version": "1.0.0",
        "description": "AI一体化服务平台 · 应用中心 MCP（OCR、静态站点部署）",
    }
