from __future__ import annotations

from collections import Counter
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db import get_db
from app.deps import get_current_admin
from app.models import Skill, SkillClassificationSettings, UpstreamAccount
from app.schemas import (
    SkillCategoryCreate,
    SkillCategoryListOut,
    SkillCategoryManageOut,
    SkillCategoryOut,
    SkillCategoryUpdate,
    SkillDetailOut,
    SkillListOut,
    SkillOut,
    SkillSkippedOut,
    SkillUpdate,
    SkillUploadOut,
    SkillAnalysisOut,
    SkillClassificationSettingsOut,
    SkillClassificationSettingsUpdate,
)
from app.services.skills import (
    MAX_FILE_COUNT,
    MAX_UPLOAD_BYTES,
    SkillError,
    UploadedFile,
    build_skill_zip,
    analyze_skill_with_gateway,
    category_to_dict,
    classify_with_gateway,
    create_skill_category,
    delete_skill_category,
    delete_skill_files,
    import_uploaded_files,
    list_category_names,
    list_category_rules,
    list_managed_categories,
    get_classification_settings,
    list_skill_files,
    normalize_category,
    persist_parsed_skills,
    replace_skill_with_parsed,
    read_skill_file,
    skill_to_dict,
    update_skill_category,
)

router = APIRouter(prefix="/api/admin/skills", tags=["admin-skills"], dependencies=[Depends(get_current_admin)])


async def _read_uploads(files: list[UploadFile]) -> list[UploadedFile]:
    if len(files) > MAX_FILE_COUNT:
        raise SkillError("文件数量过多")
    uploaded: list[UploadedFile] = []
    total = 0
    for item in files:
        remaining = MAX_UPLOAD_BYTES - total
        data = await item.read(remaining + 1)
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise SkillError("上传体积超过 20MB")
        uploaded.append(UploadedFile(path=item.filename or "upload.bin", data=data))
    return uploaded


@router.post("/upload", response_model=SkillUploadOut)
async def upload_skills(
    files: list[UploadFile] = File(default_factory=list),
    category: str = Form(default="自动识别"),
    db: Session = Depends(get_db),
) -> SkillUploadOut:
    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件或目录")
    existing = set(db.scalars(select(Skill.slug)).all())
    try:
        uploaded = await _read_uploads(files)
        parsed, skipped = import_uploaded_files(
            uploaded,
            category=category,
            source_name=files[0].filename if len(files) == 1 else "directory",
            existing_slugs=existing,
            category_names=list_category_names(db),
            category_rules=list_category_rules(db),
        )
        await classify_with_gateway(db, parsed, list_category_names(db), list_category_rules(db))
        created = persist_parsed_skills(db, parsed)
    except SkillError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SkillUploadOut(
        items=[SkillOut(**skill_to_dict(item)) for item in created],
        created=len(created),
        skipped=[SkillSkippedOut(name=name, reason=reason) for name, reason in skipped],
    )


@router.post("/{skill_id}/replace", response_model=SkillOut)
async def replace_skill(
    skill_id: int,
    files: list[UploadFile] = File(default_factory=list),
    category: str = Form(default="自动识别"),
    db: Session = Depends(get_db),
) -> SkillOut:
    if not files:
        raise HTTPException(status_code=400, detail="请选择要覆盖的 Skill 目录或压缩包")
    item = _get_skill(db, skill_id)
    try:
        uploaded = await _read_uploads(files)
        parsed, skipped = import_uploaded_files(
            uploaded,
            category=category,
            source_name=files[0].filename if len(files) == 1 else "directory",
            existing_slugs=set(db.scalars(select(Skill.slug)).all()) - {item.slug},
            category_names=list_category_names(db),
            category_rules=list_category_rules(db),
        )
        if skipped or len(parsed) != 1:
            reason = skipped[0][1] if skipped else "单个覆盖只能包含一个 Skill"
            raise SkillError(reason)
        await classify_with_gateway(db, parsed, list_category_names(db), list_category_rules(db))
        updated = replace_skill_with_parsed(db, item, parsed[0])
    except SkillError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SkillOut(**skill_to_dict(updated))


