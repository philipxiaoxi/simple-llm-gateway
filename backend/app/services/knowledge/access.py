from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeBase, KnowledgeBaseKey, McpKey
from app.services.knowledge.errors import KnowledgeError

SCOPE_PUBLIC = "public"
SCOPE_RESTRICTED = "restricted"
SCOPE_PRIVATE = "private"
VALID_SCOPES = (SCOPE_PUBLIC, SCOPE_RESTRICTED, SCOPE_PRIVATE)


@dataclass(frozen=True)
class Access:
    """一次知识库访问的调用方身份。

    - admin：管理后台，可见全部
    - key：下游 MCP Key，受 scope 约束
    - system：内部任务（如采集 worker），等同于 admin
    """

    admin: bool = False
    mcp_key: McpKey | None = None

    @classmethod
    def for_admin(cls) -> Access:
        return cls(admin=True)

    @classmethod
    def for_system(cls) -> Access:
        return cls(admin=True)

    @classmethod
    def for_key(cls, mcp_key: McpKey) -> Access:
        return cls(admin=False, mcp_key=mcp_key)

    def allowed_kb_ids(self, db: Session) -> set[str] | None:
        """返回可见 kb_id 集合；None 表示不受限（admin）。"""
        if self.admin:
            return None
        base_ids = set(db.scalars(select(KnowledgeBase.id)).all())
        public_ids = set(
            db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.scope == SCOPE_PUBLIC)).all()
        )
        restricted_ids: set[str] = set()
        if self.mcp_key is not None:
            restricted_ids = set(
                db.scalars(
                    select(KnowledgeBaseKey.kb_id).where(KnowledgeBaseKey.mcp_key_id == self.mcp_key.id)
                ).all()
            )
        return (public_ids | restricted_ids) & base_ids


def assert_scope_valid(scope: str) -> str:
    normalized = (scope or "").strip().lower()
    if normalized not in VALID_SCOPES:
        raise KnowledgeError("scope 必须是 public / restricted / private")
    return normalized


def can_access(db: Session, access: Access, base: KnowledgeBase) -> bool:
    if access.admin:
        return True
    if base.scope == SCOPE_PUBLIC:
        return True
    if base.scope == SCOPE_RESTRICTED and access.mcp_key is not None:
        link = db.scalar(
            select(KnowledgeBaseKey.id).where(
                KnowledgeBaseKey.kb_id == base.id,
                KnowledgeBaseKey.mcp_key_id == access.mcp_key.id,
            )
        )
        return link is not None
    return False


def assert_can_access(db: Session, access: Access, base: KnowledgeBase) -> None:
    if can_access(db, access, base):
        return
    # 对下游不暴露知识库是否存在
    raise KnowledgeError("知识库不存在或无权访问", status_code=404, error_type="not_found")


def set_allowed_keys(db: Session, kb_id: str, mcp_key_ids: list[int]) -> None:
    db.query(KnowledgeBaseKey).filter(KnowledgeBaseKey.kb_id == kb_id).delete(synchronize_session=False)
    seen: set[int] = set()
    for key_id in mcp_key_ids:
        if key_id in seen:
            continue
        seen.add(key_id)
        db.add(KnowledgeBaseKey(kb_id=kb_id, mcp_key_id=key_id))


def allowed_key_ids(db: Session, kb_id: str) -> list[int]:
    return list(db.scalars(select(KnowledgeBaseKey.mcp_key_id).where(KnowledgeBaseKey.kb_id == kb_id)).all())
