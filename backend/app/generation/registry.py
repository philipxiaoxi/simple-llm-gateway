"""供应商注册表：自动发现 `providers/` 下的供应商模块，并聚合模型目录。

## 接入一个新供应商要做什么

**只需要在 `app/generation/providers/` 下新增一个 .py 文件**，本文件会自动发现它。
模块需要暴露：

```python
PROVIDER = "acme"                    # 供应商标识（DB 里 generation_providers.provider）
LABEL = "Acme（acme）"                # 展示名
DEFAULT_BASE_URL = "https://..."     # 默认地址，管理员可在配置里改
KINDS = ("image",)                   # 支持的任务类型
ADAPTER = AcmeAdapter                # 实现 app.generation.base.ProviderAdapter
MODELS = (ModelSpec(...), ...)       # 该供应商的模型清单
ORDER = 100                          # 可选，越小越靠前，决定默认供应商
```

`ADAPTER` 只需实现 `submit()` / `poll()`；上游差异全部收敛在适配器内部，
编排层（`service.py`）和前端都只认 `GenerationUpdate` 与 `ModelSpec`，
所以新增供应商不需要改 `service.py`、路由或任何前端代码。

## 为什么模型挂在供应商下面

不同供应商的模型集合并不相同（RightAPI 只有图片、没有视频），同名模型也可能同时出现在
多个供应商（都卖 nano-banana）。所以模型不再是一张全局扁平列表，而是按供应商分组，
查找一律带 provider，避免同名互相覆盖。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from app.generation import providers as providers_package
from app.generation.base import ProviderAdapter
from app.generation.catalog import ModelSpec, custom_model_spec

logger = logging.getLogger(__name__)

DEFAULT_ORDER = 100
# 自定义模型名可带 `:image` / `:video` 后缀，给「图片 + 视频」的供应商指定类型
_KIND_SUFFIXES = ("image", "video")


@dataclass(frozen=True)
class ProviderInfo:
    provider: str
    label: str
    default_base_url: str
    kinds: tuple[str, ...]
    adapter: type
    models: tuple[ModelSpec, ...] = field(default_factory=tuple)
    order: int = DEFAULT_ORDER

    def supports(self, kind: str) -> bool:
        return kind in self.kinds

    def models_for(self, kind: str = "") -> tuple[ModelSpec, ...]:
        """该供应商的模型（已带上 provider / provider_label）。"""

        models = self.models if not kind else tuple(m for m in self.models if m.kind == kind)
        return tuple(m.with_provider(self.provider, self.label) for m in models)


def _load(module: ModuleType) -> ProviderInfo | None:
    code = str(getattr(module, "PROVIDER", "") or "").strip()
    adapter = getattr(module, "ADAPTER", None)
    if not code or adapter is None:
        logger.warning(
            "供应商模块 %s 缺少 PROVIDER 或 ADAPTER，已跳过（该供应商不会出现在目录里）",
            getattr(module, "__name__", module),
        )
        return None
    models = tuple(getattr(module, "MODELS", ()) or ())
    kinds = tuple(getattr(module, "KINDS", ()) or ()) or tuple(sorted({m.kind for m in models}))
    try:
        order = int(getattr(module, "ORDER", DEFAULT_ORDER))
    except (TypeError, ValueError):
        # 写错 ORDER 只是排序问题，不该把整个应用启动搞崩
        logger.warning("供应商 %s 的 ORDER 不是整数，已按默认值 %d 处理", code, DEFAULT_ORDER)
        order = DEFAULT_ORDER
    return ProviderInfo(
        provider=code,
        label=str(getattr(module, "LABEL", "") or code),
        default_base_url=str(getattr(module, "DEFAULT_BASE_URL", "") or ""),
        kinds=kinds,
        adapter=adapter,
        models=models,
        order=order,
    )


def _discover() -> dict[str, ProviderInfo]:
    """扫描 providers 包，导入每个模块并读取其自描述信息。"""

    found: dict[str, ProviderInfo] = {}
    for module_info in pkgutil.iter_modules(providers_package.__path__):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{providers_package.__name__}.{module_info.name}")
        info = _load(module)
        if info is None:
            continue
        found[info.provider] = info
    return dict(sorted(found.items(), key=lambda item: (item[1].order, item[0])))


PROVIDERS: dict[str, ProviderInfo] = _discover()


def provider_info(provider: str) -> ProviderInfo | None:
    return PROVIDERS.get(provider)


def provider_codes() -> list[str]:
    return list(PROVIDERS)


def default_provider(kind: str = "image") -> str:
    """默认供应商：按注册顺序取第一个支持该类型的。"""

    for info in PROVIDERS.values():
        if info.supports(kind):
            return info.provider
    return next(iter(PROVIDERS), "")


def build_adapter(provider: str, *, base_url: str, api_key: str) -> ProviderAdapter:
    info = PROVIDERS.get(provider)
    if info is None:
        raise ValueError(f"不支持的供应商：{provider}")
    return info.adapter(base_url, api_key)  # type: ignore[no-any-return]


def static_models(provider: str, kind: str = "") -> tuple[ModelSpec, ...]:
    info = PROVIDERS.get(provider)
    if info is None:
        return ()
    return info.models_for(kind)


def iter_models(kind: str = "") -> list[ModelSpec]:
    models: list[ModelSpec] = []
    for info in PROVIDERS.values():
        models.extend(info.models_for(kind))
    return models


def parse_names(raw: object) -> list[str]:
    """把配置里的自定义模型名（逗号 / 空格 / 换行分隔）拆干净并去重。"""

    seen: list[str] = []
    text = str(raw or "").replace(",", " ").replace("，", " ").replace("\n", " ")
    for token in text.split():
        name = token.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def extra_model_specs(
    raw: object,
    *,
    static_ids: set[str],
    default_kind: str = "image",
    kinds: tuple[str, ...] = ("image",),
) -> list[ModelSpec]:
    """把自定义模型名包装成规格；已在静态清单里的名字忽略。

    供应商同时支持图片和视频时（如 gsai），用 `模型名:video` / `模型名:image` 指定类型，
    不写就用 default_kind。后缀不是该供应商支持的类型时，整个 token 当作模型名，
    不做静默改写（免得把用户想要的名字悄悄换成另一个）。
    """

    specs: list[ModelSpec] = []
    for token in parse_names(raw):
        name, _, suffix = token.rpartition(":")
        kind = default_kind
        if suffix.lower() in _KIND_SUFFIXES and suffix.lower() in kinds:
            kind = suffix.lower()
        else:
            name = token
        if not name or name in static_ids:
            continue
        specs.append(custom_model_spec(name, kind))
    return specs


def get_model(model_id: str, *, kind: str = "", provider: str = "") -> ModelSpec | None:
    """按 provider + id 精确查找；不指定 provider 时在所有供应商里找第一个匹配。"""

    if provider:
        for spec in static_models(provider, kind):
            if spec.id == model_id:
                return spec
        return None
    for spec in iter_models(kind):
        if spec.id == model_id:
            return spec
    return None


def default_model(kind: str) -> str:
    for info in PROVIDERS.values():
        models = info.models_for(kind)
        if models:
            return models[0].id
    return ""


def catalog_payload(extra: dict[str, list[ModelSpec]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """前端目录：按 image / video 分组，每个模型带 provider 字段。

    `extra` 是「管理员在配置里手填的自定义模型」，形如 {provider: [ModelSpec, ...]}。
    """

    grouped: dict[str, list[dict[str, Any]]] = {"image": [], "video": []}
    for info in PROVIDERS.values():
        for spec in info.models_for():
            grouped.setdefault(spec.kind, []).append(spec.to_dict())
    for provider, specs in (extra or {}).items():
        label = PROVIDERS[provider].label if provider in PROVIDERS else provider
        for spec in specs:
            grouped.setdefault(spec.kind, []).append(spec.with_provider(provider, label).to_dict())
    return grouped
