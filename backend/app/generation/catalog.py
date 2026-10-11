"""生成模型目录：每个模型支持哪些参数，直接驱动前端表单渲染。

新增供应商或模型时，只要在这里补一条 `ModelSpec`，前端表单会自动出现对应字段，
无需改任何前端代码 —— 这是「未来可扩展」的核心。
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
    type: str  # select | combo | number
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
        }


def _opts(values: tuple[str, ...]) -> tuple[Option, ...]:
    return tuple(Option(value, value) for value in values)


IMAGE_RATIOS = ("auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9")
IMAGE_RATIOS_EXTENDED = IMAGE_RATIOS + ("1:4", "4:1", "1:8", "8:1")
GPT_RATIOS = IMAGE_RATIOS + ("9:21", "1:2", "2:1")
PIXEL_SIZES = (
    "auto",
    "1024x1024",
    "2048x2048",
    "2880x2880",
    "1280x720",
    "2048x1152",
    "3840x2160",
    "720x1280",
    "1152x2048",
    "2160x3840",
    "1152x864",
    "2304x1728",
    "3264x2448",
    "864x1152",
    "1728x2304",
    "2448x3264",
    "1024x1536",
    "1536x1024",
    "1792x896",
    "896x1792",
    "1120x896",
    "896x1120",
    "2912x1248",
    "1456x624",
)

_IMAGE_SIZE = ParamSpec(
    key="imageSize",
    label="分辨率",
    type="select",
    options=_opts(("1K", "2K", "4K")),
    default="1K",
    required=True,
)

_BACKGROUND = ParamSpec(
    key="background",
    label="背景",
    type="select",
    options=(Option("", "默认"), Option("transparent", "透明")),
    default="",
    help="仅部分模型支持透明背景",
)
_MASK = ParamSpec(key="mask", label="蒙版图片地址", type="text", default="", help="可选，图片 URL")

_NANO_MODELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nano-banana", IMAGE_RATIOS),
    ("nano-banana-fast", IMAGE_RATIOS),
    ("nano-banana-2", IMAGE_RATIOS_EXTENDED),
    ("nano-banana-2-cl", IMAGE_RATIOS),
    ("nano-banana-2-2k-cl", IMAGE_RATIOS),
    ("nano-banana-2-4k-cl", IMAGE_RATIOS),
    ("nano-banana-pro", IMAGE_RATIOS),
    ("nano-banana-pro-vt", IMAGE_RATIOS),
    ("nano-banana-pro-cl", IMAGE_RATIOS),
    ("nano-banana-pro-vip", IMAGE_RATIOS),
    ("nano-banana-pro-4k-vip", IMAGE_RATIOS),
)


def _nano(model_id: str, ratios: tuple[str, ...]) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        label=model_id,
        kind="image",
        params=(
            ParamSpec("aspectRatio", "画面比例", "combo", _opts(ratios), "1:1"),
            _IMAGE_SIZE,
        ),
        supports_images=True,
        max_images=9,
    )


def _gpt(model_id: str, *, sizes: tuple[str, ...], quality: tuple[str, ...], quality_default: str) -> ModelSpec:
    params: list[ParamSpec] = [
        ParamSpec("aspectRatio", "画面比例 / 尺寸", "combo", _opts(sizes), sizes[1] if len(sizes) > 1 else "auto", required=True),
        ParamSpec("quality", "质量", "select", _opts(quality), quality_default),
        _BACKGROUND,
        _MASK,
    ]
    return ModelSpec(
        id=model_id,
        label=model_id,
        kind="image",
        params=tuple(params),
        supports_images=True,
        max_images=9,
    )


_MODELS: tuple[ModelSpec, ...] = (
    # ---- nano-banana 系列 ----
    *(_nano(model_id, ratios) for model_id, ratios in _NANO_MODELS),
    # ---- gpt-image-2 系列（比例）----
    _gpt("gpt-image-2", sizes=GPT_RATIOS, quality=("auto",), quality_default="auto"),
    _gpt("gpt-image-2.5", sizes=GPT_RATIOS, quality=("auto",), quality_default="auto"),
    # ---- gpt-image-2 系列（像素尺寸）----
    _gpt("gpt-image-2-vip", sizes=PIXEL_SIZES, quality=("medium",), quality_default="medium"),
    _gpt("gpt-image-2.5-flare", sizes=PIXEL_SIZES, quality=("low", "medium", "high"), quality_default="medium"),
    _gpt(
        "gpt-image-2.5-sunburst",
        sizes=PIXEL_SIZES,
        quality=("low", "medium", "high", "xhigh", "max"),
        quality_default="high",
    ),
    # ---- 视频 minimax-h3 ----
    ModelSpec(
        id="minimax-h3",
        label="minimax-h3",
        kind="video",
        params=(
            ParamSpec(
                "aspectRatio",
                "画面方向",
                "select",
                (Option("portrait", "竖屏 portrait"), Option("landscape", "横屏 landscape")),
                "portrait",
                required=True,
            ),
            ParamSpec(
                "resolution",
                "分辨率",
                "select",
                _opts(("480p", "768p", "1080p")),
                "480p",
                required=True,
                help="1080p 最多 10 秒",
            ),
            ParamSpec("duration", "时长（秒）", "number", default=5, minimum=1, maximum=15, required=True),
            ParamSpec("seed", "随机种子", "number", default=None, minimum=0, help="可选，固定后更可复现"),
        ),
        supports_images=True,
        max_images=9,
        supports_audios=True,
        max_audios=3,
    ),
)

_BY_ID = {model.id: model for model in _MODELS}


def models_for(kind: str) -> list[ModelSpec]:
    return [model for model in _MODELS if model.kind == kind]


def get_model(model_id: str) -> ModelSpec | None:
    return _BY_ID.get(model_id)


def catalog_payload() -> dict[str, list[dict[str, Any]]]:
    return {
        "image": [model.to_dict() for model in models_for("image")],
        "video": [model.to_dict() for model in models_for("video")],
    }


def default_model(kind: str) -> str:
    models = models_for(kind)
    return models[0].id if models else ""
