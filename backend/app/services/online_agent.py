from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.capabilities import catalog_payload, ensure_defaults
from app.clock import utcnow
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret, generate_api_key, hash_api_key, key_prefix
from app.models import (
    ApiKey,
    ApiKeyAccount,
    McpKey,
    McpKeyCapability,
    OnlineAgentSession,
    OnlineAgentSetting,
    OnlineAgentToggle,
    Skill,
    UpstreamAccount,
)
from app.services import skills as skills_service
from app.services.key_models import build_model_catalog, replace_key_accounts
from app.services.mcp_auth import create_mcp_key_record, replace_mcp_key_capabilities

OPENCODE_PORT = 4096
OPENCODE_HOST = "127.0.0.1"
PROVIDER_ID = "gateway"


class OnlineAgentError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_process: subprocess.Popen[bytes] | None = None


def opencode_base() -> str:
    return f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"


def root_dir() -> Path:
    return get_settings().resolved_opencode_path


def config_path() -> Path:
    return root_dir() / "opencode.json"


def skills_link_dir() -> Path:
    path = root_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspaces_dir() -> Path:
    path = root_dir() / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_setting(db: Session) -> OnlineAgentSetting:
    row = db.get(OnlineAgentSetting, 1)
    if row is None:
        row = OnlineAgentSetting(id=1, status="stopped")
        db.add(row)
        db.flush()
    return row


def process_running() -> bool:
    return _process is not None and _process.poll() is None


def refresh_status(db: Session) -> OnlineAgentSetting:
    setting = get_setting(db)
    if setting.status == "running" and not process_running():
        setting.status = "stopped"
        setting.updated_at = utcnow()
        db.flush()
    return setting


def list_models(db: Session) -> list[dict[str, str]]:
    setting = get_setting(db)
    if setting.internal_key_id is None:
        return []
    key = _load_internal_key(db, setting.internal_key_id)
    if key is None:
        return []
    rows = []
    for entry in build_model_catalog(key):
        rows.append(
            {
                "id": entry.public_id,
                "label": f"{entry.account.name} / {entry.public_id}",
                "account_name": entry.account.name,
            }
        )
    return rows


def sync(db: Session) -> OnlineAgentSetting:
    setting = get_setting(db)
    _ensure_internal_key(db, setting)
    _ensure_mcp_key(db, setting)
    _sync_skill_links(db)
    _write_config(db, setting)
    setting.updated_at = utcnow()
    setting.last_error = None
    db.flush()
    return setting


