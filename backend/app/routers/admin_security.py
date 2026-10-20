from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, SecurityEvent, SecuritySettings
from app.schemas import (
    BeaconEvent,
    SecurityEventDetailOut,
    SecurityEventListOut,
    SecurityEventOut,
    SecurityLocationEntry,
    SecurityOverviewOut,
    SecuritySettingsOut,
    SecuritySettingsUpdate,
    SecurityTrendPoint,
)
from app.services.client_meta import client_meta
from app.services.security_audit import (
    allow_beacon,
    automation_signals,
    enqueue_beacon_event,
    invalidate_trusted_locations,
    normalize_location,
)

router = APIRouter(
    prefix="/api/admin/security",
    tags=["admin-security"],
    dependencies=[Depends(get_current_admin)],
)

_SUSPICIOUS_TYPES = ("suspicious_request", "auth_fail_burst")
_LOGIN_TYPES = ("login_success", "login_failure", "login_locked", "logout", "admin_credential_change")
_GROUP_TYPES = {"login": _LOGIN_TYPES, "suspicious": _SUSPICIOUS_TYPES, "beacon": ("frontend_beacon",)}
_MAX_BEACON_EVENTS = 50
_MAX_LOCATION_ENTRIES = 200


def _valid_cidr(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    candidate = text if "/" in text else (f"{text}/32" if ":" not in text else f"{text}/128")
    try:
        ipaddress.ip_network(candidate, strict=False)
    except ValueError:
        return False
    return True


def _event_to_out(row: SecurityEvent) -> SecurityEventOut:
    return SecurityEventOut(
        id=row.id,
        created_at=row.created_at,
        event_type=row.event_type,
        severity=row.severity,
        source=row.source,
        actor=row.actor,
        client_ip=row.client_ip,
        ip_location=row.ip_location,
        location_trusted=row.location_trusted,
        location_rule=row.location_rule,
        user_agent=row.user_agent,
        client_name=row.client_name,
        client_version=row.client_version,
        device_type=row.device_type,
        request_headers=_load_json_dict(row.request_headers),
        method=row.method,
        path=row.path,
        status_code=row.status_code,
        outcome=row.outcome,
        risk_tags=_load_json_list(row.risk_tags),
        detail=_load_json_dict(row.detail_json),
    )


def _load_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_json_dict(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


@router.get("/events", response_model=SecurityEventListOut)
def list_events(
    event_type: str | None = None,
    group: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    ip: str | None = None,
    q: str | None = None,
    tags: list[str] | None = Query(default=None),
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SecurityEventListOut:
    filters = select(SecurityEvent)
    if event_type:
        filters = filters.where(SecurityEvent.event_type == event_type)
    if group and group in _GROUP_TYPES:
        filters = filters.where(SecurityEvent.event_type.in_(_GROUP_TYPES[group]))
    if tags:
        clauses = [SecurityEvent.risk_tags.like(f'%"{tag}"%') for tag in tags if tag]
        if clauses:
            filters = filters.where(or_(*clauses))
    if severity:
        filters = filters.where(SecurityEvent.severity == severity)
    if source:
        filters = filters.where(SecurityEvent.source == source)
    if ip:
        filters = filters.where(SecurityEvent.client_ip == ip.strip())
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        filters = filters.where(
            or_(
                SecurityEvent.path.like(like),
                SecurityEvent.actor.like(like),
                SecurityEvent.user_agent.like(like),
                SecurityEvent.client_name.like(like),
                SecurityEvent.ip_location.like(like),
            )
        )
    if since:
        filters = filters.where(SecurityEvent.created_at >= since)
    if until:
        filters = filters.where(SecurityEvent.created_at <= until)
    total = db.scalar(select(func.count()).select_from(filters.subquery())) or 0
    rows = db.scalars(
        filters.order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return SecurityEventListOut(
        items=[_event_to_out(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/events/{event_id}", response_model=SecurityEventDetailOut)
def get_event(event_id: int, db: Session = Depends(get_db)) -> SecurityEventDetailOut:
    row = db.get(SecurityEvent, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    detail = SecurityEventDetailOut(**_event_to_out(row).model_dump())
    if row.attempted_password_encrypted:
        try:
            detail.attempted_password = decrypt_secret(
                row.attempted_password_encrypted, get_settings().app_secret_key
            )
        except ValueError:
            detail.password_unavailable = True
    return detail


@router.get("/overview", response_model=SecurityOverviewOut)
def overview(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> SecurityOverviewOut:
    until = utcnow()
    since = until - timedelta(days=days)
    base = [SecurityEvent.created_at >= since, SecurityEvent.created_at <= until]

    login_success = db.scalar(
        select(func.count()).select_from(SecurityEvent).where(*base, SecurityEvent.event_type == "login_success")
    ) or 0
    login_failure = db.scalar(
        select(func.count())
        .select_from(SecurityEvent)
        .where(*base, SecurityEvent.event_type.in_(("login_failure", "login_locked")))
    ) or 0
    suspicious = db.scalar(
        select(func.count()).select_from(SecurityEvent).where(*base, SecurityEvent.event_type.in_(_SUSPICIOUS_TYPES))
    ) or 0
    distinct_ips = db.scalar(
        select(func.count(func.distinct(SecurityEvent.client_ip))).where(*base, SecurityEvent.client_ip.is_not(None))
    ) or 0
    severity_counts = {"info": 0, "warn": 0, "critical": 0}
    for sev, count in db.execute(
        select(SecurityEvent.severity, func.count()).where(*base).group_by(SecurityEvent.severity)
    ).all():
        severity_counts[str(sev)] = int(count or 0)
    outcome_counts: dict[str, int] = {}
    for outcome, count in db.execute(
        select(SecurityEvent.outcome, func.count()).where(*base).group_by(SecurityEvent.outcome)
    ).all():
        outcome_counts[str(outcome or "unknown")] = int(count or 0)
    client_counts = [
        {"name": str(name or "未知"), "count": int(count or 0)}
        for name, count in db.execute(
            select(SecurityEvent.client_name, func.count())
            .where(*base)
            .group_by(SecurityEvent.client_name)
            .order_by(func.count().desc())
            .limit(8)
        ).all()
    ]
    top_ips = [
        {"ip": ip, "count": int(count or 0)}
        for ip, count in db.execute(
            select(SecurityEvent.client_ip, func.count())
            .where(*base, SecurityEvent.client_ip.is_not(None))
            .group_by(SecurityEvent.client_ip)
            .order_by(func.count().desc())
            .limit(5)
        ).all()
    ]
    day_expr = func.strftime("%Y-%m-%d", SecurityEvent.created_at)
    buckets: dict[str, SecurityTrendPoint] = {}
    for day, event_type, count in db.execute(
        select(day_expr, SecurityEvent.event_type, func.count()).where(*base).group_by(day_expr, SecurityEvent.event_type)
    ).all():
        point = buckets.setdefault(str(day), SecurityTrendPoint(date=str(day)))
        if event_type == "login_success":
            point.login_success = int(count or 0)
        elif event_type in ("login_failure", "login_locked"):
            point.login_failure += int(count or 0)
        elif event_type in _SUSPICIOUS_TYPES:
            point.suspicious += int(count or 0)
    return SecurityOverviewOut(
        since=since,
        until=until,
        login_success=int(login_success),
        login_failure=int(login_failure),
        suspicious=int(suspicious),
        distinct_ips=int(distinct_ips),
        severity_counts=severity_counts,
        outcome_counts=outcome_counts,
        client_counts=client_counts,
        top_ips=top_ips,
        trend=[buckets[key] for key in sorted(buckets)],
    )


@router.post("/beacon", status_code=204)
def beacon(
    payload: list[BeaconEvent],
    request: Request,
    admin: Admin = Depends(get_current_admin),
) -> Response:
    settings = get_settings()
    if not settings.security_beacon_enabled or not payload:
        return Response(status_code=204)
    events = payload[:_MAX_BEACON_EVENTS]
    meta = client_meta(
        dict(request.headers),
        peer=request.client.host if request.client else None,
    )
    for item in events:
        if not allow_beacon(admin.username):
            break
        data = item.model_dump()
        signals = automation_signals(meta.get("user_agent"), dict(request.headers), beacon=data)
        enqueue_beacon_event(
            data, admin.username, meta, signals=signals, headers=dict(request.headers), status=204
        )
    return Response(status_code=204)


def _stored_settings(db: Session) -> SecuritySettings:
    row = db.get(SecuritySettings, 1)
    if row is None:
        row = SecuritySettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _settings_out(row: SecuritySettings) -> SecuritySettingsOut:
    entries = []
    for item in _load_json_list(row.trusted_locations_json):
        if not isinstance(item, dict):
            continue
        entries.append(
            SecurityLocationEntry(
                country=str(item.get("country") or ""),
                province=str(item.get("province") or ""),
                city=str(item.get("city") or ""),
                raw=str(item.get("raw") or ""),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return SecuritySettingsOut(
        location_check_enabled=bool(row.location_check_enabled),
        trusted_locations=entries,
        ip_allowlist=[str(x) for x in _load_json_list(row.ip_allowlist_json)],
    )


@router.get("/settings", response_model=SecuritySettingsOut)
def get_settings_endpoint(db: Session = Depends(get_db)) -> SecuritySettingsOut:
    return _settings_out(_stored_settings(db))


@router.put("/settings", response_model=SecuritySettingsOut)
def update_settings(
    payload: SecuritySettingsUpdate,
    db: Session = Depends(get_db),
) -> SecuritySettingsOut:
    stored_entries: list[dict] = []
    for entry in payload.trusted_locations[:_MAX_LOCATION_ENTRIES]:
        tokens = normalize_location(entry.raw) if entry.raw else None
        country = (tokens.country if tokens and tokens.country else entry.country).strip()
        province = (tokens.province if tokens and tokens.province else entry.province).strip()
        city = (tokens.city if tokens and tokens.city else entry.city).strip()
        raw = entry.raw.strip() or " ".join(part for part in (country, province, city) if part)
        if not (country or province or city):
            continue
        stored_entries.append(
            {"country": country, "province": province, "city": city, "raw": raw, "enabled": entry.enabled}
        )
    allowlist: list[str] = []
    for item in payload.ip_allowlist:
        text = str(item).strip()
        if text and text not in allowlist and _valid_cidr(text):
            allowlist.append(text)
    row = _stored_settings(db)
    row.location_check_enabled = bool(payload.location_check_enabled)
    row.trusted_locations_json = json.dumps(stored_entries, ensure_ascii=False)
    row.ip_allowlist_json = json.dumps(allowlist, ensure_ascii=False)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    invalidate_trusted_locations()
    return _settings_out(row)
