from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import contextmanager
from datetime import UTC, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import jwt
from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.capabilities.base import CapabilityError
from app.clock import utcnow
from app.config import DEFAULT_RESOURCE_TOKEN_TTL_SECONDS, get_settings
from app.db import get_session_factory
from app.models import ExternalMcpCall, ExternalMcpCredential, ExternalMcpInstance, ExternalMcpService

PREFIX = "ext_"
HEARTBEAT_SECONDS = 15
LEASE_SECONDS = 45
MAX_PAYLOAD = 128 * 1024
QUEUE_LIMIT = 64
INSTANCE_LIMIT = 16
PER_INSTANCE_LIMIT = 8
AUDIENCE = "external-mcp-instance"
Operation = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class Tool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Operation
    description: str = Field(min_length=1, max_length=1000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    example: dict[str, Any] | None = None
    returns: str = Field(default="", max_length=1000)
    timeout_seconds: int = Field(default=20, ge=1, le=60)
    side_effects: bool = True


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["external-mcp/v1"]
    instance_id: UUID
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=2000)
    notes: list[Annotated[str, StringConstraints(max_length=500)]] = Field(default_factory=list, max_length=8)
    tools: list[Tool] = Field(min_length=1, max_length=32)


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"error": {"type": code, "message": message}})


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def bounded_json(value: Any) -> str:
    try:
        raw = encode(value)
        if len(raw.encode()) <= MAX_PAYLOAD:
            return raw
    except (ValueError, RecursionError, UnicodeError):
        raise fail(422, "invalid_json", "JSON 内容无效") from None
    raise fail(413, "payload_too_large", "JSON 内容过大")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def stamp(value) -> str:
    return value.replace(tzinfo=UTC).isoformat()


@contextmanager
def write_session():
    with get_session_factory()() as db, db.begin():
        # SQLite serializes read-modify-write so remove, renew and claim cannot race.
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        yield db