@router.post("/bulk-update", response_model=SkillUploadOut)
async def bulk_update_skills(
    files: list[UploadFile] = File(default_factory=list),
    category: str = Form(default="自动识别"),
    db: Session = Depends(get_db),
) -> SkillUploadOut:
    if not files:
        raise HTTPException(status_code=400, detail="请选择要批量更新的目录或压缩包")
    try:
        uploaded = await _read_uploads(files)
        parsed, skipped = import_uploaded_files(
            uploaded,
            category=category,
            source_name=files[0].filename if len(files) == 1 else "directory",
            existing_slugs=set(),
            category_names=list_category_names(db),
            category_rules=list_category_rules(db),
        )
        await classify_with_gateway(db, parsed, list_category_names(db), list_category_rules(db))
        existing = {item.slug: item for item in db.scalars(select(Skill)).all()}
        updated: list[Skill] = []
        seen: set[str] = set()
        for item in parsed:
            if item.slug in seen:
                skipped.append((item.slug, "批量文件中存在重复 slug"))
                continue
            seen.add(item.slug)
            target = existing.get(item.slug)
            if target is None:
                skipped.append((item.slug, "未找到同 slug 的已有 Skill"))
                continue
            updated.append(replace_skill_with_parsed(db, target, item))
    except SkillError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SkillUploadOut(
        items=[SkillOut(**skill_to_dict(item)) for item in updated],
        created=len(updated),
        skipped=[SkillSkippedOut(name=name, reason=reason) for name, reason in skipped],
    )


@router.get("/classification-settings", response_model=SkillClassificationSettingsOut)
def get_classification_config(db: Session = Depends(get_db)) -> SkillClassificationSettingsOut:
    item = get_classification_settings(db)
    account = db.get(UpstreamAccount, item.account_id) if item.account_id else None
    report_account = db.get(UpstreamAccount, item.report_account_id) if item.report_account_id else None
    return SkillClassificationSettingsOut(
        account_id=item.account_id,
        account_name=account.name if account else None,
        model=item.model,
        enabled=item.enabled,
        report_account_id=item.report_account_id,
        report_account_name=report_account.name if report_account else None,
        report_model=item.report_model,
        report_enabled=item.report_enabled,
    )


@router.put("/classification-settings", response_model=SkillClassificationSettingsOut)
def update_classification_config(
    payload: SkillClassificationSettingsUpdate,
    db: Session = Depends(get_db),
) -> SkillClassificationSettingsOut:
    item = get_classification_settings(db)
    account = db.get(UpstreamAccount, payload.account_id) if payload.account_id else None
    report_account = db.get(UpstreamAccount, payload.report_account_id) if payload.report_account_id else None
    if payload.account_id and account is None:
        raise HTTPException(status_code=400, detail="指定的账号不存在")
    if payload.report_account_id and report_account is None:
        raise HTTPException(status_code=400, detail="指定的报告账号不存在")
    item.account_id = payload.account_id
    item.model = (payload.model or "").strip() or None
    item.enabled = bool(payload.enabled and account is not None)
    item.report_account_id = payload.report_account_id
    item.report_model = (payload.report_model or "").strip() or None
    item.report_enabled = bool(payload.report_enabled and report_account is not None)
    db.flush()
    return SkillClassificationSettingsOut(
        account_id=item.account_id,
        account_name=account.name if account else None,
        model=item.model,
        enabled=item.enabled,
        report_account_id=item.report_account_id,
        report_account_name=report_account.name if report_account else None,
        report_model=item.report_model,
        report_enabled=item.report_enabled,
    )


@router.get("/list", response_model=SkillListOut)
@router.get("", response_model=SkillListOut)
def list_skills(
    q: str = Query(default=""),
    category: str = Query(default=""),
    db: Session = Depends(get_db),
) -> SkillListOut:
    rows = list(db.scalars(select(Skill).order_by(Skill.updated_at.desc(), Skill.id.desc())).all())
    counts = Counter(row.category for row in rows)
    keyword = q.strip().lower()
    filtered = rows
    if category and category != "全部":
        filtered = [row for row in filtered if row.category == category]
    if keyword:
        filtered = [
            row
            for row in filtered
            if keyword in row.name.lower()
            or keyword in row.slug.lower()
            or keyword in (row.description or "").lower()
            or keyword in (row.author or "").lower()
        ]
    managed = list_managed_categories(db)
    return SkillListOut(
        items=[SkillOut(**skill_to_dict(row)) for row in filtered],
        total=len(filtered),
        categories=[SkillCategoryOut(name="全部", count=len(rows))]
        + [SkillCategoryOut(name=item.name, count=counts.get(item.name, 0)) for item in managed],
    )


