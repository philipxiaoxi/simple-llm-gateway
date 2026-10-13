"""供应商接入层的测试：注册表发现、目录按供应商隔离、RightAPI 适配器协议。"""

from __future__ import annotations

import json
import pkgutil
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from app.capabilities.generation.provider import GenerationProvider
from app.generation import registry, service
from app.generation.base import InputMedia
from app.generation.catalog import ModelSpec
from app.generation.providers import rightapi
from app.generation.providers.rightapi import RightApiAdapter

# --------------------------------------------------------------------------- #
# 注册表：新增供应商只落一个模块，registry 自动发现
# --------------------------------------------------------------------------- #


def test_registry_discovers_providers() -> None:
    assert "gsai" in registry.PROVIDERS
    assert "rightapi" in registry.PROVIDERS


def test_default_provider_does_not_drift() -> None:
    # ORDER=0 的 gsai 必须仍是默认，否则升级会把老配置指到新供应商上
    assert registry.default_provider("image") == "gsai"
    assert registry.default_provider("video") == "gsai"


def test_provider_kinds_match_their_models() -> None:
    for info in registry.PROVIDERS.values():
        assert info.kinds, f"{info.provider} 未声明 KINDS"
        for spec in info.models:
            assert spec.kind in info.kinds, f"{info.provider} 的 {spec.id} 类型与 KINDS 不符"


def test_rightapi_has_no_video() -> None:
    assert registry.PROVIDERS["rightapi"].kinds == ("image",)
    assert registry.static_models("rightapi", "video") == ()


def test_model_lookup_is_scoped_by_provider() -> None:
    # 两家都卖 nano-banana-2，必须能分别取到、互不覆盖
    ours = registry.get_model("nano-banana-2", provider="gsai")
    theirs = registry.get_model("nano-banana-2", provider="rightapi")
    assert ours is not None and ours.provider == "gsai"
    assert theirs is not None and theirs.provider == "rightapi"
    # 只有 gsai 有的模型，不能从 rightapi 里查到
    assert registry.get_model("nano-banana-2-cl", provider="rightapi") is None
    assert registry.get_model("nano-banana-2-cl", provider="gsai") is not None
    assert registry.get_model("minimax-h3", provider="rightapi") is None


def test_catalog_entries_carry_provider() -> None:
    payload = registry.catalog_payload()
    providers = {item["provider"] for item in payload["image"]}
    assert {"gsai", "rightapi"} <= providers
    assert all(item["provider_label"] for item in payload["image"])


def test_gsai_catalog_keeps_models_and_human_labels() -> None:
    """模型从 catalog.py 搬到 providers/gsai.py 时，不能丢模型、也不能丢人话标签。

    整份清单写死在这里是故意的：它是对外契约，少一个模型或改一个默认值都该让测试红。
    """

    assert {model.id for model in registry.static_models("gsai")} == {
        "gpt-image-2",
        "gpt-image-2-vip",
        "gpt-image-2.5",
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst",
        "minimax-h3",
        "nano-banana",
        "nano-banana-2",
        "nano-banana-2-2k-cl",
        "nano-banana-2-4k-cl",
        "nano-banana-2-cl",
        "nano-banana-fast",
        "nano-banana-pro",
        "nano-banana-pro-4k-vip",
        "nano-banana-pro-cl",
        "nano-banana-pro-vip",
        "nano-banana-pro-vt",
    }

    finish = registry.get_model("minimax-h3", provider="gsai")
    assert finish is not None
    direction = next(param for param in finish.params if param.key == "aspectRatio")
    # 值与人话标签不同，别退化成 opts() 那种「值即标签」
    assert [(opt.value, opt.label) for opt in direction.options] == [
        ("portrait", "竖屏 portrait"),
        ("landscape", "横屏 landscape"),
    ]
    assert finish.supports_images and finish.max_images == 9
    assert finish.supports_audios and finish.max_audios == 3
    duration = next(param for param in finish.params if param.key == "duration")
    assert (duration.default, duration.minimum, duration.maximum, duration.required) == (5, 1, 15, True)


