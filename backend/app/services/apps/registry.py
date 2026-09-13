from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AppManifest:
    id: str
    name: str
    description: str
    icon: str
    category: str
    entry_path: str
    version: str
    capabilities: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)
    default_config: dict[str, Any] = field(default_factory=dict)
    default_enabled: bool = True


BUILTIN_APPS: tuple[AppManifest, ...] = (
    AppManifest(
        id="ocr",
        name="图片 OCR",
        description="上传图片，通过已绑定的上游视觉模型识别文字，结果可复制。",
        icon="scan-text",
        category="AI 能力",
        entry_path="/apps/ocr",
        version="1.0.0",
        capabilities=("vision", "image_input"),
        config_schema={
            "type": "object",
            "properties": {
                "system_prompt": {"type": "string", "title": "系统提示词"},
                "max_tokens": {"type": "integer", "title": "最大输出 Token", "minimum": 64, "maximum": 8192},
            },
        },
        default_config={
            "system_prompt": (
                "你是 OCR 引擎。只输出图片中的文字内容，保持原有换行与阅读顺序。"
                "不要解释、不要翻译、不要添加 Markdown 或前后缀。"
                "若图中没有文字，输出空字符串。"
            ),
            "max_tokens": 2048,
        },
        default_enabled=True,
    ),
    AppManifest(
        id="static-deploy",
        name="静态站点部署",
        description="上传静态前端页面（HTML/CSS/JS 或 zip），生成可公开访问的预览地址。",
        icon="globe",
        category="运维工具",
        entry_path="/apps/static-deploy",
        version="1.0.0",
        capabilities=("static_hosting",),
        config_schema={
            "type": "object",
            "properties": {
                "max_sites": {"type": "integer", "title": "站点数量上限", "minimum": 1, "maximum": 100},
                "max_site_bytes": {"type": "integer", "title": "单站体积上限（字节）"},
            },
        },
        default_config={
            "max_sites": 20,
            "max_site_bytes": 50 * 1024 * 1024,
        },
        default_enabled=True,
    ),
)


def get_manifest(app_id: str) -> AppManifest | None:
    for item in BUILTIN_APPS:
        if item.id == app_id:
            return item
    return None


def list_manifests() -> list[AppManifest]:
    return list(BUILTIN_APPS)
