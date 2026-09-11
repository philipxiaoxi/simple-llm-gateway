"""语音房管理 API 测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.voice_hub import voice_hub
from app.services.voice_session import voice_session_manager


def _create_room(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    payload = {
        "name": "书房",
        "polishMode": "error_fix",
        "asrModel": "qwen-audio-3.0-asr-flash-streaming",
    }
    payload.update(overrides)
    response = client.post("/api/admin/voice/rooms", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_admin(client: TestClient) -> None:
    assert client.get("/api/admin/voice/rooms").status_code == 401
    assert client.post("/api/admin/voice/rooms", json={"name": "x"}).status_code == 401


def test_create_and_list_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    assert room["roomId"] and len(room["roomId"]) == 8
    assert room["joinCode"] and len(room["joinCode"]) == 6
    assert room["polishMode"] == "error_fix"
    assert room["requirePin"] is False

    listing = client.get("/api/admin/voice/rooms", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["roomId"] == room["roomId"]
    assert body["items"][0]["online"] == {"busy": False, "phones": 0, "desktops": 0}


def test_create_room_with_pin(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers, pin="2468")
    assert room["requirePin"] is True
    detail = client.get(f"/api/admin/voice/rooms/{room['roomId']}", headers=auth_headers).json()
    assert detail["requirePin"] is True


def test_reject_unknown_asr_model(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/voice/rooms",
        json={"name": "x", "asrModel": "not-a-model"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "不支持的语音识别模型" in response.json()["detail"]


def test_reject_unknown_polish_mode(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/voice/rooms",
        json={"name": "x", "polishMode": "fancy"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_update_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    response = client.patch(
        f"/api/admin/voice/rooms/{room['roomId']}",
        json={"name": "客厅", "polishMode": "off", "disfluencyRemoval": False},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "客厅"
    assert body["polishMode"] == "off"
    assert body["disfluencyRemoval"] is False


def test_rotate_code_changes_code(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    response = client.post(f"/api/admin/voice/rooms/{room['roomId']}/rotate-code", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["joinCode"] != room["joinCode"]


def test_delete_room(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    assert client.delete(f"/api/admin/voice/rooms/{room['roomId']}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/admin/voice/rooms/{room['roomId']}", headers=auth_headers).status_code == 404


def test_unknown_room_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/admin/voice/rooms/abcdefgh", headers=auth_headers).status_code == 404


def test_polish_accounts_lists_site_accounts(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/voice/polish-accounts", headers=auth_headers)
    assert response.status_code == 200
    assert "items" in response.json()


def test_issue_desktop_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    response = client.post(f"/api/admin/voice/rooms/{room['roomId']}/token", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["desktopConfig"]["roomId"] == room["roomId"]
    assert body["desktopConfig"]["token"]
    assert body["desktopConfig"]["serverUrl"].endswith("/api/voice/desktop/connect")

    from app.routers.voice_rooms import verify_room_token

    claims = verify_room_token(body["token"], room_id=room["roomId"], role="desktop")
    assert claims["scope"] == "voice"


def test_live_endpoint_shape(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    response = client.get(f"/api/admin/voice/rooms/{room['roomId']}/live", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["room"]["roomId"] == room["roomId"]
    assert body["recentSegments"] == []
    assert body["sessions"] == []


def test_segments_and_events_pagination(client: TestClient, auth_headers: dict[str, str]) -> None:
    room = _create_room(client, auth_headers)
    segments = client.get(f"/api/admin/voice/rooms/{room['roomId']}/segments", headers=auth_headers)
    assert segments.status_code == 200
    assert segments.json()["total"] == 0
    events = client.get(f"/api/admin/voice/rooms/{room['roomId']}/events", headers=auth_headers)
    assert events.status_code == 200
    assert events.json()["kinds"] == []


def test_cleanup_singletons() -> None:
    voice_hub._rooms.clear()
    voice_session_manager._rooms.clear()
