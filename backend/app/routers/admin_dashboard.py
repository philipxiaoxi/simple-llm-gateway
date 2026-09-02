
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.clock import utcnow
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
    DashboardLeaderboardTopOut,
    DashboardOut,
)
from app.services.leaderboard import snapshot_to_payload
from app.services.local_agent_relay import local_agent_relay

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"], dependencies=[Depends(get_current_admin)])


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


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    account_count = db.scalar(select(func.count()).select_from(UpstreamAccount)) or 0
    probe_failed = (
        db.scalar(
            select(func.count()).select_from(UpstreamAccount).where(UpstreamAccount.last_probe_ok.is_(False))
        )
        or 0
    )
    accounts = db.scalars(select(UpstreamAccount).options(joinedload(UpstreamAccount.oauth_token))).all()
    missing_credential = sum(
        1 for account in accounts if get_provider(account.provider).missing_credential(account)
    )
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_requests = (
        db.scalar(select(func.count()).select_from(RequestLog).where(RequestLog.created_at >= today)) or 0
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
    total_requests = db.scalar(select(func.count()).select_from(RequestLog)) or 0
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
        unhealthy_count=probe_failed + missing_credential,
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
        benchmark_speed_top=_benchmark_speed_top(db),
    )
