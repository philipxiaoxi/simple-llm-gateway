#!/usr/bin/env python3
"""回填 request_logs.turn_count / last_latency_ms（会话聚合口径）。

背景：一条 request_logs 记录是一个「会话」（同账号/Key/协议/session 当天续写合并）。
latency_ms 是会话累计耗时，进延迟监控会失真；tokens 也是累加值，不除以调用次数就没法
得到单次量级。新增两列后：latency_ms 仍是会话累计，last_latency_ms 是最近一次调用耗时，
turn_count 是该会话聚合的调用次数。

历史回填规则（无法精确恢复的部分标近似）：
- 未被续写合并过的行（updated_at 为空或 <= created_at）→ turn_count=1、
  last_latency_ms=latency_ms（精确）。
- 被合并过的行（updated_at > created_at）→ turn_count 用消息写入批次近似：
  同一次 save_log 写入的消息共享同一个 created_at，所以不同 created_at 的个数
  ≈ 该会话的调用次数，至少取 2；last_latency_ms 留空（单轮耗时不可恢复）。
  超过 1000 条会滚动截断，因此是下界。

按主键分批、幂等（只处理 turn_count IS NULL）、可中断续跑。
9G 级库建议先 `--dry-run` 估规模，再用小批试跑确认吞吐，最后低峰或停机全量跑。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_engine, init_db  # noqa: E402

PENDING = text("SELECT id FROM request_logs WHERE turn_count IS NULL ORDER BY id LIMIT :n")

SINGLE = text(
    """
    UPDATE request_logs
    SET turn_count = 1, last_latency_ms = coalesce(last_latency_ms, latency_ms)
    WHERE id BETWEEN :lo AND :hi AND turn_count IS NULL
      AND (updated_at IS NULL OR updated_at <= created_at)
    """
)

MERGED = text(
    """
    UPDATE request_logs
    SET turn_count = max(
        (SELECT count(DISTINCT m.created_at) FROM request_log_messages m WHERE m.log_id = request_logs.id),
        2
    )
    WHERE id BETWEEN :lo AND :hi AND turn_count IS NULL AND updated_at > created_at
    """
)

LEFTOVER = text(
    """
    UPDATE request_logs
    SET turn_count = 1, last_latency_ms = coalesce(last_latency_ms, latency_ms)
    WHERE id BETWEEN :lo AND :hi AND turn_count IS NULL
    """
)

STATS = text(
    """
    SELECT
        (SELECT count(*) FROM request_logs WHERE turn_count IS NULL) AS pending,
        (SELECT count(*) FROM request_logs WHERE turn_count IS NULL AND updated_at > created_at) AS pending_merged,
        (SELECT count(*) FROM request_logs) AS total
    """
)


def _set_busy_timeout(connection) -> None:  # type: ignore[no-untyped-def]
    # 脚本连接默认 busy_timeout=10s（db.py 的连接事件），回填批事务放宽到 60s
    connection.execute(text("PRAGMA busy_timeout=60000"))


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 request_logs 的 turn_count / last_latency_ms")
    parser.add_argument("--batch-size", type=int, default=2000, help="每批处理的主键条数（默认 2000）")
    parser.add_argument("--max-rows", type=int, default=0, help="最多处理多少行，0 表示不限")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="软时间上限（秒），0 表示不限")
    parser.add_argument("--sleep", type=float, default=0.05, help="每批之间的休眠秒数，给在线写入让路")
    parser.add_argument("--dry-run", action="store_true", help="只统计待回填规模，不写库")
    args = parser.parse_args()

    init_db()  # 幂等：补齐 turn_count / last_latency_ms 列与索引
    engine = get_engine()
    with engine.begin() as connection:
        _set_busy_timeout(connection)
        pending, pending_merged, total = connection.execute(STATS).one()

    print(f"数据库：{get_settings().database_path}")
    print(f"总日志 {total} 条；待回填 {pending} 条（其中续写合并行约 {pending_merged} 条）")
    if args.dry_run:
        print("dry-run，未做任何写入。")
        return
    if pending == 0:
        print("没有需要回填的数据。")
        return

    started = time.monotonic()
    done = merged_done = batches = 0
    while True:
        if args.max_rows and done >= args.max_rows:
            print(f"已达到 --max-rows={args.max_rows}，停止（可重跑继续）。")
            break
        if args.max_seconds and time.monotonic() - started >= args.max_seconds:
            print(f"已达到 --max-seconds={args.max_seconds}，停止（可重跑继续）。")
            break
        with engine.begin() as connection:
            _set_busy_timeout(connection)
            rows = connection.execute(PENDING, {"n": args.batch_size}).fetchall()
            if not rows:
                break
            lo, hi = int(rows[0][0]), int(rows[-1][0])
            params = {"lo": lo, "hi": hi}
            single = connection.execute(SINGLE, params).rowcount or 0
            merged = connection.execute(MERGED, params).rowcount or 0
            connection.execute(LEFTOVER, params)
        done += len(rows)
        merged_done += merged
        batches += 1
        elapsed = time.monotonic() - started
        rate = done / elapsed if elapsed > 0 else 0.0
        print(
            f"[{batches}] id {lo}-{hi}：本批 {len(rows)} 行（单轮 {single} / 合并 {merged}），"
            f"累计 {done}，{rate:.0f} 行/秒"
        )
        if args.sleep:
            time.sleep(args.sleep)

    elapsed = time.monotonic() - started
    with engine.begin() as connection:
        remaining = connection.execute(STATS).one().pending
    print(f"完成：处理 {done} 行（合并行 {merged_done}），用时 {elapsed:.1f}s，剩余 {remaining} 条")


if __name__ == "__main__":
    main()
