#!/usr/bin/env python3
"""生成正式服规模的数据集，用于性能复现与前后对比。

正式服现状（2026-09-10 用户提供）：request_logs 约 6156 条、
content_audit_findings 约 16489 条、SQLite、日常 10 人以内 / 峰值 5 并发。
本地库只有百余条，测不出真实耗时，因此用本脚本造一份同量级数据集。

用法：

    python3 scripts/perf_seed_scale.py                      # 生成 data/perf-scale.db
    python3 scripts/perf_seed_scale.py --force              # 覆盖已有文件

然后用它启动一个独立后端实例：

    DATABASE_PATH=data/perf-scale.db .venv/bin/uvicorn app.main:app \
        --app-dir backend --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROTOCOLS = ["openai_chat", "anthropic_messages", "openai_responses"]
MODELS = ["glm-5.3", "kimi-k2.6", "deepseek-v3", "grok-4", "claude-sonnet-4.5"]
CATEGORIES = ["sensitive", "pii", "secret"]
SEVERITIES = ["high", "medium", "low"]
LEXICON_CATEGORIES = ["政治", "色情", "暴恐", "广告", "其他"]
RULE_KEYS = {
    "sensitive": ["lexicon-hit", "lexicon-政治", "lexicon-广告"],
    "pii": ["pii-phone", "pii-idcard", "pii-email", "pii-bankcard"],
    "secret": ["secret-openai-key", "secret-bearer", "secret-private-key", "secret-github-pat"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成正式服规模的测试数据集")
    parser.add_argument("--db", default="data/perf-scale.db", help="输出数据库路径")
    parser.add_argument("--logs", type=int, default=6156, help="request_logs 行数")
    parser.add_argument("--findings", type=int, default=16489, help="content_audit_findings 行数")
    parser.add_argument("--messages", type=int, default=48000, help="request_log_messages 行数")
    parser.add_argument("--big-logs", type=int, default=40, help="超大会话数量（每条 300+ 消息）")
    parser.add_argument("--bench-runs", type=int, default=5, help="测速运行次数")
    parser.add_argument("--bench-results", type=int, default=400, help="测速结果总条数（用于验证排行榜扫描成本）")
    parser.add_argument("--seed", type=int, default=20260910, help="随机种子，保证可复现")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的数据库")
    return parser.parse_args()


def build_schema(db_path: Path) -> None:
    """用应用自身的建表逻辑创建 schema，确保索引与线上一致。"""
    os.environ["DATABASE_PATH"] = str(db_path)
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()
    from app.db import init_db, reset_db_runtime  # noqa: PLC0415

    reset_db_runtime()
    init_db()
    reset_db_runtime()


def main() -> int:
    args = parse_args()
    db_path = (ROOT / args.db).resolve() if not Path(args.db).is_absolute() else Path(args.db)
    if db_path.exists():
        if not args.force:
            raise SystemExit(f"{db_path} 已存在，加 --force 覆盖")
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"建表中：{db_path}")
    build_schema(db_path)

    rng = random.Random(args.seed)
    now = datetime(2026, 9, 10, 12, 0, 0)
    started = time.perf_counter()

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA foreign_keys=ON")
    cursor = connection.cursor()

    # 上游账号与 Key
    for index in range(5):
        cursor.execute(
            """
            INSERT INTO upstream_accounts
                (name, provider, source, auth_type, base_url, status, risk_level, header_spoof,
                 model_prefix, created_at, updated_at)
            VALUES (?, ?, 'upstream', 'api_key', ?, 'active', 'low', 'none', ?, ?, ?)
            """,
            (
                f"账号{index + 1}",
                ["opencode_go", "grok", "deepseek", "anthropic_generic", "openai_generic"][index],
                f"https://upstream{index + 1}.example.com/v1",
                f"acct{index + 1}",
                now - timedelta(days=60),
                now,
            ),
        )
    account_ids = [row[0] for row in cursor.execute("SELECT id FROM upstream_accounts")]

    for index in range(2):
        cursor.execute(
            """
            INSERT INTO api_keys (name, key_hash, key_encrypted, key_prefix, account_id, status, created_at, last_used_at)
            VALUES (?, ?, 'encrypted-placeholder', ?, ?, 'active', ?, ?)
            """,
            (f"key-{index + 1}", f"hash{index + 1:060d}", f"sk-bench{index + 1}", account_ids[index], now - timedelta(days=60), now),
        )
    key_ids = [row[0] for row in cursor.execute("SELECT id FROM api_keys")]
    for index, key_id in enumerate(key_ids):
        cursor.execute(
            "INSERT INTO api_key_accounts (api_key_id, account_id, sort_order) VALUES (?, ?, 0)",
            (key_id, account_ids[index]),
        )

    # 超大会话：单条记录数千条消息是内容审计需求里明确提过的真实形态
    big_log_ids: list[int] = []
    total_logs = args.logs
    big_count = min(args.big_logs, total_logs)
    message_budget = args.messages - big_count * 300
    normal_messages_each = max(1, message_budget // max(1, total_logs - big_count))

    print(f"写入 {total_logs} 条 request_logs（含 {big_count} 条超大会话）…")
    log_rows: list[tuple] = []
    for index in range(total_logs):
        created = now - timedelta(minutes=rng.randint(0, 60 * 24 * 60))
        protocol = rng.choice(PROTOCOLS)
        status = rng.choices(["success", "error"], weights=[94, 6])[0]
        has_big_body = rng.random() < 0.12
        request_body = None
        response_body = None
        if has_big_body:
            # 正文只留长度，内容不重要；用于还原「宽表 + 大 TEXT」的真实页数
            request_body = json.dumps({"messages": [{"role": "user", "content": "x" * rng.randint(4000, 40000)}]}, ensure_ascii=False)
            response_body = json.dumps({"content": "y" * rng.randint(4000, 40000)}, ensure_ascii=False)
        log_rows.append(
            (
                rng.choice(account_ids),
                f"账号{rng.randint(1, 5)}",
                "upstream",
                rng.choice(key_ids),
                f"key-{rng.randint(1, 2)}",
                protocol,
                rng.choice(MODELS),
                1 if rng.random() < 0.55 else 0,
                status,
                200 if status == "success" else rng.choice([400, 429, 500, 504]),
                None if status == "success" else "upstream error",
                rng.randint(50, 8000),
                rng.randint(100, 60000),
                rng.randint(150, 68000),
                rng.randint(200, 45000),
                request_body,
                response_body,
                f"session-{index // 7}",
                None,
                created,
                created + timedelta(seconds=rng.randint(1, 600)),
            )
        )
    cursor.executemany(
        """
        INSERT INTO request_logs
            (account_id, account_name, account_source, api_key_id, api_key_name, protocol, model, stream,
             status, http_status, error_message, prompt_tokens, completion_tokens, total_tokens, latency_ms,
             request_body, response_body, session_key, reasoning_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        log_rows,
    )
    log_ids = [row[0] for row in cursor.execute("SELECT id FROM request_logs ORDER BY id")]
    big_log_ids = log_ids[-big_count:]

    print(f"写入 request_log_messages（约 {args.messages} 行）…")
    message_rows: list[tuple] = []
    for log_id in log_ids:
        count = 320 if log_id in big_log_ids else max(1, normal_messages_each + rng.randint(-2, 2))
        for seq in range(count):
            message_rows.append(
                (
                    log_id,
                    seq,
                    "user" if seq % 2 == 0 else "assistant",
                    json.dumps({"role": "user" if seq % 2 == 0 else "assistant", "content": f"消息内容 {seq} " + "z" * rng.randint(20, 400)}, ensure_ascii=False),
                    now - timedelta(minutes=rng.randint(0, 60000)),
                )
            )
        if len(message_rows) > 20000:
            cursor.executemany(
                "INSERT INTO request_log_messages (log_id, seq, role, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                message_rows,
            )
            message_rows = []
    if message_rows:
        cursor.executemany(
            "INSERT INTO request_log_messages (log_id, seq, role, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
            message_rows,
        )

    # 内容审计：绝大多数日志已扫描，留一部分未扫描，制造 remaining 的真实分布
    print("写入 content_audit_scans / content_audit_findings …")
    scanned_ids = log_ids[: int(len(log_ids) * 0.92)]
    cursor.executemany(
        "INSERT INTO content_audit_scans (log_id, last_scanned_at, last_message_seq, finding_count, status) VALUES (?, ?, ?, ?, 'ok')",
        [(log_id, now - timedelta(hours=rng.randint(1, 300)), 999, 0) for log_id in scanned_ids],
    )

    finding_rows: list[tuple] = []
    for index in range(args.findings):
        log_id = rng.choice(log_ids)
        category = rng.choices(CATEGORIES, weights=[70, 22, 8])[0]
        created = now - timedelta(minutes=rng.randint(0, 60 * 24 * 60))
        finding_rows.append(
            (
                log_id,
                rng.randint(0, 300),
                category,
                rng.choice(LEXICON_CATEGORIES) if category == "sensitive" else None,
                rng.choice(RULE_KEYS[category]),
                rng.choices(SEVERITIES, weights=[35, 45, 20])[0],
                "命中片段 " + "q" * rng.randint(10, 120),
                rng.randint(0, 500),
                rng.randint(0, 500),
                rng.choice(key_ids),
                f"key-{rng.randint(1, 2)}",
                f"账号{rng.randint(1, 5)}",
                created,
            )
        )
    # 唯一约束 (log_id, message_seq, category, rule_key, start_offset)：冲突时忽略
    cursor.executemany(
        """
        INSERT OR IGNORE INTO content_audit_findings
            (log_id, message_seq, category, lexicon_category, rule_key, severity, excerpt,
             start_offset, end_offset, api_key_id, api_key_name, account_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        finding_rows,
    )

    # 其余页面依赖的数据：测速历史按 (运行次数, 结果总数) 生成，
    # 便于复现「排行榜/仪表盘全表扫描」这类随历史增长的问题
    run_ids: list[int] = []
    for run_index in range(max(1, args.bench_runs)):
        cursor.execute(
            "INSERT INTO benchmark_runs (prompt, max_tokens, created_at) VALUES (?, 256, ?)",
            ("尺度测试", now - timedelta(days=1) + timedelta(minutes=run_index)),
        )
        run_ids.append(cursor.lastrowid)
    per_run = max(1, args.bench_results // max(1, args.bench_runs))
    cursor.executemany(
        """
        INSERT INTO benchmark_results
            (run_id, account_id, account_name, provider, model, ok, timeout,
             first_token_ms, total_ms, output_chars, estimated_output_tokens, output_tokens_per_second, preview, error)
        VALUES (?, ?, ?, 'opencode_go', ?, 1, 0, ?, ?, ?, ?, ?, '预览文本', NULL)
        """,
        [
            (
                rng.choice(run_ids),
                rng.choice(account_ids),
                f"账号{rng.randint(1, 5)}",
                rng.choice(MODELS),
                rng.randint(200, 3000),
                rng.randint(400, 9000),
                rng.randint(50, 900),
                rng.randint(20, 400),
                rng.random() * 80,
            )
            for _ in range(per_run * max(1, args.bench_runs))
        ],
    )
    cursor.execute(
        "INSERT INTO leaderboard_snapshots (source_url, fetched_at, entries_json, source_updated_label, error_message) VALUES ('https://example.com', ?, ?, NULL, NULL)",
        (
            now - timedelta(hours=2),
            json.dumps(
                [
                    {
                        "name": f"{model}-{index}",
                        "slug": f"{model}-{index}",
                        "provider": rng.choice(["openai", "anthropic", "google", "deepseek"]),
                        "score": rng.random(),
                        "rank": index + 1,
                    }
                    for index, model in enumerate(MODELS * 20)
                ],
                ensure_ascii=False,
            ),
        ),
    )
    cursor.executemany(
        "INSERT INTO skill_categories (name, sort_order, keywords_json, is_protected, created_at) VALUES (?, 0, NULL, 0, ?)",
        [(name, now) for name in ["通用", "编程", "写作", "数据分析", "运维"]],
    )

    connection.commit()
    cursor.execute("ANALYZE")
    connection.commit()
    cursor.execute("PRAGMA optimize")
    connection.close()

    size_mb = db_path.stat().st_size / 1048576
    print(f"完成：{db_path}（{size_mb:.1f} MB，耗时 {time.perf_counter() - started:.1f}s）")
    print(f"  request_logs={total_logs}  findings={args.findings}  scans={len(scanned_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
