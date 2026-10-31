from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.source import SCOPES, get_source
from app.capabilities.reference.specs import COSTUME, PORTRAIT, POSE, ReferenceSpec
from app.deps import get_current_admin


def _raise(error: ReferenceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


def _build_router(spec: ReferenceSpec) -> APIRouter:
    router = APIRouter(
        prefix=f"/api/admin/mcp/{spec.route}",
        tags=[f"admin-{spec.kind}"],
        dependencies=[Depends(get_current_admin)],
    )

    @router.get("", name=f"{spec.kind}_list", operation_id=f"{spec.kind}_list")
    def list_items(
        q: str = Query(default=""),
        group: str = Query(default=""),
        category: str = Query(default=""),
        tag: str = Query(default=""),
        detail: str = Query(default=""),
        scope: str = Query(default="all"),
        include_adult: bool = Query(default=False),
        limit: int = Query(default=40, ge=1, le=60),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail=f"未知范围：{scope}")
        try:
            return get_source(spec.kind).search(
                query=q,
                group=group,
                category=category,
                tag=tag,
                detail=detail,
                scope=scope,
                include_adult=include_adult,
                limit=limit,
                offset=offset,
            )
        except ReferenceError as error:
            _raise(error)

    @router.get("/meta", name=f"{spec.kind}_meta", operation_id=f"{spec.kind}_meta")
    def item_meta(include_adult: bool = Query(default=False)) -> dict[str, Any]:
        try:
            return get_source(spec.kind).meta(include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    @router.post("/refresh", name=f"{spec.kind}_refresh", operation_id=f"{spec.kind}_refresh")
    def refresh_items(include_adult: bool = Query(default=False)) -> dict[str, Any]:
        try:
            return get_source(spec.kind).refresh(include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    @router.get("/{reference_id}", name=f"{spec.kind}_detail", operation_id=f"{spec.kind}_detail")
    def item_detail(
        reference_id: str,
        include_adult: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return get_source(spec.kind).get(reference_id, include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    return router


portrait_router = _build_router(PORTRAIT)
pose_router = _build_router(POSE)
costume_router = _build_router(COSTUME)
