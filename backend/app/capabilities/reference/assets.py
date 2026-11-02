"""本地参考图的校验、存储和记录转换。"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import suppress
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.source import ReferenceRecord
from app.capabilities.reference.specs import ReferenceSpec
from app.config import get_settings
from app.models import ReferenceAsset

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 15 * 1024 * 1024
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def local_id(asset_id: int) -> str:
    return f"local:{asset_id}"


def parse_local_id(value: str) -> int | None:
    text = (value or "").strip()
    if not text.startswith("local:"):
        return None
    raw = text.removeprefix("local:")
    if not raw.isdigit():
        return None
    return int(raw)


def list_assets(db: Session, kind: str) -> list[ReferenceAsset]:
    return list(
        db.scalars(
            select(ReferenceAsset).where(ReferenceAsset.kind == kind).order_by(ReferenceAsset.created_at.desc(), ReferenceAsset.id.desc())
        ).all()
    )


def create_asset(
    db: Session,
    spec: ReferenceSpec,
    *,
    data: bytes,
    title: str,
    group: str = "",
    category: str = "",
    detail: str = "",
    tags: list[str] | None = None,
    prompt: str = "",
    adult: bool = False,
    created_by: int | None = None,
) -> ReferenceAsset:
    cleaned_title = _limit(title, 200)
    if not cleaned_title:
        raise ReferenceError("请填写标题", status_code=400, error_type="invalid_request")
    content_type = _detect_image(data)
    if content_type is None:
        raise ReferenceError("只支持 JPEG、PNG、WebP 和 GIF 图片", status_code=400, error_type="invalid_request")
    if len(data) > MAX_IMAGE_BYTES:
        raise ReferenceError("图片不能超过 15 MB", status_code=400, error_type="invalid_request")

    asset = ReferenceAsset(
        kind=spec.kind,
        title=cleaned_title,
        group_name=_limit(group, 120),
        category=_limit(category, 120),
        detail=_limit(detail, 120),
        tags_json=json.dumps(_clean_tags(tags or []), ensure_ascii=False),
        prompt=(prompt or "").strip(),
        adult=bool(adult),
        created_by=created_by,
    )
    db.add(asset)
    db.flush()
    directory = _asset_dir(spec.kind, asset.id)
    directory.mkdir(parents=True, exist_ok=True)
    extension = ALLOWED_TYPES[content_type]
    image = directory / f"image{extension}"
    thumb = directory / "thumb.jpg"
    try:
        image.write_bytes(data)
        width, height = _image_size(image)
        if not _write_thumb(image, thumb):
            thumb.write_bytes(data)
        asset.image_path = str(image)
        asset.thumb_path = str(thumb if thumb.is_file() else image)
        asset.width = width
        asset.height = height
        db.flush()
    except Exception:
        db.delete(asset)
        db.flush()
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return asset


def delete_asset(db: Session, spec: ReferenceSpec, reference_id: str) -> None:
    asset_id = parse_local_id(reference_id)
    if asset_id is None:
        raise ReferenceError("上游参考图不能删除", status_code=400, error_type="invalid_request")
    asset = db.get(ReferenceAsset, asset_id)
    if asset is None or asset.kind != spec.kind:
        raise ReferenceError("本地参考图不存在", status_code=404, error_type="not_found")
    directory = _asset_dir(spec.kind, asset.id)
    db.delete(asset)
    db.flush()
    shutil.rmtree(directory, ignore_errors=True)


def to_record(asset: ReferenceAsset, spec: ReferenceSpec) -> ReferenceRecord:
    tags = tuple(_clean_tags(_load_tags(asset.tags_json)))
    adult = bool(asset.adult) or _adult_by_rule(asset, spec)
    search_text = " ".join(
        filter(None, (asset.title, asset.group_name, asset.category, asset.detail, " ".join(tags), asset.prompt, local_id(asset.id)))
    ).lower()
    return ReferenceRecord(
        id=local_id(asset.id),
        title=asset.title,
        group=asset.group_name,
        category=asset.category,
        detail=asset.detail,
        tags=tags,
        keywords="",
        width=asset.width,
        height=asset.height,
        thumb_path=asset.thumb_path,
        image_path=asset.image_path,
        curated=False,
        hot=False,
        adult=adult,
        search_text=search_text,
        extra=(("提示词", asset.prompt),) if asset.prompt else (),
        origin="local",
    )


def file_for(asset: ReferenceAsset, *, thumb: bool) -> Path:
    raw = asset.thumb_path if thumb and asset.thumb_path else asset.image_path
    path = Path(raw)
    root = _kind_dir(asset.kind).resolve()
    resolved = path.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ReferenceError("参考图文件不存在", status_code=404, error_type="not_found")
    return resolved


def _adult_by_rule(asset: ReferenceAsset, spec: ReferenceSpec) -> bool:
    return bool(spec.adult_groups and asset.group_name in spec.adult_groups) or bool(
        spec.adult_categories and asset.category in spec.adult_categories
    )


def _asset_dir(kind: str, asset_id: int) -> Path:
    return _kind_dir(kind) / str(asset_id)


def _kind_dir(kind: str) -> Path:
    return get_settings().data_dir / "references" / kind


def _detect_image(data: bytes) -> str | None:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for signature, content_type in _SIGNATURES:
        if content_type == "image/webp":
            continue
        if data.startswith(signature):
            return content_type
    return None


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return 0, 0
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _write_thumb(source: Path, dest: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((480, 480))
            image.save(dest, format="JPEG", quality=80, optimize=True)
        return dest.is_file()
    except Exception as error:
        logger.warning("参考图缩略图生成失败: %s", error)
        return False


def _load_tags(raw: str) -> list[str]:
    with suppress(TypeError, ValueError):
        value = json.loads(raw or "[]")
        if isinstance(value, list):
            return [str(item) for item in value]
    return []


def _clean_tags(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _limit(value, 40)
        if text and text not in result:
            result.append(text)
        if len(result) >= 12:
            break
    return result


def _limit(value: str, size: int) -> str:
    return " ".join((value or "").split())[:size].strip()
