from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin
from app.schemas import (
    BundleMemberOut,
    BundleMemberSkippedOut,
    BundleMembersAdd,
    BundleMembersAddOut,
    SkillBundleCreate,
    SkillBundleDetailOut,
    SkillBundleListOut,
    SkillBundleOut,
    SkillBundleUpdate,
)
from app.services.skill_bundles import (
    BundleError,
    add_members,
    build_bundle_zip,
    bundle_slug,
    bundle_to_dict,
    count_valid_members,
    create_bundle,
    delete_bundle,
    get_bundle,
    list_bundles,
    list_members,
    member_to_dict,
    remove_member,
    update_bundle,
)

router = APIRouter(
    prefix="/api/admin/skill-bundles",
    tags=["admin-skill-bundles"],
    dependencies=[Depends(get_current_admin)],
)
download_router = APIRouter(prefix="/api/skill-bundles", tags=["skill-bundle-downloads"])
_DOWNLOAD_TOKEN_TTL = timedelta(minutes=5)
_DOWNLOAD_TOKEN_SCOPE = "skill_bundle"


def _http_error(error: BundleError, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(error))


def _detail_out(db: Session, bundle) -> SkillBundleDetailOut:
    members = [BundleMemberOut(**member_to_dict(item)) for item in list_members(db, bundle.id)]
    base = bundle_to_dict(bundle, count=len(members))
    return SkillBundleDetailOut(**base, members=members)


@router.get("", response_model=SkillBundleListOut)
@router.get("/", response_model=SkillBundleListOut)
def list_skill_bundles(q: str | None = None, db: Session = Depends(get_db)) -> SkillBundleListOut:
    rows = list_bundles(db, q=q)
    items = [SkillBundleOut(**bundle_to_dict(bundle, count=count)) for bundle, count in rows]
    return SkillBundleListOut(items=items, total=len(items))


@router.post("", response_model=SkillBundleOut)
@router.post("/", response_model=SkillBundleOut)
def create_skill_bundle(payload: SkillBundleCreate, db: Session = Depends(get_db)) -> SkillBundleOut:
    try:
        bundle = create_bundle(db, name=payload.name, description=payload.description)
    except BundleError as error:
        raise _http_error(error) from error
    return SkillBundleOut(**bundle_to_dict(bundle, count=0))


@router.get("/{bundle_id}", response_model=SkillBundleDetailOut)
def get_skill_bundle(bundle_id: int, db: Session = Depends(get_db)) -> SkillBundleDetailOut:
    try:
        bundle = get_bundle(db, bundle_id)
    except BundleError as error:
        raise _http_error(error, status_code=404) from error
    return _detail_out(db, bundle)


@router.patch("/{bundle_id}", response_model=SkillBundleOut)
def patch_skill_bundle(bundle_id: int, payload: SkillBundleUpdate, db: Session = Depends(get_db)) -> SkillBundleOut:
    try:
        bundle = get_bundle(db, bundle_id)
        bundle = update_bundle(db, bundle, name=payload.name, description=payload.description)
    except BundleError as error:
        status = 404 if str(error) == "组合包不存在" else 400
        raise _http_error(error, status_code=status) from error
    return SkillBundleOut(**bundle_to_dict(bundle, db=db))


@router.delete("/{bundle_id}")
def delete_skill_bundle(bundle_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        bundle = get_bundle(db, bundle_id)
        delete_bundle(db, bundle)
    except BundleError as error:
        raise _http_error(error, status_code=404) from error
    return {"ok": True}


@router.post("/{bundle_id}/members", response_model=BundleMembersAddOut)
def add_bundle_members(bundle_id: int, payload: BundleMembersAdd, db: Session = Depends(get_db)) -> BundleMembersAddOut:
    try:
        bundle = get_bundle(db, bundle_id)
        added, skipped = add_members(db, bundle, payload.skill_ids)
    except BundleError as error:
        raise _http_error(error, status_code=404) from error
    members = [BundleMemberOut(**member_to_dict(item)) for item in list_members(db, bundle.id)]
    return BundleMembersAddOut(
        added=added,
        skipped=[BundleMemberSkippedOut(skill_id=skill_id, reason=reason) for skill_id, reason in skipped],
        members=members,
    )


@router.delete("/{bundle_id}/members/{skill_id}")
def delete_bundle_member(bundle_id: int, skill_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        bundle = get_bundle(db, bundle_id)
        remove_member(db, bundle, skill_id)
    except BundleError as error:
        status = 404 if "不存在" in str(error) else 400
        raise _http_error(error, status_code=status) from error
    return {"ok": True}


@router.get("/{bundle_id}/download")
def download_bundle(bundle_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        bundle = get_bundle(db, bundle_id)
        archive = build_bundle_zip(db, bundle)
    except BundleError as error:
        status = 404 if str(error) == "组合包不存在" else 400
        raise _http_error(error, status_code=status) from error
    filename = f"{bundle_slug(bundle)}.zip"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/{bundle_id}/download-url")
def create_download_url(
    bundle_id: int,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    try:
        bundle = get_bundle(db, bundle_id)
        if count_valid_members(db, bundle.id) <= 0:
            raise BundleError("组合包没有可打包的 Skill")
    except BundleError as error:
        status = 404 if str(error) == "组合包不存在" else 400
        raise _http_error(error, status_code=status) from error
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "scope": _DOWNLOAD_TOKEN_SCOPE,
            "bundle_id": bundle.id,
            "sub": admin.username,
            "ver": int(admin.token_version or 0),
            "iat": now,
            "exp": now + _DOWNLOAD_TOKEN_TTL,
        },
        get_settings().app_secret_key,
        algorithm="HS256",
    )
    return {
        "url": f"/api/skill-bundles/{bundle.id}/download?token={token}",
        "expiresInSeconds": int(_DOWNLOAD_TOKEN_TTL.total_seconds()),
    }


@download_router.get("/{bundle_id}/download")
def download_bundle_with_token(bundle_id: int, token: str, db: Session = Depends(get_db)) -> Response:
    try:
        claims = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="下载链接已失效，请重新生成") from error
    if claims.get("scope") != _DOWNLOAD_TOKEN_SCOPE or claims.get("bundle_id") != bundle_id:
        raise HTTPException(status_code=403, detail="下载链接无效")
    admin = db.scalar(select(Admin).where(Admin.username == claims.get("sub")))
    token_version = claims.get("ver")
    if admin is None or not isinstance(token_version, int) or int(admin.token_version or 0) != token_version:
        raise HTTPException(status_code=401, detail="下载链接已失效，请重新生成")
    try:
        bundle = get_bundle(db, bundle_id)
        archive = build_bundle_zip(db, bundle)
    except BundleError as error:
        status = 404 if str(error) == "组合包不存在" else 400
        raise _http_error(error, status_code=status) from error
    filename = f"{bundle_slug(bundle)}.zip"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