def test_rightapi_ratio_models_use_the_documented_aspect_ratio_key() -> None:
    """比例类模型必须用文档宣传的 aspectRatio。

    用 size 的话，按 MCP 能力 _PARAM_NOTE 调用的请求会因 _collect_params 过滤而被
    静默丢掉，只剩一句「请填写画面比例」。像素尺寸类没法用 aspectRatio 表达，仍走 size。
    """

    ratio = registry.get_model("nano-banana-2", provider="rightapi")
    assert ratio is not None
    assert {param.key for param in ratio.params} == {"aspectRatio", "imageSize", "n"}

    pixel = registry.get_model("gpt-image-2-vip", provider="rightapi")
    assert pixel is not None
    assert {param.key for param in pixel.params} == {"size", "imageSize", "n"}


def test_rightapi_ratio_param_is_not_silently_dropped(client: TestClient, auth_headers: dict[str, str]) -> None:
    """按文档传 aspectRatio 必须真的落进任务参数，而不是被过滤掉。"""

    created = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "provider": "rightapi",
            "model": "nano-banana-2",
            "prompt": "x",
            "params": {"aspectRatio": "16:9", "imageSize": "2K"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["params"] == {"aspectRatio": "16:9", "imageSize": "2K"}


def test_mcp_param_collection_keeps_the_documented_key() -> None:
    """MCP 侧 `_collect_params` 按 spec 的键过滤：键名对不上就被静默丢掉。

    这条直接盯着 M1 那个坑 —— 比例类模型必须收得下 `aspectRatio`。
    """

    provider = GenerationProvider()

    ratio = registry.get_model("nano-banana-2", provider="rightapi")
    assert ratio is not None
    assert provider._collect_params(ratio, {"prompt": "x", "aspectRatio": "16:9", "imageSize": "2K"}) == {
        "aspectRatio": "16:9",
        "imageSize": "2K",
    }

    pixel = registry.get_model("gpt-image-2-vip", provider="rightapi")
    assert pixel is not None
    assert provider._collect_params(pixel, {"prompt": "x", "size": "2048x2048"}) == {"size": "2048x2048"}


def test_rightapi_gpt_model_uses_size(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "provider": "rightapi",
            "model": "gpt-image-2-vip",
            "prompt": "x",
            "params": {"size": "2048x2048", "imageSize": "2K"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["params"] == {"size": "2048x2048", "imageSize": "2K"}


def test_custom_model_can_be_declared_as_video(client: TestClient, auth_headers: dict[str, str]) -> None:
    """双类型供应商要能用 `模型名:video` 补视频模型（否则自定义模型只能是图片）。"""

    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"providers": [{"provider": "gsai", "extra_models": "my-image-model my-video-model:video"}]},
    )
    assert saved.status_code == 200, saved.text
    catalog = saved.json()["catalog"]
    gsai_image = {item["id"] for item in catalog["image"] if item["provider"] == "gsai"}
    gsai_video = {item["id"] for item in catalog["video"] if item["provider"] == "gsai"}

    assert "my-image-model" in gsai_image
    assert "my-video-model" in gsai_video
    assert "my-video-model" not in gsai_image

    created = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "video", "provider": "gsai", "model": "my-video-model", "prompt": "x", "params": {}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["model"] == "my-video-model"


def test_custom_model_kind_suffix_ignored_when_provider_lacks_that_kind(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """RightAPI 只有图片：`:video` 后缀不被采纳，也不会把模型名悄悄改写。"""

    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"providers": [{"provider": "rightapi", "extra_models": "only-image:video"}]},
    )
    assert saved.status_code == 200, saved.text
    catalog = saved.json()["catalog"]
    ids = {item["id"] for item in catalog["image"] if item["provider"] == "rightapi"}
    # 整段 token 当作模型名，不做静默改写
    assert "only-image:video" in ids


class _AcmeAdapter:
    """只为验证发现机制，不会被实例化。"""


def _acme_module() -> ModuleType:
    module = ModuleType("app.generation.providers.acme_fake")
    module.PROVIDER = "acme"
    module.LABEL = "Acme（acme）"
    module.DEFAULT_BASE_URL = "https://api.acme.test"
    module.KINDS = ("image",)
    module.ADAPTER = _AcmeAdapter
    module.MODELS = (ModelSpec(id="acme-1", label="acme-1", kind="image"),)
    module.ORDER = 250
    return module


