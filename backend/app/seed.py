from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.crypto import hash_password
from app.db import get_session_factory
from app.models import Admin


def seed_admin() -> None:
    settings = get_settings()
    session = get_session_factory()()
    try:
        existing = session.scalar(select(Admin).where(Admin.username == settings.admin_username))
        if existing is not None:
            return
        session.add(
            Admin(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
            )
        )
        session.commit()
    finally:
        session.close()
