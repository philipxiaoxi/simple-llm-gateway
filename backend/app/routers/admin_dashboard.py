
import json
import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.clock import SHANGHAI_TZ, shanghai_day_start_utc
from app.db import get_db
from app.deps import get_current_admin
from app.models import (
    ApiKey,
    BenchmarkResult,
    BenchmarkRun,
    DesktopTool,
    GatewayAgent,
    LeaderboardSnapshot,
    RequestLog,
    Skill,
    UpstreamAccount,
)
from app.providers import get_provider
from app.schemas import (
    DashboardBenchmarkTopOut,
    DashboardLeaderboardStatusOut,
    DashboardLeaderboardTopOut,
    DashboardOut,
    DashboardStatsAccountOut,
    DashboardStatsDayOut,
    DashboardStatsHourOut,
    DashboardStatsModelOut,
    DashboardStatsOut,
    DashboardStatsSummaryOut,
)
from app.services.dashboard_stats import empty_stats, load_snapshot, refresh_snapshot
from app.services.leaderboard import latest_snapshot, snapshot_to_payload
from app.services.local_agent_relay import local_agent_relay

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"], dependencies=[Depends(get_current_admin)])

# 统计接口的区间上限：再长就看不出日粒度趋势，该用日志导出做离线分析
STATS_MAX_DAYS = 90
# 每日调用图最多拆出 12 个模型图例，其余归入「其他」，避免图例无限增长
STATS_MODEL_SERIES = 12
# 排行卡默认展示条数
STATS_RANK_LIMIT = 8

# created_at 存的是 naive UTC，图表按上海自然日/小时分桶，统一在 SQL 里 +8 小时
_DAY_EXPR = func.strftime("%Y-%m-%d", RequestLog.created_at, "+8 hours")
_HOUR_EXPR = func.strftime("%H", RequestLog.created_at, "+8 hours")
# 历史数据 total_tokens 可能为空，回退成 prompt+completion，避免旧日志被算成 0
_TOKEN_EXPR = func.coalesce(
    func.sum(
        func.coalesce(
            RequestLog.total_tokens,
            func.coalesce(RequestLog.prompt_tokens, 0) + func.coalesce(RequestLog.completion_tokens, 0),
        )
    ),
    0,
)
_FAIL_EXPR = func.coalesce(func.sum(case((RequestLog.status == "error", 1), else_=0)), 0)
# 一条日志 = 一个会话，可能聚合多次调用；按调用数统计时用 turn_count（历史未回填按 1 计）
_CALLS_EXPR = func.coalesce(func.sum(func.coalesce(RequestLog.turn_count, 1)), 0)
# 单次调用耗时：合并行的 latency_ms 是会话累计耗时，不能进延迟口径（否则 p95/均值被拉爆）。
# 单轮行用 last_latency_ms，历史未回填时回退 latency_ms（对单轮行两者相等）
_LATENCY_EXPR = func.coalesce(
    RequestLog.last_latency_ms,
    # 历史未回填：只有单轮行的 latency_ms 等于单次耗时；合并行的 latency_ms 是会话累计，
    # 不能回退使用，留 NULL 从延迟统计中排除
    case(
        (func.coalesce(RequestLog.turn_count, 1) <= 1, RequestLog.latency_ms),
        else_=None,
    ),
)


def _utc_naive(moment: datetime) -> datetime:
    aware = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    return aware.astimezone(UTC).replace(tzinfo=None)


