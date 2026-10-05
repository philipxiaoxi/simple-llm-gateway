"""资讯 AI 判定（广告过滤 + 价值打分）后端测试。

上游调用一律 monkeypatch，不依赖网络与真实账号。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.clock import utcnow
from app.db import get_session_factory
from app.info import storage
from app.models import InfoItem, InfoMedia, UpstreamAccount


def _session():
    return get_session_factory()()


def _seed_account(account_id: int = 1, *, name: str = "测试账号") -> int:
    session = _session()
    try:
        session.add(
            UpstreamAccount(
                id=account_id,
                name=name,
                provider="openai",
                auth_type="api_key",
                base_url="https://api.example.com/v1",
                status="active",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.commit()
    finally:
        session.close()
    return account_id


def _seed_item(
    item_id: str,
    *,
    text: str = "分享一个开源工具",
    ai_status: str = "pending",
    with_image: bool = False,
    is_featured: bool = False,
    featured_manual: bool = False,
) -> None:
    session = _session()
    try:
        now = utcnow()
        session.add(
            InfoItem(
                id=item_id,
                source_id=None,
                external_id=item_id,
                kind="image" if with_image else "text",
                text=text,
                excerpt=text[:100],
                media_count=1 if with_image else 0,
                cover_seed=1,
                status="ready",
                ai_status=ai_status,
                is_featured=is_featured,
                ai_featured_manual=featured_manual,
                collected_at=now,
                created_at=now,
            )
        )
        if with_image:
            session.add(
                InfoMedia(
                    id=f"{item_id}-m",
                    item_id=item_id,
                    index_no=0,
                    kind="image",
                    remote_url="https://cdn.example.com/1.jpg",
                    filename="000.jpg",
                    content_type="image/jpeg",
                    size_bytes=4,
                    status="ready",
                )
            )
        session.commit()
    finally:
        session.close()
    if with_image:
        storage.media_path(item_id, "000.jpg").write_bytes(b"\xff\xd8\xff\xd9")


def _fake_result(payload: dict):
    class _Message:
        content = json.dumps(payload, ensure_ascii=False)

    class _Choice:
        message = _Message()

    class _Result:
        choices = [_Choice()]

    return _Result()


def _configure(client: TestClient, auth_headers: dict[str, str], **overrides) -> None:
    payload = {
        "enabled": True,
        "account_id": 1,
        "model": "deepseek-chat",
        "feature_threshold": 80,
        "hide_ads": True,
        "vision_max_images": 3,
        "max_attempts": 2,
    }
    payload.update(overrides)
    response = client.put("/api/admin/info/ai/settings", headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text


def _run_worker(monkeypatch, results: dict[str, dict]):
    """按条目文本路由返回不同判定结果。"""

    async def fake_call_chat(account, messages, model, stream, extra, api_key):  # noqa: ANN001
        text = messages[0]["content"][0]["text"]
        if "加群" in text:
            return _fake_result(results["ad"])
        return _fake_result(results["good"])

    monkeypatch.setattr("app.services.info_ai.call_chat", fake_call_chat)
    monkeypatch.setattr("app.services.info_ai.require_upstream_credential", lambda account: "token")
    return asyncio.run(_import_info_ai().score_pending_once())


def _import_info_ai():
    from app.services import info_ai

    return info_ai


def test_ai_settings_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    missing = client.put(
        "/api/admin/info/ai/settings",
        headers=auth_headers,
        json={"enabled": True, "account_id": None},
    )
    assert missing.status_code == 400

    _seed_account(1)
    bad_threshold = client.put(
        "/api/admin/info/ai/settings",
        headers=auth_headers,
        json={"enabled": True, "account_id": 1, "feature_threshold": 200},
    )
    assert bad_threshold.status_code == 400

    unknown_account = client.put(
        "/api/admin/info/ai/settings",
        headers=auth_headers,
        json={"enabled": True, "account_id": 999},
    )
    assert unknown_account.status_code == 400


def test_ad_filtering_and_scoring(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _seed_account(1)
    _seed_item("item-ad", text="广告 加群 带货 优惠券")
    _seed_item("item-good", text="推荐一个优秀的开源工具")
    _configure(client, auth_headers)

    result = _run_worker(
        monkeypatch,
        {
            "ad": {"is_ad": True, "score": 5, "label": "ad", "tags": ["推广"], "reason": "纯广告"},
            "good": {"is_ad": False, "score": 92, "label": "valuable", "tags": ["工具"], "reason": "优质工具推荐"},
        },
    )
    assert result["scored"] == 2

    session = _session()
    try:
        ad = session.get(InfoItem, "item-ad")
        good = session.get(InfoItem, "item-good")
        assert ad.ai_status == "done"
        assert ad.ai_label == "ad"
        assert ad.is_hidden is True
        assert ad.is_featured is False
        assert good.ai_status == "done"
        assert good.ai_score == 92
        assert good.is_featured is True
    finally:
        session.close()

    # 默认列表过滤广告；精选筛选只剩高价值条目
    default_items = client.get("/api/admin/info/items?limit=10", headers=auth_headers).json()["items"]
    assert {item["id"] for item in default_items} == {"item-good"}
    featured = client.get("/api/admin/info/items?featured=1", headers=auth_headers).json()["items"]
    assert {item["id"] for item in featured} == {"item-good"}
    min_score = client.get("/api/admin/info/items?min_score=90", headers=auth_headers).json()["items"]
    assert {item["id"] for item in min_score} == {"item-good"}


def test_parse_failure_marks_failed(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    _seed_account(1)
    _seed_item("item-bad", text="任意内容")
    _configure(client, auth_headers, max_attempts=1)

    async def fake_call_chat(*args, **kwargs):  # noqa: ANN002, ANN003
        class _Message:
            content = "这不是 JSON"

        class _Choice:
            message = _Message()

        class _Result:
            choices = [_Choice()]

        return _Result()

    monkeypatch.setattr("app.services.info_ai.call_chat", fake_call_chat)
    monkeypatch.setattr("app.services.info_ai.require_upstream_credential", lambda account: "token")
    result = asyncio.run(_import_info_ai().score_pending_once())
    assert result["failed"] == 1

    session = _session()
    try:
        row = session.get(InfoItem, "item-bad")
        assert row.ai_status == "failed"
        assert row.ai_attempts == 1
        assert row.ai_error
    finally:
        session.close()


def test_vision_gating(client: TestClient, auth_headers: dict[str, str], monkeypatch) -> None:
    _seed_account(1)
    _seed_item("item-vision", text="带图内容", with_image=True)
    _configure(client, auth_headers, model="gpt-4o-mini")

    captured: list[dict] = []

    async def fake_call_chat(account, messages, model, stream, extra, api_key):  # noqa: ANN001
        captured.append(messages[0]["content"])
        return _fake_result({"is_ad": False, "score": 50, "label": "general", "tags": [], "reason": ""})

    monkeypatch.setattr("app.services.info_ai.call_chat", fake_call_chat)
    monkeypatch.setattr("app.services.info_ai.require_upstream_credential", lambda account: "token")
    asyncio.run(_import_info_ai().score_pending_once())

    assert any(part.get("type") == "image_url" for part in captured[0])

    # 换成非视觉模型后重新判定：不再附带图片
    _configure(client, auth_headers, model="deepseek-chat")
    captured.clear()
    session = _session()
    try:
        row = session.get(InfoItem, "item-vision")
        row.ai_status = "pending"
        session.commit()
    finally:
        session.close()
    asyncio.run(_import_info_ai().score_pending_once())
    assert not any(part.get("type") == "image_url" for part in captured[0])


def test_manual_featured_override_persists(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _seed_account(1)
    _seed_item("item-manual", text="人工精选内容", is_featured=True, featured_manual=True)
    _configure(client, auth_headers)

    async def fake_call_chat(*args, **kwargs):  # noqa: ANN002, ANN003
        return _fake_result({"is_ad": False, "score": 10, "label": "general", "tags": [], "reason": ""})

    monkeypatch.setattr("app.services.info_ai.call_chat", fake_call_chat)
    monkeypatch.setattr("app.services.info_ai.require_upstream_credential", lambda account: "token")
    asyncio.run(_import_info_ai().score_pending_once())

    session = _session()
    try:
        row = session.get(InfoItem, "item-manual")
        assert row.ai_score == 10
        assert row.is_featured is True
    finally:
        session.close()


def test_rescore_resets_done_items(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed_account(1)
    _seed_item("item-done", ai_status="done")
    response = client.post("/api/admin/info/ai/rescore", headers=auth_headers, json={"scope": "all"})
    assert response.status_code == 200
    assert response.json()["count"] == 1

    session = _session()
    try:
        row = session.get(InfoItem, "item-done")
        assert row.ai_status == "pending"
        assert row.ai_attempts == 0
    finally:
        session.close()
