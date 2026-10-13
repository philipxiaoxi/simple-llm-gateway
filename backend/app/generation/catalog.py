"""生成模型的**公共词汇表**：参数描述、模型描述与共享的取值常量。

这里只放「所有供应商都能复用」的东西，不放任何具体供应商的模型清单：
- 具体模型定义在 `app/generation/providers/<provider>.py` 里，与适配器放在一起；
- 目录聚合与查找在 `app/generation/registry.py`。

这样接入新供应商时不需要改动本文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    type: str  # select | combo | number | text
    options: tuple[Option, ...] = ()
    default: str | int | None = None
    minimum: int | None = None
    maximum: int | None = None
    required: bool = False
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": [{"value": opt.value, "label": opt.label} for opt in self.options],
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "required": self.required,
            "help": self.help,
        }


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    kind: str  # image | video
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    supports_images: bool = False
    max_images: int = 0
    supports_audios: bool = False
    max_audios: int = 0
    # 归属供应商：由 registry 聚合时填充，前端据此分组
    provider: str = ""
    provider_label: str = ""

    def with_provider(self, provider: str, provider_label: str) -> ModelSpec:
        """登记到某个供应商名下（dataclass 是 frozen 的，返回副本）。"""

        return ModelSpec(
            id=self.id,
            label=self.label,
            kind=self.kind,
            params=self.params,
            supports_images=self.supports_images,
            max_images=self.max_images,
            supports_audios=self.supports_audios,
            max_audios=self.max_audios,
            provider=provider,
            provider_label=provider_label,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "params": [param.to_dict() for param in self.params],
            "supports_images": self.supports_images,
            "max_images": self.max_images,
            "supports_audios": self.supports_audios,
            "max_audios": self.max_audios,
            "provider": self.provider,
            "provider_label": self.provider_label,
        }


def opts(values: tuple[str, ...]) -> tuple[Option, ...]:
    """把一串取值变成「值即标签」的下拉项。"""

    return tuple(Option(value, value) for value in values)


def labelled_opts(pairs: tuple[tuple[str, str], ...]) -> tuple[Option, ...]:
    """值与人话标签不同的下拉项，例如 (\"portrait\", \"竖屏 portrait\")。"""

    return tuple(Option(value, label) for value, label in pairs)


def options_with_blank(values: tuple[str, ...], blank_label: str = "默认") -> tuple[Option, ...]:
    """首项为「不传该参数」的下拉项。"""

    return (Option("", blank_label),) + opts(values)


# --------------------------------------------------------------------------- #
# 共享取值：聚合类供应商的模型基本都吃这几套比例 / 尺寸
# --------------------------------------------------------------------------- #

IMAGE_RATIOS = ("auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9")
IMAGE_RATIOS_EXTENDED = IMAGE_RATIOS + ("1:4", "4:1", "1:8", "8:1")
GPT_RATIOS = IMAGE_RATIOS + ("9:21", "1:2", "2:1")
PIXEL_SIZES = (
    "auto",
    # 1:1
    "1024x1024",
    "2048x2048",
    "2880x2880",
    # 16:9 / 9:16
    "1280x720",
    "2048x1152",
    "3840x2160",
    "720x1280",
    "1152x2048",
    "2160x3840",
    # 4:3 / 3:4
    "1152x864",
    "2304x1728",
    "3264x2448",
    "864x1152",
    "1728x2304",
    "2448x3264",
    # 3:2 / 2:3
    "1536x1024",
    "2048x1360",
    "3504x2336",
    "1024x1536",
    "1360x2048",
    "2336x3504",
    # 5:4 / 4:5
    "1120x896",
    "2240x1792",
    "3200x2560",
    "896x1120",
    "1792x2240",
    "2560x3200",
    # 21:9 / 9:21
    "1456x624",
    "2912x1248",
    "3840x1648",
    "624x1456",
    "1248x2912",
    "1648x3840",
    # 1:3 / 3:1
    "688x2048",
    "1280x3840",
    "2048x688",
    "3840x1280",
    # 2:1 / 1:2
    "1536x768",
    "3072x1536",
    "3840x1920",
    "768x1536",
    "1536x3072",
    "1920x3840",
)
# gpt-image-2 / 2.5 仅支持 1K 的固定像素（比例之外的另一种写法）
GPT_1K_PIXEL = ("1024x1024",)
GPT_RATIO_SIZES = GPT_RATIOS + GPT_1K_PIXEL

IMAGE_SIZE_PARAM = ParamSpec(
    key="imageSize",
    label="分辨率",
    type="select",
    options=opts(("1K", "2K", "4K")),
    default="1K",
    required=True,
)

BACKGROUND_PARAM = ParamSpec(
    key="background",
    label="背景",
    type="select",
    options=(Option("", "默认"), Option("transparent", "透明")),
    default="",
    help="仅部分模型支持透明背景",
)

MASK_PARAM = ParamSpec(key="mask", label="蒙版图片地址", type="text", default="", help="可选，图片 URL")


def custom_model_spec(model_id: str, kind: str = "image") -> ModelSpec:
    """把管理员手填的模型名包装成可用规格。

    供应商的模型列表未必公开（RightAPI 只在自家后台可见），所以允许在配置里补模型名，
    用一套通用的比例 / 分辨率参数兜底，不必为了试某个模型先改代码。
    """

    if kind == "video":
        return ModelSpec(
            id=model_id,
            label=model_id,
            kind="video",
            params=(
                ParamSpec("aspectRatio", "画面方向", "select", opts(("portrait", "landscape")), "portrait"),
                ParamSpec("duration", "时长（秒）", "number", default=5, minimum=1, maximum=30),
            ),
            supports_images=True,
            max_images=9,
        )
    return ModelSpec(
        id=model_id,
        label=model_id,
        kind="image",
        params=(
            ParamSpec("aspectRatio", "画面比例", "select", opts(IMAGE_RATIOS), "1:1"),
            ParamSpec(
                "imageSize",
                "分辨率",
                "select",
                options_with_blank(("1K", "2K", "4K")),
                "",
                help="不确定就留默认",
            ),
            ParamSpec("n", "生成数量", "number", default=1, minimum=1, maximum=4),
        ),
        supports_images=True,
        max_images=9,
    )
