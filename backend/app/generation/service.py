"""图片 / 视频生成的任务编排。

职责：
- 供应商配置读写（加密存储 API Key）；
- 参考文件上传入库；
- 任务创建与参数校验；
- 后台循环：提交待处理任务、轮询进行中任务、转存生成结果到本地。

所有上游调用都在线程里执行（`asyncio.to_thread`），不阻塞事件循环；数据库写入用
短事务，避免网络等待期间长时间占用 SQLite 写锁（与资讯媒体转存同一思路）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from contextlib import suppress
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import get_session_factory
from app.generation import catalog, registry, storage
from app.generation.base import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_VIOLATION,
    GenerationUpdate,
    InputMedia,
    ProviderAdapter,
    ProviderError,
)
from app.info.errors import InfoError
from app.info.media import probe_image_size
from app.info.tokens import make_token
from app.info.urlguard import assert_safe_url
from app.models import GenerationAsset, GenerationProvider, GenerationSettings, GenerationTask

_TERMINAL = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_VIOLATION}
_MAX_RESULT_REDIRECTS = 3


class GenerationError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# 供应商配置
# --------------------------------------------------------------------------- #


def _provider_row(db: Session, provider: str) -> GenerationProvider | None:
    return db.scalar(select(GenerationProvider).where(GenerationProvider.provider == provider))


def resolve_credentials(db: Session, provider: str) -> tuple[str, str]:
    info = registry.provider_info(provider)
    default_base = info.default_base_url if info else ""
    row = _provider_row(db, provider)
    base_url = (row.base_url if row else "") or default_base
    api_key = ""
    if row and row.api_key_encrypted:
        try:
            api_key = decrypt_secret(row.api_key_encrypted, get_settings().app_secret_key)
        except ValueError:
            api_key = ""
    return base_url.strip().rstrip("/"), api_key.strip()


def _adapter_for(db: Session, provider: str) -> ProviderAdapter:
    base_url, api_key = resolve_credentials(db, provider)
    return registry.build_adapter(provider, base_url=base_url, api_key=api_key)


def settings_payload(db: Session) -> dict[str, Any]:
    providers: list[dict[str, Any]] = []
    for code, info in registry.PROVIDERS.items():
        row = _provider_row(db, code)
        base_url, api_key = resolve_credentials(db, code)
        providers.append(
            {
                "provider": code,
                "label": info.label,
                "default_base_url": info.default_base_url,
                "base_url": base_url,
                "has_key": bool(api_key),
                "enabled": bool(row.enabled) if row else True,
                "configured": bool(base_url and api_key),
                "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            }
        )
    settings = _settings_row(db, create=True)
    return {
        "providers": providers,
        "default_image_model": settings.default_image_model or catalog.default_model("image"),
        "default_video_model": settings.default_video_model or catalog.default_model("video"),
        "catalog": catalog.catalog_payload(),
    }


def _settings_row(db: Session, *, create: bool = False) -> GenerationSettings:
    row = db.get(GenerationSettings, 1)
    if row is None and create:
        row = GenerationSettings(id=1)
        db.add(row)
        db.flush()
    return row  # type: ignore[return-value]


def update_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    for entry in payload.get("providers") or []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("provider") or "").strip()
        if code not in registry.PROVIDERS:
            continue
        row = _provider_row(db, code)
        if row is None:
            row = GenerationProvider(provider=code)
            db.add(row)
            db.flush()
        if "base_url" in entry:
            cleaned = str(entry.get("base_url") or "").strip()
            if cleaned and not cleaned.startswith(("http://", "https://")):
                raise GenerationError("base_url 必须以 http:// 或 https:// 开头")
            row.base_url = cleaned[:256]
        if entry.get("api_key"):
            row.api_key_encrypted = encrypt_secret(str(entry["api_key"]).strip(), settings.app_secret_key)
        if "enabled" in entry:
            row.enabled = bool(entry["enabled"])
        row.updated_at = utcnow()
    row = _settings_row(db, create=True)
    if "default_image_model" in payload:
        row.default_image_model = str(payload.get("default_image_model") or "").strip()[:128]
    if "default_video_model" in payload:
        row.default_video_model = str(payload.get("default_video_model") or "").strip()[:128]
    row.updated_at = utcnow()
    db.flush()
    return settings_payload(db)


def clear_provider_key(db: Session, provider: str) -> None:
    row = _provider_row(db, provider)
    if row is None:
        return
    row.api_key_encrypted = None
    row.updated_at = utcnow()
    db.flush()


# --------------------------------------------------------------------------- #
# 参考文件上传
# --------------------------------------------------------------------------- #

_AUDIO_TYPES = {
    "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    "audio/wav": (b"RIFF",),
    "audio/ogg": (b"OggS",),
    "audio/flac": (b"fLaC",),
}


def _detect_audio_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:3] == b"ID3":
        return "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def _detect_input(data: bytes) -> tuple[str, str] | None:
    from app.info.media import detect_upload_type

    image_or_video = detect_upload_type(data)
    if image_or_video is not None:
        kind = "video" if image_or_video.startswith("video/") else "image"
        return kind, image_or_video
    audio = _detect_audio_type(data)
    if audio is not None:
        return "audio", audio
    return None


def create_upload(db: Session, *, filename: str, content_type: str, data: bytes) -> GenerationAsset:
    settings = get_settings()
    if not data:
        raise GenerationError("上传内容为空")
    if len(data) > settings.generation_max_input_bytes:
        raise GenerationError("文件超过大小上限")
    detected = _detect_input(data)
    if detected is None:
        raise GenerationError("不支持的文件类型（图片 jpg/png/webp/gif、视频 mp4/webm/mov、音频 mp3/wav/ogg/m4a/flac）")
    kind, resolved_type = detected
    asset_id = str(uuid.uuid4())
    ext = storage.guess_extension(resolved_type, kind)
    stored_name = f"{asset_id}{ext}"
    folder = storage.pending_dir() / asset_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / stored_name
    path.write_bytes(data)
    width = height = None
    if kind == "image":
        width, height = probe_image_size(path)
    asset = GenerationAsset(
        id=asset_id,
        task_id=None,
        role="input",
        index_no=0,
        kind=kind,
        remote_url="",
        filename=stored_name,
        content_type=resolved_type,
        size_bytes=len(data),
        width=width,
        height=height,
        status="ready",
    )
    db.add(asset)
    db.flush()
    return asset


def _attach_inputs(db: Session, task: GenerationTask, asset_ids: list[str], spec: catalog.ModelSpec) -> None:
    assets: list[GenerationAsset] = []
    for asset_id in asset_ids:
        asset = db.get(GenerationAsset, asset_id)
        if asset is None or asset.role != "input" or asset.task_id is not None:
            raise GenerationError("参考文件不存在或已被使用")
        assets.append(asset)
    image_count = sum(1 for asset in assets if asset.kind == "image")
    video_count = sum(1 for asset in assets if asset.kind == "video")
    audio_count = sum(1 for asset in assets if asset.kind == "audio")
    if (image_count or video_count) and not spec.supports_images:
        raise GenerationError("该模型不支持参考图片")
    if image_count + video_count > max(0, spec.max_images):
        raise GenerationError(f"参考图片最多 {spec.max_images} 张")
    if audio_count and not spec.supports_audios:
        raise GenerationError("该模型不支持参考音频")
    if audio_count > max(0, spec.max_audios):
        raise GenerationError(f"参考音频最多 {spec.max_audios} 个")
    for index, asset in enumerate(assets):
        source = storage.pending_dir() / asset.id / asset.filename
        destination = storage.task_dir(task.id) / asset.filename
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        except OSError as error:
            raise GenerationError(f"参考文件转移失败：{error}") from error
        asset.task_id = task.id
        asset.index_no = index
    db.flush()


# --------------------------------------------------------------------------- #
# 任务创建
# --------------------------------------------------------------------------- #


def _clean_params(spec: catalog.ModelSpec, raw: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for param in spec.params:
        if param.key not in raw:
            continue
        value = raw[param.key]
        if param.type == "number":
            if value is None or value == "":
                continue
            try:
                number = int(value)
            except (TypeError, ValueError) as error:
                raise GenerationError(f"{param.label} 需要是整数") from error
            if param.minimum is not None and number < param.minimum:
                raise GenerationError(f"{param.label} 不能小于 {param.minimum}")
            if param.maximum is not None and number > param.maximum:
                raise GenerationError(f"{param.label} 不能大于 {param.maximum}")
            cleaned[param.key] = number
        else:
            text = str(value or "").strip()
            if text:
                cleaned[param.key] = text
    for param in spec.params:
        if param.required and not cleaned.get(param.key):
            raise GenerationError(f"请填写 {param.label}")
    return cleaned


def create_task(
    db: Session,
    *,
    kind: str,
    model: str,
    prompt: str,
    params: dict[str, Any] | None,
    provider: str = "",
    input_asset_ids: list[str] | None = None,
    mcp_key_id: int | None = None,
) -> GenerationTask:
    if kind not in ("image", "video"):
        raise GenerationError("任务类型只能是 image 或 video")
    spec = catalog.get_model(model)
    if spec is None or spec.kind != kind:
        raise GenerationError("模型不存在或与任务类型不匹配")
    prompt = (prompt or "").strip()
    if not prompt:
        raise GenerationError("请输入提示词")
    provider = (provider or registry.default_provider()).strip()
    if provider not in registry.PROVIDERS:
        raise GenerationError("供应商不存在")
    row = _provider_row(db, provider)
    if row is not None and not row.enabled:
        raise GenerationError("该供应商已停用")
    cleaned = _clean_params(spec, params or {})
    task = GenerationTask(
        id=str(uuid.uuid4()),
        kind=kind,
        provider=provider,
        model=model,
        prompt=prompt,
        params_json=json.dumps(cleaned, ensure_ascii=False),
        status="queued",
        mcp_key_id=mcp_key_id,
    )
    db.add(task)
    db.flush()
    if input_asset_ids:
        _attach_inputs(db, task, input_asset_ids, spec)
    return task


def add_task_input(
    db: Session,
    task: GenerationTask,
    *,
    data: bytes,
    content_type: str = "",
    filename: str = "",
) -> GenerationAsset:
    """给任务直接追加一个本地参考文件（MCP 传入 URL/base64 时用）。"""
    spec = catalog.get_model(task.model)
    detected = _detect_input(data)
    if detected is None:
        raise GenerationError("不支持的参考文件类型")
    kind, resolved_type = detected
    if kind == "audio" and (spec is None or not spec.supports_audios):
        raise GenerationError("该模型不支持参考音频")
    if kind != "audio" and (spec is None or not spec.supports_images):
        raise GenerationError("该模型不支持参考图片")
    existing = db.scalars(
        select(GenerationAsset).where(GenerationAsset.task_id == task.id, GenerationAsset.role == "input")
    ).all()
    images = sum(1 for a in existing if a.kind != "audio")
    audios = sum(1 for a in existing if a.kind == "audio")
    if kind == "audio" and audios >= max(0, spec.max_audios if spec else 0):
        raise GenerationError(f"参考音频最多 {spec.max_audios if spec else 0} 个")
    if kind != "audio" and images >= max(0, spec.max_images if spec else 0):
        raise GenerationError(f"参考图片最多 {spec.max_images if spec else 0} 张")
    asset_id = str(uuid.uuid4())
    ext = storage.guess_extension(resolved_type, kind)
    stored_name = f"{asset_id}{ext}"
    path = storage.task_dir(task.id) / stored_name
    path.write_bytes(data)
    width = height = None
    if kind == "image":
        width, height = probe_image_size(path)
    asset = GenerationAsset(
        id=asset_id,
        task_id=task.id,
        role="input",
        index_no=len(existing),
        kind=kind,
        filename=stored_name,
        content_type=resolved_type,
        size_bytes=len(data),
        width=width,
        height=height,
        status="ready",
    )
    db.add(asset)
    db.flush()
    return asset


# --------------------------------------------------------------------------- #
# 后台循环
# --------------------------------------------------------------------------- #


def _input_data_url(task_id: str, asset: GenerationAsset) -> str:
    path = storage.asset_path(task_id, asset.filename)
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{asset.content_type or 'application/octet-stream'};base64,{encoded}"


def _load_inputs(db: Session, task: GenerationTask) -> tuple[list[InputMedia], list[InputMedia]]:
    assets = db.scalars(
        select(GenerationAsset)
        .where(GenerationAsset.task_id == task.id, GenerationAsset.role == "input")
        .order_by(GenerationAsset.index_no)
    ).all()
    images: list[InputMedia] = []
    audios: list[InputMedia] = []
    for asset in assets:
        try:
            data_url = _input_data_url(task.id, asset)
        except OSError as error:
            raise ProviderError(f"读取参考文件失败：{error}") from error
        if asset.kind == "audio":
            audios.append(InputMedia(kind="audio", data_url=data_url))
        else:
            images.append(InputMedia(kind="image", data_url=data_url))
    return images, audios


def _record_results(db: Session, task: GenerationTask, urls: list[str]) -> None:
    existing = {
        asset.remote_url
        for asset in db.scalars(
            select(GenerationAsset).where(
                GenerationAsset.task_id == task.id, GenerationAsset.role == "result"
            )
        ).all()
    }
    start = len(existing)
    for offset, url in enumerate(urls):
        if url in existing:
            continue
        db.add(
            GenerationAsset(
                id=str(uuid.uuid4()),
                task_id=task.id,
                role="result",
                index_no=start + offset,
                kind=task.kind,
                remote_url=url[:1024],
                status="pending",
            )
        )
        existing.add(url)


def _apply_update(db: Session, task: GenerationTask, update: GenerationUpdate) -> None:
    task.progress = max(task.progress, update.progress)
    if update.upstream_id:
        task.upstream_id = update.upstream_id[:128]
    if update.terminal:
        task.status = update.status
        task.finished_at = utcnow()
        task.error = (update.error or "")[:2000]
        if update.status == STATUS_SUCCEEDED:
            task.progress = 100
            _record_results(db, task, update.results)
    else:
        task.status = STATUS_RUNNING
    task.updated_at = utcnow()


def submit_pending_once(limit: int = 3) -> dict[str, int]:
    factory = get_session_factory()
    settings = get_settings()
    session = factory()
    try:
        task_ids = [
            row
            for row in session.scalars(
                select(GenerationTask.id)
                .where(GenerationTask.status == "queued")
                .order_by(GenerationTask.created_at.asc())
                .limit(max(1, limit))
            ).all()
        ]
    finally:
        session.close()

    submitted = 0
    failed = 0
    for task_id in task_ids:
        result = submit_task_now(factory, settings, task_id)
        if result == "submitted":
            submitted += 1
        elif result == "failed":
            failed += 1
    return {"submitted": submitted, "failed": failed}


def submit_task_now(factory, settings, task_id: str) -> str:
    """提交单个 queued 任务。返回 submitted / retry / failed / skip。

    用条件 UPDATE 原子领取任务，避免后台 worker 与 MCP 内联推进并发重复提交。
    """
    session = factory()
    try:
        claimed = session.execute(
            sa_update(GenerationTask)
            .where(GenerationTask.id == task_id, GenerationTask.status == "queued")
            .values(status="submitting", attempts=GenerationTask.attempts + 1, updated_at=utcnow())
        )
        if claimed.rowcount == 0:
            session.rollback()
            return "skip"
        session.commit()
    finally:
        session.close()

    update: GenerationUpdate | None = None
    error: ProviderError | None = None
    try:
        session = factory()
        try:
            task = session.get(GenerationTask, task_id)
            assert task is not None
            adapter = _adapter_for(session, task.provider)
            spec = catalog.get_model(task.model)
            params = json.loads(task.params_json or "{}")
            images, audios = _load_inputs(session, task)
            update = adapter.submit(
                kind=task.kind,
                model=task.model,
                prompt=task.prompt,
                params=params if spec else {},
                images=images,
                audios=audios,
            )
        finally:
            session.close()
    except Exception as caught:  # noqa: BLE001
        error = ProviderError(str(caught))

    session = factory()
    try:
        task = session.get(GenerationTask, task_id)
        if task is None:
            session.rollback()
            return "skip"
        if error is not None:
            if int(task.attempts or 0) >= max(1, settings.generation_max_attempts):
                task.status = STATUS_FAILED
                task.error = error.args[0][:2000] if error.args else "提交失败"
                task.finished_at = utcnow()
                result = "failed"
            else:
                task.status = "queued"
                result = "retry"
            task.updated_at = utcnow()
        else:
            assert update is not None
            if not task.upstream_id and update.upstream_id:
                task.upstream_id = update.upstream_id[:128]
            if not task.upstream_id and not update.terminal:
                # 上游没给 id，无法轮询，按失败处理
                task.status = STATUS_FAILED
                task.error = "上游未返回任务 id"
                task.finished_at = utcnow()
                result = "failed"
            else:
                _apply_update(session, task, update)
                result = "submitted"
        session.commit()
        return result
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


def poll_running_once(limit: int = 5) -> dict[str, int]:
    factory = get_session_factory()
    session = factory()
    try:
        task_ids = [
            row
            for row in session.scalars(
                select(GenerationTask.id)
                .where(GenerationTask.status == STATUS_RUNNING, GenerationTask.upstream_id != "")
                .order_by(GenerationTask.created_at.asc())
                .limit(max(1, limit))
            ).all()
        ]
    finally:
        session.close()

    polled = 0
    finished = 0
    for task_id in task_ids:
        result = poll_task_now(factory, task_id)
        if result == "polled":
            polled += 1
        elif result == "finished":
            polled += 1
            finished += 1
    return {"polled": polled, "finished": finished}


def poll_task_now(factory, task_id: str) -> str:
    """轮询单个进行中的任务。返回 polled / finished / error / skip。"""
    update: GenerationUpdate | None = None
    error: ProviderError | None = None
    try:
        session = factory()
        try:
            task = session.get(GenerationTask, task_id)
            if task is None:
                return "skip"
            adapter = _adapter_for(session, task.provider)
            upstream_id = task.upstream_id
        finally:
            session.close()
        update = adapter.poll(upstream_id)
    except Exception as caught:  # noqa: BLE001
        error = ProviderError(str(caught))

    session = factory()
    try:
        task = session.get(GenerationTask, task_id)
        if task is None:
            session.rollback()
            return "skip"
        if error is not None or update is None:
            task.attempts = int(task.attempts or 0) + 1
            task.updated_at = utcnow()
            result = "error"
        else:
            _apply_update(session, task, update)
            result = "finished" if update.terminal else "polled"
        session.commit()
        return result
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


def run_task_inline(task_id: str, wait_seconds: int = 0) -> None:
    """把单个任务在等待窗口内推进到终态（提交 + 轮询 + 转存结果）。"""
    factory = get_session_factory()
    settings = get_settings()
    deadline = time.monotonic() + max(0, int(wait_seconds))
    while True:
        session = factory()
        try:
            task = session.get(GenerationTask, task_id)
            status = task.status if task is not None else None
        finally:
            session.close()
        if status is None or status in _TERMINAL:
            break
        if status == "queued":
            submit_task_now(factory, settings, task_id)
        elif status == STATUS_RUNNING:
            poll_task_now(factory, task_id)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
    # 结果转存：尽量在窗口内把已经生成的图片/视频落到本地
    download_task_results(factory, settings, task_id)


def download_task_results(factory, settings, task_id: str) -> int:
    session = factory()
    try:
        asset_ids = [
            row
            for row in session.scalars(
                select(GenerationAsset.id).where(
                    GenerationAsset.task_id == task_id,
                    GenerationAsset.role == "result",
                    GenerationAsset.status == "pending",
                )
            ).all()
        ]
    finally:
        session.close()
    ready = 0
    for asset_id in asset_ids:
        if _download_one(factory, settings, asset_id):
            ready += 1
    return ready


def download_pending_once(limit: int = 3) -> dict[str, int]:
    factory = get_session_factory()
    settings = get_settings()
    session = factory()
    try:
        asset_ids = [
            row
            for row in session.scalars(
                select(GenerationAsset.id)
                .where(
                    GenerationAsset.role == "result",
                    GenerationAsset.status == "pending",
                    GenerationAsset.task_id.is_not(None),
                )
                .order_by(GenerationAsset.created_at.asc())
                .limit(max(1, limit))
            ).all()
        ]
    finally:
        session.close()

    processed = 0
    ready = 0
    for asset_id in asset_ids:
        session = factory()
        try:
            asset = session.get(GenerationAsset, asset_id)
            if asset is None or asset.status != "pending":
                session.rollback()
                continue
        finally:
            session.close()
        processed += 1
        if _download_one(factory, settings, asset_id):
            ready += 1
    return {"processed": processed, "ready": ready}


def _download_one(factory, settings, asset_id: str) -> bool:
    session = factory()
    try:
        asset = session.get(GenerationAsset, asset_id)
        if asset is None or asset.status != "pending" or not asset.task_id:
            session.rollback()
            return False
        task_id = asset.task_id
        remote_url = asset.remote_url
        # 原子领取，避免 worker 与 MCP 内联转存并发下载同一结果
        claimed = session.execute(
            sa_update(GenerationAsset)
            .where(GenerationAsset.id == asset_id, GenerationAsset.status == "pending")
            .values(status="downloading")
        )
        if claimed.rowcount == 0:
            session.rollback()
            return False
        session.commit()
    finally:
        session.close()

    stored: dict[str, Any] | None = None
    failure = ""
    try:
        stored = _download_result(task_id, asset_id, remote_url, settings.generation_result_max_bytes)
    except Exception as caught:  # noqa: BLE001
        failure = str(caught)

    session = factory()
    try:
        asset = session.get(GenerationAsset, asset_id)
        if asset is None:
            session.rollback()
            return False
        if stored is not None:
            asset.filename = stored["filename"]
            asset.content_type = stored["content_type"]
            asset.size_bytes = stored["size"]
            asset.width = stored.get("width")
            asset.height = stored.get("height")
            asset.thumb_filename = stored.get("thumb_filename")
            asset.status = "ready"
            asset.error_message = None
            ok = True
        else:
            asset.attempts = int(asset.attempts or 0) + 1
            asset.error_message = failure[:2000]
            # 未达重试上限时退回 pending 以便下次重试
            asset.status = "failed" if int(asset.attempts or 0) >= max(1, settings.generation_max_attempts) else "pending"
            ok = False
        session.commit()
        return ok
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


def _download_result(
    task_id: str, asset_id: str, remote_url: str, max_bytes: int
) -> dict[str, Any]:
    settings = get_settings()
    timeout = httpx.Timeout(max(30, int(settings.generation_http_timeout_seconds)), connect=10)
    stored_name = ""
    content_type = ""
    size = 0
    current = remote_url
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(_MAX_RESULT_REDIRECTS + 1):
            if not current.startswith(("http://", "https://")):
                raise ProviderError("结果地址无效")
            try:
                # 上游返回的结果地址也逐跳校验，避免被重定向到内网
                assert_safe_url(current)
            except InfoError as error:
                raise ProviderError(f"结果地址不安全：{error.message}") from error
            with client.stream("GET", current) as response:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise ProviderError(f"下载结果失败：HTTP {response.status_code}")
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                kind = "video" if content_type.startswith("video/") else "image"
                ext = storage.guess_extension(content_type, kind)
                stored_name = f"{asset_id}{ext}"
                target = storage.task_dir(task_id) / stored_name
                tmp = target.with_suffix(target.suffix + ".part")
                with tmp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            handle.close()
                            tmp.unlink(missing_ok=True)
                            raise ProviderError("结果文件超过大小上限")
                        handle.write(chunk)
                tmp.replace(target)
                break
        else:
            raise ProviderError("结果地址重定向次数过多")

    width = height = None
    thumb_filename: str | None = None
    if not content_type.startswith("video/"):
        target = storage.task_dir(task_id) / stored_name
        width, height = probe_image_size(target)
        thumb_name = f"{asset_id}-thumb.jpg"
        if storage.make_thumbnail(target, storage.task_dir(task_id) / thumb_name):
            thumb_filename = thumb_name
    return {
        "filename": stored_name,
        "content_type": content_type or "application/octet-stream",
        "size": size,
        "width": width,
        "height": height,
        "thumb_filename": thumb_filename,
    }


def reconcile_stuck(db: Session) -> int:
    """把上次进程中断时卡在 submitting 的任务重新排队。"""
    rows = db.scalars(select(GenerationTask).where(GenerationTask.status == "submitting")).all()
    for task in rows:
        task.status = "queued"
        task.updated_at = utcnow()
    # 中断时卡在「下载中」的结果退回 pending，交给转存循环重试
    stuck_assets = db.scalars(
        select(GenerationAsset).where(GenerationAsset.status == "downloading")
    ).all()
    for asset in stuck_assets:
        asset.status = "pending"
    if rows or stuck_assets:
        db.flush()
    return len(rows)


def backfill_thumbnails(db: Session, limit: int = 20) -> int:
    """给历史已转存但缺缩略图的图片结果补生成缩略图。"""
    rows = db.scalars(
        select(GenerationAsset)
        .where(
            GenerationAsset.role == "result",
            GenerationAsset.status == "ready",
            GenerationAsset.kind == "image",
            GenerationAsset.thumb_filename.is_(None),
            GenerationAsset.filename != "",
        )
        .limit(max(1, limit))
    ).all()
    done = 0
    for asset in rows:
        if not asset.task_id:
            continue
        source = storage.asset_path(asset.task_id, asset.filename)
        if not source.is_file():
            continue
        name = f"{asset.id}-thumb.jpg"
        if storage.make_thumbnail(source, storage.task_dir(asset.task_id) / name):
            asset.thumb_filename = name
            if asset.width is None or asset.height is None:
                asset.width, asset.height = probe_image_size(source)
            done += 1
    if done:
        db.flush()
    return done


async def generation_loop() -> None:
    interval = max(2, int(get_settings().generation_tick_seconds))
    cleanup_counter = 0
    while True:
        try:
            submit = await asyncio.to_thread(submit_pending_once)
            if submit.get("submitted") or submit.get("failed"):
                print(f"[generation] 提交：成功 {submit.get('submitted')}，失败 {submit.get('failed')}")
            poll = await asyncio.to_thread(poll_running_once)
            if poll.get("finished"):
                print(f"[generation] 轮询：完成 {poll.get('finished')}")
            download = await asyncio.to_thread(download_pending_once)
            if download.get("processed"):
                print(f"[generation] 转存：处理 {download.get('processed')}，就绪 {download.get('ready')}")
            # 历史作品补缩略图（有则秒查一次空集）
            await asyncio.to_thread(_backfill_thumbnails_once)
            cleanup_counter += 1
            if cleanup_counter >= max(1, 3600 // interval):
                cleanup_counter = 0
                await asyncio.to_thread(_cleanup_inputs_once)
        except Exception as error:  # noqa: BLE001
            print(f"[generation] 循环异常：{error}")
        await asyncio.sleep(interval)


def _cleanup_inputs_once() -> int:
    """清理上传后一直未提交任务的参考文件（连带删除落盘文件与数据库记录）。"""
    session = get_session_factory()()
    try:
        removed = cleanup_orphan_inputs(session)
        session.commit()
        return removed
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


def _backfill_thumbnails_once() -> int:
    session = get_session_factory()()
    try:
        count = backfill_thumbnails(session)
        session.commit()
        return count
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #


def _asset_url(asset: GenerationAsset) -> str:
    token = make_token(asset.id, ttl_seconds=get_settings().generation_token_ttl_seconds)
    return f"/api/admin/generation/assets/{asset.id}?token={token}"


def asset_to_dict(asset: GenerationAsset) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": asset.id,
        "role": asset.role,
        "kind": asset.kind,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "status": asset.status,
        "error_message": asset.error_message,
    }
    if asset.remote_url:
        # 结果尚未转存完成时，调用方可先用上游地址
        payload["remote_url"] = asset.remote_url
    if asset.status == "ready" and asset.filename:
        payload["url"] = _asset_url(asset)
        if asset.thumb_filename:
            payload["thumb_url"] = f"{_asset_url(asset)}&thumb=1"
    return payload


def task_to_dict(task: GenerationTask, assets: list[GenerationAsset]) -> dict[str, Any]:
    try:
        params = json.loads(task.params_json or "{}")
    except ValueError:
        params = {}
    inputs = [asset_to_dict(a) for a in assets if a.role == "input"]
    results = [asset_to_dict(a) for a in assets if a.role == "result"]
    return {
        "id": task.id,
        "kind": task.kind,
        "provider": task.provider,
        "model": task.model,
        "prompt": task.prompt,
        "params": params,
        "status": task.status,
        "progress": task.progress,
        "error": task.error,
        "attempts": task.attempts,
        "inputs": inputs,
        "results": results,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def get_task(db: Session, task_id: str) -> GenerationTask | None:
    return db.get(GenerationTask, task_id)


def get_task_for_key(db: Session, task_id: str, mcp_key_id: int) -> GenerationTask | None:
    return db.scalar(
        select(GenerationTask).where(
            GenerationTask.id == task_id, GenerationTask.mcp_key_id == mcp_key_id
        )
    )


def task_assets(db: Session, task_id: str) -> list[GenerationAsset]:
    return list(
        db.scalars(
            select(GenerationAsset)
            .where(GenerationAsset.task_id == task_id)
            .order_by(GenerationAsset.role, GenerationAsset.index_no)
        ).all()
    )


def list_tasks(
    db: Session,
    *,
    kind: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
    mcp_key_id: int | None = None,
) -> dict[str, Any]:
    filters = []
    if kind:
        filters.append(GenerationTask.kind == kind)
    if status:
        filters.append(GenerationTask.status == status)
    if mcp_key_id is not None:
        filters.append(GenerationTask.mcp_key_id == mcp_key_id)
    query = select(GenerationTask).where(*filters)
    count_query = select(GenerationTask.id).where(*filters)
    total = len(db.scalars(count_query).all())
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    rows = list(
        db.scalars(
            query.order_by(GenerationTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    items = []
    for task in rows:
        assets = task_assets(db, task.id)
        items.append(task_to_dict(task, assets))
    count_filters = [GenerationTask.kind == kind] if kind else []
    if mcp_key_id is not None:
        count_filters.append(GenerationTask.mcp_key_id == mcp_key_id)
    counts: dict[str, int] = {}
    for row in db.execute(select(GenerationTask.status).where(*count_filters)).all():
        counts[row[0]] = counts.get(row[0], 0) + 1
    return {"items": items, "total": total, "page": page, "page_size": page_size, "counts": counts}


def delete_task(db: Session, task_id: str) -> bool:
    task = db.get(GenerationTask, task_id)
    if task is None:
        return False
    db.delete(task)
    db.flush()
    storage.purge_task(task_id)
    return True


def delete_uploaded_input(db: Session, asset_id: str) -> bool:
    asset = db.get(GenerationAsset, asset_id)
    if asset is None or asset.role != "input" or asset.task_id is not None:
        return False
    filename = asset.filename
    folder = storage.pending_dir() / asset.id
    path = folder / filename if filename else None
    if path is not None:
        with suppress(OSError):
            path.unlink(missing_ok=True)
    with suppress(OSError):
        folder.rmdir()
    db.delete(asset)
    db.flush()
    return True


def cleanup_orphan_inputs(db: Session, *, max_age_seconds: int = 24 * 3600) -> int:
    cutoff = time.time() - max_age_seconds
    rows = db.scalars(
        select(GenerationAsset).where(
            GenerationAsset.role == "input",
            GenerationAsset.task_id.is_(None),
        )
    ).all()
    removed = 0
    for asset in rows:
        created = asset.created_at.timestamp() if asset.created_at else 0
        if created and created < cutoff:
            folder = storage.pending_dir() / asset.id
            if asset.filename:
                with suppress(OSError):
                    (folder / asset.filename).unlink(missing_ok=True)
            with suppress(OSError):
                folder.rmdir()
            db.delete(asset)
            removed += 1
    if removed:
        db.flush()
    return removed


__all__ = [
    "GenerationError",
    "add_task_input",
    "asset_to_dict",
    "create_task",
    "create_upload",
    "delete_task",
    "delete_uploaded_input",
    "download_pending_once",
    "download_task_results",
    "generation_loop",
    "get_task",
    "get_task_for_key",
    "list_tasks",
    "poll_running_once",
    "poll_task_now",
    "reconcile_stuck",
    "resolve_credentials",
    "run_task_inline",
    "settings_payload",
    "submit_pending_once",
    "submit_task_now",
    "task_assets",
    "task_to_dict",
    "update_settings",
]