def test_discovery_picks_up_a_brand_new_provider_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """核心承诺：在 providers/ 里新增一个 .py 就会被自动发现，registry 本身不用改。

    这里用打桩的 iter_modules / import_module 模拟「多了一个模块」，
    避免在包目录里真写文件（dev server 会热重载并把它当成真供应商）。
    """

    module = _acme_module()
    real_iter = pkgutil.iter_modules
    real_import = registry.importlib.import_module

    def fake_iter(path, prefix: str = ""):  # noqa: ANN001, ANN202
        yield from real_iter(path, prefix)
        yield pkgutil.ModuleInfo(None, "acme_fake", False)  # type: ignore[arg-type]

    def fake_import(name: str):  # noqa: ANN202
        if name == module.__name__:
            return module
        return real_import(name)

    monkeypatch.setattr(registry.pkgutil, "iter_modules", fake_iter)
    monkeypatch.setattr(registry.importlib, "import_module", fake_import)

    discovered = registry._discover()

    assert "acme" in discovered
    info = discovered["acme"]
    assert info.label == "Acme（acme）"
    assert info.kinds == ("image",)
    assert info.adapter is _AcmeAdapter
    # 模型目录自动带上归属，前端据此分组
    assert info.models_for("image")[0].provider == "acme"
    # ORDER=250 排在两家之后，默认供应商不受影响
    assert list(discovered) == ["gsai", "rightapi", "acme"]


def test_discovery_skips_modules_without_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """没声明 PROVIDER / ADAPTER 的模块会被跳过，不会把注册表弄脏。"""

    module = ModuleType("app.generation.providers.incomplete")
    module.MODELS = ()
    real_iter = pkgutil.iter_modules
    real_import = registry.importlib.import_module

    def fake_iter(path, prefix: str = ""):  # noqa: ANN001, ANN202
        yield from real_iter(path, prefix)
        yield pkgutil.ModuleInfo(None, "incomplete", False)  # type: ignore[arg-type]

    def fake_import(name: str):  # noqa: ANN202
        if name == module.__name__:
            return module
        return real_import(name)

    monkeypatch.setattr(registry.pkgutil, "iter_modules", fake_iter)
    monkeypatch.setattr(registry.importlib, "import_module", fake_import)

    assert "incomplete" not in str(list(registry._discover()))
    assert list(registry._discover()) == ["gsai", "rightapi"]


# --------------------------------------------------------------------------- #
# RightAPI 适配器
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, payload: dict | None, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else "<html>"

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse | None = None,
    *,
    responses: dict[str, _FakeResponse] | None = None,
) -> dict:
    """替换适配器用的 httpx.Client；`responses` 可按 post/get 分别给响应。"""

    table = responses or {}
    calls: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args) -> bool:  # noqa: ANN002
            return False

        def post(self, url: str, **kwargs):  # noqa: ANN003, ANN201
            calls["post"] = (url, kwargs)
            return table.get("post", response)

        def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
            calls["get"] = (url, kwargs)
            return table.get("get", response)

    monkeypatch.setattr(rightapi.httpx, "Client", FakeClient)
    return calls


def _media() -> list[InputMedia]:
    return [InputMedia(kind="image", data_url="data:image/png;base64,AAAA")]


def test_rightapi_urls_accept_draw_base_and_site_root() -> None:
    for base in ("https://www.rightapi.ai", "https://www.rightapi.ai/draw", "https://www.rightapi.ai/draw/"):
        adapter = RightApiAdapter(base, "sk-1")
        # 提交走 /draw 前缀，查询走站点级（不带 /draw）——文档明确区分
        assert adapter._draw_base == "https://www.rightapi.ai/draw"
        assert adapter._site_base == "https://www.rightapi.ai"