@router.get("/categories", response_model=SkillCategoryListOut)
def list_skill_categories(db: Session = Depends(get_db)) -> SkillCategoryListOut:
    rows = list(db.scalars(select(Skill)).all())
    counts = Counter(row.category for row in rows)
    return SkillCategoryListOut(
        items=[SkillCategoryManageOut(**category_to_dict(item, counts.get(item.name, 0))) for item in list_managed_categories(db)]
    )


@router.post("/categories", response_model=SkillCategoryManageOut)
def create_category(payload: SkillCategoryCreate, db: Session = Depends(get_db)) -> SkillCategoryManageOut:
    try:
        item = create_skill_category(db, payload.name, payload.keywords)
    except SkillError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return SkillCategoryManageOut(**category_to_dict(item, 0))


@router.patch("/categories/{category_id}", response_model=SkillCategoryManageOut)
def update_category(category_id: int, payload: SkillCategoryUpdate, db: Session = Depends(get_db)) -> SkillCategoryManageOut:
    try:
        item = update_skill_category(
            db,
            category_id,
            name=payload.name,
            keywords=payload.keywords,
            sort_order=payload.sort_order,
        )
    except SkillError as error:
        status = 404 if str(error) == "分类不存在" else 400
        raise HTTPException(status_code=status, detail=str(error)) from error
    count = len(list(db.scalars(select(Skill).where(Skill.category == item.name)).all()))
    return SkillCategoryManageOut(**category_to_dict(item, count))


@router.delete("/categories/{category_id}")
def remove_category(category_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        delete_skill_category(db, category_id)
    except SkillError as error:
        status = 404 if str(error) == "分类不存在" else 400
        raise HTTPException(status_code=status, detail=str(error)) from error
    return {"ok": True}


@router.get("/{skill_id}", response_model=SkillDetailOut)
def get_skill(skill_id: int, db: Session = Depends(get_db)) -> SkillDetailOut:
    item = _get_skill(db, skill_id)
    payload = skill_to_dict(item)
    payload["skill_md"] = item.skill_md
    payload["files"] = list_skill_files(item)
    payload["analysis"] = json.loads(item.analysis_json) if item.analysis_json else None
    payload["analysis_generated_at"] = item.analysis_generated_at
    return SkillDetailOut(**payload)


@router.post("/{skill_id}/analysis", response_model=SkillAnalysisOut)
async def analyze_skill(skill_id: int, db: Session = Depends(get_db)) -> SkillAnalysisOut:
    item = _get_skill(db, skill_id)
    report = await analyze_skill_with_gateway(db, item)
    if report is None:
        raise HTTPException(status_code=400, detail="请先在 Skills 的 AI 配置中配置并启用分析报告账号和模型")
    item.analysis_json = json.dumps(report, ensure_ascii=False)
    item.analysis_generated_at = utcnow()
    db.commit()
    return SkillAnalysisOut(**report)


@router.patch("/{skill_id}", response_model=SkillOut)
def update_skill(skill_id: int, payload: SkillUpdate, db: Session = Depends(get_db)) -> SkillOut:
    item = _get_skill(db, skill_id)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        item.name = name[:128]
    if payload.description is not None:
        item.description = payload.description.strip()[:2000]
    if payload.category is not None:
        item.category = normalize_category(payload.category, list_category_names(db))
    item.updated_at = utcnow()
    db.flush()
    return SkillOut(**skill_to_dict(item))


@router.delete("/{skill_id}")
def delete_skill(skill_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    item = _get_skill(db, skill_id)
    delete_skill_files(item)
    db.delete(item)
    return {"ok": True}


@router.get("/{skill_id}/download")
def download_skill(skill_id: int, db: Session = Depends(get_db)) -> Response:
    item = _get_skill(db, skill_id)
    try:
        payload = build_skill_zip(item)
    except SkillError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    filename = f"{item.slug}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{skill_id}/files/{file_path:path}")
def download_skill_file(skill_id: int, file_path: str, db: Session = Depends(get_db)) -> Response:
    item = _get_skill(db, skill_id)
    try:
        payload, filename = read_skill_file(item, file_path)
    except SkillError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _get_skill(db: Session, skill_id: int) -> Skill:
    item = db.get(Skill, skill_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return item
