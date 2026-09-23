from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import KnowledgeBase, RequestLog, SkillClassificationSettings, UpstreamAccount, VoiceRoom
from app.schemas import AccountUsageItemOut, AccountUsageOut
from app.services.key_models import bound_accounts


def collect_account_usage(db: Session, account: UpstreamAccount) -> AccountUsageOut:
    items: list[AccountUsageItemOut] = []

    affected_keys = {link.api_key for link in account.key_links if link.api_key is not None}
    affected_keys.update(key for key in account.api_keys if key is not None)
    for api_key in sorted(affected_keys, key=lambda item: item.id):
        remaining = [bound for bound in bound_accounts(api_key) if bound.id != account.id]
        if remaining:
            names = "、".join(item.name for item in remaining)
            action = f"将解绑本账号，Key 继续使用 {names}"
            severity = "info"
        else:
            action = "将解绑并停用，因为没有其它上游账号"
            severity = "warning"
        items.append(
            AccountUsageItemOut(
                kind="api_key",
                id=str(api_key.id),
                name=api_key.name,
                detail=f"状态 {api_key.status}",
                action=action,
                severity=severity,
            )
        )

    settings_rows = db.scalars(
        select(SkillClassificationSettings).where(
            or_(
                SkillClassificationSettings.account_id == account.id,
                SkillClassificationSettings.report_account_id == account.id,
            )
        )
    ).all()
    for settings in settings_rows:
        if settings.account_id == account.id:
            enabled_text = "已启用" if settings.enabled else "未启用"
            model_text = settings.model or "未指定模型"
            items.append(
                AccountUsageItemOut(
                    kind="skill_classification",
                    id=str(settings.id),
                    name="Skill 自动分类",
                    detail=f"{enabled_text} · {model_text}",
                    action="将解除绑定并关闭自动分类",
                    severity="warning" if settings.enabled else "info",
                )
            )
        if settings.report_account_id == account.id:
            enabled_text = "已启用" if settings.report_enabled else "未启用"
            model_text = settings.report_model or "未指定模型"
            items.append(
                AccountUsageItemOut(
                    kind="skill_report",
                    id=str(settings.id),
                    name="Skill AI 分析报告",
                    detail=f"{enabled_text} · {model_text}",
                    action="将解除绑定并关闭分析报告",
                    severity="warning" if settings.report_enabled else "info",
                )
            )

    knowledge_bases = db.scalars(
        select(KnowledgeBase).where(KnowledgeBase.embedding_account_id == account.id).order_by(KnowledgeBase.name)
    ).all()
    for base in knowledge_bases:
        model_text = base.embedding_model or "未指定向量模型"
        items.append(
            AccountUsageItemOut(
                kind="knowledge_base",
                id=base.id,
                name=base.name,
                detail=f"知识库向量账号 · {model_text}",
                action="将解除向量账号绑定，后续入库需要重新选择账号",
                severity="warning",
            )
        )

    rooms = db.scalars(
        select(VoiceRoom).where(VoiceRoom.polish_account_id == account.id).order_by(VoiceRoom.name)
    ).all()
    for room in rooms:
        mode_text = room.polish_mode or "off"
        model_text = room.polish_model or "未指定模型"
        items.append(
            AccountUsageItemOut(
                kind="voice_room",
                id=room.room_id,
                name=room.name,
                detail=f"语音纠错 {mode_text} · {model_text}",
                action="将解除纠错账号绑定，语音纠错会暂停直到重新配置",
                severity="warning" if mode_text != "off" else "info",
            )
        )

    log_count = db.scalar(select(func.count()).select_from(RequestLog).where(RequestLog.account_id == account.id)) or 0
    if log_count:
        items.append(
            AccountUsageItemOut(
                kind="request_log",
                id=None,
                name=f"{log_count} 条请求日志",
                detail="历史调用记录",
                action="会保留账号名，日志不会删除",
                severity="info",
            )
        )

    if account.oauth_token is not None:
        items.append(
            AccountUsageItemOut(
                kind="oauth",
                id=None,
                name="OAuth 授权",
                detail="已保存上游授权凭证",
                action="授权凭证将一并删除，需要重新授权才能恢复",
                severity="warning",
            )
        )

    risks = [
        "账号及其凭证将被永久删除，无法恢复。",
        "之后不能再通过本账号向该上游发起请求。",
    ]
    if any(item.kind == "api_key" and item.severity == "warning" for item in items):
        risks.append("只绑定本账号的 API Key 会被解绑并停用。")
    if any(item.kind == "api_key" and item.severity == "info" for item in items):
        risks.append("多账号 API Key 会解绑本账号，继续使用其余上游。")
    if any(item.kind == "skill_classification" for item in items):
        risks.append("Skill 自动分类会关闭，上传后需要重新配置账号才会继续识别。")
    if any(item.kind == "skill_report" for item in items):
        risks.append("Skill AI 分析报告会关闭，详情页分析需要重新选择账号。")
    if any(item.kind == "knowledge_base" for item in items):
        risks.append("知识库会失去向量账号，新文档入库前需要重新绑定。")
    if any(item.kind == "voice_room" for item in items):
        risks.append("语音房纠错会失去上游账号，需要重新配置后才会继续纠错。")
    if any(item.kind == "request_log" for item in items):
        risks.append("历史请求日志会保留，仅作为审计记录。")

    return AccountUsageOut(
        account_id=account.id,
        account_name=account.name,
        has_relations=any(item.kind != "request_log" for item in items),
        items=items,
        risks=risks,
    )


def detach_account_refs(db: Session, account_id: int) -> None:
    settings_rows = db.scalars(
        select(SkillClassificationSettings).where(
            or_(
                SkillClassificationSettings.account_id == account_id,
                SkillClassificationSettings.report_account_id == account_id,
            )
        )
    ).all()
    now = utcnow()
    for settings in settings_rows:
        if settings.account_id == account_id:
            settings.account_id = None
            settings.enabled = False
        if settings.report_account_id == account_id:
            settings.report_account_id = None
            settings.report_enabled = False
        settings.updated_at = now

    db.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.embedding_account_id == account_id)
        .values(embedding_account_id=None, updated_at=now)
    )
    db.execute(
        update(VoiceRoom)
        .where(VoiceRoom.polish_account_id == account_id)
        .values(polish_account_id=None, updated_at=now)
    )
    db.flush()
