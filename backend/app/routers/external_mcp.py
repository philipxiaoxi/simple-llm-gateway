from __future__ import annotations

import asyncio
import json
import secrets
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select, update

from app.clock import utcnow
from app.db import get_session_factory
from app.deps import get_current_admin
from app.models import (
    Admin,
    ExternalMcpAudit,
    ExternalMcpCall,
    ExternalMcpCredential,
    ExternalMcpInstance,
    ExternalMcpService,
)
from app.services import external_mcp as ext
from app.services.permission_guard import require_permission

router = APIRouter(prefix="/v1/external-mcp", tags=["external-mcp"])
admin_router = APIRouter(prefix="/api/admin/mcp/external-services", tags=["external-mcp-admin"])
view = Depends(require_permission("mcp_external:view"))
manage = Depends(require_permission("mcp_external:manage"))
Actor = Annotated[Admin, Depends(get_current_admin)]
Auth = Annotated[str, Header(alias="Authorization", max_length=2048)]


def bearer(header: str) -> str:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ext.fail(401, "invalid_token", "需要 Bearer 凭证")
    return token


async def body(request: Request, model):
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > ext.MAX_PAYLOAD:
            raise ext.fail(413, "payload_too_large", "请求体过大")
        buf.extend(chunk)
    try:
        return model.model_validate_json(buf)
    except (ValidationError, RecursionError):
        raise ext.fail(422, "invalid_request", "请求体不符合协议") from None


def lease_payload(instance):
    return {"service_id": instance.service_id, "lease_epoch": instance.epoch,
            "lease_token": ext.lease_token(instance), "heartbeat_seconds": ext.HEARTBEAT_SECONDS,
            "lease_expires_at": ext.stamp(instance.expires_at)}


@router.post("/instances", status_code=201, responses={200: {}, 409: {}}, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Registration"}}}},
})
async def register(request: Request, response: Response, authorization: Auth = ""):
    token = bearer(authorization)
    if not token.startswith("mcpreg-"):
        raise ext.fail(401, "invalid_token", "注册凭证无效")
    token_digest = ext.digest(token)
    await asyncio.to_thread(check_registration_credential, token_digest)
    registration = await body(request, ext.Registration)
    payload, reused = await asyncio.to_thread(register_instance, token_digest, registration)
    if reused:
        response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return payload


def registration_credential(db, token_digest, now):
    credential = db.scalar(select(ExternalMcpCredential).where(ExternalMcpCredential.digest == token_digest))
    if credential is None or credential.revoked_at or credential.expires_at <= now:
        raise ext.fail(401, "invalid_token", "注册凭证无效或已过期")
    return credential


def check_registration_credential(token_digest):
    with get_session_factory()() as db:
        registration_credential(db, token_digest, utcnow())


def register_instance(token_digest, registration):
    encoded = ext.validate_manifest(registration)
    identifier = str(registration.instance_id)
    revision = ext.digest(encoded)
    pending = False
    reused = False
    with ext.write_session() as db:
        now = utcnow()
        credential = registration_credential(db, token_digest, now)
        service = db.get(ExternalMcpService, credential.service_id)
        assert service is not None
        if service.manifest and service.manifest != encoded:
            service.pending_manifest = encoded
            pending = True
        else:
            service.manifest = encoded
            instance = db.get(ExternalMcpInstance, (service.id, identifier))
            if instance is None or instance.expires_at <= now:
                active = db.scalars(ext.live_instances(now).where(ExternalMcpInstance.service_id == service.id)).all()
                if len(active) >= ext.INSTANCE_LIMIT:
                    raise ext.fail(429, "instance_limit", "服务实例已达上限")
            if instance is None:
                instance = ExternalMcpInstance(service_id=service.id, instance_id=identifier)
                db.add(instance)
            if (instance.epoch and instance.expires_at > now and instance.revision == revision
                    and instance.credential_id == credential.id):
                reused = True
            else:
                instance.epoch = secrets.token_hex(16)
            instance.credential_id = credential.id
            instance.revision = revision
            instance.last_seen_at = credential.last_used_at = service.last_seen_at = now
            instance.expires_at = now + timedelta(seconds=ext.LEASE_SECONDS)
            payload = lease_payload(instance)
    if pending:
        raise ext.fail(409, "manifest_pending", "新清单等待管理员审核")
    return payload, reused


