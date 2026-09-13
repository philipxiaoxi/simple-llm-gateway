from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import Skill, SkillBundle, SkillBundleMember
from app.services.skills import SkillError, build_skill_zip, skill_to_dict

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class BundleError(Exception):
    pass


@dataclass
class MemberView:
    skill_id: int
    added_at: object
    missing: bool
    skill: Skill | None


def _clean_name(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise BundleError("名称必填")
    return cleaned[:128]


def _clean_description(value: str | None) -> str:
    return (value or "").strip()[:2000]


def bundle_slug(bundle: SkillBundle) -> str:
    slug = _SLUG_RE.sub("-", bundle.name.strip().lower()).strip("-")
    base = (slug or "bundle")[:60]
    return f"{base}-{bundle.id}"


def member_count(db: Session, bundle_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(SkillBundleMember).where(SkillBundleMember.bundle_id == bundle_id)) or 0)


def bundle_to_dict(bundle: SkillBundle, *, count: int | None = None, db: Session | None = None) -> dict:
    total = count
    if total is None:
        if db is None:
            total = 0
        else:
            total = member_count(db, bundle.id)
    return {
        "id": bundle.id,
        "name": bundle.name,
        "description": bundle.description or "",
        "member_count": int(total or 0),
        "created_at": bundle.created_at,
        "updated_at": bundle.updated_at,
    }


def list_members(db: Session, bundle_id: int) -> list[MemberView]:
    rows = db.scalars(
        select(SkillBundleMember).where(SkillBundleMember.bundle_id == bundle_id).order_by(SkillBundleMember.id.asc())
    ).all()
    skill_ids = [row.skill_id for row in rows]
    skills = {}
    if skill_ids:
        for item in db.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all():
            skills[item.id] = item
    result: list[MemberView] = []
    for row in rows:
        skill = skills.get(row.skill_id)
        result.append(MemberView(skill_id=row.skill_id, added_at=row.created_at, missing=skill is None, skill=skill))
    return result


def member_to_dict(view: MemberView) -> dict:
    return {
        "skill_id": view.skill_id,
        "added_at": view.added_at,
        "missing": view.missing,
        "skill": skill_to_dict(view.skill) if view.skill is not None else None,
    }


def list_bundles(db: Session, *, q: str | None = None) -> list[tuple[SkillBundle, int]]:
    keyword = (q or "").strip()
    stmt = select(SkillBundle)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(SkillBundle.name.ilike(like), SkillBundle.description.ilike(like)))
    stmt = stmt.order_by(SkillBundle.updated_at.desc(), SkillBundle.id.desc())
    bundles = list(db.scalars(stmt).all())
    if not bundles:
        return []
    counts = {
        bundle_id: int(total or 0)
        for bundle_id, total in db.execute(
            select(SkillBundleMember.bundle_id, func.count())
            .where(SkillBundleMember.bundle_id.in_([item.id for item in bundles]))
            .group_by(SkillBundleMember.bundle_id)
        ).all()
    }
    return [(item, counts.get(item.id, 0)) for item in bundles]


def get_bundle(db: Session, bundle_id: int) -> SkillBundle:
    item = db.get(SkillBundle, bundle_id)
    if item is None:
        raise BundleError("组合包不存在")
    return item


def create_bundle(db: Session, *, name: str, description: str = "") -> SkillBundle:
    now = utcnow()
    item = SkillBundle(
        name=_clean_name(name),
        description=_clean_description(description),
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    return item


def update_bundle(db: Session, bundle: SkillBundle, *, name: str | None = None, description: str | None = None) -> SkillBundle:
    if name is not None:
        bundle.name = _clean_name(name)
    if description is not None:
        bundle.description = _clean_description(description)
    bundle.updated_at = utcnow()
    db.flush()
    return bundle


def delete_bundle(db: Session, bundle: SkillBundle) -> None:
    db.execute(delete(SkillBundleMember).where(SkillBundleMember.bundle_id == bundle.id))
    db.delete(bundle)
    db.flush()


def add_members(db: Session, bundle: SkillBundle, skill_ids: list[int]) -> tuple[int, list[tuple[int, str]]]:
    existing = {
        row.skill_id
        for row in db.scalars(select(SkillBundleMember).where(SkillBundleMember.bundle_id == bundle.id)).all()
    }
    added = 0
    skipped: list[tuple[int, str]] = []
    seen: set[int] = set()
    now = utcnow()
    for raw_id in skill_ids:
        try:
            skill_id = int(raw_id)
        except (TypeError, ValueError):
            skipped.append((0, "无效的 Skill ID"))
            continue
        if skill_id in seen:
            skipped.append((skill_id, "请求中重复"))
            continue
        seen.add(skill_id)
        if skill_id in existing:
            skipped.append((skill_id, "已在组合包中"))
            continue
        skill = db.get(Skill, skill_id)
        if skill is None:
            skipped.append((skill_id, "Skill 不存在"))
            continue
        db.add(SkillBundleMember(bundle_id=bundle.id, skill_id=skill_id, created_at=now))
        existing.add(skill_id)
        added += 1
    if added:
        bundle.updated_at = now
    db.flush()
    return added, skipped


def remove_member(db: Session, bundle: SkillBundle, skill_id: int) -> None:
    row = db.scalar(
        select(SkillBundleMember).where(
            SkillBundleMember.bundle_id == bundle.id,
            SkillBundleMember.skill_id == skill_id,
        )
    )
    if row is None:
        raise BundleError("成员不存在")
    db.delete(row)
    bundle.updated_at = utcnow()
    db.flush()


def build_bundle_zip(db: Session, bundle: SkillBundle) -> bytes:
    members = db.scalars(
        select(SkillBundleMember).where(SkillBundleMember.bundle_id == bundle.id).order_by(SkillBundleMember.id.asc())
    ).all()
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            skill = db.get(Skill, member.skill_id)
            if skill is None:
                continue
            try:
                skill_zip = build_skill_zip(skill)
            except SkillError:
                continue
            with zipfile.ZipFile(io.BytesIO(skill_zip), "r") as source:
                for info in source.infolist():
                    if info.is_dir():
                        continue
                    archive.writestr(info.filename, source.read(info.filename))
                    written += 1
    if written == 0:
        raise BundleError("组合包没有可打包的 Skill")
    return buffer.getvalue()


def count_valid_members(db: Session, bundle_id: int) -> int:
    members = db.scalars(select(SkillBundleMember.skill_id).where(SkillBundleMember.bundle_id == bundle_id)).all()
    if not members:
        return 0
    return int(db.scalar(select(func.count()).select_from(Skill).where(Skill.id.in_(list(members)))) or 0)
