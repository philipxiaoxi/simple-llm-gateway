"""离线下载来源登记表：新增来源时只在这里追加一条。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProviderInfo:
    slug: str
    label: str
    short_label: str
    description: str
    placeholder: str


PROVIDERS: tuple[ProviderInfo, ...] = (
    ProviderInfo(
        slug="vscode",
        label="VSCode 插件",
        short_label="VSCode",
        description="粘贴 Marketplace 插件页链接，获取 .vsix 直链。",
        placeholder="https://marketplace.visualstudio.com/items?itemName=publisher.extension",
    ),
    ProviderInfo(
        slug="chrome",
        label="Chrome 扩展",
        short_label="Chrome",
        description="扩展名称、32 位 ID 或商店链接，下载 .crx / .zip。",
        placeholder="扩展名称 / 32 位扩展 ID / Chrome 应用商店链接",
    ),
    ProviderInfo(
        slug="edge",
        label="Edge 扩展",
        short_label="Edge",
        description="名称、CRX ID、ProductId 或商店链接，下载 .crx / .zip。",
        placeholder="名称 / CRX ID / ProductId / Edge 加载项链接",
    ),
    ProviderInfo(
        slug="docker",
        label="Docker 镜像",
        short_label="Docker",
        description="镜像名或 Docker Hub 链接，打包为 docker load 兼容的 .tar。",
        placeholder="nginx:latest / library/nginx / Docker Hub 链接",
    ),
    ProviderInfo(
        slug="msstore",
        label="Microsoft 商店",
        short_label="MS商店",
        description="商店链接、ProductId、PackageFamilyName 或 CategoryId，获取安装包。",
        placeholder="商店链接 / ProductId / PackageFamilyName / CategoryId",
    ),
)

_BY_SLUG = {provider.slug: provider for provider in PROVIDERS}


def get_provider(slug: str) -> ProviderInfo | None:
    return _BY_SLUG.get(slug)


def serialize_providers() -> list[dict]:
    return [asdict(provider) for provider in PROVIDERS]
