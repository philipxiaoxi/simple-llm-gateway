from __future__ import annotations

from fastapi.testclient import TestClient


def _account(client: TestClient, auth_headers: dict[str, str], name: str = "A") -> int:
    created = client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={"name": name, "provider": "deepseek", "api_key": "sk-up"},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _add_model(client: TestClient, auth_headers: dict[str, str], account_id: int, model: str = "m1") -> None:
    added = client.post(
        f"/api/admin/accounts/{account_id}/models/custom",
        headers=auth_headers,
        json={"id": model},
    )
    assert added.status_code == 200, added.text


def _create_group(
    client: TestClient,
    auth_headers: dict[str, str],
    name: str,
    targets: list[dict[str, object]],
):
    return client.post(
        "/api/admin/benchmark/groups",
        headers=auth_headers,
        json={"name": name, "targets": targets},
    )


def _save_run(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    model: str = "m1",
    group_id: int | None = None,
):
    payload: dict[str, object] = {
        "prompt": "测速",
        "max_tokens": 32,
        "results": [
            {
                "account_id": 1,
                "account_name": "A",
                "provider": "deepseek",
                "model": model,
                "ok": True,
                "timeout": False,
                "output_tokens_per_second": 12.5,
            }
        ],
    }
    if group_id is not None:
        payload["group_id"] = group_id
    return client.post("/api/admin/benchmark/history", headers=auth_headers, json=payload)


def test_create_list_and_resolve_group(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")

    created = _create_group(client, auth_headers, "主力模型", [{"account_id": account_id, "model": "m1"}])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "主力模型"
    assert body["member_count"] == 1
    assert body["available_count"] == 1
    assert body["members"][0]["available"] is True
    assert body["members"][0]["account_name"] == "A"

    listing = client.get("/api/admin/benchmark/groups", headers=auth_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [item["name"] for item in items] == ["主力模型"]


def test_group_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    target = [{"account_id": account_id, "model": "m1"}]

    blank = _create_group(client, auth_headers, "   ", target)
    assert blank.status_code == 400

    empty_targets = _create_group(client, auth_headers, "空分组", [])
    assert empty_targets.status_code == 200
    assert empty_targets.json()["member_count"] == 0

    missing_account = _create_group(client, auth_headers, "缺失账号", [{"account_id": 99999, "model": "m1"}])
    assert missing_account.status_code == 400

    assert _create_group(client, auth_headers, "重名", target).status_code == 200
    duplicate = _create_group(client, auth_headers, "重名", target)
    assert duplicate.status_code == 409
    case_duplicate = _create_group(client, auth_headers, "重名".upper(), target)
    assert case_duplicate.status_code in (200, 409)


def test_update_group_replaces_targets_and_renames(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    group_id = _create_group(
        client, auth_headers, "旧名", [{"account_id": account_id, "model": "m1"}]
    ).json()["id"]

    updated = client.patch(
        f"/api/admin/benchmark/groups/{group_id}",
        headers=auth_headers,
        json={"name": "新名", "targets": [{"account_id": account_id, "model": "m2"}]},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "新名"
    assert [member["model"] for member in body["members"]] == ["m2"]

    empty = client.patch(
        f"/api/admin/benchmark/groups/{group_id}",
        headers=auth_headers,
        json={"targets": []},
    )
    assert empty.status_code == 200
    assert empty.json()["members"] == []

    missing = client.patch("/api/admin/benchmark/groups/99999", headers=auth_headers, json={"name": "x"})
    assert missing.status_code == 404


def test_member_availability_tracks_account_and_model(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    group_id = _create_group(
        client, auth_headers, "G", [{"account_id": account_id, "model": "m1"}]
    ).json()["id"]

    def member() -> dict:
        return client.get("/api/admin/benchmark/groups", headers=auth_headers).json()["items"][0]["members"][0]

    client.patch(
        f"/api/admin/accounts/{account_id}/models/m1",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert member()["available"] is False
    assert member()["reason"] == "模型已关闭"

    client.patch(
        f"/api/admin/accounts/{account_id}/models/m1",
        headers=auth_headers,
        json={"enabled": True},
    )
    assert member()["available"] is True

    client.patch(f"/api/admin/accounts/{account_id}", headers=auth_headers, json={"status": "disabled"})
    assert member()["available"] is False
    assert member()["reason"] == "账号已停用"

    client.patch(f"/api/admin/accounts/{account_id}", headers=auth_headers, json={"status": "active"})
    client.delete(f"/api/admin/accounts/{account_id}", headers=auth_headers)
    pruned = client.get("/api/admin/benchmark/groups", headers=auth_headers).json()["items"][0]
    assert pruned["members"] == []
    assert group_id  # 分组仍在，仅清掉失效成员


def test_group_prunes_delisted_models(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    _add_model(client, auth_headers, account_id, "m2")
    _create_group(
        client,
        auth_headers,
        "G",
        [{"account_id": account_id, "model": "m1"}, {"account_id": account_id, "model": "m2"}],
    )

    removed = client.delete(f"/api/admin/accounts/{account_id}/models/custom/m1", headers=auth_headers)
    assert removed.status_code == 200

    group = client.get("/api/admin/benchmark/groups", headers=auth_headers).json()["items"][0]
    assert [member["model"] for member in group["members"]] == ["m2"]
    assert group["member_count"] == 1


def test_delete_group_keeps_runs(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    group_id = _create_group(
        client, auth_headers, "保留历史", [{"account_id": account_id, "model": "m1"}]
    ).json()["id"]
    saved = _save_run(client, auth_headers, group_id=group_id)
    assert saved.status_code == 200, saved.text
    run_id = saved.json()["id"]

    removed = client.delete(f"/api/admin/benchmark/groups/{group_id}", headers=auth_headers)
    assert removed.status_code == 200
    assert client.get("/api/admin/benchmark/groups", headers=auth_headers).json()["items"] == []

    detail = client.get(f"/api/admin/benchmark/history/{run_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["group_name"] == "保留历史"


def test_history_records_and_filters_by_group(client: TestClient, auth_headers: dict[str, str]) -> None:
    account_id = _account(client, auth_headers)
    _add_model(client, auth_headers, account_id, "m1")
    group_id = _create_group(
        client, auth_headers, "分组", [{"account_id": account_id, "model": "m1"}]
    ).json()["id"]

    grouped = _save_run(client, auth_headers, model="m1", group_id=group_id)
    assert grouped.status_code == 200
    assert grouped.json()["group_id"] == group_id
    assert grouped.json()["group_name"] == "分组"
    _save_run(client, auth_headers, model="m2")

    filtered = client.get(f"/api/admin/benchmark/history?group_id={group_id}", headers=auth_headers)
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["group_name"] == "分组"

    all_runs = client.get("/api/admin/benchmark/history", headers=auth_headers)
    assert all_runs.json()["total"] == 2

    bad = _save_run(client, auth_headers, group_id=99999)
    assert bad.status_code == 400
