from __future__ import annotations

import sys

from sqlalchemy import select

from app.config import get_settings, validate_bootstrap_admin_password
from app.crypto import hash_password
from app.db import get_session_factory
from app.models import Admin, DesktopTool
from app.services.skills import ensure_skill_categories


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


def seed_skill_categories() -> None:
    session = get_session_factory()()
    try:
        ensure_skill_categories(session)
        session.commit()
    finally:
        session.close()


def seed_desktop_tools() -> None:
    session = get_session_factory()()
    try:
        if session.scalar(select(DesktopTool).limit(1)) is None:
            script = get_settings().resolved_tools_path / "scripts" / "demo.py"
            script.write_text(
                'import json, pathlib, sys\n'
                'payload = json.loads(sys.argv[1])\n'
                'print("开始准备安装包", flush=True)\n'
                'path = pathlib.Path(payload["output_dir"]) / "demo.txt"\n'
                'path.parent.mkdir(parents=True, exist_ok=True)\n'
                'path.write_text("desktop tool demo", encoding="utf-8")\n'
                'print(json.dumps({"status":"success","file_path":str(path),"file_size":path.stat().st_size,"version":"1.0.0","error_message":None}), flush=True)\n',
                encoding="utf-8",
            )
            platform = "windows" if sys.platform.startswith("win") else "linux"
            session.add(DesktopTool(tool_id="demo-tool", platform=platform, name="示例桌面工具", description="可编辑脚本的示例工具，用于验证预下载链路。", script_name="demo.py"))
            session.commit()
    finally:
        session.close()