def start(db: Session) -> OnlineAgentSetting:
    setting = sync(db)
    if process_running():
        setting.status = "running"
        return setting
    binary = shutil.which("opencode")
    if binary is None:
        setting.status = "failed"
        setting.last_error = "未安装 opencode。Docker 镜像需预装 Node.js 后执行 npm install -g opencode-ai"
        db.flush()
        raise OnlineAgentError(setting.last_error, status_code=503)
    global _process
    _process = subprocess.Popen(
        [binary, "serve", "--hostname", OPENCODE_HOST, "--port", str(OPENCODE_PORT)],
        cwd=str(root_dir()),
        env={**os.environ, "OPENCODE_CONFIG": str(config_path())},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    setting.status = "running"
    setting.last_error = None
    setting.updated_at = utcnow()
    db.flush()
    return setting


def stop(db: Session) -> OnlineAgentSetting:
    global _process
    setting = get_setting(db)
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    setting.status = "stopped"
    setting.updated_at = utcnow()
    db.flush()
    return setting


def restart(db: Session) -> OnlineAgentSetting:
    stop(db)
    return start(db)


def set_toggle(db: Session, kind: str, target_id: str, enabled: bool) -> OnlineAgentToggle:
    if kind not in {"skill", "mcp"}:
        raise OnlineAgentError("开关类型只支持 skill 或 mcp")
    if kind == "skill":
        skill = db.get(Skill, int(target_id))
        if skill is None:
            raise OnlineAgentError("Skill 不存在", status_code=404)
    row = db.scalar(
        select(OnlineAgentToggle).where(OnlineAgentToggle.kind == kind, OnlineAgentToggle.target_id == target_id)
    )
    if row is None:
        row = OnlineAgentToggle(kind=kind, target_id=target_id, enabled=1 if enabled else 0)
        db.add(row)
    else:
        row.enabled = 1 if enabled else 0
    db.flush()
    sync(db)
    return row


def state_payload(db: Session) -> dict[str, Any]:
    setting = refresh_status(db)
    models = list_models(db)
    toggles = list(db.scalars(select(OnlineAgentToggle)).all())
    ensure_defaults()
    skills = [
        {
            "id": str(skill.id),
            "name": skill.name,
            "enabled": _enabled(toggles, "skill", str(skill.id)),
        }
        for skill in db.scalars(select(Skill).order_by(Skill.name)).all()
    ]
    capabilities = [
        {
            "id": item["capability_id"],
            "name": item["name"],
            "enabled": _enabled(toggles, "mcp", item["capability_id"]),
        }
        for item in catalog_payload(only_enabled=True)
    ]
    return {
        "status": setting.status,
        "last_error": setting.last_error,
        "selected_model": setting.selected_model if any(item["id"] == setting.selected_model for item in models) else (models[0]["id"] if models else None),
        "last_session_id": setting.last_session_id,
        "model_count": len(models),
        "models": models,
        "skills": skills,
        "mcp": capabilities,
        "running": process_running(),
    }


def remember_selection(db: Session, *, model: str | None = None, session_id: str | None = None) -> None:
    setting = get_setting(db)
    if model is not None:
        setting.selected_model = model or None
    if session_id is not None:
        setting.last_session_id = session_id or None
    setting.updated_at = utcnow()
    db.flush()


def create_session(db: Session, title: str = "新会话") -> dict[str, Any]:
    _require_running()
    response = _request("POST", "/session", json={"title": title})
    session = response.json()
    session_id = str(session.get("id") or "")
    if not session_id:
        raise OnlineAgentError("OpenCode 没有返回会话 ID", status_code=502)
    workspace = workspaces_dir() / session_id
    workspace.mkdir(parents=True, exist_ok=True)
    db.merge(OnlineAgentSession(session_id=session_id, workspace_path=str(workspace), title_generated=0))
    remember_selection(db, session_id=session_id)
    session["workspace_path"] = str(workspace)
    return session


def list_sessions(db: Session) -> list[dict[str, Any]]:
    _require_running()
    rows = _request("GET", "/session").json()
    if not isinstance(rows, list):
        return []
    known = {item.session_id: item for item in db.scalars(select(OnlineAgentSession)).all()}
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("id") or "")
        stored = known.get(session_id)
        row["workspace_path"] = stored.workspace_path if stored else ""
        result.append(row)
    return result


def session_messages(session_id: str) -> Any:
    _require_running()
    return _request("GET", f"/session/{session_id}/message").json()


def prompt(db: Session, session_id: str, text: str, model: str) -> dict[str, Any]:
    _require_running()
    stored = db.get(OnlineAgentSession, session_id)
    if stored is None:
        raise OnlineAgentError("会话不存在", status_code=404)
    remember_selection(db, model=model, session_id=session_id)
    if not stored.title_generated:
        title = _title_for(text, model)
        if title:
            _request("PATCH", f"/session/{session_id}", json={"title": title})
            stored.title_generated = 1
            db.flush()
    directory = stored.workspace_path
    _request(
        "POST",
        f"/session/{session_id}/prompt_async",
        json={
            "model": {"providerID": PROVIDER_ID, "modelID": model},
            "parts": [{"type": "text", "text": text}],
        },
        params={"directory": directory} if directory else None,
    )
    return {"ok": True, "session_id": session_id}


def compact(session_id: str) -> dict[str, Any]:
    _require_running()
    if not session_id:
        raise OnlineAgentError("没有选中会话")
    _request("POST", f"/session/{session_id}/summarize")
    return {"ok": True}


def reset_session(db: Session, session_id: str) -> dict[str, Any]:
    _require_running()
    stored = db.get(OnlineAgentSession, session_id)
    if stored is None:
        raise OnlineAgentError("会话不存在", status_code=404)
    _request("POST", f"/session/{session_id}/abort")
    _request("DELETE", f"/session/{session_id}")
    db.delete(stored)
    db.flush()
    return create_session(db)


