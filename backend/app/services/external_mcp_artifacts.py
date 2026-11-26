from __future__ import annotations

import logging
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.config import get_settings
from app.models import ExternalMcpArtifact

logger = logging.getLogger(__name__)
ID_PATTERN = r"^[0-9a-f]{32}$"
ArtifactId = Annotated[str, Field(pattern=ID_PATTERN)]
MAX_FILES = 4
MAX_SIZE = 32 * 1024 * 1024
STORAGE_QUOTA = 512 * 1024 * 1024
DOWNLOAD_TTL = timedelta(minutes=15)
DOWNLOAD_PATH = "/v1/external-mcp/artifacts/"
DOWNLOAD_AUDIENCE = "external-mcp-download"


class CreateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: ArtifactId
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=128,
                              pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.+_-]*/[a-zA-Z0-9][a-zA-Z0-9.+_-]*$")
    size_bytes: int = Field(strict=True, gt=0, le=MAX_SIZE)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if (not value.strip() or value in {".", ".."} or "/" in value or "\\" in value
                or any(unicodedata.category(char).startswith("C") for char in value)):
            raise ValueError("Invalid filename")
        return value


class ArtifactResult(dict):
    """Trusted file metadata kept separate from the worker's schema-validated JSON."""

    def __init__(self, value: dict, artifacts: list[dict]):
        super().__init__(value)
        self.artifacts = artifacts

    def envelope(self) -> dict:
        return {"result": dict(self), "artifacts": self.artifacts}


def public_base() -> str:
    base = get_settings().app_base_url.rstrip("/")
    url = urlsplit(base)
    if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
            or (url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("APP_BASE_URL must be a public HTTPS base URL (loopback HTTP allowed for development)")
    return base


def file_path(artifact_id: str, *, partial: bool = False):
    return get_settings().data_dir / "external_mcp_artifacts" / (artifact_id + (".part" if partial else ""))


def delivery_metadata(artifact: ExternalMcpArtifact, base: str) -> dict:
    expires = artifact.expires_at.replace(tzinfo=UTC)
    token = jwt.encode({"aud": DOWNLOAD_AUDIENCE, "sub": artifact.id, "exp": expires},
                       get_settings().app_secret_key, algorithm="HS256")
    return {"artifact_id": artifact.id, "filename": artifact.filename, "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes, "expires_at": expires.isoformat(),
            "download_url": f"{base}{DOWNLOAD_PATH}{artifact.id}/download?token={token}"}


def valid_download_token(artifact_id: str, token: str) -> bool:
    try:
        claims = jwt.decode(token, get_settings().app_secret_key, algorithms=["HS256"], audience=DOWNLOAD_AUDIENCE,
                            options={"require": ["sub", "exp"]})
        return claims["sub"] == artifact_id
    except jwt.PyJWTError:
        return False


def cleanup_expired(db, now: datetime) -> None:
    expired = db.scalars(select(ExternalMcpArtifact).where(ExternalMcpArtifact.expires_at <= now).limit(256)).all()
    for artifact in expired:
        try:
            file_path(artifact.id).unlink(missing_ok=True)
            file_path(artifact.id, partial=True).unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove expired external MCP artifact %s", artifact.id)
        else:
            db.delete(artifact)
