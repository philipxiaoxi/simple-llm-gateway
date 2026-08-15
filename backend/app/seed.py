from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings, validate_bootstrap_admin_password
from app.crypto import hash_password
from app.db import get_session_factory
from app.models import Admin


def seed_admin() -> None:
    settings = get_settings()
    session = get_session_factory()()
    try:
        existing = session.scalar(select(Admin).limit(1))
        if existing is not None:
            return
        validate_bootstrap_admin_password(settings.admin_password)
        session.add(
            Admin(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
            )
        )
        session.commit()
    finally:
        session.close()
