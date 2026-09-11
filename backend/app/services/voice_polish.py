"""语音文本的 AI 纠错。

定位（重要，见 docs/voice-input/技术方案.md §4.4.3）：
ASR 自己只做「忠实转写 + 标点 + 数字规范化」，开启 disfluency_removal 后能删掉语气词，
但**修不了同音字错误**（实测「嗯」被听成「问」、「吧」被听成「把」）。
这一层只做纠错，不做文风润色，并且带「过度改写保护」，防止模型幻觉改坏用户内容。
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UpstreamAccount
from app.services.bridge import call_chat, prepare_credential
from app.services.key_models import is_account_available
from app.services.model_caps import first_model_id

MODE_OFF = "off"
MODE_ERROR_FIX = "error_fix"
MODE_REWRITE = "rewrite"
VALID_MODES = (MODE_OFF, MODE_ERROR_FIX, MODE_REWRITE)

# 过度改写阈值：超过就认为模型在"改写"而不是"纠错"，放弃应用（防幻觉）
MAX_LENGTH_DELTA_RATIO = 0.30
MAX_EDIT_DISTANCE_RATIO = 0.40
REWRITE_MODE_MAX_EDIT_DISTANCE_RATIO = 0.60

ERROR_FIX_PROMPT = """你是语音识别的纠错器，不是改写器。输入是一句语音识别结果。

只做下面五件事：
1. 改正同音字、近音字、明显错别字（例如「问」应为「嗯」、「把」应为「吧」、「在」与「再」混用）；
2. 结合上下文改正听错的专有名词（人名、产品名、专业术语）；
3. 修正标点与断句；
4. 删除孤立的口语语气词（嗯、啊、呃、那个、就是说）；
5. 合并语音识别抖动造成的相邻重复（「你好你好」→「你好」、「我们我们明天」→「我们明天」）。

最重要的约束（违反即失败）：
- 输出必须与输入几乎等长（允许因合并重复而略短），只允许改动少数几个字；
- 严禁删减、合并、概括、精简任何信息或句子成分；
- 严禁改写句式、调整语序、书面化、同义替换、调整语气；
- 不增加原文没有的信息，不解释，不加引号，不加 Markdown；
- 如果原文没有明显错误，原样输出。"""

REWRITE_PROMPT = """你是语音输入的文本优化器。输入是一句语音识别结果，可能有错别字、同音字错误、
口语赘词和缺失的标点。