def _stats_window(days: int, periods_ago: int = 0) -> tuple[datetime, datetime]:
    """返回 [start, end) 的 naive UTC 边界，按上海自然日对齐。

    periods_ago=1 表示上一个等长区间，用于环比。
    """
    today = datetime.now(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = today + timedelta(days=1) - timedelta(days=days * periods_ago)
    start_local = end_local - timedelta(days=days)
    return _utc_naive(start_local), _utc_naive(end_local)


def _leaderboard_top(db: Session, limit: int = 3) -> list[DashboardLeaderboardTopOut]:
    snapshot = db.scalar(select(LeaderboardSnapshot).order_by(LeaderboardSnapshot.id.desc()).limit(1))
    payload = snapshot_to_payload(db, snapshot)
    items = payload.get("items") or []
    ranked = sorted(
        (item for item in items if isinstance(item, dict)),
        key=lambda item: (
            item.get("rank") is None,
            item.get("rank") if isinstance(item.get("rank"), (int, float)) else 10**9,
            -(item.get("score") or 0),
        ),
    )
    top: list[DashboardLeaderboardTopOut] = []
    for item in ranked[:limit]:
        top.append(
            DashboardLeaderboardTopOut(
                rank=item.get("rank"),
                name=str(item.get("name") or ""),
                provider=str(item.get("provider") or ""),
                score=item.get("score"),
                slug=str(item.get("slug") or ""),
                context_window_tokens=item.get("context_window_tokens"),
                max_output_tokens=item.get("max_output_tokens"),
            )
        )
    return top


def _leaderboard_status(db: Session) -> DashboardLeaderboardStatusOut:
    """榜单同步健康度：有缓存且最近一次拉取无错 → 正常，否则异常。"""
    snapshot = latest_snapshot(db)
    if snapshot is None:
        return DashboardLeaderboardStatusOut(ok=False, error_message="暂无榜单缓存", total=0)
    try:
        loaded = json.loads(snapshot.entries_json or "[]")
        total = len(loaded) if isinstance(loaded, list) else 0
    except (TypeError, ValueError):
        total = 0
    error = (snapshot.error_message or "").strip() or None
    if total <= 0 and error is None:
        error = "暂无榜单缓存"
    return DashboardLeaderboardStatusOut(
        ok=total > 0 and error is None,
        error_message=error,
        fetched_at=snapshot.fetched_at,
        total=total,
    )


def _benchmark_speed_top(db: Session, limit: int = 3) -> list[DashboardBenchmarkTopOut]:
    rows = db.scalars(
        select(BenchmarkResult)
        .join(BenchmarkRun, BenchmarkRun.id == BenchmarkResult.run_id)
        .where(
            BenchmarkResult.ok.is_(True),
            BenchmarkResult.output_tokens_per_second.is_not(None),
            BenchmarkResult.output_tokens_per_second > 0,
        )
        .order_by(BenchmarkRun.created_at.desc(), BenchmarkResult.id.desc())
        .limit(limit)
    ).all()
    return [
        DashboardBenchmarkTopOut(
            model=row.model,
            account_name=row.account_name,
            provider=row.provider,
            output_tokens_per_second=float(row.output_tokens_per_second or 0),
            first_token_ms=row.first_token_ms,
            total_ms=row.total_ms,
            run_id=row.run_id,
            created_at=row.run.created_at,
        )
        for row in rows
    ]


def _p95_latency(db: Session, start: datetime, end: datetime) -> int | None:
    """成功单次调用的 p95 耗时。SQLite 没有 percentile，用排序 + 偏移取近似分位。"""
    filters = (
        RequestLog.created_at >= start,
        RequestLog.created_at < end,
        RequestLog.status != "error",
        _LATENCY_EXPR.is_not(None),
    )
    ok_count = db.scalar(select(func.count()).select_from(RequestLog).where(*filters)) or 0
    if ok_count <= 0:
        return None
    offset = min(ok_count - 1, max(0, math.ceil(ok_count * 0.95) - 1))
    value = db.scalar(select(_LATENCY_EXPR).where(*filters).order_by(_LATENCY_EXPR).offset(offset).limit(1))
    return int(value) if value is not None else None


def _stats_summary(db: Session, start: datetime, end: datetime) -> DashboardStatsSummaryOut:
    row = db.execute(
        select(
            _CALLS_EXPR,
            _FAIL_EXPR,
            _TOKEN_EXPR,
            func.coalesce(func.sum(func.coalesce(RequestLog.prompt_tokens, 0)), 0),
            func.coalesce(func.sum(func.coalesce(RequestLog.completion_tokens, 0)), 0),
            func.count(func.distinct(func.coalesce(func.nullif(RequestLog.model, ""), "未知"))),
            func.count(func.distinct(_DAY_EXPR)),
            func.avg(case((RequestLog.status != "error", _LATENCY_EXPR), else_=None)),
        ).where(RequestLog.created_at >= start, RequestLog.created_at < end)
    ).one()
    requests = int(row[0] or 0)
    failures = int(row[1] or 0)
    return DashboardStatsSummaryOut(
        requests=requests,
        failures=failures,
        success_rate=round((requests - failures) / requests, 4) if requests else 0.0,
        tokens=int(row[2] or 0),
        prompt_tokens=int(row[3] or 0),
        completion_tokens=int(row[4] or 0),
        active_models=int(row[5] or 0),
        active_days=int(row[6] or 0),
        avg_latency_ms=int(round(float(row[7]))) if row[7] is not None else None,
        p95_latency_ms=_p95_latency(db, start, end),
    )


def _shanghai_dates(days: int) -> list[str]:
    first_day = datetime.now(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return [(first_day + timedelta(days=index)).strftime("%Y-%m-%d") for index in range(days)]


@router.get("/dashboard/stats", response_model=DashboardStatsOut)
def dashboard_stats(
    days: int = Query(default=7, ge=1, le=STATS_MAX_DAYS),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> DashboardStatsOut:
    """概览图表数据：近 N 天每日调用/Token、模型与账号占比、小时分布、区间汇总与环比。

    - 日期按上海自然日分桶，未发生请求的日期补 0，前端可直接画连续时间轴
    - 模型超过 STATS_MODEL_SERIES 个时，其余归入「其他」，保证图例可读
    - 聚合结果由定时任务写入数据库；请求只读快照
    - `refresh=1` 当场重算并覆盖快照
    """
    if refresh:
        return refresh_snapshot(db, days)
    cached = load_snapshot(db, days)
    if cached is not None:
        return cached
    return empty_stats(days)


def compute_stats(db: Session, days: int) -> DashboardStatsOut:
    start, end = _stats_window(days)
    prev_start, prev_end = _stats_window(days, periods_ago=1)
    window = (RequestLog.created_at >= start, RequestLog.created_at < end)

    # 每日汇总
    daily_totals: dict[str, dict[str, int]] = {}
    for day, requests, failures, prompt_tokens, completion_tokens, tokens in db.execute(
        select(
            _DAY_EXPR.label("day"),
            _CALLS_EXPR,
            _FAIL_EXPR,
            func.coalesce(func.sum(func.coalesce(RequestLog.prompt_tokens, 0)), 0),
            func.coalesce(func.sum(func.coalesce(RequestLog.completion_tokens, 0)), 0),
            _TOKEN_EXPR,
        )
        .where(*window)
        .group_by(_DAY_EXPR)
    ):
        daily_totals[str(day)] = {
            "requests": int(requests or 0),
            "failures": int(failures or 0),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "tokens": int(tokens or 0),
        }

    # 每日 × 模型：既用于堆叠柱，也用于区间模型排行
    model_expr = func.coalesce(func.nullif(RequestLog.model, ""), "未知模型")
    model_totals: dict[str, dict[str, int]] = {}
    daily_models: dict[str, dict[str, int]] = {}
    for day, model, requests, tokens in db.execute(
        select(_DAY_EXPR.label("day"), model_expr.label("model"), _CALLS_EXPR, _TOKEN_EXPR)
        .where(*window)
        .group_by(_DAY_EXPR, model_expr)
    ):
        name = str(model)
        bucket = model_totals.setdefault(name, {"requests": 0, "tokens": 0})
        bucket["requests"] += int(requests or 0)
        bucket["tokens"] += int(tokens or 0)
        daily_models.setdefault(str(day), {})[name] = int(requests or 0)

    ranked_models = sorted(model_totals.items(), key=lambda item: (-item[1]["requests"], item[0]))
    series_models = [name for name, _ in ranked_models[:STATS_MODEL_SERIES]]
    has_other_models = len(ranked_models) > len(series_models)
    total_requests = sum(entry["requests"] for entry in daily_totals.values()) or 0

    models: list[DashboardStatsModelOut] = [
        DashboardStatsModelOut(
            model=name,
            requests=entry["requests"],
            tokens=entry["tokens"],
            request_share=round(entry["requests"] / total_requests, 4) if total_requests else 0.0,
        )
        for name, entry in ranked_models[:STATS_MODEL_SERIES]
    ]
    if has_other_models:
        other_requests = sum(entry["requests"] for _, entry in ranked_models[STATS_MODEL_SERIES:])
        other_tokens = sum(entry["tokens"] for _, entry in ranked_models[STATS_MODEL_SERIES:])
        models.append(
            DashboardStatsModelOut(
                model="其他",
                requests=other_requests,
                tokens=other_tokens,
                request_share=round(other_requests / total_requests, 4) if total_requests else 0.0,
            )
        )

    dates = _shanghai_dates(days)
    daily: list[DashboardStatsDayOut] = []
    for date in dates:
        totals = daily_totals.get(date, {})
        counts: dict[str, int] = {}
        bucketed: dict[str, int] = {}
        for name, count in daily_models.get(date, {}).items():
            key = name if name in series_models else "其他"
            bucketed[key] = bucketed.get(key, 0) + count
        if has_other_models or series_models:
            counts = {key: bucketed[key] for key in series_models if bucketed.get(key)}
            if bucketed.get("其他"):
                counts["其他"] = bucketed["其他"]
        daily.append(
            DashboardStatsDayOut(
                date=date,
                requests=int(totals.get("requests", 0)),
                failures=int(totals.get("failures", 0)),
                prompt_tokens=int(totals.get("prompt_tokens", 0)),
                completion_tokens=int(totals.get("completion_tokens", 0)),
                tokens=int(totals.get("tokens", 0)),
                model_counts=counts,
            )
        )

    # 小时分布（上海时区）：看网关在一天里什么时候最忙
    hourly_counts = {
        int(hour): int(requests or 0)
        for hour, requests in db.execute(
            select(_HOUR_EXPR.label("hour"), _CALLS_EXPR).where(*window).group_by(_HOUR_EXPR)
        )
        if hour is not None
    }
    hourly = [DashboardStatsHourOut(hour=hour, requests=hourly_counts.get(hour, 0)) for hour in range(24)]

    # 上游账号消耗占比：多账号时能看出流量压在哪个账号上
    account_expr = func.coalesce(func.nullif(RequestLog.account_name, ""), "未知上游")
    account_rows = db.execute(
        select(account_expr.label("account"), _CALLS_EXPR, _TOKEN_EXPR)
        .where(*window)
        .group_by(account_expr)
        .order_by(_CALLS_EXPR.desc())
        .limit(STATS_RANK_LIMIT)
    ).all()
    accounts = [
        DashboardStatsAccountOut(
            account_name=str(account),
            requests=int(requests or 0),
            tokens=int(tokens or 0),
            request_share=round(int(requests or 0) / total_requests, 4) if total_requests else 0.0,
        )
        for account, requests, tokens in account_rows
    ]

    return DashboardStatsOut(
        days=days,
        start_date=dates[0],
        end_date=dates[-1],
        daily=daily,
        models=models,
        accounts=accounts,
        hourly=hourly,
        summary=_stats_summary(db, start, end),
        previous=_stats_summary(db, prev_start, prev_end),
    )


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    account_count = db.scalar(select(func.count()).select_from(UpstreamAccount)) or 0
    accounts = db.scalars(select(UpstreamAccount).options(joinedload(UpstreamAccount.oauth_token))).all()
    # 探测失败与缺凭据可能命中同一账号，用去重集合统计，避免「异常账号」重复计数
    unhealthy_ids = {account.id for account in accounts if account.last_probe_ok is False}
    unhealthy_ids.update(
        account.id for account in accounts if get_provider(account.provider).missing_credential(account)
    )
    today = shanghai_day_start_utc()
    today_requests = (
        db.scalar(
            select(func.coalesce(func.sum(func.coalesce(RequestLog.turn_count, 1)), 0)).where(
                RequestLog.created_at >= today
            )
        )
        or 0
    )
    today_failures = (
        db.scalar(
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.created_at >= today, RequestLog.status == "error")
        )
        or 0
    )
    today_tokens = (
        db.scalar(
            select(func.coalesce(func.sum(RequestLog.total_tokens), 0)).where(RequestLog.created_at >= today)
        )
        or 0
    )
    total_requests = (
        db.scalar(select(func.coalesce(func.sum(func.coalesce(RequestLog.turn_count, 1)), 0))) or 0
    )
    total_tokens = db.scalar(select(func.coalesce(func.sum(RequestLog.total_tokens), 0))) or 0
    benchmark_count = db.scalar(select(func.count()).select_from(BenchmarkRun)) or 0
    skill_count = db.scalar(select(func.count()).select_from(Skill)) or 0
    key_count = db.scalar(select(func.count()).select_from(ApiKey)) or 0
    tool_count = db.scalar(select(func.count()).select_from(DesktopTool)) or 0
    agents = db.scalars(select(GatewayAgent)).all()
    agent_count = len(agents)
    agent_online_count = sum(1 for agent in agents if local_agent_relay.is_agent_online(agent.agent_id))
    return DashboardOut(
        account_count=account_count,
        unhealthy_count=len(unhealthy_ids),
        today_requests=today_requests,
        today_failures=today_failures,
        today_tokens=int(today_tokens),
        total_requests=total_requests,
        total_tokens=int(total_tokens),
        benchmark_count=benchmark_count,
        skill_count=skill_count,
        key_count=key_count,
        tool_count=tool_count,
        agent_count=agent_count,
        agent_online_count=agent_online_count,
        leaderboard_top=_leaderboard_top(db),
        leaderboard_status=_leaderboard_status(db),
        benchmark_speed_top=_benchmark_speed_top(db),
    )