@router.put("/instances/{instance_id}/lease")
def renew_lease(instance_id: UUID, response: Response, authorization: Auth = ""):
    claims = ext.lease_claims(bearer(authorization), str(instance_id))
    with ext.write_session() as db:
        now = utcnow()
        instance = ext.require_lease(db, claims, now)
        instance.last_seen_at = now
        service = service_for(db, instance.service_id)
        service.last_seen_at = now
        instance.expires_at = now + timedelta(seconds=ext.LEASE_SECONDS)
        payload = lease_payload(instance)
    response.headers["Cache-Control"] = "no-store"
    return payload


@router.delete("/instances/{instance_id}/lease", status_code=204)
def release(instance_id: UUID, authorization: Auth = ""):
    claims = ext.lease_claims(bearer(authorization), str(instance_id))
    with ext.write_session() as db:
        db.delete(ext.require_lease(db, claims, utcnow()))
    return Response(status_code=204)


@router.post("/instances/{instance_id}/claims", responses={204: {}})
async def claim(instance_id: UUID, authorization: Auth = "", wait: int = 20):
    claims = ext.lease_claims(bearer(authorization), str(instance_id))
    until = asyncio.get_running_loop().time() + min(20, max(0, wait))
    while True:
        payload = await asyncio.to_thread(claim_once, claims)
        if payload is not None:
            return payload
        if asyncio.get_running_loop().time() >= until:
            return Response(status_code=204)
        await asyncio.sleep(0.25)


def claim_once(claims):
    with get_session_factory()() as db:
        now = utcnow()
        instance = ext.require_lease(db, claims, now)
        candidate = db.scalar(select(ExternalMcpCall.id).where(
            ExternalMcpCall.service_id == instance.service_id, ExternalMcpCall.revision == instance.revision,
            ExternalMcpCall.state == "queued", ExternalMcpCall.deadline > now,
        ).order_by(ExternalMcpCall.deadline).limit(1))
    if candidate:
        with ext.write_session() as db:
            now = utcnow()
            instance = ext.require_lease(db, claims, now)
            active = db.scalar(select(func.count()).select_from(ExternalMcpCall).where(
                ExternalMcpCall.epoch == instance.epoch, ExternalMcpCall.state == "claimed",
                ExternalMcpCall.deadline > now,
            ))
            call = db.get(ExternalMcpCall, candidate)
            if (active is not None and active < ext.PER_INSTANCE_LIMIT and call is not None
                    and call.revision == instance.revision and call.state == "queued" and call.deadline > now):
                call.state, call.epoch, call.dispatch_id = "claimed", instance.epoch, secrets.token_hex(16)
                return {"call_id": call.id, "dispatch_id": call.dispatch_id, "operation": call.operation,
                        "arguments": json.loads(call.arguments), "deadline_at": ext.stamp(call.deadline),
                        "request_id": call.id}
    return None


@router.put("/instances/{instance_id}/calls/{call_id}/result", status_code=204, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}},
})
async def result(instance_id: UUID, call_id: str, request: Request, authorization: Auth = ""):
    claims = ext.lease_claims(bearer(authorization), str(instance_id))
    report = await body(request, ext.Result)
    if report.ok != (report.result is not None) or report.ok == (report.error is not None):
        raise ext.fail(422, "invalid_result", "结果或错误必须且只能提供一种")
    if report.error is not None:
        code = report.error.get("type", "")
        report.error = {"type": code if code in {"invalid_request", "not_found", "timeout"} else "external_error",
                        "message": {"invalid_request": "外部工具拒绝了参数", "not_found": "外部资源不存在",
                                    "timeout": "外部工具执行超时"}.get(code, "外部工具执行失败")}
    encoded = ext.bounded_json(report.model_dump())
    with ext.write_session() as db:
        now = utcnow()
        instance = ext.require_lease(db, claims, now)
        call = db.get(ExternalMcpCall, call_id)
        if call is None or call.service_id != instance.service_id or call.epoch != instance.epoch or call.dispatch_id != report.dispatch_id:
            raise ext.fail(404, "call_not_found", "没有匹配的调用")
        service = db.get(ExternalMcpService, instance.service_id)
        assert service is not None
        schema = next(tool["output_schema"] for tool in json.loads(service.manifest)["tools"] if tool["name"] == call.operation)
        if report.ok and schema is not None and not ext.Draft202012Validator(schema).is_valid(report.result):
            encoded = ext.encode({"ok": False, "error": {"type": "external_error", "message": "外部工具结果不符合 schema"}})
        if call.state == "done":
            if call.result != encoded:
                raise ext.fail(409, "result_conflict", "调用已收到不同的结果")
        elif call.state != "claimed" or call.deadline <= now:
            raise ext.fail(410, "call_finished", "调用已结束")
        else:
            call.state, call.result, call.arguments = "done", encoded, "{}"
    return Response(status_code=204)


class ServiceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CredentialInput(ServiceInput):
    days: Literal[7, 30, 90] = 30
    replaces_id: str | None = Field(default=None, max_length=32)


class RenewInput(BaseModel):
    days: Literal[7, 30, 90] = 30


class ApproveInput(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


def service_for(db, service_id):
    service = db.get(ExternalMcpService, service_id)
    if service is None:
        raise ext.fail(404, "not_found", "外部服务不存在")
    return service


def credential_for(db, service_id, credential_id):
    item = db.get(ExternalMcpCredential, credential_id)
    if item is None or item.service_id != service_id:
        raise ext.fail(404, "not_found", "注册凭证不存在")
    return item


def audit(db, actor, service_id, action, **details):
    db.add(ExternalMcpAudit(actor_id=actor.id, service_id=service_id, action=action, details=ext.encode(details)))


def credential_info(item):
    return {"id": item.id, "name": item.name, "prefix": item.prefix, "expires_at": ext.stamp(item.expires_at),
            "created_at": ext.stamp(item.created_at), "created_by": item.created_by,
            "last_used_at": ext.stamp(item.last_used_at) if item.last_used_at else None,
            "status": "revoked" if item.revoked_at else "expired" if item.expires_at <= utcnow() else "active"}


def cancel_calls(db, service_id, epochs=None):
    condition = ExternalMcpCall.epoch.in_(epochs) if epochs is not None else True
    db.execute(update(ExternalMcpCall).where(ExternalMcpCall.service_id == service_id, condition,
        ExternalMcpCall.state.in_(("queued", "claimed"))).values(state="cancelled", arguments="{}"))


@admin_router.get("", dependencies=[view])
def services():
    with get_session_factory()() as db:
        now = utcnow()
        items = []
        for service in db.scalars(select(ExternalMcpService).order_by(ExternalMcpService.name)):
            online = db.scalars(ext.live_instances(now).where(ExternalMcpInstance.service_id == service.id,
                ExternalMcpInstance.revision == ext.digest(service.manifest))).all()
            last = service.last_seen_at
            items.append({"id": service.id, "name": service.name, "online": bool(online), "online_instances": len(online),
                "last_seen_at": ext.stamp(last) if last else None,
                "version": json.loads(service.manifest)["version"] if service.manifest else None,
                "pending_revision": ext.digest(service.pending_manifest) if service.pending_manifest else None,
                "pending_manifest": json.loads(service.pending_manifest) if service.pending_manifest else None})
        return {"items": items}


@admin_router.post("", status_code=201, dependencies=[manage])
def create_service(payload: ServiceInput, actor: Actor):
    service_id = secrets.token_hex(8)
    name = payload.name.strip()
    if not name:
        raise ext.fail(422, "invalid_name", "名称不能为空")
    with ext.write_session() as db:
        db.add(ExternalMcpService(id=service_id, name=name))
        db.flush()
        audit(db, actor, service_id, "service_created")
    return {"id": service_id, "name": name}


@admin_router.delete("/{service_id}/registration", status_code=204, dependencies=[manage])
def remove(service_id: str, actor: Actor):
    with ext.write_session() as db:
        service_for(db, service_id)
        db.execute(delete(ExternalMcpInstance).where(ExternalMcpInstance.service_id == service_id))
        cancel_calls(db, service_id)
        audit(db, actor, service_id, "registration_removed")
    return Response(status_code=204)


@admin_router.post("/{service_id}/manifest/approve", status_code=204, dependencies=[manage])
def approve(service_id: str, payload: ApproveInput, actor: Actor):
    with ext.write_session() as db:
        service = service_for(db, service_id)
        if not service.pending_manifest or ext.digest(service.pending_manifest) != payload.revision:
            raise ext.fail(409, "manifest_changed", "待审清单已变化，请刷新")
        service.manifest, service.pending_manifest = service.pending_manifest, ""
        db.execute(delete(ExternalMcpInstance).where(ExternalMcpInstance.service_id == service_id))
        cancel_calls(db, service_id)
        audit(db, actor, service_id, "manifest_approved", revision=payload.revision)
    return Response(status_code=204)


@admin_router.get("/{service_id}/credentials", dependencies=[view])
def credentials(service_id: str):
    with get_session_factory()() as db:
        service_for(db, service_id)
        return {"items": [credential_info(row) for row in db.scalars(select(ExternalMcpCredential).where(
            ExternalMcpCredential.service_id == service_id).order_by(ExternalMcpCredential.created_at.desc()))]}


@admin_router.post("/{service_id}/credentials", status_code=201, dependencies=[manage])
def issue(service_id: str, payload: CredentialInput, actor: Actor, response: Response):
    token = "mcpreg-" + secrets.token_urlsafe(32)
    with ext.write_session() as db:
        service_for(db, service_id)
        if payload.replaces_id:
            credential_for(db, service_id, payload.replaces_id)
        item = ExternalMcpCredential(id=secrets.token_hex(16), service_id=service_id, name=payload.name.strip(),
            digest=ext.digest(token), prefix=token[:15], created_by=actor.id, expires_at=utcnow() + timedelta(days=payload.days))
        if not item.name:
            raise ext.fail(422, "invalid_name", "名称不能为空")
        db.add(item)
        db.flush()
        audit(db, actor, service_id, "credential_rotated" if payload.replaces_id else "credential_issued",
              credential_id=item.id, replaces_id=payload.replaces_id, expires_at=ext.stamp(item.expires_at))
        result_data = credential_info(item)
    response.headers["Cache-Control"] = "no-store"
    return {**result_data, "token": token}


@admin_router.post("/{service_id}/credentials/{credential_id}/renew", dependencies=[manage])
def renew(service_id: str, credential_id: str, payload: RenewInput, actor: Actor):
    with ext.write_session() as db:
        item = credential_for(db, service_id, credential_id)
        now = utcnow()
        if item.revoked_at or item.expires_at <= now:
            raise ext.fail(409, "credential_inactive", "已过期或销毁的凭证不能续期")
        old = item.expires_at
        item.expires_at = min(now + timedelta(days=90), old + timedelta(days=payload.days))
        if item.expires_at <= old:
            raise ext.fail(409, "renewal_limit", "凭证剩余期限已达 90 天")
        audit(db, actor, service_id, "credential_renewed", credential_id=item.id,
              old_expires_at=ext.stamp(old), expires_at=ext.stamp(item.expires_at))
        return credential_info(item)


@admin_router.delete("/{service_id}/credentials/{credential_id}", status_code=204, dependencies=[manage])
def revoke(service_id: str, credential_id: str, actor: Actor):
    with ext.write_session() as db:
        item = credential_for(db, service_id, credential_id)
        if item.revoked_at is None:
            item.revoked_at = utcnow()
            epochs = list(db.scalars(select(ExternalMcpInstance.epoch).where(ExternalMcpInstance.credential_id == item.id)))
            cancel_calls(db, service_id, epochs)
            audit(db, actor, service_id, "credential_revoked", credential_id=item.id)
    return Response(status_code=204)


@router.get("/openapi.json", include_in_schema=False)
def worker_openapi():
    document = get_openapi(title="External MCP Worker API", version="1.0.0", routes=router.routes)
    components = document.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    for model in (ext.Registration, ext.Result):
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    components["securitySchemes"] = {"WorkerBearer": {"type": "http", "scheme": "bearer"}}
    document["security"] = [{"WorkerBearer": []}]
    for path in document["paths"].values():
        for operation in path.values():
            operation["parameters"] = [p for p in operation.get("parameters", []) if p["name"] != "Authorization"]
    return document
