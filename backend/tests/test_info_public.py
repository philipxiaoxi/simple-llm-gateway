"""公开资讯接口：默认精选、隐藏渠道来源、排除被隐藏条目。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.clock import utcnow
from app.db import get_session_factory
from app.models import InfoItem
from app.services import info_public_gate as gate


def _seed() -> None:
    now = utcnow()
    session = get_session_factory()()
    try:
        session.add_all(
            [
                InfoItem(
                    id="pub-featured",
                    source_id=None,
                    external_id="pub-featured",
                    kind="text",
                    text="精选内容",
                    excerpt="精选内容",
                    media_count=0,
                    cover_seed=1,
                    status="ready",
                    ai_status="done",
                    ai_score=90,
                    is_featured=True,
                    is_hidden=False,
                    permalink="https://t.me/secretchannel/1",
                    author_name="secretchannel",
                    collected_at=now,
                    created_at=now,
                ),
                InfoItem(
                    id="pub-normal",
                    source_id=None,
                    external_id="pub-normal",
                    kind="text",
                    text="普通内容",
                    excerpt="普通内容",
                    media_count=0,
                    cover_seed=2,
                    status="ready",
                    ai_status="done",
                    ai_score=40,
                    is_featured=False,
                    is_hidden=False,
                    collected_at=now,
                    created_at=now,
                ),
                InfoItem(
                    id="pub-hidden",
                    source_id=None,
                    external_id="pub-hidden",
                    kind="text",
                    text="广告内容",
                    excerpt="广告内容",
                    media_count=0,
                    cover_seed=3,
                    status="ready",
                    ai_status="done",
                    ai_label="ad",
                    is_featured=True,
                    is_hidden=True,
                    collected_at=now,
                    created_at=now,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def test_public_items_default_featured_and_strips_source(client: TestClient) -> None:
    _seed()

    response = client.get("/api/public/info/items")
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [row["id"] for row in body["items"]]
    assert ids == ["pub-featured"]  # 默认精选；隐藏条目被排除

    row = body["items"][0]
    assert row["source"] is None
    assert row["author_name"] == ""
    assert "permalink" not in row
    assert "is_hidden" not in row

    # 全部（含非精选），仍排除隐藏
    response = client.get("/api/public/info/items", params={"featured": 0})
    ids = [row["id"] for row in response.json()["items"]]
    assert set(ids) == {"pub-featured", "pub-normal"}

    # 详情：隐藏条目 404
    assert client.get("/api/public/info/items/pub-featured").status_code == 200
    assert client.get("/api/public/info/items/pub-hidden").status_code == 404

    detail = client.get("/api/public/info/items/pub-featured").json()
    assert detail["source"] is None
    assert detail["media"] == []

    stats = client.get("/api/public/info/stats").json()
    assert stats["item_count"] == 2
    assert stats["featured_count"] == 1


def _enable_gate(client: TestClient, auth_headers: dict[str, str], password: str = "secret123") -> dict:
    gate.unlock_gate.reset()
    response = client.put(
        "/api/admin/public-gate?scope=info", json={"password": password}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_public_gate_disabled_by_default(client: TestClient) -> None:
    _seed()
    status = client.get("/api/public/info/gate").json()
    assert status["required"] is False
    assert status["unlocked"] is True
    assert status["watermark"] is None
    assert client.get("/api/public/info/items").status_code == 200


def test_public_gate_requires_password(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed()
    status = _enable_gate(client, auth_headers)
    assert status["required"] is True and status["has_password"] is True

    client.cookies.clear()
    assert client.get("/api/public/info/items").status_code == 401
    assert client.get("/api/public/info/items/pub-featured").status_code == 401
    assert client.get("/api/public/info/stats").status_code == 401

    status = client.get("/api/public/info/gate").json()
    assert status["required"] is True
    assert status["unlocked"] is False
    assert status["watermark"] is None

    bad = client.post("/api/public/info/unlock", json={"password": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/api/public/info/unlock", json={"password": "secret123"})
    assert ok.status_code == 200, ok.text
    code = ok.json()["watermark"]["code"]
    assert len(code) == 8
    assert client.get("/api/public/info/items").status_code == 200
    assert client.get("/api/public/info/items/pub-featured").status_code == 200
    assert client.get("/api/public/info/stats").status_code == 200

    # 解锁后 /gate 下发可溯源水印码，且与后台会话记录一致
    status = client.get("/api/public/info/gate").json()
    assert status["unlocked"] is True
    assert status["watermark"]["code"] == code
    sessions = client.get(
        "/api/admin/public-gate/sessions?scope=info", headers=auth_headers
    ).json()["sessions"]
    match = next(row for row in sessions if row["code"] == code)
    assert match["gate_version"] >= 1
    settings = client.get("/api/admin/public-gate?scope=info", headers=auth_headers).json()
    assert len(match["password_fingerprint"]) == 8
    assert match["password_fingerprint"] == settings["password_fingerprint"]

    client.post("/api/public/info/lock")
    assert client.get("/api/public/info/items").status_code == 401


def test_public_gate_password_rotation_invalidates_session(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _seed()
    _enable_gate(client, auth_headers, "secret123")
    client.cookies.clear()
    assert client.post("/api/public/info/unlock", json={"password": "secret123"}).status_code == 200
    assert client.get("/api/public/info/items").status_code == 200

    client.put(
        "/api/admin/public-gate?scope=info", json={"password": "newsecret1"}, headers=auth_headers
    )
    assert client.get("/api/public/info/items").status_code == 401

    gate.unlock_gate.reset()
    assert client.post("/api/public/info/unlock", json={"password": "newsecret1"}).status_code == 200
    assert client.get("/api/public/info/items").status_code == 200


def test_public_gate_clear_reopens(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed()
    _enable_gate(client, auth_headers)
    client.cookies.clear()
    assert client.get("/api/public/info/items").status_code == 401

    response = client.put(
        "/api/admin/public-gate?scope=info", json={"clear_password": True}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["required"] is False
    assert client.get("/api/public/info/items").status_code == 200
    status = client.get("/api/public/info/gate").json()
    assert status["required"] is False and status["watermark"] is None


def test_public_gate_rejects_short_password(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed()
    response = client.put(
        "/api/admin/public-gate?scope=info", json={"password": "123"}, headers=auth_headers
    )
    assert response.status_code == 400
    # 后台接口需要管理员鉴权
    assert client.get("/api/admin/public-gate?scope=info").status_code == 401
