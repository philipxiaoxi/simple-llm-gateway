from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

RAW: dict[str, list[dict[str, Any]]] = {
    "portrait": [
        {
            "id": "a1",
            "标题": "短发・标准款",
            "分区": "正脸参考",
            "发长": "短发",
            "发型名称": "标准基础款",
            "宽": 1122,
            "高": 1402,
            "缩略图": "thumbs/portrait/a1.webp",
            "原图": "img/portrait/a1.webp",
            "精选": True,
            "热门": True,
            "检索": "a1 正脸参考 短发 标准基础款",
        },
        {
            "id": "b2",
            "标题": "长发・波浪卷",
            "分区": "造型氛围",
            "发长": "长发",
            "发型名称": "大波浪",
            "宽": 1200,
            "高": 1500,
            "缩略图": "thumbs/portrait/b2.webp",
            "原图": "img/portrait/b2.webp",
            "精选": False,
            "热门": True,
            "检索": "b2 造型氛围 长发 大波浪",
        },
    ],
    "pose": [
        {
            "id": "p1",
            "体系": "全身单人・标准系",
            "姿势编号": "P001",
            "分类": "站姿",
            "姿势名称": "标准站姿",
            "角度": "正面",
            "缩略图": "thumbs/full_std_P001_front.jpg",
            "原图": "img/full_std_P001_front.png",
            "检索": "p1 全身单人・标准系 站姿 标准站姿 正面",
        },
        {
            "id": "p2",
            "体系": "全身单人・性感系",
            "姿势编号": "P002",
            "分类": "趴卧",
            "姿势名称": "S形趴卧",
            "角度": "侧面",
            "缩略图": "thumbs/full_sexy_P002_side.jpg",
            "原图": "img/full_sexy_P002_side.png",
            "检索": "p2 全身单人・性感系 趴卧 侧面",
        },
        {
            "id": "p3",
            "体系": "上半身单人",
            "姿势编号": "P003",
            "分类": "挤胸・强调事业线系",
            "姿势名称": "强调曲线",
            "角度": "斜侧",
            "缩略图": "thumbs/upper_P003.jpg",
            "原图": "img/upper_P003.png",
            "检索": "p3 上半身单人 强调曲线 斜侧",
        },
    ],
    "costume": [
        {
            "id": "c1",
            "分类": "春季穿搭",
            "服装名称": "象牙白针织衫",
            "标签": ["象牙白", "针织"],
            "关键词": "ivory knit",
            "缩略图": "thumbs/costume/c1.webp",
            "原图": "img/costume/c1.webp",
            "检索": "c1 春季穿搭 象牙白针织衫 ivory knit",
        },
        {
            "id": "c2",
            "分类": "内衣・睡衣",
            "服装名称": "真丝睡裙",
            "标签": ["真丝", "睡裙"],
            "关键词": "silk nightgown",
            "缩略图": "thumbs/costume/c2.webp",
            "原图": "img/costume/c2.webp",
            "检索": "c2 内衣・睡衣 真丝睡裙 silk nightgown",
        },
    ],
}

META: dict[str, dict[str, Any]] = {
    "portrait": {"total": 2, "分区列表": ["正脸参考", "造型氛围"], "发长列表": ["短发", "长发"]},
    "pose": {},
    "costume": {"total": 2, "分类列表": ["春季穿搭", "内衣・睡衣"]},
}


@pytest.fixture()
def reference_sources(monkeypatch):
    from app.capabilities import registry
    from app.capabilities.reference import source as source_module
    from app.capabilities.reference.provider import ReferenceProvider
    from app.capabilities.reference.specs import SPECS

    registry.ensure_defaults()
    for kind, spec in SPECS.items():
        if registry.get_provider(kind) is None:
            registry.register(ReferenceProvider(spec))
        source = source_module.ReferenceSource(spec)
        source._apply(RAW[kind], META[kind], time.time())
        source._loaded = True
        source._retry_at = time.time() + 3600
        source_module.set_source(kind, source)
    yield
    source_module.reset_sources_for_tests()


