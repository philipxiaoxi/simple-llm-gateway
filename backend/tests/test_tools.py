from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db import get_session_factory
from app.models import DesktopTool, DesktopToolRun, DesktopToolRunLog
from app.services.desktop_tools import reconcile_stuck_downloads, start_download


def _add_tool(status: str) -> DesktopTool:
    session = get_session_factory()()
    try:
        item = DesktopTool(
            tool_id=f"tool-{status}",
            platform="windows",
            name=f"工具-{status}",
            description="",
            script_name=f"tool-{status}.py",
            status=status,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item
    finally:
        session.close()


def test_reconcile_stuck_downloads_resets_downloading(client: TestClient) -> None:
    """后端重启后，卡在 downloading 的工具应被复位为 not_downloaded。"""
    stuck = _add_tool("downloading")
    done = _add_tool("downloaded")

    session = get_session_factory()()
    try:
        reconcile_stuck_downloads(session)
    finally:
        session.close()

    session = get_session_factory()()
    try:
        assert session.get(DesktopTool, done.id).status == "downloaded"
    finally:
        session.close()

    session = get_session_factory()()
    try:
        assert session.get(DesktopTool, stuck.id).status == "not_downloaded"
        # 非 downloading 状态不受影响
        assert session.get(DesktopTool, done.id).status == "downloaded"
    finally:
        session.close()


def test_reconcile_stuck_downloads_noop_when_none_stuck(client: TestClient) -> None:
    """没有卡住的 downloading 工具时，reconcile 不应报错也不应改动其他状态。"""
    done = _add_tool("downloaded")

    session = get_session_factory()()
    try:
        reconcile_stuck_downloads(session)
    finally:
        session.close()


def test_start_download_rejects_database_downloading_state(client: TestClient) -> None:
    item = _add_tool("downloading")
    session = get_session_factory()()
    try:
        with pytest.raises(RuntimeError, match="下载中"):
            asyncio.run(start_download(session, item))
    finally:
        session.close()


def test_tool_run_history_is_persisted_and_queryable(client: TestClient, auth_headers: dict[str, str]) -> None:
    tool = _add_tool("failed")
    session = get_session_factory()()
    try:
        run = DesktopToolRun(tool_id=tool.id, status="failed", error_message="网络中断")
        session.add(run)
        session.commit()
        session.refresh(run)
        session.add_all([
            DesktopToolRunLog(run_id=run.id, line_number=1, line="开始下载"),
            DesktopToolRunLog(run_id=run.id, line_number=2, line="网络中断"),
        ])
        session.commit()
        run_id = run.id
    finally:
        session.close()

    response = client.get(f"/api/admin/tools/{tool.id}/runs", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()[0]["id"] == run_id
    assert response.json()[0]["error_message"] == "网络中断"

    detail = client.get(f"/api/admin/tools/{tool.id}/runs/{run_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["lines"] == ["开始下载", "网络中断"]
