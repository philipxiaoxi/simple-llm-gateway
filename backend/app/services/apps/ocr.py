from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppInstallation, UpstreamAccount
from app.services.apps.registry import AppManifest
from app.services.apps.service import installation_config
from app.services.bridge import call_chat, prepare_credential
from app.services.key_models import is_account_available
from app.services.model_caps import first_model_id

DEFAULT_OCR_PROMPT = (
    "你是 OCR 引擎。只输出图片中的文字内容，保持原有换行与阅读顺序。"
    "不要解释、不要翻译、不要添加 Markdown 或前后缀。"
    "若图中没有文字，输出空字符串。"
)


@dataclass(slots=True)
class OcrResult:
    ok: bool
    text: str
    model: str | None = None
    account_id: int | None = None
    ms: int = 0
    error: str | None = None


def _guess_mime(filename: str | None, content_type: str | None) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type.split(";")[0].strip()
    name = (filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".gif"):
        return "image/gif"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".bmp"):
        return "image/bmp"
    return "image/jpeg"


def _extract_text(response: Any) -> str:
    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {}
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part).strip()
    return ""


async def recognize_image(
    db: Session,
    *,
    installation: AppInstallation,
    manifest: AppManifest,
    image_bytes: bytes,
    filename: str | None = None,
    content_type: str | None = None,
) -> OcrResult:
    settings = get_settings()
    started = time.perf_counter()
    if not image_bytes:
        return OcrResult(ok=False, text="", error="图片内容为空")
    if len(image_bytes) > settings.apps_ocr_max_image_bytes:
        limit_mb = settings.apps_ocr_max_image_bytes // (1024 * 1024)
        return OcrResult(ok=False, text="", error=f"图片超过 {limit_mb}MB 上限")

    account_id = installation.bound_account_id
    if not account_id:
        return OcrResult(ok=False, text="", error="请先在应用配置中绑定上游账号")

    account = db.get(UpstreamAccount, int(account_id))
    if account is None:
        return OcrResult(ok=False, text="", error="绑定的上游账号不存在")
    if not is_account_available(account):
        return OcrResult(ok=False, text="", error="绑定的上游账号不可用")

    model = (installation.bound_model or "").strip() or first_model_id(account.models_json)
    if not model:
        return OcrResult(ok=False, text="", error="请选择模型，或先刷新上游账号模型列表")

    config = installation_config(installation, manifest)
    system_prompt = str(config.get("system_prompt") or DEFAULT_OCR_PROMPT)
    try:
        max_tokens = int(config.get("max_tokens") or 2048)
    except (TypeError, ValueError):
        max_tokens = 2048
    max_tokens = max(64, min(max_tokens, 8192))

    mime = _guess_mime(filename, content_type)
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别下图中的全部文字："},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    try:
        credential = await prepare_credential(account, db)
        response = await asyncio.wait_for(
            call_chat(
                account,
                messages,
                model,
                False,
                {"max_tokens": max_tokens, "temperature": 0},
                credential,
            ),
            timeout=settings.apps_ocr_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return OcrResult(
            ok=False,
            text="",
            model=model,
            account_id=account.id,
            ms=int((time.perf_counter() - started) * 1000),
            error=f"识别超时（>{settings.apps_ocr_timeout_seconds}s）",
        )
    except Exception as exc:
        return OcrResult(
            ok=False,
            text="",
            model=model,
            account_id=account.id,
            ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )

    text = _extract_text(response)
    return OcrResult(
        ok=True,
        text=text,
        model=model,
        account_id=account.id,
        ms=int((time.perf_counter() - started) * 1000),
    )
