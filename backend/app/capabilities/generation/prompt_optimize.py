"""视频提示词优化。

技能文本随版本发布。请求路径只调用已配置的上游账号，不访问技能仓库。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import GenerationSettings, UpstreamAccount
from app.services.bridge import call_chat, prepare_credential
from app.services.key_models import is_account_available
from app.services.model_caps import first_model_id
from app.services.voice_polish import resolve_model

MAX_PROMPT_CHARS = 12000
TIMEOUT_SECONDS = 60
MODES = ("t2va", "i2va", "fl2va", "l2va")
CORE_FIELDS = (
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
)
_SKILL_DIR = Path(__file__).resolve().parent / "skills" / "h3-prompt-writing"
_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?$")


class PromptOptimizeError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(slots=True)
class OptimizeResult:
    prompt: str
    mode: str
    model: str


def select_mode(image_count: int, explicit: str | None = None) -> str:
    chosen = (explicit or "").strip().lower()
    if chosen:
        if chosen not in MODES:
            raise PromptOptimizeError("不支持的优化模式")
        return chosen
    count = max(0, int(image_count))
    if count <= 0:
        return "t2va"
    if count == 1:
        return "i2va"
    return "fl2va"


def load_skill_text() -> str:
    skill = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    guide = (_SKILL_DIR / "references" / "base-en.txt").read_text(encoding="utf-8")
    return f"{skill.strip()}\n\n{guide.strip()}"


def format_duration(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return "6.00"
    if not _DURATION_RE.match(text):
        raise PromptOptimizeError("时长格式不正确")
    seconds = float(text)
    if seconds <= 0 or seconds > 60:
        raise PromptOptimizeError("时长必须在 0 到 60 秒之间")
    return f"{seconds:.2f}"


def alignment_prefix(mode: str, duration: str) -> str:
    if mode == "i2va":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if mode == "fl2va":
        return (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {duration}-second mark of the target video."
        )
    if mode == "l2va":
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot 1]) aligns with the {duration}-second mark of the target video."
        )
    return ""


def validate_prompt(text: str, mode: str, duration: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if not cleaned:
        raise PromptOptimizeError("模型没有返回可用提示词", 502)
    if len(cleaned) > MAX_PROMPT_CHARS:
        raise PromptOptimizeError("优化结果超过长度限制", 502)
    expected = alignment_prefix(mode, duration)
    if expected and not cleaned.startswith(expected):
        raise PromptOptimizeError("优化结果缺少参考图对齐指令", 502)
    positions: list[int] = []
    for field in CORE_FIELDS:
        index = cleaned.find(f"{field}:")
        if index < 0:
            raise PromptOptimizeError(f"优化结果缺少 {field}", 502)
        positions.append(index)
    if positions != sorted(positions):
        raise PromptOptimizeError("优化结果字段顺序不正确", 502)
    if expected and positions[0] < len(expected):
        raise PromptOptimizeError("优化结果字段顺序不正确", 502)
    return cleaned


def build_messages(prompt: str, mode: str, duration: str, image_count: int) -> list[dict[str, str]]:
    system = (
        f"{load_skill_text()}\n\n"
        "Rewrite the user intent into one English video prompt. "
        "Output only the final prompt. Do not explain, quote, or add Markdown."
    )
    user = (
        f"Mode: {mode}\n"
        f"Duration seconds: {duration}\n"
        f"Reference image count: {image_count}\n"
        f"Required opening:\n{alignment_prefix(mode, duration) or '(none)'}\n\n"
        f"User intent:\n{prompt.strip()}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_content(response: Any) -> str:
    try:
        choices = getattr(response, "choices", None)
        if choices:
            return choices[0].message.content or ""
    except Exception:
        pass
    if isinstance(response, dict):
        try:
            return response["choices"][0]["message"]["content"] or ""
        except Exception:
            return ""
    return ""


async def optimize_prompt(
    db: Session,
    settings: GenerationSettings,
    *,
    prompt: str,
    duration_seconds: Any,
    image_count: int,
    mode: str | None = None,
) -> OptimizeResult:
    source = (prompt or "").strip()
    if not source:
        raise PromptOptimizeError("提示词不能为空")
    if not settings.prompt_optimize_enabled:
        raise PromptOptimizeError("提示词优化未启用", 409)
    account_id = settings.prompt_optimize_account_id
    if not account_id:
        raise PromptOptimizeError("未配置优化账号", 409)
    account = db.get(UpstreamAccount, int(account_id))
    if account is None or not is_account_available(account):
        raise PromptOptimizeError("优化账号不可用", 409)
    resolved_model = resolve_model(account, settings.prompt_optimize_model)
    if not resolved_model:
        raise PromptOptimizeError("未配置优化模型", 409)

    selected = select_mode(image_count, mode)
    duration = format_duration(duration_seconds)
    try:
        credential = await prepare_credential(account, db)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise PromptOptimizeError("优化账号鉴权失败", 502) from exc

    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            call_chat(
                account,
                build_messages(source, selected, duration, max(0, int(image_count))),
                resolved_model,
                False,
                {"max_tokens": 4096, "temperature": 0.4},
                credential,
            ),
            timeout=TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise PromptOptimizeError(f"优化超时（>{TIMEOUT_SECONDS}s）", 502) from exc
    except Exception as exc:
        raise PromptOptimizeError("上游优化失败", 502) from exc
    finally:
        _ = int((time.perf_counter() - started) * 1000)

    cleaned = validate_prompt(_extract_content(response), selected, duration)
    return OptimizeResult(prompt=cleaned, mode=selected, model=resolved_model)


def account_model_allowed(db: Session, account_id: int, model: str) -> bool:
    account = db.get(UpstreamAccount, int(account_id))
    if account is None or not is_account_available(account):
        return False
    chosen = model.strip()
    if not chosen:
        return bool(first_model_id(account.models_json))
    return True
