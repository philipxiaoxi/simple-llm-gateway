from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.services.knowledge.text_files import OFFICE_EXTENSIONS, clean_source_name

from .errors import DocParseError

LEGACY_EXTENSIONS = frozenset({".doc", ".xls", ".ppt"})
OOXML_FROM_LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}
MARKITDOWN_EXTENSIONS = frozenset({".docx", ".xlsx", ".pptx", ".html", ".htm", ".pdf"})
RESULT_CHAR_CAP = 1_000_000


@dataclass
class ConvertResult:
    markdown: str
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


def supported_extensions() -> frozenset[str]:
    return OFFICE_EXTENSIONS


def extension_of(name: str) -> str:
    return Path(name or "").suffix.lower()


def markdown_source_name(name: str) -> str:
    cleaned = clean_source_name(name, fallback="document.md")
    stem = Path(cleaned).stem or "document"
    parent = Path(cleaned).parent
    renamed = f"{stem}.md"
    if str(parent) in ("", "."):
        return renamed[:256]
    return str(parent / renamed)[:256]


def assert_supported(name: str, raw: bytes) -> str:
    settings = get_settings()
    ext = extension_of(name)
    allowed = ", ".join(sorted(supported_extensions()))
    if ext not in supported_extensions():
        raise DocParseError(f"不支持的文件类型，允许: {allowed}")
    if not raw:
        raise DocParseError("空文件")
    if len(raw) > settings.doc_parse_max_bytes:
        raise DocParseError(f"文件超过上限 {settings.doc_parse_max_bytes} 字节")
    if ext in LEGACY_EXTENSIONS and shutil.which("libreoffice") is None and shutil.which("soffice") is None:
        raise DocParseError(
            "当前环境未安装 LibreOffice，无法转换 .doc/.xls/.ppt",
            error_type="converter_unavailable",
        )
    return ext


def convert_bytes(name: str, raw: bytes) -> ConvertResult:
    ext = assert_supported(name, raw)
    with tempfile.TemporaryDirectory(prefix="docparse-") as temp_dir:
        source = Path(temp_dir) / f"source{ext}"
        source.write_bytes(raw)
        if ext in LEGACY_EXTENSIONS:
            source = _convert_legacy(source, Path(temp_dir))
            ext = source.suffix.lower()
        return _convert_path(source, ext)


def _convert_legacy(source: Path, workdir: Path) -> Path:
    binary = shutil.which("libreoffice") or shutil.which("soffice")
    if binary is None:
        raise DocParseError("当前环境未安装 LibreOffice，无法转换 .doc/.xls/.ppt", error_type="converter_unavailable")
    target_ext = OOXML_FROM_LEGACY[source.suffix.lower()]
    timeout = max(30, int(get_settings().doc_parse_timeout_seconds))
    try:
        completed = subprocess.run(
            [binary, "--headless", "--convert-to", target_ext.lstrip("."), "--outdir", str(workdir), str(source)],
            check=False,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired as error:
        raise DocParseError(f"旧版 Office 转换超时（{timeout} 秒）") from error
    produced = workdir / f"{source.stem}{target_ext}"
    if completed.returncode != 0 or not produced.is_file():
        detail = (completed.stderr or completed.stdout or "转换失败").strip()[:300]
        raise DocParseError(f"旧版 Office 转换失败: {detail}")
    return produced


def _convert_path(source: Path, ext: str) -> ConvertResult:
    if ext not in MARKITDOWN_EXTENSIONS:
        raise DocParseError(f"不支持的文件类型: {ext}")
    try:
        from markitdown import MarkItDown
    except ImportError as error:
        raise DocParseError("未安装 markitdown，无法转换文档", error_type="converter_unavailable") from error

    warnings: list[str] = []
    page_count = 0
    if ext == ".pdf":
        page_count, warnings = _inspect_pdf(source)
    try:
        result = MarkItDown(enable_plugins=False).convert(str(source))
    except Exception as error:
        message = str(error) or error.__class__.__name__
        if "password" in message.lower() or "encrypt" in message.lower():
            raise DocParseError("PDF 已加密，需要密码后才能转换") from error
        raise DocParseError(f"转换失败: {message[:300]}") from error
    markdown = (getattr(result, "text_content", None) or getattr(result, "markdown", None) or "").strip()
    if ext == ".pdf" and page_count:
        markdown = _annotate_pdf_pages(markdown, page_count, warnings)
    if not markdown and not warnings:
        raise DocParseError("转换结果为空")
    if not markdown:
        markdown = "\n".join(f"[扫描页 {index + 1}，未提取到文本]" for index in range(page_count or 1))
    return ConvertResult(markdown=markdown, page_count=page_count, warnings=warnings)


def _inspect_pdf(source: Path) -> tuple[int, list[str]]:
    settings = get_settings()
    try:
        from pypdf import PdfReader
    except ImportError:
        return 0, []
    try:
        reader = PdfReader(str(source))
    except Exception as error:
        message = str(error)
        if "password" in message.lower() or "encrypt" in message.lower():
            raise DocParseError("PDF 已加密，需要密码后才能转换") from error
        raise DocParseError(f"PDF 无法读取: {message[:300]}") from error
    if reader.is_encrypted:
        raise DocParseError("PDF 已加密，需要密码后才能转换")
    page_count = len(reader.pages)
    if page_count > settings.doc_parse_max_pdf_pages:
        raise DocParseError(f"PDF 超过 {settings.doc_parse_max_pdf_pages} 页")
    warnings: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = ""
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            warnings.append(f"第 {index} 页没有可提取文本层")
    return page_count, warnings


def _annotate_pdf_pages(markdown: str, page_count: int, warnings: list[str]) -> str:
    body = markdown.strip()
    if page_count <= 1:
        return body
    if "<!-- page:" in body:
        return body
    header = f"<!-- page: 1 -->\n{body}" if body else ""
    missing = [item for item in warnings if "没有可提取文本层" in item]
    if missing and page_count > 1 and body:
        header += "\n\n" + "\n".join(f"[扫描页占位] {item}" for item in missing)
    return header
