from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clock import utcnow


def _load_backfill_module():  # type: ignore[no-untyped-def]
    script = Path(__file__).resolve().parents[2] / "scripts" / "backfill_turn_count.py"
    spec = importlib.util.spec_from_file_location("backfill_turn_count", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _add_log(db, **overrides):  # type: ignore[no-untyped-def]
    from app.models import RequestLog

    now = overrides.pop("created_at", None) or utcnow()
    defaults = dict(
        account_id=1,
        account_name="A",
        api_key_id=1,
        api_key_name="k",
        protocol="openai_chat",
        model="m",
        stream=False,
        status="success",
        http_status=200,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_ms=100,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    log = RequestLog(**defaults)
    db.add(log)
    db.flush()
    return log


def _add_message(db, log_id: int, seq: int, role: str, created_at):  # type: ignore[no-untyped-def]
    from app.models import RequestLogMessage

    db.add(
        RequestLogMessage(log_id=log_id, seq=seq, role=role, content_json="{}", created_at=created_at)
    )


# ---------------------------------------------------------------- save_log 合并口径


def test_save_log_merge_accumulates_calls_and_keeps_last_latency(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.services.proxy import save_log

    db = get_session_factory()()
    common = dict(
        account_id=1,
        api_key_id=1,
        protocol="openai_chat",
        model="m",
        stream=False,
        status="success",
        http_status=200,
        error_message=None,
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"choices": [{"message": {"role": "assistant", "content": "yo"}}]},
        request_headers={"x-session-id": "sess-1"},
    )
    first = save_log(db, usage=(10, 5, 15), latency_ms=100, **common)
    assert first.turn_count == 1
    assert first.last_latency_ms == 100
    assert first.latency_ms == 100
    db.commit()

    second = save_log(db, usage=(20, 8, 28), latency_ms=250, **common)
    db.commit()
    assert second.id == first.id, "同一会话应合并成一条日志"
    assert second.turn_count == 2
    assert second.last_latency_ms == 250, "last_latency_ms 应为最近一次调用耗时"
    assert second.latency_ms == 350, "latency_ms 仍是会话累计"
    assert second.prompt_tokens == 30
    db.close()


# ---------------------------------------------------------------- 历史回填 SQL


def test_backfill_sql_marks_single_and_approximates_merged(client: TestClient) -> None:
    from app.db import get_engine, get_session_factory

    db = get_session_factory()()
    now = utcnow()
    single = _add_log(db, latency_ms=120, created_at=now, updated_at=now, turn_count=None, last_latency_ms=None)
    merged2 = _add_log(
        db,
        latency_ms=9000,
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=2),
        turn_count=None,
        last_latency_ms=None,
    )
    for seq, moment in enumerate([now - timedelta(hours=3), now - timedelta(hours=2)]):
        _add_message(db, merged2.id, seq, "assistant", moment)
        _add_message(db, merged2.id, seq + 100, "user", moment)
    merged3 = _add_log(
        db,
        latency_ms=30000,
        created_at=now - timedelta(hours=5),
        updated_at=now - timedelta(hours=1),
        turn_count=None,
        last_latency_ms=None,
    )
    for seq, moment in enumerate(
        [now - timedelta(hours=5), now - timedelta(hours=3), now - timedelta(hours=1)]
    ):
        _add_message(db, merged3.id, seq, "assistant", moment)
    db.commit()
    ids = {"single": single.id, "merged2": merged2.id, "merged3": merged3.id}
    lo, hi = 0, 10**9
    db.close()

    module = _load_backfill_module()
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(module.SINGLE, {"lo": lo, "hi": hi})
        connection.execute(module.MERGED, {"lo": lo, "hi": hi})
        connection.execute(module.LEFTOVER, {"lo": lo, "hi": hi})
        rows = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                text("SELECT id, turn_count, last_latency_ms FROM request_logs")
            ).all()
        }

    assert rows[ids["single"]][0] == 1
    assert rows[ids["single"]][1] == 120, "单轮行回填 last_latency_ms=latency_ms"
    assert rows[ids["merged2"]][0] == 2, "按消息写入批次近似调用次数"
    assert rows[ids["merged2"]][1] is None, "合并行单轮耗时不可恢复，保持为空"
    assert rows[ids["merged3"]][0] == 3


# ---------------------------------------------------------------- 统计口径


def test_dashboard_stats_count_calls_and_use_per_call_latency(client: TestClient) -> None:
    from app.db import get_session_factory
    from app.routers.admin_dashboard import compute_stats

    db = get_session_factory()()
    now = utcnow()
    _add_log(db, latency_ms=100, last_latency_ms=100, turn_count=1, created_at=now, updated_at=now)
    _add_log(
        db,
        latency_ms=100_000,
        last_latency_ms=250,
        turn_count=4,
        prompt_tokens=40,
        completion_tokens=20,
        total_tokens=60,
        created_at=now,
        updated_at=now,
    )
    db.commit()

    stats = compute_stats(db, 7)
    db.close()
    summary = stats.summary
    assert summary.requests == 5, "按调用数统计：1 + 4"
    assert summary.tokens == 75
    # 延迟只看单次调用：合并行的 100000ms 会话累计不得进入均值/p95
    assert summary.avg_latency_ms == 175
    assert summary.p95_latency_ms == 250
