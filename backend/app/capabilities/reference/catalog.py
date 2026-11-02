"""合并本地参考与上游参考的查询入口。"""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy.orm import Session

from app.capabilities.reference.assets import file_for, list_assets, local_id, to_record
from app.capabilities.reference.images import image_token
from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.source import SCOPES, ReferenceRecord, ReferenceSource
from app.capabilities.reference.specs import ReferenceSpec
from app.config import get_settings
from app.models import ReferenceAsset


class ReferenceCatalog:
    def __init__(self, spec: ReferenceSpec, source: ReferenceSource) -> None:
        self.spec = spec
        self.source = source

    def search(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        return self._merged(db, **kwargs)

    def meta(self, db: Session, *, include_adult: bool = False) -> dict[str, Any]:
        result = self._merged(db, include_adult=include_adult, limit=1, offset=0)
        return {key: value for key, value in result.items() if key not in {"items", "limit", "offset"}}

    def get(self, db: Session, reference_id: str, *, include_adult: bool = False) -> dict[str, Any]:
        record = self._find(db, reference_id)
        if record is None or (record.adult and not include_adult):
            raise ReferenceError("参考图不存在", status_code=404, error_type="not_found")
        return {**self.source._envelope(), "item": self._item(record)}

    def random(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        matched = self._filter(self._records(db), **kwargs)
        if not matched:
            raise ReferenceError("没有匹配的参考图", status_code=404, error_type="not_found")
        return {**self.source._envelope(), "item": self._item(random.choice(matched))}

    def file_asset(self, db: Session, reference_id: str) -> tuple[ReferenceAsset, Any]:
        asset_id = reference_id.removeprefix("local:")
        if not asset_id.isdigit():
            raise ReferenceError("参考图不存在", status_code=404, error_type="not_found")
        asset = db.get(ReferenceAsset, int(asset_id))
        if asset is None or asset.kind != self.spec.kind:
            raise ReferenceError("参考图不存在", status_code=404, error_type="not_found")
        return asset, asset

    def _merged(self, db: Session, **kwargs: Any) -> dict[str, Any]:
        include_adult = bool(kwargs.get("include_adult"))
        limit = max(1, min(int(kwargs.get("limit") or 40), get_settings().references_max_results))
        offset = max(0, int(kwargs.get("offset") or 0))
        records = self._records(db)
        filters = dict(kwargs)
        filters.pop("limit", None)
        filters.pop("offset", None)
        visible = self._filter(records, **filters)
        page = visible[offset : offset + limit]
        hidden_filters = dict(filters)
        hidden_filters["include_adult"] = False
        hidden = len(self._filter(records, **{**filters, "include_adult": True})) - len(self._filter(records, **hidden_filters))
        return {
            **self.source._envelope(),
            "items": [self._item(record) for record in page],
            "total": len(visible),
            "hidden_adult": max(0, hidden),
            "limit": limit,
            "offset": offset,
            "facets": self._facets(db, include_adult=include_adult),
        }

    def _records(self, db: Session) -> list[ReferenceRecord]:
        local = [to_record(asset, self.spec) for asset in list_assets(db, self.spec.kind)]
        return [*local, *self._upstream_records()]

    def _filter(self, records: list[ReferenceRecord], **kwargs: Any) -> list[ReferenceRecord]:
        tokens = [token for token in str(kwargs.get("query") or "").lower().split() if token]
        group = str(kwargs.get("group") or "")
        category = str(kwargs.get("category") or "")
        tag = str(kwargs.get("tag") or "")
        detail = str(kwargs.get("detail") or "")
        scope = str(kwargs.get("scope") or "all")
        include_adult = bool(kwargs.get("include_adult"))
        if scope not in SCOPES:
            scope = "all"
        return [
            record
            for record in records
            if self.source._matches(record, tokens, group, category, tag, detail)
            and (include_adult or not record.adult)
            and self._scope_match(record, scope)
        ]

    def _upstream_records(self) -> list[ReferenceRecord]:
        self.source._ensure()
        return list(self.source._records)

    def _find(self, db: Session, reference_id: str) -> ReferenceRecord | None:
        target = (reference_id or "").strip()
        for asset in list_assets(db, self.spec.kind):
            if local_id(asset.id) == target:
                return to_record(asset, self.spec)
        self.source._ensure()
        return self.source._by_id.get(target)

    def _facets(self, db: Session, *, include_adult: bool) -> dict[str, list[str]]:
        upstream = self.source.meta(include_adult=include_adult)["facets"]
        local = [
            record
            for asset in list_assets(db, self.spec.kind)
            if include_adult or not (record := to_record(asset, self.spec)).adult
        ]
        return {
            "groups": _merge(upstream["groups"], [record.group for record in local]),
            "categories": _merge(upstream["categories"], [record.category for record in local]),
            "details": _merge(upstream["details"], [record.detail for record in local]),
            "tags": _merge(upstream["tags"], [tag for record in local for tag in record.tags]),
        }

    def _item(self, record: ReferenceRecord) -> dict[str, Any]:
        item = self.source._item(record)
        if record.origin == "local":
            asset_id = record.id.removeprefix("local:")
            file_path = f"/api/admin/mcp/{self.spec.route}/{asset_id}/file"
            token = image_token(file_path)
            item["thumb_url"] = f"{file_path}?thumb=true&token={token}"
            item["image_url"] = f"{file_path}?token={token}"
            return item
        item["thumb_url"] = self.source._image_url(record.thumb_path, thumb=True)
        item["image_url"] = self.source._image_url(record.image_path, thumb=False)
        return item

    def _scope_match(self, record: ReferenceRecord, scope: str) -> bool:
        if scope == "featured":
            return record.curated or record.hot
        if scope == "curated":
            return record.curated
        if scope == "hot":
            return record.hot
        return True


def _merge(base: list[str], extra: list[str]) -> list[str]:
    result = [item for item in base if item]
    for item in extra:
        if item and item not in result:
            result.append(item)
    return result


def resolve_file(asset: ReferenceAsset, *, thumb: bool):
    return file_for(asset, thumb=thumb)
