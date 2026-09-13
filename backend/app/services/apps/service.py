from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.models import AppInstallation
from app.services.apps.registry import AppManifest, get_manifest, list_manifests


def _parse_config(raw: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return dict(fallback)
    try:
        data = json.loads(raw)
    except Exception:
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    merged = dict(fallback)
    merged.update(data)
    return merged


def ensure_app_installations(db: Session) -> None:
    existing = {row.app_id: row for row in db.scalars(select(AppInstallation)).all()}
    changed = False
    for manifest in list_manifests():
        if manifest.id in existing:
            continue
        db.add(
            AppInstallation(
                app_id=manifest.id,
                enabled=manifest.default_enabled,
                config_json=json.dumps(manifest.default_config, ensure_ascii=False),
            )
        )
        changed = True
    if changed:
        db.flush()


def get_installation(db: Session, app_id: str) -> AppInstallation | None:
    return db.scalar(select(AppInstallation).where(AppInstallation.app_id == app_id))


def require_enabled_app(db: Session, app_id: str) -> tuple[AppManifest, AppInstallation]:
    from fastapi import HTTPException

    manifest = get_manifest(app_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    ensure_app_installations(db)
    installation = get_installation(db, app_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="应用未安装")
    if not installation.enabled:
        raise HTTPException(status_code=403, detail="应用已禁用，请先在应用中心启用")
    return manifest, installation


def installation_config(installation: AppInstallation, manifest: AppManifest) -> dict[str, Any]:
    return _parse_config(installation.config_json, manifest.default_config)


def app_to_dict(manifest: AppManifest, installation: AppInstallation | None) -> dict[str, Any]:
    enabled = installation.enabled if installation is not None else manifest.default_enabled
    config = installation_config(installation, manifest) if installation is not None else dict(manifest.default_config)
    updated_at = installation.updated_at if installation else None
    return {
        "id": manifest.id,
        "name": manifest.name,
        "description": manifest.description,
        "icon": manifest.icon,
        "category": manifest.category,
        "entry_path": manifest.entry_path,
        "version": manifest.version,
        "capabilities": list(manifest.capabilities),
        "config_schema": manifest.config_schema,
        "enabled": enabled,
        "config": config,
        "bound_account_id": installation.bound_account_id if installation else None,
        "bound_model": installation.bound_model if installation else None,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


def list_apps(db: Session) -> list[dict[str, Any]]:
    ensure_app_installations(db)
    rows = {row.app_id: row for row in db.scalars(select(AppInstallation)).all()}
    return [app_to_dict(manifest, rows.get(manifest.id)) for manifest in list_manifests()]


def get_app(db: Session, app_id: str) -> dict[str, Any]:
    manifest = get_manifest(app_id)
    if manifest is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="应用不存在")
    ensure_app_installations(db)
    installation = get_installation(db, app_id)
    return app_to_dict(manifest, installation)


def update_app(
    db: Session,
    app_id: str,
    *,
    enabled: bool | None = None,
    config: dict[str, Any] | None = None,
    bound_account_id: int | None | object = ...,
    bound_model: str | None | object = ...,
) -> dict[str, Any]:
    from fastapi import HTTPException

    manifest = get_manifest(app_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="应用不存在")
    ensure_app_installations(db)
    installation = get_installation(db, app_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="应用未安装")

    if enabled is not None:
        installation.enabled = bool(enabled)
    if config is not None:
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="config 必须是对象")
        merged = installation_config(installation, manifest)
        merged.update(config)
        installation.config_json = json.dumps(merged, ensure_ascii=False)
    if bound_account_id is not ...:
        installation.bound_account_id = bound_account_id  # type: ignore[assignment]
    if bound_model is not ...:
        value = bound_model
        if isinstance(value, str):
            value = value.strip() or None
        installation.bound_model = value  # type: ignore[assignment]
    installation.updated_at = utcnow()
    db.add(installation)
    db.flush()
    return app_to_dict(manifest, installation)
