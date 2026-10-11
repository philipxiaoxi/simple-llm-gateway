"""供应商注册表：代码侧的能力声明 + 适配器构造。

未来接入新供应商时，只需：
1. 在 `catalog.py` 增加该供应商支持的 `ModelSpec`（模型 id 唯一即可），
2. 在这里 `PROVIDERS` 增加一条 `ProviderInfo`，
3. 若协议不同，再写一个实现 `ProviderAdapter` 的适配器。
前端与编排层无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.generation.base import ProviderAdapter
from app.generation.gsai import PROVIDER as GSAI
from app.generation.gsai import GsaiAdapter


@dataclass(frozen=True)
class ProviderInfo:
    provider: str
    label: str
    default_base_url: str


PROVIDERS: dict[str, ProviderInfo] = {
    GSAI: ProviderInfo(provider=GSAI, label="Grsai（gsai）", default_base_url="https://grsaiapi.com"),
}

# 供应商代码 -> 适配器构造器
_ADAPTERS = {
    GSAI: GsaiAdapter,
}


def provider_info(provider: str) -> ProviderInfo | None:
    return PROVIDERS.get(provider)


def default_provider() -> str:
    return next(iter(PROVIDERS))


def build_adapter(provider: str, *, base_url: str, api_key: str) -> ProviderAdapter:
    factory = _ADAPTERS.get(provider)
    if factory is None:
        raise ValueError(f"不支持的供应商：{provider}")
    return factory(base_url, api_key)
