from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.clock import SHANGHAI_TZ
from app.db import get_session_factory
from app.models import RequestLog


def _shanghai_today(hour: int = 12) -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(hour=hour, minute=0, second=0, microsecond=0)


def _to_utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _add_log(
    account_id: int,
    api_key_id: int,
    model: str,
    *,
    total_tokens: int | None = 10,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    status: str = "success",
    latency_ms: int = 100,
    created_at: datetime | None = None,
) -> None:
    session = get_session_factory()()
    try:
        session.add(
            RequestLog(
                account_id=account_id,
                api_key_id=api_key_id,
                account_name="DS",
                api_key_name="k",
                protocol="openai",
                model=model,
                stream=False,
                status=status,
                http_status=200 if status != "error" else 500,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                created_at=created_at or _to_utc_naive(_shanghai_today()),
            )
        )
        session.commit()
    finally:
        session.close()


def _seed(client: TestClient, auth_headers: dict[str, str]) -> tuple[int, int]:
    account = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": "DS", "provider": "deepseek", "api_key": "sk-up"},
    ).json()
    key_id = client.post(
        "/api/admin/keys",
        headers=auth_headers,
        json={"name": "k", "account_id": account["id"]},
    ).json()["id"]
    return account["id"], key_id


def test_dashboard_stats_reads_stored_snapshot(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id, key_id = _seed(client, auth_headers)
    _add_log(account_id, key_id, "glm-5.2", total_tokens=100)
    empty = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7})
    assert empty.status_code == 200
    assert empty.json()["summary"]["requests"] == 0
    refreshed = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7, "refresh": 1})
    assert refreshed.status_code == 200
    assert refreshed.json()["summary"]["requests"] == 1
    cached = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7})
    assert cached.json()["computed_at"] == refreshed.json()["computed_at"]
    assert cached.json()["summary"]["requests"] == 1


def test_dashboard_stats_buckets_by_shanghai_day(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id, key_id = _seed(client, auth_headers)
    # 上海 01:00 == 前一天 UTC 17:00，必须落在上海「今天」而不是 UTC 昨天
    early_today = _shanghai_today(hour=1)
    _add_log(account_id, key_id, "glm-5.2", total_tokens=100, created_at=_to_utc_naive(early_today))
    _add_log(account_id, key_id, "glm-5.2", total_tokens=50)
    _add_log(account_id, key_id, "deepseek-v4-flash", total_tokens=30, status="error")
    # 7 天窗口之外：不应计入
    _add_log(
        account_id,
        key_id,
        "glm-5.2",
        total_tokens=999,
        created_at=_to_utc_naive(_shanghai_today() - timedelta(days=8)),
    )

    response = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7, "refresh": 1})
    assert response.status_code == 200, response.text
    body = response.json()

    today = _shanghai_today().strftime("%Y-%m-%d")
    assert body["days"] == 7
    assert body["end_date"] == today
    assert len(body["daily"]) == 7
    assert body["daily"][-1]["date"] == today
    assert body["daily"][-1]["requests"] == 3
    assert body["daily"][-1]["failures"] == 1
    assert body["daily"][-1]["tokens"] == 180
    assert body["daily"][-1]["model_counts"] == {"glm-5.2": 2, "deepseek-v4-flash": 1}

    # 补零：没有请求的日期也返回，前端时间轴才连续
    assert body["daily"][0]["requests"] == 0
    assert body["daily"][0]["model_counts"] == {}

    assert body["summary"]["requests"] == 3
    assert body["summary"]["failures"] == 1
    assert body["summary"]["success_rate"] == 0.6667
    assert body["summary"]["tokens"] == 180
    assert body["summary"]["active_days"] == 1
    assert body["summary"]["active_models"] == 2

    # 上海 01:00 落在小时分布的 1 点
    hourly = {item["hour"]: item["requests"] for item in body["hourly"]}
    assert len(body["hourly"]) == 24
    assert hourly[1] == 1
    assert hourly[12] == 2

    models = {item["model"]: item for item in body["models"]}
    assert models["glm-5.2"]["requests"] == 2
    assert models["glm-5.2"]["request_share"] == 0.6667
    assert body["accounts"][0]["account_name"] == "DS"
    assert body["accounts"][0]["requests"] == 3


def test_dashboard_stats_token_fallback_and_previous_window(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    account_id, key_id = _seed(client, auth_headers)
    # 历史日志 total_tokens 可能为空，回退为 prompt+completion
    _add_log(account_id, key_id, "glm-5.2", total_tokens=None, prompt_tokens=7, completion_tokens=5)
    # 上一个等长区间的数据用于环比：days=7 时落在 7~13 天前
    _add_log(
        account_id,
        key_id,
        "glm-5.2",
        total_tokens=40,
        created_at=_to_utc_naive(_shanghai_today() - timedelta(days=9)),
    )

    body = client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 7, "refresh": 1}).json()
    assert body["summary"]["requests"] == 1
    assert body["summary"]["tokens"] == 12
    assert body["previous"]["requests"] == 1
    assert body["previous"]["tokens"] == 40


def test_dashboard_stats_rejects_invalid_range(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 0}).status_code == 422
    assert client.get("/api/admin/dashboard/stats", headers=auth_headers, params={"days": 91}).status_code == 422
