"""语音日志清理测试。

日志会持续增长，正式服必须有清理手段；同时清理不能误删新数据、不能拖垮主流程。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.clock import utcnow
from app.db import get_session_factory
from app.models import VoiceEvent, VoiceRoom, VoiceSegment, VoiceSession
from app.services.voice_retention import cleanup_voice_logs, voice_log_stats


def _seed(room_pk: int, *, age_days: int, seq: int) -> None:
    """写一组段落/事件/会话，created_at 回溯 age_days 天。"""
    when = utcnow() - timedelta(days=age_days)
    db = get_session_factory()()
    try:
        session = VoiceSession(
            session_uid=f"sess-{seq}",
            room_pk=room_pk,
            status="finished",
            asr_model="m",
            started_at=when,
        )
        db.add(session)
        db.flush()
        segment = VoiceSegment(
            seg_uid=f"seg-{seq}",
            session_id=session.id,
            room_pk=room_pk,
            seq=seq,
            rev=1,
            state="final",
            raw_text="测试",
            created_at=when,
        )
        db.add(segment)
        db.flush()
        db.add(
            VoiceEvent(
                room_pk=room_pk,
                session_id=session.id,
                segment_id=segment.id,
                kind="asr.final",
                level="info",
                message="测试",
                created_at=when,
            )
        )
        db.commit()
    finally:
        db.close()


def _make_room(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/admin/voice/rooms", json={"name": "清理测试", "polishMode": "off"}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["roomId"]


def _room_pk(room_id: str) -> int:
    db = get_session_factory()()
    try:
        return db.query(VoiceRoom).filter(VoiceRoom.room_id == room_id).one().id
    finally:
        db.close()


def test_cleanup_removes_only_expired(client: TestClient, auth_headers: dict[str, str]) -> None:
    room_id = _make_room(client, auth_headers)
    room_pk = _room_pk(room_id)
    _seed(room_pk, age_days=40, seq=1)  # 过期
    _seed(room_pk, age_days=2, seq=2)  # 新鲜

    stats_before = voice_log_stats()
    assert stats_before["segments"] == 2 and stats_before["events"] == 2

    removed = cleanup_voice_logs(retention_days=30)
    assert removed["segments"] == 1
    assert removed["events"] == 1

    stats_after = voice_log_stats()
    assert stats_after["segments"] == 1
    assert stats_after["events"] == 1

    db = get_session_factory()()
    try:
        remaining = db.query(VoiceSegment).one()
        assert remaining.seg_uid == "seg-2"  # 留下的是新鲜的
    finally:
        db.close()


def test_cleanup_zero_days_is_noop(client: TestClient, auth_headers: dict[str, str]) -> None:
    room_pk = _room_pk(_make_room(client, auth_headers))
    _seed(room_pk, age_days=400, seq=1)
    removed = cleanup_voice_logs(retention_days=0)
    assert removed.get("skipped") == 1
    assert voice_log_stats()["segments"] == 1


def test_cleanup_keeps_sessions_with_segments(client: TestClient, auth_headers: dict[str, str]) -> None:
    """只要还有段落引用该会话，就不该把会话删掉（否则日志页会缺上下文）。"""
    room_pk = _room_pk(_make_room(client, auth_headers))
    _seed(room_pk, age_days=90, seq=1)

    db = get_session_factory()()
    try:
        # 只把段落改成"新鲜"，会话仍是旧的
        segment = db.query(VoiceSegment).one()
        segment.created_at = utcnow()
        db.commit()
    finally:
        db.close()

    cleanup_voice_logs(retention_days=30)
    stats = voice_log_stats()
    assert stats["segments"] == 1
    assert stats["sessions"] == 1  # 会话被保留


def test_cleanup_removes_orphan_session(client: TestClient, auth_headers: dict[str, str]) -> None:
    room_pk = _room_pk(_make_room(client, auth_headers))
    _seed(room_pk, age_days=90, seq=1)
    cleanup_voice_logs(retention_days=30)  # 段落和事件都过期，一起删
    stats = voice_log_stats()
    assert stats["segments"] == 0
    assert stats["events"] == 0
    assert stats["sessions"] == 0  # 会话成了孤儿，一并清掉


def test_storage_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/admin/voice/storage", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"segments", "events", "sessions", "retentionDays"}
    assert body["retentionDays"] == 30


def test_cleanup_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    room_pk = _room_pk(_make_room(client, auth_headers))
    _seed(room_pk, age_days=99, seq=1)

    response = client.post("/api/admin/voice/cleanup?retention_days=7", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["removed"]["segments"] == 1
    assert body["retentionDays"] == 7


def test_cleanup_endpoints_require_admin(client: TestClient) -> None:
    assert client.get("/api/admin/voice/storage").status_code == 401
    assert client.post("/api/admin/voice/cleanup").status_code == 401
