from __future__ import annotations

from pathlib import Path

# 已知文本类扩展名：命中即视为文本
TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".text", ".md", ".markdown", ".mdx", ".rst", ".org", ".adoc", ".log",
        ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".conf", ".properties", ".env", ".xml", ".html", ".htm", ".xhtml",
        ".css", ".scss", ".less", ".svg", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
        ".vue", ".svelte", ".py", ".java", ".kt", ".kts", ".go", ".rs", ".c", ".h",
        ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".scala",
        ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1", ".sql", ".graphql",
        ".gql", ".proto", ".tex", ".bib", ".srt", ".vtt",
    }
)

# 办公文档：不按纯文本解码，交给 DocParse 转成 Markdown 后再入库
OFFICE_EXTENSIONS = frozenset(
    {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".html", ".htm",
    }
)

# 已知二进制扩展名：命中直接跳过，避免把二进制解成乱码入库
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".tif",
        ".zip", ".gz", ".tgz", ".tar", ".rar", ".7z", ".bz2", ".xz",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".jar", ".war",
        ".pyc", ".whl", ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".mp4", ".mov",
        ".avi", ".mkv", ".webm", ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".db", ".sqlite",
        ".parquet", ".npy", ".npz", ".pkl", ".pth", ".onnx",
    }
)

_DECODE_ENCODINGS = ("utf-8-sig", "gb18030", "utf-16")
_SNIFF_BYTES = 8192
_PRINTABLE_THRESHOLD = 0.9


def decode_text(raw: bytes) -> str | None:
    """按常见编码把字节解码为文本；无法解码时返回 None。"""
    if not raw:
        return None
    if b"\x00" in raw[:_SNIFF_BYTES] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    for encoding in _DECODE_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


def printable_ratio(text: str, sample: int = 4096) -> float:
    chunk = text[:sample]
    if not chunk:
        return 0.0
    printable = sum(1 for ch in chunk if ch.isprintable() or ch in "\n\r\t")
    return printable / len(chunk)


def is_office_file(name: str) -> bool:
    return Path(name or "").suffix.lower() in OFFICE_EXTENSIONS


def is_text_file(name: str, raw: bytes) -> bool:
    """判断是否为可入库的文本文件。

    已知文本扩展名 → 直接接受；已知二进制扩展名 → 直接拒绝；
    其余按可解码程度与可打印字符占比判定，覆盖无扩展名的文本（如 Makefile、LICENSE）。
    """
    suffix = Path(name or "").suffix.lower()
    if suffix in OFFICE_EXTENSIONS or suffix in BINARY_EXTENSIONS:
        return False
    text = decode_text(raw)
    if text is None:
        return False
    if suffix in TEXT_EXTENSIONS:
        return True
    return printable_ratio(text) >= _PRINTABLE_THRESHOLD


def clean_source_name(name: str, fallback: str = "upload.txt") -> str:
    """规范化来源名：统一分隔符、去掉空段与上级目录，限制长度。"""
    value = (name or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in value.split("/") if part not in ("", ".", "..")]
    joined = "/".join(parts)
    return joined[:256] or fallback
