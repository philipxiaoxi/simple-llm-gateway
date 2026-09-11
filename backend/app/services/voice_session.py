"""一次录音会话的编排：音频直通 → ASR → 分段广播 → AI 纠错 → 替换 → 全量日志。

关键设计：
- 音频帧从手机 WS 直接转发给阿里云，中间不做任何缓冲或转码。
- 所有落库操作走 asyncio.to_thread，绝不阻塞事件循环（音频路径对延迟极敏感）。
- 日志写失败必须吞掉异常，否则会拖垮整条语音链路。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import VoiceClient, VoiceEvent, VoiceRoom, VoiceSegment, VoiceSession
from app.services.voice_asr import AsrError, FinalSentence, VoiceAsrSession
from app.services.voice_hub import ROLE_DESKTOP, ROLE_PHONE, VoiceConnection, voice_hub
from app.services.voice_polish import MODE_OFF, edit_distance_ratio, polish_text, should_apply

# 中间结果下发节流：太快会刷屏，太慢会显得卡
PARTIAL_BROADCAST_INTERVAL_MS = 120
# 纠错结果"过期"判定：该段之后又说了这么多段，或距终稿超过这么久，就不再替换
STALE_AFTER_SEGMENTS = 2
STALE_AFTER_MS = 12_000


@dataclass
class LiveSegment:
    """内存中的当前段落状态。"""

    seg_uid: str
    seq: int
    db_id: int | None = None
    raw_text: str = ""
    rev: int = 0
    state: str = "partial"
    finalized_at_ms: int | None = None
    last_broadcast_ms: int = 0
    polish_task: asyncio.Task[None] | None = None


@dataclass
class LiveSession:
    """一次按住说话的会话状态。"""

    session_uid: str
    room_pk: int
    room_id: str
    client_id: int | None
    client_name: str
    client_uid: str
    asr_model: str
    polish_mode: str = MODE_OFF
    polish_account_id: int | None = None
    polish_model: str | None = None
    polish_system_prompt: str | None = None
    polish_temperature: float = 0.2
    started_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    db_id: int | None = None
    asr: VoiceAsrSession | None = None
    audio_bytes: int = 0
    frame_count: int = 0
    sentence_count: int = 0
    first_partial_ms: int | None = None
    current: LiveSegment | None = None
    recent_finals: list[str] = field(default_factory=list)
    stopping: bool = False
    discard: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def audio_ms(self) -> int:
        return self.audio_bytes // 32  # 16000Hz * 2 字节


class VoiceRoomRuntime:
    """单个房间的运行时状态（会话 + 段落序号）。"""

    def __init__(self, room_pk: int, room_id: str) -> None:
        self.room_pk = room_pk
        self.room_id = room_id
        self.session: LiveSession | None = None
        self.next_seq = 1
        self.lock = asyncio.Lock()


class VoiceSessionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, VoiceRoomRuntime] = {}
        self._polish_semaphore = asyncio.Semaphore(max(1, get_settings().voice_polish_concurrency))

    def runtime(self, room_pk: int, room_id: str) -> VoiceRoomRuntime:
        runtime = self._rooms.get(room_id)
        if runtime is None or runtime.room_pk != room_pk:
            runtime = VoiceRoomRuntime(room_pk, room_id)
            self._rooms[room_id] = runtime
        return runtime

    def drop_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    # ---------- 会话生命周期 ----------

    async def start(
        self,
        *,
        room: VoiceRoom,
        connection: VoiceConnection,
        client_id: int | None,
        recording_id: str | None,
    ) -> LiveSession:
        runtime = self.runtime(room.id, room.room_id)
        async with runtime.lock:
            if runtime.session is not None and not runtime.session.stopping:
                await self._finish_locked(runtime, runtime.session, reason="superseded")

            api_key = get_settings().aliyun_dashscope_api_key.strip()
            if not api_key:
                raise AsrError("服务端未配置阿里云语音识别 API Key", code="ASR_UNAVAILABLE")

            session = LiveSession(
                session_uid=str(uuid.uuid4()),
                room_pk=room.id,
                room_id=room.room_id,
                client_id=client_id,
                client_name=connection.name,
                client_uid=connection.client_uid,
                asr_model=room.asr_model,
                polish_mode=room.polish_mode,
                polish_account_id=room.polish_account_id,
                polish_model=room.polish_model,
                polish_system_prompt=room.polish_system_prompt,
                polish_temperature=room.polish_temperature,
            )
            session.asr = VoiceAsrSession(
                api_key=api_key,
                model=room.asr_model,
                disfluency_removal=bool(room.disfluency_removal),
                on_partial=lambda text: self._on_partial(session, text),
                on_final=lambda sentence: self._on_final(session, sentence),
            )
            await session.asr.start()  # 失败会抛 AsrError，由路由层转成客户端错误

            runtime.session = session
            session.db_id = await asyncio.to_thread(
                self._insert_session, session, room, recording_id
            )
            voice_hub.set_busy(room.room_id, True)
            await self._log(
                room.id,
                "session.start",
                session_id=session.db_id,
                client_id=client_id,
                message=f"{connection.name or connection.client_uid} 开始录音",
                payload={
                    "sessionUid": session.session_uid,
                    "recordingId": recording_id,
                    "asrModel": room.asr_model,
                    "disfluencyRemoval": bool(room.disfluency_removal),
                },
            )
            await voice_hub.broadcast_room_state(room.room_id)
            return session

    async def push_audio(self, session: LiveSession, pcm: bytes) -> None:
        if session.asr is None or session.stopping:
            return
        session.audio_bytes += len(pcm)
        session.frame_count += 1
        await session.asr.push_audio(pcm)

    async def stop(self, *, room: VoiceRoom, session: LiveSession, discarded: bool) -> None:
        runtime = self.runtime(room.id, room.room_id)
        async with runtime.lock:
            session.discard = discarded
            await self._finish_locked(runtime, session, reason="discarded" if discarded else "stopped")

    async def abort_room(self, room_id: str, reason: str = "client_gone") -> None:
        runtime = self._rooms.get(room_id)
        if runtime is None or runtime.session is None:
            return
        async with runtime.lock:
            await self._finish_locked(runtime, runtime.session, reason=reason)

    async def _finish_locked(self, runtime: VoiceRoomRuntime, session: LiveSession, *, reason: str) -> None:
        if session.stopping:
            return
        session.stopping = True
        try:
            if session.asr is not None:
                if reason == "discarded":
                    await session.asr.abort()
                else:
                    await session.asr.finish()
        finally:
            with contextlib.suppress(Exception):
                if session.asr is not None:
                    await session.asr.close()
            runtime.session = None
            voice_hub.set_busy(session.room_id, False)

        # 收尾：丢弃就撤回当前段落；正常结束则把还没定稿的中间结果按原文定稿，避免丢字
        pending = session.current
        session.current = None
        if pending is not None and pending.raw_text:
            if reason == "discarded":
                await self._mark_aborted(runtime, session, pending)
            elif pending.state == "partial":
                await self._finalize_segment(session, pending.raw_text, None, None, seg=pending)

        await asyncio.to_thread(self._update_session_end, session)
        await self._log(
            session.room_pk,
            "session.stop" if reason != "discarded" else "session.discarded",
            session_id=session.db_id,
            client_id=session.client_id,
            message=f"录音结束（{reason}）",
            payload={
                "audioMs": session.audio_ms,
                "frames": session.frame_count,
                "sentences": session.sentence_count,
                "usageSeconds": session.asr.usage_seconds if session.asr else None,
                "firstPartialMs": session.first_partial_ms,
            },
        )
        await voice_hub.broadcast_room_state(session.room_id)
        await voice_hub.broadcast_to_phone(
            session.room_id,
            {
                "type": "session.done",
                "sessionUid": session.session_uid,
                "sentences": session.sentence_count,
                "audioMs": session.audio_ms,
                "discarded": reason == "discarded",
            },
        )

    # ---------- ASR 回调 ----------

    async def _on_partial(self, session: LiveSession, text: str) -> None:
        if session.stopping or session.discard:
            return
        if session.first_partial_ms is None:
            session.first_partial_ms = int(time.time() * 1000) - session.started_ms
        runtime = self.runtime(session.room_pk, session.room_id)
        seg = session.current
        if seg is None:
            seg = LiveSegment(seg_uid=str(uuid.uuid4()), seq=runtime.next_seq)
            runtime.next_seq += 1
            session.current = seg
            seg.db_id = await asyncio.to_thread(self._insert_segment, session, seg)
        now = int(time.time() * 1000)
        if seg.rev == 0 and now - seg.last_broadcast_ms < PARTIAL_BROADCAST_INTERVAL_MS:
            seg.raw_text = text
            return
        seg.last_broadcast_ms = now
        seg.raw_text = text
        seg.rev = 0
        seg.state = "partial"
        await voice_hub.broadcast_to_phone(
            session.room_id, {"type": "asr.partial", "segId": seg.seg_uid, "seq": seg.seq, "text": text}
        )
        await voice_hub.broadcast_snapshot(
            session.room_id, seg_uid=seg.seg_uid, seq=seg.seq, rev=0, text=text, final=False
        )

    async def _on_final(self, session: LiveSession, sentence: FinalSentence) -> None:
        if session.discard:
            return
        runtime = self.runtime(session.room_pk, session.room_id)
        seg = session.current
        if seg is None:
            seg = LiveSegment(seg_uid=str(uuid.uuid4()), seq=runtime.next_seq)
            runtime.next_seq += 1
            session.current = seg
            seg.db_id = await asyncio.to_thread(self._insert_segment, session, seg)
        session.current = None
        await self._finalize_segment(session, sentence.text, sentence.begin_ms, sentence.end_ms, seg=seg)

    async def _finalize_segment(
        self,
        session: LiveSession,
        text: str,
        begin_ms: int | None,
        end_ms: int | None,
        *,
        seg: LiveSegment | None = None,
    ) -> None:
        target = seg or session.current
        if target is None or not text.strip():
            return
        target.raw_text = text
        target.rev = 1
        target.state = "final"
        target.finalized_at_ms = int(time.time() * 1000)
        session.sentence_count += 1
        session.recent_finals.append(text)

        if target.db_id is not None:
            await asyncio.to_thread(self._update_segment_final, target, begin_ms, end_ms)

        delivered = await voice_hub.broadcast_snapshot(
            session.room_id, seg_uid=target.seg_uid, seq=target.seq, rev=1, text=text, final=False
        )
        await voice_hub.broadcast_to_phone(
            session.room_id,
            {
                "type": "asr.final",
                "segId": target.seg_uid,
                "seq": target.seq,
                "text": text,
                "beginMs": begin_ms,
                "endMs": end_ms,
            },
        )
        await self._log(
            session.room_pk,
            "asr.final",
            session_id=session.db_id,
            segment_id=target.db_id,
            client_id=session.client_id,
            message=text[:120],
            payload={
                "segId": target.seg_uid,
                "seq": target.seq,
                "text": text,
                "beginMs": begin_ms,
                "endMs": end_ms,
                "delivered": delivered,
            },
        )
        await self._log(
            session.room_pk,
            "segment.send",
            session_id=session.db_id,
            segment_id=target.db_id,
            client_id=session.client_id,
            message=f"下发第 {target.seq} 段（原文）给 {delivered} 台电脑",
            payload={"segId": target.seg_uid, "rev": 1, "text": text, "count": delivered},
        )
        if target.db_id is not None and delivered:
            await asyncio.to_thread(self._bump_deliver_count, target.db_id, delivered)

        if session.polish_mode != MODE_OFF and session.polish_account_id:
            target.polish_task = asyncio.create_task(self._polish_and_revise(session, target))

    async def _polish_and_revise(self, session: LiveSession, seg: LiveSegment) -> None:
        room_pk = session.room_pk
        try:
            async with self._polish_semaphore:
                await self._log(
                    room_pk,
                    "polish.request",
                    session_id=session.db_id,
                    segment_id=seg.db_id,
                    message=f"请求纠错（{session.polish_model or '账号默认模型'}）",
                    payload={
                        "segId": seg.seg_uid,
                        "accountId": session.polish_account_id,
                        "model": session.polish_model,
                        "mode": session.polish_mode,
                        "input": seg.raw_text,
                    },
                )
                db = get_session_factory()()
                try:
                    result = await polish_text(
                        db,
                        account_id=session.polish_account_id,
                        model=session.polish_model,
                        text=seg.raw_text,
                        context=session.recent_finals[:-1],
                        system_prompt=session.polish_system_prompt,
                        mode=session.polish_mode,
                        temperature=session.polish_temperature,
                    )
                finally:
                    db.close()

            await self._log(
                room_pk,
                "polish.result" if result.ok else "polish.failed",
                session_id=session.db_id,
                segment_id=seg.db_id,
                level="info" if result.ok else "warn",
                message=(result.text or result.error or "")[:120],
                payload={
                    "segId": seg.seg_uid,
                    "input": result.raw,
                    "output": result.text,
                    "status": result.status,
                    "model": result.model,
                    "ms": result.ms,
                    "error": result.error,
                },
            )

            if seg.db_id is not None:
                await asyncio.to_thread(self._update_segment_polish, seg.db_id, result)

            if not result.ok:
                return
            if seg.state == "aborted":
                return

            runtime = self.runtime(session.room_pk, session.room_id)
            # 竞态保护：用户已经继续往下说了，就不再回改旧段落
            if runtime.next_seq - seg.seq > STALE_AFTER_SEGMENTS:
                await self._log(
                    room_pk,
                    "polish.stale",
                    session_id=session.db_id,
                    segment_id=seg.db_id,
                    message="纠错结果已过期，未替换",
                    payload={"segId": seg.seg_uid, "reason": "newer_segments"},
                )
                return
            if seg.finalized_at_ms and int(time.time() * 1000) - seg.finalized_at_ms > STALE_AFTER_MS:
                await self._log(
                    room_pk,
                    "polish.stale",
                    session_id=session.db_id,
                    segment_id=seg.db_id,
                    message="纠错结果超时返回，未替换",
                    payload={"segId": seg.seg_uid, "reason": "timeout"},
                )
                return

            apply, reason = should_apply(seg.raw_text, result.text, session.polish_mode)
            if not apply:
                await self._log(
                    room_pk,
                    "polish.skipped",
                    session_id=session.db_id,
                    segment_id=seg.db_id,
                    level="warn",
                    message=f"纠错结果未采用（{reason}）",
                    payload={
                        "segId": seg.seg_uid,
                        "reason": reason,
                        "raw": seg.raw_text,
                        "polished": result.text,
                        "editDistanceRatio": round(edit_distance_ratio(seg.raw_text, result.text), 3),
                    },
                )
                return

            seg.rev = 2
            seg.state = "revised"
            delivered = await voice_hub.broadcast_snapshot(
                session.room_id, seg_uid=seg.seg_uid, seq=seg.seq, rev=2, text=result.text, final=True
            )
            await voice_hub.broadcast_to_phone(
                session.room_id,
                {
                    "type": "segment.revised",
                    "segId": seg.seg_uid,
                    "seq": seg.seq,
                    "text": result.text,
                    "polishMs": result.ms,
                    "model": result.model,
                },
            )
            await self._log(
                room_pk,
                "segment.send",
                session_id=session.db_id,
                segment_id=seg.db_id,
                message=f"下发第 {seg.seq} 段（纠错后）给 {delivered} 台电脑",
                payload={"segId": seg.seg_uid, "rev": 2, "text": result.text, "count": delivered},
            )
            if seg.db_id is not None and delivered:
                await asyncio.to_thread(self._bump_deliver_count, seg.db_id, delivered)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 纠错链路任何异常都不能影响语音主流程
            with contextlib.suppress(Exception):
                await self._log(
                    room_pk,
                    "polish.failed",
                    session_id=session.db_id,
                    segment_id=seg.db_id,
                    level="error",
                    message=f"纠错异常：{type(exc).__name__}",
                    payload={"segId": seg.seg_uid, "error": str(exc)},
                )

    async def _mark_aborted(self, runtime: VoiceRoomRuntime, session: LiveSession, seg: LiveSegment) -> None:
        seg.state = "aborted"
        if seg.db_id is not None:
            await asyncio.to_thread(self._set_segment_state, seg.db_id, "aborted")
        await voice_hub.broadcast(
            session.room_id,
            {"type": "segment.abort", "segId": seg.seg_uid, "reason": "user_cancelled"},
            role=ROLE_DESKTOP,
        )
        await voice_hub.broadcast_to_phone(
            session.room_id, {"type": "segment.abort", "segId": seg.seg_uid, "reason": "user_cancelled"}
        )
        await self._log(
            session.room_pk,
            "segment.abort",
            session_id=session.db_id,
            segment_id=seg.db_id,
            message="段落被撤销",
            payload={"segId": seg.seg_uid, "reason": "user_cancelled", "text": seg.raw_text},
        )

    # ---------- 桌面端回执 ----------

    async def record_ack(
        self,
        *,
        room: VoiceRoom,
        client_id: int | None,
        seg_uid: str,
        rev: int,
        ok: bool,
        action: str | None,
        chars: int | None,
        text: str | None,
    ) -> None:
        seg = await asyncio.to_thread(self._find_segment, room.id, seg_uid)
        first = False
        if seg is not None and ok:
            first = seg.first_ack_ms is None
            await asyncio.to_thread(self._bump_ack, seg.id, first)
        await self._log(
            room.id,
            "segment.ack",
            segment_id=seg.id if seg is not None else None,
            client_id=client_id,
            level="info" if ok else "warn",
            message=f"{'已上屏' if ok else '上屏失败'}：{action or ''}",
            payload={
                "segId": seg_uid,
                "rev": rev,
                "ok": ok,
                "action": action,
                "chars": chars,
                "text": text,
            },
        )
        # 回执转给手机端，用于显示 ✓
        await voice_hub.broadcast_to_phone(
            room.room_id,
            {"type": "segment.ack", "segId": seg_uid, "rev": rev, "ok": ok, "action": action, "chars": chars},
        )

    async def record_client_event(
        self,
        *,
        room: VoiceRoom,
        client_id: int | None,
        kind: str,
        seg_uid: str | None,
        message: str | None,
        payload: dict[str, Any] | None,
    ) -> None:
        seg = None
        if seg_uid:
            seg = await asyncio.to_thread(self._find_segment, room.id, seg_uid)
        await self._log(
            room.id,
            kind,
            segment_id=seg.id if seg is not None else None,
            client_id=client_id,
            level="warn",
            message=message,
            payload=payload,
        )

    # ---------- 日志与落库（同步函数，调用方用 to_thread 包） ----------

    async def _log(
        self,
        room_pk: int,
        kind: str,
        *,
        session_id: int | None = None,
        segment_id: int | None = None,
        client_id: int | None = None,
        level: str = "info",
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                record_voice_event,
                room_pk,
                kind,
                session_id=session_id,
                segment_id=segment_id,
                client_id=client_id,
                level=level,
                message=message,
                payload=payload,
            )
        except Exception:
            # 日志故障绝不能打断语音链路
            return

    def _insert_session(self, session: LiveSession, room: VoiceRoom, recording_id: str | None) -> int:
        db = get_session_factory()()
        try:
            row = VoiceSession(
                session_uid=session.session_uid,
                room_pk=room.id,
                client_id=session.client_id,
                client_name=session.client_name,
                status="active",
                asr_model=room.asr_model,
                started_at=utcnow(),
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def _update_session_end(self, session: LiveSession) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSession, session.db_id) if session.db_id else None
            if row is None:
                return
            row.status = "discarded" if session.discard else "finished"
            row.audio_bytes = session.audio_bytes
            row.audio_ms = session.audio_ms
            row.frame_count = session.frame_count
            row.sentence_count = session.sentence_count
            row.first_partial_ms = session.first_partial_ms
            row.asr_usage_seconds = session.asr.usage_seconds if session.asr else None
            row.ended_at = utcnow()
            db.commit()
        finally:
            db.close()

    def _insert_segment(self, session: LiveSession, seg: LiveSegment) -> int:
        db = get_session_factory()()
        try:
            row = VoiceSegment(
                seg_uid=seg.seg_uid,
                session_id=session.db_id,
                room_pk=session.room_pk,
                seq=seg.seq,
                rev=0,
                state="partial",
                raw_text=seg.raw_text,
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def _update_segment_final(self, seg: LiveSegment, begin_ms: int | None, end_ms: int | None) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSegment, seg.db_id) if seg.db_id else None
            if row is None:
                return
            row.raw_text = seg.raw_text
            row.rev = 1
            row.state = "final"
            row.asr_begin_ms = begin_ms
            row.asr_end_ms = end_ms
            row.finalized_at = utcnow()
            row.updated_at = utcnow()
            db.commit()
        finally:
            db.close()

    def _update_segment_polish(self, segment_id: int, result: Any) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSegment, segment_id)
            if row is None:
                return
            row.polish_status = result.status
            row.polish_ms = result.ms
            row.polish_model = result.model
            row.polish_account_id = result.account_id
            row.polish_error = result.error
            if result.ok:
                row.polished_text = result.text
                row.rev = 2
                row.state = "revised"
            row.updated_at = utcnow()
            db.commit()
        finally:
            db.close()

    def _set_segment_state(self, segment_id: int, state: str) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSegment, segment_id)
            if row is None:
                return
            row.state = state
            row.updated_at = utcnow()
            db.commit()
        finally:
            db.close()

    def _bump_deliver_count(self, segment_id: int, count: int) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSegment, segment_id)
            if row is None:
                return
            row.deliver_count = max(row.deliver_count, count)
            db.commit()
        finally:
            db.close()

    def _bump_ack(self, segment_id: int, first: bool) -> None:
        db = get_session_factory()()
        try:
            row = db.get(VoiceSegment, segment_id)
            if row is None:
                return
            row.ack_count = (row.ack_count or 0) + 1
            if first:
                row.first_ack_ms = int(time.time() * 1000)
            db.commit()
        finally:
            db.close()

    def _find_segment(self, room_pk: int, seg_uid: str) -> VoiceSegment | None:
        db = get_session_factory()()
        try:
            return (
                db.query(VoiceSegment)
                .filter(VoiceSegment.room_pk == room_pk, VoiceSegment.seg_uid == seg_uid)
                .one_or_none()
            )
        finally:
            db.close()

def record_voice_event(
    room_pk: int,
    kind: str,
    *,
    session_id: int | None = None,
    segment_id: int | None = None,
    client_id: int | None = None,
    level: str = "info",
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """写一条房间事件。供路由层与会话层共用；调用方负责用 to_thread 包裹。"""
    db = get_session_factory()()
    try:
        db.add(
            VoiceEvent(
                room_pk=room_pk,
                session_id=session_id,
                segment_id=segment_id,
                client_id=client_id,
                kind=kind,
                level=level,
                message=(message or "")[:255] or None,
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
            )
        )
        db.commit()
    finally:
        db.close()


async def record_voice_event_async(room_pk: int, kind: str, **kwargs: Any) -> None:
    try:
        await asyncio.to_thread(record_voice_event, room_pk, kind, **kwargs)
    except Exception:
        return


def touch_client(
    *,
    room_pk: int,
    client_uid: str,
    role: str,
    name: str,
    online: bool,
    insert_mode: bool = False,
) -> VoiceClient | None:
    """登记/更新房间成员，返回成员行。"""
    db = get_session_factory()()
    try:
        row = db.query(VoiceClient).filter(VoiceClient.client_uid == client_uid).one_or_none()
        now = utcnow()
        if row is None:
            row = VoiceClient(
                room_pk=room_pk,
                client_uid=client_uid,
                role=role,
                name=(name or "")[:64],
                status="online" if online else "offline",
                insert_mode=insert_mode,
                last_seen_at=now,
                last_connected_at=now if online else None,
                last_disconnected_at=None if online else now,
            )
            db.add(row)
        else:
            row.room_pk = room_pk
            row.role = role
            if name:
                row.name = name[:64]
            row.status = "online" if online else "offline"
            row.insert_mode = insert_mode
            row.last_seen_at = now
            if online:
                row.last_connected_at = now
            else:
                row.last_disconnected_at = now
            row.updated_at = now
        db.commit()
        db.refresh(row)
        # 会话关闭后仍要能读到属性
        db.expunge(row)
        return row
    finally:
        db.close()


voice_session_manager = VoiceSessionManager()


def room_client_counts(room_pk: int) -> dict[str, int]:
    db = get_session_factory()()
    try:
        rows = db.query(VoiceClient).filter(VoiceClient.room_pk == room_pk).all()
        online = [row for row in rows if row.status == "online"]
        return {
            "clients": len(rows),
            "online": len(online),
            "phones": len([row for row in online if row.role == ROLE_PHONE]),
            "desktops": len([row for row in online if row.role == ROLE_DESKTOP]),
        }
    finally:
        db.close()


def latest_segments(room_pk: int, limit: int = 30) -> list[dict[str, Any]]:
    db = get_session_factory()()
    try:
        rows = (
            db.query(VoiceSegment)
            .filter(VoiceSegment.room_pk == room_pk)
            .order_by(VoiceSegment.id.desc())
            .limit(limit)
            .all()
        )
        result = []
        for row in rows:
            # text/final 是给桌面端的自描述字段：客户端不必自己推断"该显示哪个版本的文本"
            effective = row.polished_text or row.raw_text
            result.append(
                {
                    "segId": row.seg_uid,
                    "seq": row.seq,
                    "rev": row.rev,
                    "state": row.state,
                    "text": effective,
                    "final": row.rev >= 2 or row.state == "revised",
                    "rawText": row.raw_text,
                    "polishedText": row.polished_text,
                    "polishStatus": row.polish_status,
                    "polishModel": row.polish_model,
                    "polishMs": row.polish_ms,
                    "deliverCount": row.deliver_count,
                    "ackCount": row.ack_count,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return list(reversed(result))
    finally:
        db.close()


def recent_segments_for_desktop(room_pk: int, limit: int = 5) -> list[dict[str, Any]]:
    """桌面端重连补发用：只补发已经定稿的段落。"""
    return [item for item in latest_segments(room_pk, limit) if item["rev"] >= 1]
