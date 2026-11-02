from __future__ import annotations

from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.capabilities.reference.assets import create_asset, delete_asset, parse_local_id
from app.capabilities.reference.catalog import ReferenceCatalog, resolve_file
from app.capabilities.reference.images import cached_image, image_token, image_token_valid, safe_image_path
from app.capabilities.reference.errors import ReferenceError
from app.capabilities.reference.source import SCOPES, get_source
from app.capabilities.reference.specs import COSTUME, PORTRAIT, POSE, ReferenceSpec
from app.db import get_db
from app.deps import get_current_admin
from app.models import ReferenceAsset


def _raise(error: ReferenceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


def _build_router(spec: ReferenceSpec) -> tuple[APIRouter, APIRouter]:
    router = APIRouter(
        prefix=f"/api/admin/mcp/{spec.route}",
        tags=[f"admin-{spec.kind}"],
        dependencies=[Depends(get_current_admin)],
    )
    media_router = APIRouter(
        prefix=f"/api/admin/mcp/{spec.route}",
        tags=[f"admin-{spec.kind}"],
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
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        if scope not in SCOPES:
            raise HTTPException(status_code=400, detail=f"未知范围：{scope}")
        try:
            return ReferenceCatalog(spec, get_source(spec.kind)).search(
                db,
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
    def item_meta(include_adult: bool = Query(default=False), db: Session = Depends(get_db)) -> dict[str, Any]:
        try:
            return ReferenceCatalog(spec, get_source(spec.kind)).meta(db, include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    @media_router.get("/images/{image_path:path}", name=f"{spec.kind}_image", operation_id=f"{spec.kind}_image")
    def upstream_image(image_path: str, token: str = Query(default="")) -> FileResponse:
        request_path = f"/api/admin/mcp/{spec.route}/images/{quote(safe_image_path(image_path), safe='/')}"
        if not image_token_valid(request_path, token):
            raise HTTPException(status_code=401, detail="需要有效的图片令牌")
        try:
            path, media_type = cached_image(spec.kind, safe_image_path(image_path))
        except ReferenceError as error:
            _raise(error)
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/refresh", name=f"{spec.kind}_refresh", operation_id=f"{spec.kind}_refresh")
    def refresh_items(include_adult: bool = Query(default=False)) -> dict[str, Any]:
        try:
            return get_source(spec.kind).refresh(include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    @router.post("", name=f"{spec.kind}_create", operation_id=f"{spec.kind}_create", status_code=201)
    async def create_item(
        file: UploadFile = File(...),
        title: str = Form(...),
        group: str = Form(default=""),
        category: str = Form(default=""),
        detail: str = Form(default=""),
        tags: str = Form(default=""),
        prompt: str = Form(default=""),
        adult: bool = Form(default=False),
        db: Session = Depends(get_db),
        admin=Depends(get_current_admin),
    ) -> dict[str, Any]:
        data = await file.read()
        try:
            asset = create_asset(
                db,
                spec,
                data=data,
                title=title,
                group=group,
                category=category,
                detail=detail,
                tags=[item.strip() for item in tags.split(",") if item.strip()],
                prompt=prompt,
                adult=adult,
                created_by=admin.id,
            )
            db.commit()
            return ReferenceCatalog(spec, get_source(spec.kind)).get(db, f"local:{asset.id}", include_adult=True)
        except ReferenceError as error:
            db.rollback()
            _raise(error)

    @router.delete("/{reference_id}", name=f"{spec.kind}_delete", operation_id=f"{spec.kind}_delete")
    def delete_item(reference_id: str, db: Session = Depends(get_db)) -> Response:
        try:
            delete_asset(db, spec, reference_id)
            db.commit()
        except ReferenceError as error:
            db.rollback()
            _raise(error)
        return Response(status_code=204)

    @media_router.get("/{reference_id}/file", name=f"{spec.kind}_file", operation_id=f"{spec.kind}_file")
    def item_file(
        reference_id: str,
        thumb: bool = Query(default=False),
        token: str = Query(default=""),
        db: Session = Depends(get_db),
    ) -> FileResponse:
        request_path = f"/api/admin/mcp/{spec.route}/{reference_id}/file"
        if not image_token_valid(request_path, token):
            raise HTTPException(status_code=401, detail="需要有效的图片令牌")
        asset_id = parse_local_id(f"local:{reference_id}")
        asset = db.get(ReferenceAsset, asset_id) if asset_id is not None else None
        if asset is None or asset.kind != spec.kind:
            raise HTTPException(status_code=404, detail="参考图不存在")
        try:
            path = resolve_file(asset, thumb=thumb)
        except ReferenceError as error:
            _raise(error)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    @router.get("/{reference_id}", name=f"{spec.kind}_detail", operation_id=f"{spec.kind}_detail")
    def item_detail(
        reference_id: str,
        include_adult: bool = Query(default=False),
        db: Session = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            return ReferenceCatalog(spec, get_source(spec.kind)).get(db, reference_id, include_adult=include_adult)
        except ReferenceError as error:
            _raise(error)

    return router, media_router


portrait_router, portrait_media_router = _build_router(PORTRAIT)
pose_router, pose_media_router = _build_router(POSE)
costume_router, costume_media_router = _build_router(COSTUME)