def _key(client: TestClient, auth: dict[str, str], capabilities: list[str]) -> str:
    response = client.post(
        "/api/admin/mcp/keys",
        headers=auth,
        json={"name": f"ref-{'-'.join(capabilities)}", "capability_ids": capabilities},
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_reference_search_and_prompt(client: TestClient, auth_headers: dict[str, str], reference_sources) -> None:
    key = _key(client, auth_headers, ["portrait", "pose", "costume"])
    auth = {"Authorization": f"Bearer {key}"}

    portrait = client.post("/v1/capabilities/portrait/search", headers=auth, json={"query": "短发"})
    assert portrait.status_code == 200, portrait.text
    assert portrait.json()["items"][0]["id"] == "a1"
    assert portrait.json()["items"][0]["image_url"].endswith("/img/portrait/a1.webp")
    assert "人物参考" in portrait.json()["items"][0]["prompt"]

    pose = client.post(
        "/v1/capabilities/pose/search",
        headers=auth,
        json={"group": "全身单人・标准系"},
    )
    assert pose.status_code == 200, pose.text
    body = pose.json()
    assert body["total"] == 1
    assert body["items"][0]["detail"] == "正面"
    assert "姿势参考" in body["items"][0]["prompt"]
    assert "P001" in body["items"][0]["prompt"]

    costume = client.post(
        "/v1/capabilities/costume/search",
        headers=auth,
        json={"tag": "针织"},
    )
    assert costume.status_code == 200, costume.text
    assert costume.json()["total"] == 1
    assert "英文关键词：ivory knit" in costume.json()["items"][0]["prompt"]


def test_reference_filters_adult_by_default(
    client: TestClient, auth_headers: dict[str, str], reference_sources
) -> None:
    key = _key(client, auth_headers, ["pose", "costume"])
    auth = {"Authorization": f"Bearer {key}"}

    safe = client.post("/v1/capabilities/pose/search", headers=auth, json={})
    assert safe.status_code == 200, safe.text
    assert [item["id"] for item in safe.json()["items"]] == ["p1"]
    assert safe.json()["hidden_adult"] == 2
    assert "全身单人・性感系" not in safe.json()["facets"]["groups"]

    unsafe = client.post("/v1/capabilities/pose/search", headers=auth, json={"include_adult": True})
    assert {item["id"] for item in unsafe.json()["items"]} == {"p1", "p2", "p3"}
    assert "全身单人・性感系" in unsafe.json()["facets"]["groups"]

    costume_safe = client.post("/v1/capabilities/costume/search", headers=auth, json={})
    assert [item["id"] for item in costume_safe.json()["items"]] == ["c1"]

    hidden_detail = client.post("/v1/capabilities/costume/get", headers=auth, json={"id": "c2"})
    assert hidden_detail.status_code == 404
    visible_detail = client.post("/v1/capabilities/costume/get", headers=auth, json={"id": "c2", "include_adult": True})
    assert visible_detail.status_code == 200


def test_reference_capability_binding(client: TestClient, auth_headers: dict[str, str], reference_sources) -> None:
    key = _key(client, auth_headers, ["pose"])
    auth = {"Authorization": f"Bearer {key}"}
    assert client.post("/v1/capabilities/pose/search", headers=auth, json={}).status_code == 200
    assert client.post("/v1/capabilities/portrait/search", headers=auth, json={}).status_code == 403


def test_reference_admin_endpoints(client: TestClient, auth_headers: dict[str, str], reference_sources) -> None:
    from app.capabilities.reference import source as source_module

    source = source_module.get_source("pose")
    source._fetch_json = lambda url: RAW["pose"]  # type: ignore[method-assign]

    listing = client.get("/api/admin/mcp/poses?group=全身单人・标准系", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["items"]] == ["p1"]
    assert listing.json()["labels"]["group"] == "体系"

    meta = client.get("/api/admin/mcp/poses/meta", headers=auth_headers)
    assert meta.status_code == 200, meta.text
    assert meta.json()["total"] == 1
    assert meta.json()["hidden_adult"] == 2

    refreshed = client.post("/api/admin/mcp/poses/refresh", headers=auth_headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["synced_at"]

    detail = client.get("/api/admin/mcp/poses/p1", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["item"]["title"] == "标准站姿"


def test_reference_admin_permission_split(client: TestClient, auth_headers: dict[str, str], reference_sources) -> None:
    role = client.post(
        "/api/admin/roles",
        headers=auth_headers,
        json={"name": "姿势只读", "permissions": ["mcp_poses:view"]},
    )
    assert role.status_code == 201, role.text
    user = client.post(
        "/api/admin/users",
        headers=auth_headers,
        json={"username": "pose-reader", "password": "user-pass-123", "role_id": role.json()["id"]},
    )
    assert user.status_code == 201, user.text
    token = client.post("/api/admin/login", json={"username": "pose-reader", "password": "user-pass-123"}).json()[
        "token"
    ]
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/admin/mcp/poses", headers=auth).status_code == 200
    assert client.get("/api/admin/mcp/portraits", headers=auth).status_code == 403
    denied = client.post("/api/admin/mcp/poses/refresh", headers=auth)
    assert denied.status_code == 403


def test_reference_catalog_permissions(client: TestClient, auth_headers: dict[str, str], reference_sources) -> None:
    catalog = client.get("/api/admin/mcp/catalog?scope=all", headers=auth_headers)
    assert catalog.status_code == 200, catalog.text
    items = {item["capability_id"]: item for item in catalog.json()["items"]}
    assert items["portrait"]["permissions"] == ["mcp_portraits:view"]
    assert items["pose"]["permissions"] == ["mcp_poses:view"]
    assert items["costume"]["permissions"] == ["mcp_costumes:view"]
    assert items["pose"]["admin_path"] == "/mcp-plaza/poses"
    assert {tool["name"] for tool in items["costume"]["tools"]} == {
        "costume_search",
        "costume_get",
        "costume_random",
    }