def test_rightapi_submit_is_async_and_forwards_reference_images(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch, _FakeResponse({"task_id": "task_abc", "status": "processing", "progress": 0}))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.submit(
        kind="image",
        model="nano-banana-2",
        prompt="一只太空橘猫",
        params={"size": "1:1", "imageSize": "1K", "n": 1},
        images=_media(),
        audios=[],
    )

    url, kwargs = calls["post"]
    assert url == "https://www.rightapi.ai/draw/v1/images/generations"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    body = kwargs["json"]
    assert body["async"] is True
    assert body["model"] == "nano-banana-2"
    assert body["image"] == ["data:image/png;base64,AAAA"]
    # 提交只拿到任务号，交回编排层保存
    assert update.upstream_id == "task_abc"
    assert update.status == "running"
    assert not update.terminal


def test_rightapi_submit_rejects_video(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse({"task_id": "t"}))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")
    with pytest.raises(rightapi.ProviderError):
        adapter.submit(kind="video", model="x", prompt="p", params={}, images=[], audios=[])


def test_rightapi_submit_phase_failure_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """文档：参数错误 / 余额不足 / 上游拒绝会在提交阶段直接返回 task_id + failed。"""

    payload = {
        "task_id": "task_x",
        "status": "failed",
        "error": "上游 HTTP 404: 404 page not found",
        "message": "任务id: task_x 处理失败",
    }
    _patch_client(monkeypatch, _FakeResponse(payload))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.submit(kind="image", model="nano-banana-2", prompt="p", params={}, images=[], audios=[])

    # 必须当成终态失败交回编排层，而不是抛异常去重试
    assert update.status == "failed"
    assert update.terminal
    assert "404" in update.error
    assert update.upstream_id == "task_x"


def test_rightapi_poll_uses_site_level_task_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch, _FakeResponse({"task_id": "task_abc", "status": "in_progress", "progress": 45}))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.poll("task_abc")

    url, _ = calls["get"]
    assert url == "https://www.rightapi.ai/v1/tasks/task_abc"
    assert update.status == "running"
    assert update.progress == 45
    assert not update.terminal


