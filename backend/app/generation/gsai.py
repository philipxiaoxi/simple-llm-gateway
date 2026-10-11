"""gsai（Grsai）供应商适配器。

对接两个接口：
- 生成：`POST {base}/v1/api/generate`（replyType=async 时立即返回任务 id）
- 查询：`GET {base}/v1/api/result?id=`

请求/响应字段与文档一致，状态取值 running / violation / succeeded / failed。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.generation.base import (
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_VIOLATION,
    GenerationUpdate,
    InputMedia,
    ProviderError,
)

PROVIDER = "gsai"
_KNOWN_STATUSES = {STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_VIOLATION}


def _normalize_status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in _KNOWN_STATUSES:
        return value
    return value if value else STATUS_RUNNING


def _parse(payload: dict[str, Any]) -> GenerationUpdate:
    results: list[str] = []
    for entry in payload.get("results") or []:
        url = (
            str(entry.get("url") or "").strip()
            if isinstance(entry, dict)
            else str(entry or "").strip()
        )
        if url:
            results.append(url)
    try:
        progress = int(payload.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0
    return GenerationUpdate(
        status=_normalize_status(payload.get("status")),
        progress=max(0, min(100, progress)),
        results=results,
        error=str(payload.get("error") or ""),
    )


class GsaiAdapter:
    provider = PROVIDER

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _timeout(self) -> httpx.Timeout:
        seconds = max(5, int(get_settings().generation_http_timeout_seconds))
        return httpx.Timeout(seconds, connect=min(10, seconds))

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
        if not self.base_url or not self.api_key:
            raise ProviderError("gsai 未配置 base_url 或 API Key")
        body: dict[str, Any] = {"model": model, "prompt": prompt, "replyType": "async"}
        for key, value in params.items():
            if value is None or value == "":
                continue
            body[key] = value
        if images:
            body["images"] = [media.data_url for media in images]
        if audios:
            body["audios"] = [media.data_url for media in audios]
        url = f"{self.base_url}/v1/api/generate"
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as error:
            raise ProviderError(f"请求 gsai 生成接口失败：{error}") from error
        payload = self._json(response)
        update = _parse(payload)
        # 异步返回通常只有 id + running；把 id 交回编排层保存
        update.upstream_id = str(payload.get("id") or "").strip()
        if response.status_code >= 400 and not update.terminal:
            raise ProviderError(f"gsai 返回 HTTP {response.status_code}：{update.error or response.text[:200]}")
        return update

    def poll(self, upstream_id: str) -> GenerationUpdate:
        if not self.base_url or not self.api_key:
            raise ProviderError("gsai 未配置 base_url 或 API Key")
        url = f"{self.base_url}/v1/api/result"
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                response = client.get(url, headers=self._headers(), params={"id": upstream_id})
        except httpx.HTTPError as error:
            raise ProviderError(f"查询 gsai 结果失败：{error}") from error
        payload = self._json(response)
        update = _parse(payload)
        if response.status_code >= 400 and not update.terminal:
            raise ProviderError(f"gsai 查询返回 HTTP {response.status_code}：{update.error or response.text[:200]}")
        return update

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError(f"gsai 返回了非 JSON 响应：{response.text[:200]}") from error
        if not isinstance(payload, dict):
            raise ProviderError("gsai 返回结构异常")
        return payload
