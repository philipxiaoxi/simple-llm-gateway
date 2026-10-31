"""参考图库数据源：只读外部 GitHub 静态图库（人像 / 姿势 / 服装）。

- 三套库共用同一套拉取、缓存、检索逻辑，字段差异由 `ReferenceSpec` 描述；
- 元数据缓存在内存与磁盘，按 TTL 刷新；图片不做本地镜像，按相对路径拼上游直链；
- 上游不可用时回退上一次缓存并标记 `stale`；
- 姿势与服装库含 18+ 分类，默认过滤，调用方显式传 `include_adult` 才返回。
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.specs import ReferenceSpec, get_spec
from app.config import get_settings

logger = logging.getLogger(__name__)

ATTRIBUTION = "参考图版权归原作者（ギア / @giaaibusi，Anhao 中文版）所有，仅供创作参考。"
SOURCE_LABEL = "philipxiaoxi/ai-pose-gallery-zh"
SCOPES = ("all", "featured", "curated", "hot")
_MAX_SOURCE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ReferenceRecord:
    id: str
    title: str
    group: str
    category: str
    detail: str
    tags: tuple[str, ...]
    keywords: str
    width: int
    height: int
    thumb_path: str
    image_path: str
    curated: bool
    hot: bool
    adult: bool
    search_text: str
    extra: tuple[tuple[str, str], ...]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


class ReferenceSource:
    def __init__(self, spec: ReferenceSpec) -> None:
        self.spec = spec
        self._lock = threading.Lock()
        self._records: list[ReferenceRecord] = []
        self._by_id: dict[str, ReferenceRecord] = {}
        self._meta: dict[str, Any] = {}
        self._synced_at = 0.0
        self._retry_at = 0.0
        self._loaded = False
        self._stale = False

    # ---- 对外查询 ----

    def search(
        self,
        *,
        query: str = "",
        group: str = "",
        category: str = "",
        tag: str = "",
        detail: str = "",
        scope: str = "all",
        include_adult: bool = False,
        limit: int = 40,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._ensure()
        scope = _scope(scope)
        tokens = [token for token in (query or "").lower().split() if token]
        base = [record for record in self._records if self._matches(record, tokens, group, category, tag, detail)]
        visible = [
            record for record in base if (include_adult or not record.adult) and _scope_match(record, scope, self.spec)
        ]
        if self.spec.curated_field:
            visible.sort(key=lambda record: (not record.curated, not record.hot, record.id))
        total = len(visible)
        limit = max(1, min(int(limit or 40), get_settings().references_max_results))
        offset = max(0, int(offset or 0))
        page = visible[offset : offset + limit]
        return {
            **self._envelope(),
            "items": [self._item(record) for record in page],
            "total": total,
            "hidden_adult": len(base) - len(visible),
            "limit": limit,
            "offset": offset,
            "facets": self._facets(include_adult=include_adult),
        }

    def get(self, reference_id: str, *, include_adult: bool = False) -> dict[str, Any]:
        self._ensure()
        record = self._by_id.get((reference_id or "").strip())
        if record is None or (record.adult and not include_adult):
            raise ReferenceError("参考图不存在", status_code=404, error_type="not_found")
        return {**self._envelope(), "item": self._item(record)}

    def random(
        self,
        *,
        query: str = "",
        group: str = "",
        category: str = "",
        tag: str = "",
        detail: str = "",
        scope: str = "all",
        include_adult: bool = False,
    ) -> dict[str, Any]:
        self._ensure()
        tokens = [token for token in (query or "").lower().split() if token]
        matched = [
            record
            for record in self._records
            if self._matches(record, tokens, group, category, tag, detail)
            and (include_adult or not record.adult)
            and _scope_match(record, _scope(scope), self.spec)
        ]
        if not matched:
            raise ReferenceError("没有匹配的参考图", status_code=404, error_type="not_found")
        return {**self._envelope(), "item": self._item(random.choice(matched))}

    def meta(self, *, include_adult: bool = False) -> dict[str, Any]:
        self._ensure()
        visible = [record for record in self._records if include_adult or not record.adult]
        return {
            **self._envelope(),
            "total": len(visible),
            "hidden_adult": len(self._records) - len(visible),
            "facets": self._facets(include_adult=include_adult),
        }

    def refresh(self, *, include_adult: bool = False) -> dict[str, Any]:
        self._ensure(force=True)
        return self.meta(include_adult=include_adult)

    # ---- 内部实现 ----

    def _matches(
        self,
        record: ReferenceRecord,
        tokens: list[str],
        group: str,
        category: str,
        tag: str,
        detail: str,
    ) -> bool:
        if group and record.group != group:
            return False
        if category and record.category != category:
            return False
        if detail and record.detail != detail:
            return False
        if tag and tag not in record.tags:
            return False
        return all(token in record.search_text for token in tokens)

    def _envelope(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "kind": spec.kind,
            "name": spec.name,
            "labels": {
                "group": spec.group_label,
                "category": spec.category_label,
                "detail": spec.detail_label,
                "tag": spec.tag_label,
            },
            "attribution": ATTRIBUTION,
            "source": SOURCE_LABEL,
            "synced_at": self._iso(self._synced_at),
            "stale": self._stale,
        }

    def _facets(self, *, include_adult: bool) -> dict[str, list[str]]:
        visible = [record for record in self._records if include_adult or not record.adult]
        groups = _ordered(
            [str(item) for item in (self._meta.get(f"{self.spec.group_label}列表") or [])],
            [record.group for record in visible if record.group],
        )
        categories = _ordered(
            [str(item) for item in (self._meta.get(f"{self.spec.category_label}列表") or [])],
            [record.category for record in visible if record.category],
        )
        details = sorted({record.detail for record in visible if record.detail})
        tags = sorted({tag for record in visible for tag in record.tags})
        return {"groups": groups, "categories": categories, "details": details, "tags": tags}

    def _item(self, record: ReferenceRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "kind": self.spec.kind,
            "title": record.title,
            "group": record.group,
            "category": record.category,
            "detail": record.detail,
            "tags": list(record.tags),
            "keywords": record.keywords,
            "width": record.width,
            "height": record.height,
            "thumb_url": self._image_url(record.thumb_path),
            "image_url": self._image_url(record.image_path),
            "curated": record.curated,
            "hot": record.hot,
            "adult": record.adult,
            "prompt": self._prompt(record),
        }

    def _prompt(self, record: ReferenceRecord) -> str:
        spec = self.spec
        parts: list[str] = []
        if record.group:
            parts.append(f"{spec.group_label}：{record.group}")
        if record.category:
            parts.append(f"{spec.category_label}：{record.category}")
        if record.detail:
            parts.append(f"{spec.detail_label}：{record.detail}")
        if record.tags:
            parts.append(f"{spec.tag_label}：{'、'.join(record.tags)}")
        text = f"{spec.prompt_prefix}：{record.title}"
        if parts:
            text += f"（{'；'.join(parts)}）"
        if record.keywords:
            text += f"；英文关键词：{record.keywords}"
        for name, value in record.extra:
            text += f"；{name}：{value}"
        return text

    def _image_url(self, path: str) -> str:
        settings = get_settings()
        base = (settings.references_image_cdn or settings.references_source_base).rstrip("/")
        relative = (path or "").lstrip("/")
        return f"{base}/{relative}" if base and relative else ""

    def _ensure(self, *, force: bool = False) -> None:
        settings = get_settings()
        if not settings.references_enabled:
            raise ReferenceError("参考图库未启用", status_code=503, error_type="disabled")
        ttl = max(60, settings.references_cache_ttl_seconds)
        if self._loaded and not force and time.time() < self._retry_at:
            return
        with self._lock:
            if self._loaded and not force and time.time() < self._retry_at:
                return
            base = settings.references_source_base.rstrip("/")
            try:
                raw_records = self._fetch_json(f"{base}/{self.spec.data_path}")
                meta: dict[str, Any] = {}
                if self.spec.meta_path:
                    try:
                        value = self._fetch_json(f"{base}/{self.spec.meta_path}")
                        if isinstance(value, dict):
                            meta = value
                    except Exception as error:  # noqa: BLE001 - 分类元数据可缺省
                        logger.warning("%s 分类元数据获取失败，改用数据内分类: %s", self.spec.name, error)
            except Exception as error:  # noqa: BLE001 - 统一降级
                self._retry_at = time.time() + min(ttl, 300)
                if self._loaded:
                    self._stale = True
                    logger.warning("%s 刷新失败，继续使用缓存: %s", self.spec.name, error)
                    return
                cached = self._read_cache()
                if cached is None:
                    raise ReferenceError(
                        f"{self.spec.name}拉取失败：{error}", status_code=502, error_type="upstream_error"
                    ) from error
                self._apply(cached.get("records") or [], cached.get("meta") or {}, cached.get("synced_at"))
                self._stale = True
                return
            records = self._normalize(raw_records)
            if not records:
                self._retry_at = time.time() + min(ttl, 300)
                raise ReferenceError(f"{self.spec.name}数据为空", status_code=502, error_type="upstream_error")
            self._apply(records, meta, time.time())
            self._stale = False
            self._retry_at = time.time() + ttl
            self._write_cache()

    def _apply(self, raw_records: list[Any], meta: dict[str, Any], synced_at: float | int | None) -> None:
        records: list[ReferenceRecord] = []
        by_id: dict[str, ReferenceRecord] = {}
        for item in self._normalize(raw_records):
            record = self._build_record(item)
            if record is None:
                continue
            records.append(record)
            by_id[record.id] = record
        self._records = records
        self._by_id = by_id
        self._meta = meta
        self._synced_at = float(synced_at or time.time())
        self._loaded = True

    def _build_record(self, item: dict[str, Any]) -> ReferenceRecord | None:
        spec = self.spec
        item_id = _text(item.get("id"))
        if not item_id:
            return None
        group = _text(item.get(spec.group_field))
        category = _text(item.get(spec.category_field)) if spec.category_field else ""
        detail = _text(item.get(spec.detail_field)) if spec.detail_field else ""
        raw_tags = item.get(spec.tag_field) if spec.tag_field else None
        tags = (
            tuple(str(value).strip() for value in raw_tags if str(value).strip()) if isinstance(raw_tags, list) else ()
        )
        keywords = _text(item.get(spec.keyword_field)) if spec.keyword_field else ""
        extra = tuple((name, _text(item.get(name))) for name in spec.extra_prompt_fields if _text(item.get(name)))
        adult = bool(spec.adult_groups and group in spec.adult_groups) or bool(
            spec.adult_categories and category in spec.adult_categories
        )
        search_text = " ".join(
            filter(
                None,
                (
                    _text(item.get("检索")),
                    _text(item.get(spec.title_field)),
                    group,
                    category,
                    detail,
                    " ".join(tags),
                    keywords,
                    item_id,
                ),
            )
        ).lower()
        return ReferenceRecord(
            id=item_id,
            title=_text(item.get(spec.title_field)),
            group=group,
            category=category,
            detail=detail,
            tags=tags,
            keywords=keywords,
            width=_as_int(item.get("宽")),
            height=_as_int(item.get("高")),
            thumb_path=_text(item.get(spec.thumb_field)),
            image_path=_text(item.get(spec.image_field)),
            curated=bool(item.get(spec.curated_field)) if spec.curated_field else False,
            hot=bool(item.get(spec.hot_field)) if spec.hot_field else False,
            adult=adult,
            search_text=search_text,
            extra=extra,
        )

    def _normalize(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            raw = raw.get("items") or raw.get("records") or []
        if not isinstance(raw, list):
            raise ReferenceError(f"{self.spec.name}数据格式异常", status_code=502, error_type="upstream_error")
        return [item for item in raw if isinstance(item, dict)]

    def _fetch_json(self, url: str) -> Any:
        settings = get_settings()
        timeout = max(5, settings.references_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > _MAX_SOURCE_BYTES:
                raise ReferenceError(f"{self.spec.name}元数据过大", status_code=502, error_type="upstream_error")
            return response.json()

    def _cache_path(self) -> Path:
        database = Path(get_settings().database_path).resolve()
        return database.parent / "cache" / "references" / f"{self.spec.kind}.json"

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            spec = self.spec
            payload = {
                "synced_at": self._synced_at,
                "meta": self._meta,
                "records": [
                    {
                        "id": record.id,
                        spec.title_field: record.title,
                        spec.group_field: record.group,
                        spec.category_field or "_": record.category,
                        spec.detail_field or "_": record.detail,
                        spec.tag_field or "_": list(record.tags),
                        spec.keyword_field or "_": record.keywords,
                        "宽": record.width,
                        "高": record.height,
                        spec.thumb_field: record.thumb_path,
                        spec.image_field: record.image_path,
                        spec.curated_field or "_": record.curated,
                        spec.hot_field or "_": record.hot,
                        "检索": record.search_text,
                        **dict(record.extra),
                    }
                    for record in self._records
                ],
            }
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError as error:
            logger.warning("%s 写入缓存失败: %s", self.spec.name, error)

    def _iso(self, timestamp: float) -> str:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _scope(value: str) -> str:
    value = (value or "all").strip()
    return value if value in SCOPES else "all"


def _scope_match(record: ReferenceRecord, scope: str, spec: ReferenceSpec) -> bool:
    if not spec.curated_field or scope in {"", "all"}:
        return True
    if scope == "featured":
        return record.curated or record.hot
    if scope == "curated":
        return record.curated
    if scope == "hot":
        return record.hot
    return True


def _ordered(preferred: list[str], values: list[str]) -> list[str]:
    present = set(values)
    ordered = [item for item in preferred if item in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


_sources: dict[str, ReferenceSource] = {}


def get_source(kind: str) -> ReferenceSource:
    spec = get_spec(kind)
    if spec is None:
        raise ReferenceError(f"未知参考图库：{kind}", status_code=404, error_type="not_found")
    if kind not in _sources:
        _sources[kind] = ReferenceSource(spec)
    return _sources[kind]


def set_source(kind: str, source: ReferenceSource | None) -> None:
    if source is None:
        _sources.pop(kind, None)
    else:
        _sources[kind] = source


def reset_sources_for_tests() -> None:
    _sources.clear()
