"""供应商适配层的公共类型。

所有供应商适配器都实现：提交生成任务（`submit`）与查询任务结果（`poll`）。
上游差异（请求字段、状态取值）全部收敛在适配器内部，编排层只认 `GenerationUpdate`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# 统一状态：与 generation_tasks.status 对齐
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_VIOLATION = "violation"

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_VIOLATION})


@dataclass(frozen=True)
class InputMedia:
    """提交给上游的参考文件，data_url 形如 `data:image/png;base64,...`。"""

    kind: str  # image | audio
    data_url: str


@dataclass
class GenerationUpdate:
    status: str
    progress: int = 0
    results: list[str] = field(default_factory=list)
    error: str = ""
    upstream_id: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class ProviderError(Exception):
    """调用供应商接口失败（网络、超时、非预期响应）。"""


class ProviderAdapter(Protocol):
    provider: str

    def submit(
        self,
        *,
        kind: str,
        model: str,
        prompt: str,
        params: dict[str, object],
        images: list[InputMedia],
        audios: list[InputMedia],
    ) -> GenerationUpdate: ...

    def poll(self, upstream_id: str) -> GenerationUpdate: ...
