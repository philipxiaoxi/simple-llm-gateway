"""资讯 AI 判定：广告识别与价值打分。

内容先全部入库（`ai_status = pending`），本模块的 worker 再异步处理：读取条目文本与
（模型支持视觉时的）媒体图片，调用管理员配置的上游账号与模型，产出结构化判定结果并
写回 `info_items`。判定失败只改状态，不删除条目或媒体。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.info import storage
from app.models import InfoAiSettings, InfoItem, InfoMedia, UpstreamAccount
from app.services import model_caps
from app.services.bridge import call_chat
from app.services.credentials import CredentialError, require_upstream_credential

MAX_TEXT_CHARS = 6000
MAX_REASON_CHARS = 500
MAX_TAGS = 5
IMAGE_MEDIA_KINDS = ("image", "poster")

DEFAULT_PROMPT = (
    "你是资讯价值评估助手。判断给定资讯是否为广告，并评估它对一位关注 AI 工具、"
    "开源项目、网盘资源与技术资讯的用户的价值。\n"
    "判定标准：\n"
    "- is_ad：以推广、带货、拉群、导流、刷单、博彩、诈骗为目的的纯广告为 true，否则 false。\n"
    "- score：0 到 100 的整数，越高价值越大。工具推荐、开源项目、网盘资源分享、教程、"
    "深度技术内容给高分；闲聊、纯转发、无信息量给低分。\n"
    "- label：ad / valuable / general / other 之一。\n"
    "- tags：最多 5 个简短中文标签。\n"
    "- reason：不超过 80 字的中文理由。\n"
    '只输出 JSON 对象，不要 Markdown，不要解释。字段：{"is_ad":布尔,"score":整数,'
    '"label":"字符串","tags":["字符串"],"reason":"字符串"}'
)


def get_ai_settings(db: Session) -> InfoAiSettings:
    row = db.scalar(select(InfoAiSettings).order_by(InfoAiSettings.id).limit(1))
    if row is None:
        row = InfoAiSettings(enabled=False)
        db.add(row)
        db.flush()
    return row


def serialize_settings(db: Session, row: InfoAiSettings) -> dict[str, Any]:
    account = db.get(UpstreamAccount, row.account_id) if row.account_id else None
    return {
        "enabled": bool(row.enabled),
        "account_id": row.account_id,
        "account_name": account.name if account is not None else None,
        "model": row.model,
        "vision_max_images": int(row.vision_max_images),
        "max_image_bytes": int(row.max_image_bytes),
        "feature_threshold": int(row.feature_threshold),
        "hide_ads": bool(row.hide_ads),
        "max_attempts": int(row.max_attempts),
        "prompt_template": row.prompt_template,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def model_supports_images(account: UpstreamAccount, model: str) -> bool:
    records = model_caps.parse_model_records(account.models_json)
    record = model_caps.find_model_record(records, model)
    caps = record.effective() if record is not None else model_caps.caps_from_heuristic(model)
    return "image" in caps.input_modalities


def _image_parts(
    item: InfoItem, media_rows: list[InfoMedia], *, limit: int, max_bytes: int
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if limit <= 0:
        return parts
    for row in sorted(media_rows, key=lambda entry: entry.index_no):
        if len(parts) >= limit:
            break
        if row.kind not in IMAGE_MEDIA_KINDS or row.status != "ready" or row.purged or not row.filename:
            continue
        try:
            data = storage.media_path(item.id, row.filename).read_bytes()
        except OSError:
            continue
        if not data or len(data) > max_bytes:
            continue
        mime = row.content_type if (row.content_type or "").startswith("image/") else "image/jpeg"
        encoded = base64.b64encode(data).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return parts


def _build_messages(
    item: InfoItem, prompt: str, image_parts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    source_title = item.source.title if item.source is not None else ""
    body = item.text.strip()[:MAX_TEXT_CHARS] or item.excerpt
    header = f"{prompt}\n\n来源：{source_title or '未知'}\n链接：{item.permalink or '无'}\n正文：\n{body}"
    content: list[dict[str, Any]] = [{"type": "text", "text": header}]
    content.extend(image_parts)
    return [{"role": "user", "content": content}]


def _extract_content(result: Any) -> str:
    if hasattr(result, "choices"):
        content = result.choices[0].message.content
    else:
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _parse_decision(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    is_ad = bool(data.get("is_ad"))
    raw_score = data.get("score")
    score: int | None = None
    if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
        score = max(0, min(100, int(raw_score)))
    label = str(data.get("label") or "").strip()[:32]
    if not label:
        label = "ad" if is_ad else "general"
    tags = [str(tag).strip()[:32] for tag in data.get("tags", []) if str(tag).strip()][:MAX_TAGS]
    reason = str(data.get("reason") or "").strip()[:MAX_REASON_CHARS]
    return {"is_ad": is_ad, "score": score, "label": label, "tags": tags, "reason": reason}


def _apply_decision(row: InfoItem, decision: dict[str, Any], *, model: str, config: dict[str, Any]) -> None:
    is_ad = bool(decision["is_ad"])
    score = decision["score"]
    row.ai_status = "done"
    row.ai_score = score
    row.ai_label = "ad" if is_ad else (decision["label"] or "general")
    row.ai_reason = decision["reason"]
    row.ai_tags_json = json.dumps(decision["tags"], ensure_ascii=False) if decision["tags"] else None
    row.ai_model = model[:128]
    row.ai_error = ""
    row.ai_scored_at = utcnow()
    if not row.ai_hidden_manual:
        row.is_hidden = bool(is_ad and config["hide_ads"])
    if is_ad:
        row.is_featured = False
    elif not row.ai_featured_manual and score is not None:
        row.is_featured = score >= int(config["feature_threshold"])


def _record_failure(factory, item_id: str, message: str) -> None:
    session = factory()
    try:
        row = session.get(InfoItem, item_id)
        if row is None:
            return
        row.ai_status = "failed"
        row.ai_error = str(message)[:1000]
        row.ai_attempts = int(row.ai_attempts or 0) + 1
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
    finally:
        session.close()


def _prepare_item(
    factory, item_id: str, runtime: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """读取条目并构造消息（含图片 base64，放在线程池执行，避免阻塞事件循环）。"""
    session = factory()
    try:
        item = session.get(
            InfoItem,
            item_id,
            options=[selectinload(InfoItem.media), selectinload(InfoItem.source)],
        )
        if item is None:
            return None
        image_parts: list[dict[str, Any]] = []
        if runtime["vision"]:
            image_parts = _image_parts(
                item,
                list(item.media),
                limit=int(config["vision_max_images"]),
                max_bytes=int(config["max_image_bytes"]),
            )
        return _build_messages(item, runtime["prompt"], image_parts)
    finally:
        session.close()


def _apply_and_commit(
    factory, item_id: str, decision: dict[str, Any], runtime: dict[str, Any], config: dict[str, Any]
) -> bool:
    session = factory()
    try:
        row = session.get(InfoItem, item_id)
        if row is None:
            return False
        _apply_decision(row, decision, model=runtime["model"], config=config)
        session.commit()
        return True
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


def _mark_batch(factory, ids: list[str], *, status: str, error: str = "") -> None:
    if not ids:
        return
    session = factory()
    try:
        session.execute(
            update(InfoItem)
            .where(InfoItem.id.in_(ids))
            .values(ai_status=status, ai_error=error[:1000])
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
    finally:
        session.close()


def _fail_batch(factory, ids: list[str], message: str) -> None:
    if not ids:
        return
    session = factory()
    try:
        session.execute(
            update(InfoItem)
            .where(InfoItem.id.in_(ids))
            .values(
                ai_status="failed",
                ai_error=str(message)[:1000],
                ai_attempts=InfoItem.ai_attempts + 1,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
    finally:
        session.close()


def _prepare_batch(factory, settings) -> tuple[list[str], int, dict[str, Any]] | None:
    """取一批待判定条目并标记为 processing，返回 (ids, account_id, config)。"""
    session = factory()
    try:
        ai = get_ai_settings(session)
        if not ai.enabled or not ai.account_id:
            return None
        config = {
            "model": ai.model,
            "vision_max_images": int(ai.vision_max_images),
            "max_image_bytes": int(ai.max_image_bytes),
            "feature_threshold": int(ai.feature_threshold),
            "hide_ads": bool(ai.hide_ads),
            "max_attempts": int(ai.max_attempts),
            "prompt_template": ai.prompt_template,
        }
        batch = max(1, settings.info_ai_batch_size)
        ids = list(
            session.scalars(
                select(InfoItem.id)
                .where(
                    InfoItem.ai_status.in_(("pending", "failed")),
                    InfoItem.ai_attempts < config["max_attempts"],
                    # 微信全文补全完成前（content_status=pending）不参与判定，避免拿摘要打分
                    InfoItem.content_status != "pending",
                )
                .order_by(InfoItem.collected_at.asc())
                .limit(batch)
            ).all()
        )
        if ids:
            session.execute(
                update(InfoItem).where(InfoItem.id.in_(ids)).values(ai_status="processing")
            )
            session.commit()
        return ids, int(ai.account_id), config
    finally:
        session.close()


def _resolve_runtime(factory, account_id: int, config: dict[str, Any]) -> dict[str, Any] | None:
    """解析账号、模型、凭据与视觉能力，整批只做一次。"""
    session = factory()
    try:
        account = session.get(UpstreamAccount, account_id)
        if account is None:
            return None
        model = (config["model"] or model_caps.first_model_id(account.models_json) or "").strip()
        if not model:
            return None
        credential = require_upstream_credential(account)
        return {
            "account": account,
            "model": model,
            "credential": credential,
            "vision": model_supports_images(account, model),
            "prompt": (config["prompt_template"] or "").strip() or DEFAULT_PROMPT,
        }
    finally:
        session.close()


async def _score_item(
    factory, item_id: str, runtime: dict[str, Any], config: dict[str, Any]
) -> str:
    messages = await asyncio.to_thread(_prepare_item, factory, item_id, runtime, config)
    if messages is None:
        return "skipped"
    try:
        result = await call_chat(
            runtime["account"], messages, runtime["model"], False, {}, runtime["credential"]
        )
        decision = _parse_decision(_extract_content(result))
    except Exception as error:  # noqa: BLE001 - 网络/上游失败
        await asyncio.to_thread(_record_failure, factory, item_id, str(error))
        return "failed"
    if decision is None:
        await asyncio.to_thread(_record_failure, factory, item_id, "模型返回无法解析为约定 JSON")
        return "failed"
    applied = await asyncio.to_thread(_apply_and_commit, factory, item_id, decision, runtime, config)
    return "done" if applied else "skipped"


def recover_stuck_scoring(session: Session) -> int:
    """把启动时残留的 processing 条目恢复为 pending。"""
    result = session.execute(
        update(InfoItem).where(InfoItem.ai_status == "processing").values(ai_status="pending")
    )
    return int(result.rowcount or 0)


async def score_pending_once() -> dict[str, int]:
    settings = get_settings()
    factory = get_session_factory()

    prepared = await asyncio.to_thread(_prepare_batch, factory, settings)
    if prepared is None:
        return {"processed": 0, "scored": 0, "failed": 0, "skipped": 0}
    ids, account_id, config = prepared
    if not ids:
        return {"processed": 0, "scored": 0, "failed": 0, "skipped": 0}

    try:
        runtime = await asyncio.to_thread(_resolve_runtime, factory, account_id, config)
    except CredentialError as error:
        await asyncio.to_thread(_fail_batch, factory, ids, str(error))
        return {"processed": len(ids), "scored": 0, "failed": len(ids), "skipped": 0}
    if runtime is None:
        # 上游账号或模型不可用：标记跳过，待管理员修好后重新判定
        await asyncio.to_thread(
            _mark_batch, factory, ids, status="skipped", error="未配置可用的上游账号或模型"
        )
        return {"processed": len(ids), "scored": 0, "failed": 0, "skipped": len(ids)}

    semaphore = asyncio.Semaphore(max(1, settings.info_ai_max_concurrent))

    async def run(item_id: str) -> str:
        async with semaphore:
            try:
                return await _score_item(factory, item_id, runtime, config)
            except Exception as error:  # noqa: BLE001 - 未预期异常也不能中断整批
                await asyncio.to_thread(_record_failure, factory, item_id, str(error))
                return "failed"

    outcomes = await asyncio.gather(*(run(item_id) for item_id in ids))
    return {
        "processed": len(ids),
        "scored": outcomes.count("done"),
        "failed": outcomes.count("failed"),
        "skipped": outcomes.count("skipped"),
    }
