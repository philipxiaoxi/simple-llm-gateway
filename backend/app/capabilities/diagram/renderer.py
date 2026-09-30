from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings

from .errors import DiagramError

# Archify 支持的图表类型与校验档位。
DIAGRAM_TYPES: tuple[str, ...] = ("architecture", "workflow", "sequence", "dataflow", "lifecycle")
QUALITIES: tuple[str, ...] = ("showcase", "standard")

_semaphore: asyncio.Semaphore | None = None


@dataclass
class RenderOutcome:
    html: bytes
    diagnostics: list[dict] = field(default_factory=list)


def archify_home() -> Path:
    return get_settings().resolved_archify_home


def cli_path() -> Path:
    return archify_home() / "bin" / "archify.mjs"


def _get_semaphore() -> asyncio.Semaphore:
    """并发闸门。延迟创建，避免在导入期绑定事件循环。"""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, int(get_settings().diagram_render_concurrency)))
    return _semaphore


def reset_semaphore_for_tests() -> None:
    global _semaphore
    _semaphore = None


def normalize_type(value: Any) -> str:
    diagram_type = str(value or "").strip().lower()
    if diagram_type not in DIAGRAM_TYPES:
        raise DiagramError(f"type 必须是 {', '.join(DIAGRAM_TYPES)} 之一")
    return diagram_type


def normalize_quality(value: Any) -> str:
    quality = str(value or "").strip().lower() or get_settings().diagram_default_quality
    if quality not in QUALITIES:
        raise DiagramError("quality 只能是 showcase 或 standard")
    return quality


def _parse_diagnostics(stdout: str) -> list[dict]:
    """从 deliver --json 的 stdout 中提取 diagnostics。"""
    text = (stdout or "").strip()
    if not text:
        return []
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        # --json 应输出单个 JSON 对象；退化为抓取最后一行 JSON。
        for line in reversed(text.splitlines()):
            try:
                body = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return []
    if not isinstance(body, dict):
        return []
    diagnostics = body.get("diagnostics")
    if not isinstance(diagnostics, list):
        return []
    return [item for item in diagnostics if isinstance(item, dict)]


def _run_cli(diagram_type: str, source_path: Path, out_path: Path, quality: str, sandbox: Path):
    settings = get_settings()
    cli = cli_path()
    if not cli.is_file():
        raise DiagramError("渲染器未安装", status_code=503, error_type="renderer_unavailable")

    env = os.environ.copy()
    # 关闭外联更新检查，避免每次渲染访问 GitHub Pages。
    env["ARCHIFY_UPDATE_CHECK_DISABLED"] = "1"
    env["HOME"] = str(sandbox)
    env["TMPDIR"] = str(sandbox)
    env["XDG_CACHE_HOME"] = str(sandbox / ".cache")

    cmd = [
        settings.diagram_node_binary,
        str(cli),
        "deliver",
        diagram_type,
        str(source_path),
        str(out_path),
        "--quality",
        quality,
        "--json",
    ]
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(sandbox),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise DiagramError(
            f"渲染器无法启动（缺少 {settings.diagram_node_binary}）",
            status_code=503,
            error_type="renderer_unavailable",
        ) from error

    try:
        stdout, stderr = process.communicate(timeout=max(1, int(settings.diagram_render_timeout_seconds)))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()
        raise DiagramError("渲染超时", status_code=504, error_type="diagram_render_timeout")
    return process.returncode, stdout or "", stderr or ""


def _render_sync(diagram_type: str, source_text: str, quality: str) -> RenderOutcome:
    sandbox = Path(tempfile.mkdtemp(prefix="archify-"))
    try:
        source_path = sandbox / "candidate.json"
        out_path = sandbox / "diagram.html"
        source_path.write_text(source_text, encoding="utf-8")
        returncode, stdout, stderr = _run_cli(diagram_type, source_path, out_path, quality, sandbox)
        if returncode != 0:
            diagnostics = _parse_diagnostics(stdout)
            message = "图表源未通过校验"
            if diagnostics:
                first = diagnostics[0]
                detail = first.get("message") if isinstance(first, dict) else None
                if detail:
                    message = str(detail)
            elif stderr.strip():
                message = stderr.strip().splitlines()[-1][:500]
            raise DiagramError(message, status_code=400, error_type="diagram_invalid", diagnostics=diagnostics)
        if not out_path.is_file():
            raise DiagramError("渲染未产出文件", status_code=500, error_type="internal_error")
        return RenderOutcome(html=out_path.read_bytes(), diagnostics=_parse_diagnostics(stdout))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


async def render_diagram(diagram_type: str, source: Any, quality: str) -> RenderOutcome:
    diagram_type = normalize_type(diagram_type)
    quality = normalize_quality(quality)
    if not isinstance(source, (dict, list)):
        raise DiagramError("source 必须是 JSON 对象")
    source_text = json.dumps(source, ensure_ascii=False)
    settings = get_settings()
    if len(source_text.encode("utf-8")) > max(1, int(settings.diagram_max_source_bytes)):
        raise DiagramError(f"source 超过 {settings.diagram_max_source_bytes} 字节上限")
    async with _get_semaphore():
        return await asyncio.to_thread(_render_sync, diagram_type, source_text, quality)