def validate_manifest(registration: Registration) -> str:
    manifest = registration.model_dump(mode="json", exclude={"instance_id"})
    encoded = bounded_json(manifest)

    def check(value: Any, depth: int = 0):
        if depth > 14:
            raise fail(422, "invalid_schema", "工具 schema 嵌套过深")
        if isinstance(value, dict):
            if {"$ref", "$dynamicRef", "$recursiveRef", "pattern", "patternProperties"} & value.keys():
                raise fail(422, "invalid_schema", "首版 schema 不支持引用和正则表达式")
            for nested in value.values():
                check(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                check(nested, depth + 1)

    names: set[str] = set()
    for tool in registration.tools:
        if tool.name in names or tool.name == "models":
            raise fail(422, "invalid_tool_name", "工具重名或使用了保留名称")
        names.add(tool.name)
        for schema in (tool.input_schema, tool.output_schema):
            if schema is None:
                continue
            if schema.get("type") != "object":
                raise fail(422, "invalid_schema", "工具 schema 必须声明 object 类型")
            check(schema)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                raise fail(422, "invalid_schema", "工具 JSON Schema 无效") from None
        if tool.example is not None and not Draft202012Validator(tool.input_schema).is_valid(tool.example):
            raise fail(422, "invalid_example", "工具示例不符合输入 schema")
    return encoded


def live_instances(now):
    return (
        select(ExternalMcpInstance)
        .join(ExternalMcpCredential, ExternalMcpCredential.id == ExternalMcpInstance.credential_id)
        .join(ExternalMcpService, ExternalMcpService.id == ExternalMcpInstance.service_id)
        .where(
            ExternalMcpCredential.service_id == ExternalMcpInstance.service_id,
            ExternalMcpInstance.expires_at > now,
            ExternalMcpCredential.expires_at > now,
            ExternalMcpCredential.revoked_at.is_(None),
        )
    )


def catalog(db: Session, *, include_offline: bool = False, allowed: set[str] | None = None) -> list[dict]:
    query = select(ExternalMcpService).where(ExternalMcpService.manifest != "").order_by(ExternalMcpService.name)
    if allowed is not None:
        ids = [cid[len(PREFIX):] for cid in allowed if cid.startswith(PREFIX)]
        if not ids:
            return []
        query = query.where(ExternalMcpService.id.in_(ids))
    instances = db.scalars(live_instances(utcnow())).all()
    online = {(instance.service_id, instance.revision) for instance in instances}
    rows = []
    for service in db.scalars(query):
        is_online = (service.id, digest(service.manifest)) in online
        if not is_online and not include_offline:
            continue
        manifest = json.loads(service.manifest)
        cid = PREFIX + service.id
        tools = [dict(tool, name=f"{cid}_{tool['name']}", operation=tool["name"]) for tool in manifest["tools"]]
        rows.append({
            "capability_id": cid, "name": service.name, "description": manifest["description"],
            "category": "external", "version": manifest["version"], "status": "enabled" if is_online else "offline",
            "source": "external", "admin_path": "", "icon": "", "permissions": [], "input_schema": {}, "tools": tools,
            "integration": {"notes": manifest["notes"], "rest_endpoints": [
                {"method": "POST", "path": f"/v1/capabilities/{cid}/{tool['operation']}",
                 "summary": tool["description"], "content_type": "application/json", "example_body": tool["example"]}
                for tool in tools
            ]},
        })
    return rows


def split_tool(name: str) -> tuple[str, str] | None:
    if not name.startswith(PREFIX):
        return None
    service, separator, operation = name[len(PREFIX):].partition("_")
    return (PREFIX + service, operation) if separator and len(service) == 16 and operation else None


def lease_token(instance: ExternalMcpInstance) -> str:
    return jwt.encode({
        "aud": AUDIENCE, "sub": instance.instance_id, "sid": instance.service_id, "epoch": instance.epoch,
        "exp": utcnow().replace(tzinfo=UTC) + timedelta(seconds=DEFAULT_RESOURCE_TOKEN_TTL_SECONDS),
    }, get_settings().app_secret_key, algorithm="HS256")


def lease_claims(token: str, instance_id: str) -> dict:
    try:
        claims = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"], audience=AUDIENCE,
                            options={"require": ["sub", "sid", "epoch", "exp"]})
        if claims["sub"] == instance_id and isinstance(claims["sid"], str) and isinstance(claims["epoch"], str):
            return claims
    except jwt.PyJWTError:
        pass
    raise fail(401, "invalid_lease_token", "实例令牌无效或已过期")


def require_lease(db: Session, claims: dict, now) -> ExternalMcpInstance:
    instance = db.get(ExternalMcpInstance, (claims["sid"], claims["sub"]))
    if instance is None or instance.epoch != claims["epoch"]:
        raise fail(410, "registration_missing", "实例注册不存在，请重新注册")
    if instance.expires_at <= now:
        raise fail(410, "lease_expired", "租约已到期，请重新注册")
    credential = db.get(ExternalMcpCredential, instance.credential_id)
    if credential is None or credential.revoked_at or credential.expires_at <= now:
        raise fail(401, "inactive_credential", "注册凭证已过期或撤销")
    service = db.get(ExternalMcpService, instance.service_id)
    if service is None or instance.revision != digest(service.manifest):
        raise fail(410, "registration_missing", "实例清单已更新，请重新注册")
    return instance


def has_worker(db: Session, service_id: str, revision: str, now) -> bool:
    return db.scalar(live_instances(now).where(
        ExternalMcpInstance.service_id == service_id, ExternalMcpInstance.revision == revision,
    ).limit(1)) is not None


