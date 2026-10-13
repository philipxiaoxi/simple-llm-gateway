"""RightAPI（rightapi / rc_draw）供应商：适配器 + 模型清单。

文档：https://docs.rightapi.ai/docs/rc_draw/

统一走异步模式，与 `GenerationUpdate` 的对应关系：

- 提交：`POST {draw}/v1/images/generations`，请求体固定带 `"async": true`，
  立即返回 `{"task_id": ..., "status": "processing"}`。
- 查询：`GET {site}/v1/tasks/{task_id}`（**站点级，不带 `/draw`**）。
  完成时的响应**没有 `status` 字段**，而是直接给 `data[].url`（Images 协议）
  或 `candidates[].content.parts[].text`（Gemini 协议）—— 这一点和 gsai 不同，
  所以判定顺序是「先看有没有结果，再看 status」。

因此 `base_url` 允许填站点根（`https://www.rightapi.ai`）或文档里的绘图基础地址
（`https://www.rightapi.ai/draw`），适配器内部自行推导两个前缀。

能力边界（照文档）：只有图片；`mask`（局部重绘）与 `variations` 不支持，
结果固定为图片 URL，`response_format` 不生效。

模型清单不是猜的：照 RightAPI 后台「模型列表 → 画图 1x」端点核对过（OpenAI 5 + Google 4），
各型号的分辨率档位取自该页的「模型说明」。文档示例里出现的 `nano-banana-fast` 并不在该端点内，
所以没有列进来。若你的账号开了别的型号，用配置界面的「自定义模型」补即可，不必改代码。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.generation.base import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    GenerationUpdate,
    InputMedia,
    ProviderError,
)
from app.generation.catalog import ModelSpec, ParamSpec, opts

PROVIDER = "rightapi"
LABEL = "RightAPI（rightapi）"
# 文档给的绘图基础地址；填站点根也能用
DEFAULT_BASE_URL = "https://www.rightapi.ai/draw"
KINDS = ("image",)

_DRAW_SUFFIX = "/draw"
# 进行中 / 失败 / 完成。docs：queued、in_progress 排队或处理中；
# completed 完成；expired、canceled 按失败处理。
_RUNNING_STATUSES = {"queued", "in_progress", "processing", "running", "pending", "submitted"}
_SUCCEEDED_STATUSES = {"completed", "succeeded", "success", "done", "finished"}
_FAILED_STATUSES = {"failed", "expired", "canceled", "cancelled", "error"}


def _extract_error(payload: dict[str, Any]) -> str:
    """error 可能是字符串（gsai 风格），也可能是 `{message, code}` 对象。"""

    raw = payload.get("error")
    if isinstance(raw, dict):
        message = str(raw.get("message") or "").strip()
        code = str(raw.get("code") or "").strip()
        if message and code:
            return f"{message}（{code}）"
        if message:
            return message
    elif raw:
        return str(raw).strip()
    return str(payload.get("message") or "").strip()


def _extract_results(payload: dict[str, Any]) -> list[str]:
    """结果可能是 Images 的 `data[].url`，也可能是 Gemini 的 candidates 文本。"""

    results: list[str] = []

    def _push(value: object) -> None:
        url = str(value or "").strip()
        # Gemini 那条把 URL 放在 text 里，这里只收 http(s) 开头的，避免把说明文字当结果
        if url and url.startswith(("http://", "https://")):
            results.append(url)

    for entry in payload.get("data") or []:
        # 文档：结果固定为图片 URL，不返回 base64
        if isinstance(entry, dict):
            _push(entry.get("url"))
        else:
            _push(entry)

    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                _push(part.get("text"))

    return results


def _progress(payload: dict[str, Any]) -> int:
    try:
        value = int(payload.get("progress") or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(100, value))


def _parse(payload: dict[str, Any]) -> GenerationUpdate:
    results = _extract_results(payload)
    raw = str(payload.get("status") or "").strip().lower()

    if raw in _FAILED_STATUSES:
        status = STATUS_FAILED
    elif raw in _SUCCEEDED_STATUSES:
        status = STATUS_SUCCEEDED
    elif results:
        # 完成态响应不带 status，靠结果反推
        status = STATUS_SUCCEEDED
    elif raw in _RUNNING_STATUSES:
        status = STATUS_RUNNING
    else:
        # 未知取值按进行中，交给后续轮询 / 超时兜底
        status = STATUS_RUNNING

    progress = _progress(payload)
    if status == STATUS_SUCCEEDED:
        progress = max(progress, 100)

    return GenerationUpdate(
        status=status,
        progress=progress,
        results=results,
        error=_extract_error(payload) if status == STATUS_FAILED else "",
    )


class RightApiAdapter:
    provider = PROVIDER

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()

    # -- URL 推导 ---------------------------------------------------------- #

    @property
    def _draw_base(self) -> str:
        """绘图接口前缀，始终以 /draw 结尾。"""

        if self.base_url.endswith(_DRAW_SUFFIX):
            return self.base_url
        return f"{self.base_url}{_DRAW_SUFFIX}"

    @property
    def _site_base(self) -> str:
        """站点根，任务查询接口挂在站点级。"""

        if self.base_url.endswith(_DRAW_SUFFIX):
            return self.base_url[: -len(_DRAW_SUFFIX)]
        return self.base_url

    # -- HTTP -------------------------------------------------------------- #

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _timeout(self) -> httpx.Timeout:
        seconds = max(5, int(get_settings().generation_http_timeout_seconds))
        return httpx.Timeout(seconds, connect=min(10, seconds))

    def _ensure_configured(self) -> None:
        if not self.base_url or not self.api_key:
            raise ProviderError("rightapi 未配置 base_url 或 API Key")

    def submit(
        self,
        *,
        kind: str,
        model: str,
        prompt: str,
        params: dict[str, object],
        images: list[InputMedia],
        audios: list[InputMedia],
    ) -> GenerationUpdate:
        self._ensure_configured()
        if kind != "image":
            raise ProviderError("rightapi 目前只支持图片生成")
        body: dict[str, Any] = {"model": model, "prompt": prompt, "async": True}
        for key, value in params.items():
            if value is None or value == "":
                continue
            body[key] = value
        body.setdefault("n", 1)
        if images:
            # 文档：生成接口用 image / images 数组承载参考图（data: URL 或公网 URL 均可）
            body["image"] = [media.data_url for media in images]
        url = f"{self._draw_base}/v1/images/generations"
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as error:
            raise ProviderError(f"请求 rightapi 生成接口失败：{error}") from error

        payload = self._json(response, "rightapi")
        update = _parse(payload)
        update.upstream_id = str(payload.get("task_id") or payload.get("id") or "").strip()
        if response.status_code >= 400 and not update.terminal:
            raise ProviderError(
                f"rightapi 返回 HTTP {response.status_code}：{update.error or response.text[:200]}"
            )
        # 提交阶段就直接失败（参数错误 / 余额不足 / 上游拒绝）时，文档同样返回 task_id + failed
        if not update.terminal and not update.upstream_id:
            raise ProviderError("rightapi 未返回 task_id，无法继续轮询")
        return update

    def poll(self, upstream_id: str) -> GenerationUpdate:
        self._ensure_configured()
        # 任务号是上游给的，塞进路径前先转义，免得把 / ? # 带进 URL 结构
        url = f"{self._site_base}/v1/tasks/{quote(upstream_id, safe='')}"
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.get(url, headers=self._headers())
        except httpx.HTTPError as error:
            raise ProviderError(f"查询 rightapi 结果失败：{error}") from error

        payload = self._json(response, "rightapi")
        update = _parse(payload)
        if response.status_code >= 400 and not update.terminal:
            raise ProviderError(
                f"rightapi 查询返回 HTTP {response.status_code}：{update.error or response.text[:200]}"
            )
        return update

    @staticmethod
    def _json(response: httpx.Response, name: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError(f"{name} 返回了非 JSON 响应：{response.text[:200]}") from error
        if not isinstance(payload, dict):
            raise ProviderError(f"{name} 返回结构异常")
        return payload


# --------------------------------------------------------------------------- #
# 模型清单
# --------------------------------------------------------------------------- #

# 比例档位：文档只列了 1:1 / 16:9 / 9:16 / 4:3，其余按 Gemini imageConfig 的标准档位补齐
# （nano-banana 系列就是 Gemini 图像模型）。若某个比例被上游拒绝，删掉对应项即可。
_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
# gpt-image 系列走 OpenAI 的像素串写法（文档示例：1024x1024）；
# 像素串没法用 aspectRatio 表达，所以这系列只能走 size
_PIXEL_SIZES = ("1024x1024", "1536x1024", "1024x1536", "2048x2048", "2048x1152", "1152x2048")

_COUNT_PARAM = ParamSpec("n", "生成数量", "number", default=1, minimum=1, maximum=4)


def _image_size(levels: tuple[str, ...]) -> ParamSpec:
    """每种型号支持的 imageSize 档位不同，多传可能被上游拒绝，所以逐型号声明。

    文档把 imageSize 标为可选，因此不设 required：调用方不传就用上游默认。
    """

    return ParamSpec(
        key="imageSize",
        label="分辨率",
        type="select",
        options=opts(levels),
        default=levels[0],
        help="该型号支持的分辨率档位",
    )


def _spec(
    model_id: str,
    *,
    size_key: str,
    sizes: tuple[str, ...],
    size_label: str,
    levels: tuple[str, ...] | None,
) -> ModelSpec:
    """`size_key` 必须是调用方大概率会传的那个键名。

    比例类模型用 `aspectRatio`：文档明确说它「作用同 size 的比例写法」，
    而且是本平台对外宣传的常用键（见 MCP 能力的 _PARAM_NOTE）。用 size 会让
    按文档调用的 MCP / REST 调用方参数被静默丢掉，只剩一句「请填写画面比例」。
    """

    params: list[ParamSpec] = [
        ParamSpec(size_key, size_label, "select", opts(sizes), sizes[0], required=True)
    ]
    if levels:
        params.append(_image_size(levels))
    params.append(_COUNT_PARAM)
    return ModelSpec(
        id=model_id,
        label=model_id,
        kind="image",
        params=tuple(params),
        supports_images=True,
        max_images=9,
    )


# 模型清单照 RightAPI 后台「模型列表 → 画图 1x」端点核对（OpenAI 5 + Google 4 = 9 个）。
# 各型号的分辨率档位取自该页的「模型说明」。
_RATIO_MODELS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("nano-banana", ("1K",)),  # gemini-2.5-flash-image
    ("nano-banana-2", ("1K", "2K", "4K")),  # gemini-3.1-flash-image-preview
    ("nano-banana-2-lite", ("1K",)),  # gemini-3.1-flash-lite-image
    ("nano-banana-pro", ("1K", "2K", "4K")),  # gemini-3-pro-image-preview
)

_PIXEL_MODELS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("gpt-image-2", ("1K",)),
    ("gpt-image-2-vip", ("1K", "2K", "4K")),  # 官方直连
)

# 文档只说明 imageSize 对「nano-banana / gpt-image vip 系列」生效，
# 这三个没有说明，索性不暴露该参数，免得被上游拒绝。
_PLAIN_PIXEL_MODELS: tuple[str, ...] = (
    "gpt-image-2.5",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
)

MODELS: tuple[ModelSpec, ...] = (
    # ---- Google / nano-banana 系列：比例写法，走 aspectRatio ----
    *(
        _spec(model_id, size_key="aspectRatio", sizes=_RATIOS, size_label="画面比例", levels=levels)
        for model_id, levels in _RATIO_MODELS
    ),
    # ---- OpenAI / gpt-image 系列：像素写法，只能走 size ----
    *(
        _spec(model_id, size_key="size", sizes=_PIXEL_SIZES, size_label="画面尺寸", levels=levels)
        for model_id, levels in _PIXEL_MODELS
    ),
    *(
        _spec(model_id, size_key="size", sizes=_PIXEL_SIZES, size_label="画面尺寸", levels=None)
        for model_id in _PLAIN_PIXEL_MODELS
    ),
)

ADAPTER = RightApiAdapter