要求：
1. 改正同音字、近音字、错别字，结合上下文改正听错的专有名词；
2. 把口语表达整理为通顺的书面语，补全标点；
3. 不改变原意，不增加原文没有的信息，**不删减任何有效信息**；
4. 保留专业术语、代码、URL、数字原样；
5. 直接输出结果，不要解释、不要加引号、不要 Markdown、不要换行结尾；
6. 如果原文已经很好，原样输出。"""

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class PolishResult:
    status: str  # ok / skipped / failed / no_change / over_edited
    text: str
    raw: str
    model: str | None = None
    account_id: int | None = None
    ms: int = 0
    error: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def default_prompt(mode: str) -> str:
    if mode == MODE_REWRITE:
        return REWRITE_PROMPT
    return ERROR_FIX_PROMPT


def clean_model_output(text: str) -> str:
    """模型经常多写解释或加引号，这里做保守清洗。"""
    value = (text or "").strip()
    if not value:
        return ""
    # 去掉整段被引号包裹的情况
    if len(value) >= 2 and value[0] in "「『\"'“" and value[-1] in "」』\"'”":
        value = value[1:-1].strip()
    # 去掉 markdown 代码块围栏
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    # 常见前缀：「润色后：」「结果：」
    value = re.sub(r"^(润色后|纠错后|优化后|结果|输出)\s*[:：]\s*", "", value)
    # 多行时只保留第一行有内容的（避免模型附送解释）
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines:
        value = lines[0] if len(lines) == 1 else "\n".join(lines[:1])
    return _WHITESPACE_RE.sub(" ", value).strip()


def edit_distance_ratio(a: str, b: str) -> float:
    """1 - 相似度。用 difflib 的 ratio（基于最长匹配），对中文表现稳定。"""
    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def should_apply(raw: str, polished: str, mode: str = MODE_ERROR_FIX) -> tuple[bool, str]:
    """决定纠错结果是否值得替换回原文。返回 (是否应用, 原因)。

    这是防幻觉的关键闸门：模型一旦"自作聪明"改写，宁可不改，也要保住用户原文。
    """
    if not polished:
        return False, "empty"
    if polished == raw:
        return False, "no_change"
    base = max(len(raw), 1)
    if abs(len(polished) - len(raw)) / base > MAX_LENGTH_DELTA_RATIO:
        return False, "too_long"
    limit = REWRITE_MODE_MAX_EDIT_DISTANCE_RATIO if mode == MODE_REWRITE else MAX_EDIT_DISTANCE_RATIO
    if edit_distance_ratio(raw, polished) > limit:
        return False, "too_different"
    return True, "ok"


def resolve_model(account: UpstreamAccount, model: str | None) -> str:
    configured = (model or "").strip()
    if configured:
        return configured
    detected = first_model_id(account.models_json)
    return detected or ""


async def polish_text(
    db: Session,
    *,
    account_id: int | None,
    model: str | None,
    text: str,
    context: list[str] | None = None,
    system_prompt: str | None = None,
    mode: str = MODE_ERROR_FIX,
    temperature: float = 0.2,
    timeout: float | None = None,
) -> PolishResult:
    """对一段 ASR 原文做纠错。任何异常都不抛出，返回 failed 让调用方保留原文。"""
    started = time.perf_counter()
    source = (text or "").strip()
    if not source:
        return PolishResult(status="skipped", text="", raw="", reason="empty_input")
    if mode == MODE_OFF:
        return PolishResult(status="skipped", text=source, raw=source, reason="mode_off")
    if not account_id:
        return PolishResult(status="skipped", text=source, raw=source, reason="no_account")

    account = db.get(UpstreamAccount, int(account_id))
    if account is None:
        return PolishResult(status="skipped", text=source, raw=source, reason="account_missing")
    if not is_account_available(account):
        return PolishResult(status="skipped", text=source, raw=source, reason="account_unavailable")

    resolved_model = resolve_model(account, model)
    if not resolved_model:
        return PolishResult(status="skipped", text=source, raw=source, reason="no_model")

    messages = [
        {"role": "system", "content": (system_prompt or "").strip() or default_prompt(mode)},
        {"role": "user", "content": build_user_prompt(source, context or [])},
    ]
    effective_timeout = timeout or get_settings().voice_polish_timeout_seconds

    try:
        credential = await prepare_credential(account, db)
        response = await asyncio.wait_for(
            call_chat(
                account,
                messages,
                resolved_model,
                False,
                {"max_tokens": get_settings().voice_polish_max_tokens, "temperature": temperature},
                credential,
            ),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        return PolishResult(
            status="failed",
            text=source,
            raw=source,
            model=resolved_model,
            account_id=account.id,
            ms=int((time.perf_counter() - started) * 1000),
            error=f"纠错超时（>{effective_timeout}s）",
        )
    except Exception as exc:
        return PolishResult(
            status="failed",
            text=source,
            raw=source,
            model=resolved_model,
            account_id=account.id,
            ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    content, finish_reason = _extract_content_with_reason(response)
    cleaned = clean_model_output(content)
    if not cleaned and finish_reason == "length":
        # 推理模型的思考过程吃光了 token 预算，属于配置问题，要让用户看得见
        return PolishResult(
            status="failed",
            text=source,
            raw=source,
            model=resolved_model,
            account_id=account.id,
            ms=elapsed,
            error=(
                f"模型输出为空：token 预算被推理过程耗尽（max_tokens="
                f"{get_settings().voice_polish_max_tokens}）。请换一个非推理模型，或调大 VOICE_POLISH_MAX_TOKENS。"
            ),
        )
    apply, reason = should_apply(source, cleaned, mode)
    if not apply:
        return PolishResult(
            status=reason,
            text=source,
            raw=source,
            model=resolved_model,
            account_id=account.id,
            ms=elapsed,
        )
    return PolishResult(
        status="ok",
        text=cleaned,
        raw=source,
        model=resolved_model,
        account_id=account.id,
        ms=elapsed,
    )


def build_user_prompt(text: str, context: list[str]) -> str:
    parts: list[str] = []
    if context:
        joined = "\n".join(f"- {item}" for item in context[-2:] if item)
        if joined:
            parts.append(f"上文（仅用于判断用词，不要重复输出）：\n{joined}")
    parts.append(f"需要处理的句子：\n{text}")
    return "\n\n".join(parts)


def _extract_content_with_reason(response: Any) -> tuple[str, str | None]:
    """兼容 litellm 对象与 dict 两种返回形态，同时取出 finish_reason。"""
    try:
        choices = getattr(response, "choices", None)
        if choices:
            return (choices[0].message.content or ""), getattr(choices[0], "finish_reason", None)
    except Exception:
        pass
    if isinstance(response, dict):
        try:
            choice = response["choices"][0]
            return (choice["message"]["content"] or ""), choice.get("finish_reason")
        except Exception:
            return "", None
    return "", None


def _extract_content(response: Any) -> str:
    return _extract_content_with_reason(response)[0]