def enqueue(capability_id: str, operation: str, arguments: dict, call_id: str):
    service_id = capability_id[len(PREFIX):]
    with write_session() as db:
        service = db.get(ExternalMcpService, service_id)
        if service is None or not service.manifest:
            raise CapabilityError("外部能力不存在", 404, "not_found")
        revision = digest(service.manifest)
        tool = next((tool for tool in json.loads(service.manifest)["tools"] if tool["name"] == operation), None)
        if tool is None:
            raise CapabilityError("外部工具不存在", 404, "not_found")
        try:
            body = bounded_json(arguments)
        except HTTPException as exc:
            raise CapabilityError("调用参数无效或过大", exc.status_code, "invalid_request") from exc
        if not Draft202012Validator(tool["input_schema"]).is_valid(arguments):
            raise CapabilityError("参数不符合工具输入 schema", 422, "invalid_request")
        now = utcnow()
        if not has_worker(db, service_id, revision, now):
            raise CapabilityError("外部服务离线", 503, "service_offline")
        count = db.scalar(select(func.count()).select_from(ExternalMcpCall).where(
            ExternalMcpCall.service_id == service_id, ExternalMcpCall.deadline > now,
            ExternalMcpCall.state.in_(("queued", "claimed")),
        ))
        if count is not None and count >= QUEUE_LIMIT:
            raise CapabilityError("外部服务繁忙", 429, "rate_limit_error")
        deadline = now + timedelta(seconds=tool["timeout_seconds"])
        db.add(ExternalMcpCall(id=call_id, service_id=service_id, revision=revision,
                               operation=operation, arguments=body, deadline=deadline))
    return service_id, revision, deadline


def poll_result(call_id: str, service_id: str, revision: str, deadline) -> dict | None:
    with get_session_factory()() as db:
        call = db.get(ExternalMcpCall, call_id)
        if call is not None and call.state == "done" and call.result is not None:
            result = json.loads(call.result)
            if not result["ok"]:
                error = result["error"]
                http_status = {"invalid_request": 422, "not_found": 404, "timeout": 504}.get(error["type"], 502)
                raise CapabilityError(error["message"], http_status, error["type"])
            return result["result"]
        now = utcnow()
        if now >= deadline:
            raise CapabilityError("外部工具调用超时", 504, "timeout")
        if call is None or call.state == "cancelled" or not has_worker(db, service_id, revision, now):
            raise CapabilityError("外部服务已离线", 503, "service_offline")
        if call.epoch and db.scalar(live_instances(now).where(
            ExternalMcpInstance.service_id == service_id, ExternalMcpInstance.epoch == call.epoch,
        )) is None:
            raise CapabilityError("接单实例已离线", 503, "service_offline")
        return None


def cancel(call_id: str) -> None:
    # Do not requeue claimed work: external side effects may have already occurred.
    with write_session() as db:
        db.execute(update(ExternalMcpCall).where(ExternalMcpCall.id == call_id,
            ExternalMcpCall.state.in_(("queued", "claimed"))).values(state="cancelled", arguments="{}"))


async def invoke(capability_id: str, operation: str, arguments: dict) -> dict:
    call_id = secrets.token_hex(16)
    submitted = asyncio.create_task(asyncio.to_thread(enqueue, capability_id, operation, arguments, call_id))
    try:
        service_id, revision, deadline = await asyncio.shield(submitted)
    except asyncio.CancelledError:
        try:
            await submitted
        except Exception:
            pass
        else:
            await asyncio.to_thread(cancel, call_id)
        raise
    delivered = False
    try:
        while True:
            result = await asyncio.to_thread(poll_result, call_id, service_id, revision, deadline)
            if result is not None:
                delivered = True
                return result
            await asyncio.sleep(0.2)
    finally:
        if not delivered:
            await asyncio.to_thread(cancel, call_id)


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(cleanup)


def cleanup() -> None:
    cutoff = utcnow() - timedelta(minutes=5)
    with write_session() as db:
        db.execute(delete(ExternalMcpCall).where(ExternalMcpCall.deadline < cutoff))
        db.execute(delete(ExternalMcpInstance).where(ExternalMcpInstance.expires_at < cutoff))