def test_rightapi_poll_escapes_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务号来自上游，塞进 URL 路径前必须转义，别让它改写请求结构。"""

    calls = _patch_client(monkeypatch, _FakeResponse({"task_id": "t", "status": "in_progress"}))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    adapter.poll("task/../../admin?x=1")

    url, _ = calls["get"]
    assert url == "https://www.rightapi.ai/v1/tasks/task%2F..%2F..%2Fadmin%3Fx%3D1"
    assert "?" not in url.split("/tasks/")[1]


def test_rightapi_poll_completed_images_has_no_status_field(monkeypatch: pytest.MonkeyPatch) -> None:
    # 文档：完成态响应不带 status，只有 data[].url —— 必须靠结果反推成功
    payload = {"created": 1782800000, "data": [{"url": "https://cdn.example.com/a.png"}]}
    _patch_client(monkeypatch, _FakeResponse(payload))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.poll("task_abc")

    assert update.status == "succeeded"
    assert update.terminal
    assert update.results == ["https://cdn.example.com/a.png"]
    assert update.progress == 100


def test_rightapi_poll_completed_gemini_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": "https://cdn.example.com/g.png"}]}, "index": 0}
        ]
    }
    _patch_client(monkeypatch, _FakeResponse(payload))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.poll("task_abc")

    assert update.status == "succeeded"
    assert update.results == ["https://cdn.example.com/g.png"]


def test_rightapi_poll_failure_reads_nested_error(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"task_id": "task_abc", "status": "failed", "progress": 100, "error": {"message": "上游生成失败", "code": ""}}
    _patch_client(monkeypatch, _FakeResponse(payload))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")

    update = adapter.poll("task_abc")

    assert update.status == "failed"
    assert update.terminal
    assert update.error == "上游生成失败"


def test_rightapi_expired_is_treated_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse({"task_id": "t", "status": "expired"}))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")
    assert adapter.poll("t").status == "failed"


def test_rightapi_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(None))
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "sk-test")
    with pytest.raises(rightapi.ProviderError):
        adapter.poll("t")


def test_rightapi_requires_credentials() -> None:
    adapter = RightApiAdapter("https://www.rightapi.ai/draw", "")
    with pytest.raises(rightapi.ProviderError):
        adapter.poll("t")


# --------------------------------------------------------------------------- #
# 端到端：任务校验与自定义模型
# --------------------------------------------------------------------------- #


def test_task_rejects_model_owned_by_another_provider(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "provider": "rightapi",
            # nano-banana-2-cl 只存在于 gsai 的清单里
            "model": "nano-banana-2-cl",
            "prompt": "x",
            "params": {},
        },
    )
    assert response.status_code == 400, response.text


def test_task_rejects_video_for_image_only_provider(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "video", "provider": "rightapi", "model": "nano-banana-2", "prompt": "x", "params": {}},
    )
    assert response.status_code == 400, response.text
    assert "不支持" in response.text


def test_task_accepts_rightapi_model(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "provider": "rightapi",
            "model": "nano-banana-2",
            "prompt": "一只太空橘猫",
            "params": {"aspectRatio": "16:9", "imageSize": "1K"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "rightapi"
    assert body["model"] == "nano-banana-2"


def test_extra_models_extend_catalog_and_allow_tasks(client: TestClient, auth_headers: dict[str, str]) -> None:
    # RightAPI 的模型列表只在自家后台可见，所以支持在配置里手填
    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={
            "providers": [
                {
                    "provider": "rightapi",
                    "base_url": "https://www.rightapi.ai/draw",
                    "extra_models": "seedream-4, nano-banana-x",
                }
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    rightapi_entry = next(item for item in body["providers"] if item["provider"] == "rightapi")
    assert rightapi_entry["extra_models"] == "seedream-4, nano-banana-x"
    assert rightapi_entry["kinds"] == ["image"]

    catalog_ids = {item["id"] for item in body["catalog"]["image"] if item["provider"] == "rightapi"}
    assert {"seedream-4", "nano-banana-x"} <= catalog_ids

    created = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={"kind": "image", "provider": "rightapi", "model": "seedream-4", "prompt": "x", "params": {}},
    )
    assert created.status_code == 201, created.text
    assert created.json()["model"] == "seedream-4"


def test_default_model_reads_back_provider_qualified(client: TestClient, auth_headers: dict[str, str]) -> None:
    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        # 裸模型名（旧配置形态）应被解析成 provider:model
        json={"default_image_model": "nano-banana-fast", "default_video_model": "minimax-h3"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["default_image_model"] == "gsai:nano-banana-fast"
    assert body["default_video_model"] == "gsai:minimax-h3"


def test_default_model_rejects_unknown(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={"default_image_model": "no-such-model"},
    )
    assert response.status_code == 400


def test_rightapi_task_runs_through_orchestration(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """编排层要真的把 rightapi 的提交与查询串起来（含站点级查询地址）。"""

    saved = client.put(
        "/api/admin/generation/settings",
        headers=auth_headers,
        json={
            "providers": [
                {"provider": "rightapi", "base_url": "https://www.rightapi.ai/draw", "api_key": "sk-live"}
            ]
        },
    )
    assert saved.status_code == 200, saved.text

    calls = _patch_client(
        monkeypatch,
        responses={
            "post": _FakeResponse({"task_id": "task_live", "status": "processing", "progress": 0}),
            # 完成态：没有 status，只有 data[].url
            "get": _FakeResponse({"created": 1, "data": [{"url": "https://cdn.example.com/out.png"}]}),
        },
    )

    created = client.post(
        "/api/admin/generation/tasks",
        headers=auth_headers,
        json={
            "kind": "image",
            "provider": "rightapi",
            "model": "nano-banana-2",
            "prompt": "一只太空橘猫",
            "params": {"aspectRatio": "1:1", "imageSize": "1K"},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    assert service.submit_pending_once()["submitted"] == 1
    assert calls["post"][0] == "https://www.rightapi.ai/draw/v1/images/generations"
    assert calls["post"][1]["json"]["async"] is True

    assert service.poll_running_once()["finished"] == 1
    assert calls["get"][0] == "https://www.rightapi.ai/v1/tasks/task_live"

    detail = client.get(f"/api/admin/generation/tasks/{task_id}", headers=auth_headers).json()
    assert detail["provider"] == "rightapi"
    assert detail["status"] == "succeeded"
    assert len(detail["results"]) == 1