def _title_for(text: str, model: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return ""
    return cleaned[:20]


def _require_running() -> None:
    if not process_running():
        raise OnlineAgentError("OpenCode 未启动", status_code=503)


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    try:
        response = httpx.request(method, f"{opencode_base()}{path}", timeout=30, **kwargs)
    except httpx.HTTPError as error:
        raise OnlineAgentError(f"OpenCode 未响应: {error}", status_code=503) from error
    if response.status_code >= 400:
        raise OnlineAgentError(response.text[:300] or "OpenCode 请求失败", status_code=502)
    return response


def _load_internal_key(db: Session, key_id: int) -> ApiKey | None:
    return db.scalar(
        select(ApiKey).where(ApiKey.id == key_id).options(selectinload(ApiKey.account_links).selectinload(ApiKeyAccount.account))
    )


def _ensure_internal_key(db: Session, setting: OnlineAgentSetting) -> str:
    accounts = list(db.scalars(select(UpstreamAccount).where(UpstreamAccount.status != "disabled")).all())
    key = _load_internal_key(db, setting.internal_key_id) if setting.internal_key_id else None
    if key is None:
        plaintext = generate_api_key()
        key = ApiKey(
            name="在线 Agent",
            key_hash=hash_api_key(plaintext),
            key_encrypted=encrypt_secret(plaintext, get_settings().app_secret_key),
            key_prefix=key_prefix(plaintext),
            status="active",
        )
        db.add(key)
        db.flush()
        setting.internal_key_id = key.id
    else:
        plaintext = decrypt_secret(key.key_encrypted, get_settings().app_secret_key)
    if accounts:
        replace_key_accounts(db, key, [account.id for account in accounts])
    db.flush()
    return plaintext


def _ensure_mcp_key(db: Session, setting: OnlineAgentSetting) -> str:
    enabled = [
        row.target_id
        for row in db.scalars(select(OnlineAgentToggle).where(OnlineAgentToggle.kind == "mcp", OnlineAgentToggle.enabled == 1)).all()
    ]
    key = db.get(McpKey, setting.mcp_key_id) if setting.mcp_key_id else None
    if not enabled:
        if key is not None:
            replace_mcp_key_capabilities(db, key, ["knowledge"])
            key.status = "disabled"
        return ""
    if key is None:
        key, plaintext = create_mcp_key_record(db, "在线 Agent", enabled)
        setting.mcp_key_id = key.id
        return plaintext
    key.status = "active"
    replace_mcp_key_capabilities(db, key, enabled)
    return decrypt_secret(key.key_encrypted, get_settings().app_secret_key)


def _sync_skill_links(db: Session) -> None:
    enabled = {
        row.target_id
        for row in db.scalars(select(OnlineAgentToggle).where(OnlineAgentToggle.kind == "skill", OnlineAgentToggle.enabled == 1)).all()
    }
    root = skills_link_dir()
    for child in root.iterdir():
        if child.is_symlink() or child.is_dir():
            if child.name not in enabled:
                if child.is_symlink() or child.is_file():
                    child.unlink()
                else:
                    shutil.rmtree(child, ignore_errors=True)
    for skill_id in enabled:
        skill = db.get(Skill, int(skill_id))
        if skill is None:
            continue
        source = skills_service.skill_dir(skill)
        target = root / skill.slug
        if target.is_symlink() or target.exists():
            continue
        if source.is_dir():
            target.symlink_to(source, target_is_directory=True)


def _write_config(db: Session, setting: OnlineAgentSetting) -> None:
    plaintext = ""
    if setting.internal_key_id:
        key = db.get(ApiKey, setting.internal_key_id)
        if key is not None:
            plaintext = decrypt_secret(key.key_encrypted, get_settings().app_secret_key)
    models = list_models(db)
    mcp_key = ""
    if setting.mcp_key_id:
        stored = db.get(McpKey, setting.mcp_key_id)
        if stored is not None and stored.status == "active":
            mcp_key = decrypt_secret(stored.key_encrypted, get_settings().app_secret_key)
    enabled_mcp = db.scalar(
        select(OnlineAgentToggle).where(OnlineAgentToggle.kind == "mcp", OnlineAgentToggle.enabled == 1)
    )
    config = {
        "provider": {
            PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "站点模型",
                "options": {"baseURL": "http://127.0.0.1:8000/v1", "apiKey": plaintext},
                "models": {item["id"]: {"name": item["label"]} for item in models},
            }
        },
        "mcp": {
            "gateway": {
                "type": "remote",
                "url": "http://127.0.0.1:8000/mcp",
                "enabled": bool(enabled_mcp and mcp_key),
                "headers": {"Authorization": f"Bearer {mcp_key}"} if mcp_key else {},
            }
        },
    }
    config_path().write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _enabled(rows: list[OnlineAgentToggle], kind: str, target_id: str) -> bool:
    return any(row.kind == kind and row.target_id == target_id and row.enabled for row in rows)
