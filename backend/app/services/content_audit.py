from __future__ import annotations

import io
import json
import re
import threading
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ahocorasick
import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.db import get_session_factory
from app.models import ContentAuditFinding, ContentAuditScan, RequestLog, RequestLogMessage
from app.services.conversation import decode_stored_message, extract_request_messages

BATCH_SIZE = 200
EXCERPT_LIMIT = 120
REQUEST_BODY_SEQ = -1
CATEGORY_SENSITIVE = "sensitive"
CATEGORY_PII = "pii"
CATEGORY_SECRET = "secret"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
HIGH_LEXICON_MARKERS = ("暴恐", "违禁")
LEXICON_SKIP_NAMES = {"SOURCE.TXT", "README.TXT", "LICENSE.TXT"}
LEXICON_ZIP_URLS = (
    "https://codeload.github.com/konsheng/Sensitive-lexicon/zip/refs/heads/master",
    "https://codeload.github.com/konsheng/Sensitive-lexicon/zip/refs/heads/main",
)
LEXICON_DOWNLOAD_TIMEOUT = 60.0
LEXICON_STAMP_NAME = ".updated_at"
LEXICON_META_NAME = ".meta.json"

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
EMAIL_RE = re.compile(r"(?i)(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z0-9._%+\-])")
BANK_CARD_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
SK_ANT_RE = re.compile(r"sk-ant-[A-Za-z0-9\-_]{16,}")
SK_RE = re.compile(r"sk-(?!ant-)[A-Za-z0-9\-_]{20,}")
BEARER_RE = re.compile(r"(?i)(?<![A-Za-z])Bearer\s+[A-Za-z0-9\-._=+/]{20,}")
PEM_RE = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
GHP_RE = re.compile(r"ghp_[A-Za-z0-9]{20,}")
AKIA_RE = re.compile(r"AKIA[0-9A-Z]{16}")

ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CARD_CHECK = "10X98765432"


@dataclass(frozen=True)
class Hit:
    category: str
    lexicon_category: str | None
    rule_key: str
    severity: str
    start: int
    end: int


@dataclass
class LexiconState:
    ok: bool
    automaton: Any | None
    error_message: str | None
    word_count: int
    categories: list[str]


_LEXICON: LexiconState | None = None
_LEXICON_LOCK = threading.Lock()


def reset_content_audit_cache() -> None:
    global _LEXICON
    _LEXICON = None


def lexicon_dir() -> Path:
    settings = get_settings()
    if settings.database_path == ":memory:":
        return Path("data") / "sensitive-lexicon"
    return Path(settings.database_path).expanduser().resolve().parent / "sensitive-lexicon"


def lexicon_severity(category: str) -> str:
    if any(marker in (category or "") for marker in HIGH_LEXICON_MARKERS):
        return SEVERITY_HIGH
    return SEVERITY_MEDIUM


def load_lexicon(directory: Path | None = None, *, force: bool = False) -> LexiconState:
    global _LEXICON
    if directory is not None:
        return _compile_lexicon(directory)
    if _LEXICON is not None and not force:
        return _LEXICON
    with _LEXICON_LOCK:
        if _LEXICON is not None and not force:
            return _LEXICON
        path = lexicon_dir()
        download_error = _ensure_cached_lexicon(path)
        state = _compile_lexicon(path)
        if download_error and not state.ok:
            state = LexiconState(False, None, download_error, 0, state.categories)
        _write_lexicon_meta(path, state)
        _LEXICON = state
        return state


def _lexicon_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    files: list[Path] = []
    for file_path in sorted(path.glob("*.txt")):
        if file_path.name.upper() in LEXICON_SKIP_NAMES:
            continue
        files.append(file_path)
    return files


def _lexicon_ready(path: Path) -> bool:
    return bool(_lexicon_files(path))


def lexicon_updated_at(directory: Path | None = None) -> datetime | None:
    path = directory or lexicon_dir()
    stamp = path / LEXICON_STAMP_NAME
    if stamp.exists():
        try:
            raw = stamp.read_text(encoding="utf-8").strip()
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except (OSError, ValueError):
            pass
    files = _lexicon_files(path)
    if not files:
        return None
    latest = max(file_path.stat().st_mtime for file_path in files)
    return datetime.fromtimestamp(latest, tz=UTC).replace(tzinfo=None)


def _write_lexicon_stamp(path: Path, when: datetime | None = None) -> None:
    stamp = when or utcnow()
    path.mkdir(parents=True, exist_ok=True)
    (path / LEXICON_STAMP_NAME).write_text(stamp.isoformat(), encoding="utf-8")


def _write_lexicon_meta(path: Path, state: LexiconState) -> None:
    data = {"word_count": state.word_count, "categories": list(state.categories)}
    path.mkdir(parents=True, exist_ok=True)
    (path / LEXICON_META_NAME).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def lexicon_info(directory: Path | None = None) -> dict[str, Any]:
    path = directory or lexicon_dir()
    if _LEXICON is not None:
        return {
            "ok": _LEXICON.ok,
            "word_count": _LEXICON.word_count,
            "categories": list(_LEXICON.categories),
        }
    meta = path / LEXICON_META_NAME
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return {
                "ok": True,
                "word_count": int(data.get("word_count") or 0),
                "categories": list(data.get("categories") or []),
            }
        except (OSError, ValueError):
            pass
    return {"ok": False, "word_count": 0, "categories": list_lexicon_categories(path)}


def _ensure_cached_lexicon(path: Path) -> str | None:
    if _lexicon_ready(path):
        return None
    try:
        _download_lexicon(path)
    except Exception as error:
        return f"词库未能加载：{error}"
    return None


def _download_lexicon(path: Path) -> None:
    last_error: Exception | None = None
    payload: bytes | None = None
    headers = {"User-Agent": "pivot-desk/content-audit"}
    with httpx.Client(timeout=LEXICON_DOWNLOAD_TIMEOUT, follow_redirects=True, headers=headers) as client:
        for url in LEXICON_ZIP_URLS:
            try:
                response = client.get(url)
                response.raise_for_status()
                payload = response.content
                break
            except httpx.HTTPError as error:
                last_error = error
    if payload is None:
        raise RuntimeError(last_error or "下载失败")
    extracted: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if not name.endswith(".txt") or name.upper() in LEXICON_SKIP_NAMES:
                continue
            extracted[name] = archive.read(info)
    if not extracted:
        raise RuntimeError("上游压缩包没有分类文件")
    path.mkdir(parents=True, exist_ok=True)
    for name, data in extracted.items():
        (path / name).write_bytes(data)
    keep = set(extracted)
    for file_path in path.glob("*.txt"):
        if file_path.name not in keep:
            file_path.unlink()
    _write_lexicon_stamp(path)


def sync_lexicon() -> dict[str, Any]:
    global _LEXICON
    with _LEXICON_LOCK:
        path = lexicon_dir()
        try:
            _download_lexicon(path)
        except Exception as error:
            raise RuntimeError(f"词库同步失败：{error}") from error
        state = _compile_lexicon(path)
        _write_lexicon_meta(path, state)
        _LEXICON = state
        if not state.ok:
            raise RuntimeError(state.error_message or "词库未能加载")
        return {
            "ok": True,
            "updated_at": lexicon_updated_at(path),
            "word_count": state.word_count,
            "categories": state.categories,
            "error_message": None,
        }


def _compile_lexicon(path: Path) -> LexiconState:
    files = _lexicon_files(path)
    if not files:
        return LexiconState(False, None, "词库未能加载：目录不存在", 0, [])
    words: dict[str, str] = {}
    categories: list[str] = []
    for file_path in files:
        category = file_path.stem[:64]
        try:
            text = file_path.read_text(encoding="utf-8-sig")
        except OSError as error:
            return LexiconState(False, None, f"词库未能加载：{file_path.name} {error}", 0, [])
        categories.append(category)
        for raw_line in text.splitlines():
            word = raw_line.strip()
            if len(word) < 2 or word in words:
                continue
            words[word] = category
    if not words:
        return LexiconState(False, None, "词库未能加载：词表为空", 0, categories)
    automaton = ahocorasick.Automaton()
    for word, category in words.items():
        automaton.add_word(word, (word, category))
    automaton.make_automaton()
    return LexiconState(True, automaton, None, len(words), categories)


def extract_scan_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text") or ""))
            elif item is not None:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
    elif content is not None:
        parts.append(json.dumps(content, ensure_ascii=False, default=str))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
    return "\n".join(part for part in parts if part)


def extract_request_body_text(request_body: str | None) -> str:
    if not request_body:
        return ""
    try:
        parsed = json.loads(request_body)
    except json.JSONDecodeError:
        return request_body
    if isinstance(parsed, dict):
        messages = extract_request_messages(parsed)
        chunks = [extract_scan_text(item) for item in messages]
        extra = parsed.get("input")
        if isinstance(extra, str) and extra and extra not in chunks:
            chunks.append(extra)
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(parsed, str):
        return parsed
    return json.dumps(parsed, ensure_ascii=False, default=str)


def make_excerpt(text: str, start: int, end: int, limit: int = EXCERPT_LIMIT) -> str:
    if start < 0:
        start = 0
    if end > len(text):
        end = len(text)
    if end < start:
        end = start
    hit_len = end - start
    if hit_len >= limit:
        return text[start : start + limit]
    extra = limit - hit_len
    left = extra // 2
    right = extra - left
    window_start = max(0, start - left)
    window_end = min(len(text), end + right)
    unused_left = left - (start - window_start)
    unused_right = right - (window_end - end)
    if unused_left:
        window_end = min(len(text), window_end + unused_left)
    if unused_right:
        window_start = max(0, window_start - unused_right)
    return text[window_start:window_end]


def id_card_checksum_ok(value: str) -> bool:
    if len(value) != 18:
        return False
    total = 0
    for index, weight in enumerate(ID_CARD_WEIGHTS):
        digit = value[index]
        if not digit.isdigit():
            return False
        total += int(digit) * weight
    return value[17].upper() == ID_CARD_CHECK[total % 11]


def luhn_ok(value: str) -> bool:
    if not value.isdigit():
        return False
    total = 0
    reverse = value[::-1]
    for index, char in enumerate(reverse):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detect_sensitive(text: str, lexicon: LexiconState | None = None) -> list[Hit]:
    state = lexicon if lexicon is not None else load_lexicon()
    if not state.ok or state.automaton is None or not text:
        return []
    hits: list[Hit] = []
    for end_index, payload in state.automaton.iter(text):
        word, category = payload
        start = end_index - len(word) + 1
        hits.append(
            Hit(
                category=CATEGORY_SENSITIVE,
                lexicon_category=category,
                rule_key=word[:128],
                severity=lexicon_severity(category),
                start=start,
                end=end_index + 1,
            )
        )
    return hits


def detect_pii(text: str) -> list[Hit]:
    if not text:
        return []
    hits: list[Hit] = []
    spanned: list[tuple[int, int]] = []

    def add(hit: Hit) -> None:
        hits.append(hit)
        spanned.append((hit.start, hit.end))

    for match in PHONE_RE.finditer(text):
        add(Hit(CATEGORY_PII, None, "phone", SEVERITY_MEDIUM, match.start(), match.end()))
    for match in ID_CARD_RE.finditer(text):
        if id_card_checksum_ok(match.group(0)):
            add(Hit(CATEGORY_PII, None, "id_card", SEVERITY_HIGH, match.start(), match.end()))
    for match in EMAIL_RE.finditer(text):
        add(Hit(CATEGORY_PII, None, "email", SEVERITY_MEDIUM, match.start(), match.end()))
    for match in BANK_CARD_RE.finditer(text):
        value = match.group(0)
        if not luhn_ok(value):
            continue
        if any(start < match.end() and end > match.start() for start, end in spanned):
            continue
        add(Hit(CATEGORY_PII, None, "bank_card", SEVERITY_HIGH, match.start(), match.end()))
    return hits


def detect_secrets(text: str) -> list[Hit]:
    if not text:
        return []
    hits: list[Hit] = []
    patterns = (
        (SK_ANT_RE, "sk-ant"),
        (SK_RE, "sk"),
        (BEARER_RE, "bearer"),
        (PEM_RE, "pem"),
        (GHP_RE, "ghp"),
        (AKIA_RE, "akia"),
    )
    for pattern, rule_key in patterns:
        for match in pattern.finditer(text):
            hits.append(
                Hit(CATEGORY_SECRET, None, rule_key, SEVERITY_HIGH, match.start(), match.end())
            )
    return hits


def detect_all(text: str, lexicon: LexiconState | None = None) -> list[Hit]:
    return detect_sensitive(text, lexicon) + detect_pii(text) + detect_secrets(text)


def finding_key(log_id: int, message_seq: int, hit: Hit) -> tuple[int, int, str, str, int]:
    return (log_id, message_seq, hit.category, hit.rule_key, hit.start)


def mask_value(category: str, rule_key: str, value: str) -> str:
    if not value:
        return value
    if category == CATEGORY_PII and rule_key == "phone" and len(value) >= 7:
        return f"{value[:3]}****{value[-4:]}"
    if category == CATEGORY_PII and rule_key in {"id_card", "bank_card"}:
        if len(value) >= 8:
            return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
        return "*" * len(value)
    if category == CATEGORY_PII and rule_key == "email" and "@" in value:
        local, domain = value.split("@", 1)
        keep = local[:1] if local else ""
        return f"{keep}{'*' * max(0, len(local) - 1)}@{domain}"
    if category == CATEGORY_SECRET:
        if len(value) <= 8:
            return f"{value[:2]}{'*' * max(0, len(value) - 2)}"
        return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
    return value


def mask_excerpt_for_list(excerpt: str, category: str, rule_key: str) -> str:
    if category not in {CATEGORY_PII, CATEGORY_SECRET}:
        return excerpt
    patterns = {
        ("pii", "phone"): PHONE_RE,
        ("pii", "id_card"): ID_CARD_RE,
        ("pii", "email"): EMAIL_RE,
        ("pii", "bank_card"): BANK_CARD_RE,
        ("secret", "sk-ant"): SK_ANT_RE,
        ("secret", "sk"): SK_RE,
        ("secret", "bearer"): BEARER_RE,
        ("secret", "pem"): PEM_RE,
        ("secret", "ghp"): GHP_RE,
        ("secret", "akia"): AKIA_RE,
    }
    pattern = patterns.get((category, rule_key))
    if pattern is None:
        return excerpt
    return pattern.sub(lambda match: mask_value(category, rule_key, match.group(0)), excerpt)


def scan_text(text: str, lexicon: LexiconState | None = None) -> list[Hit]:
    return detect_all(text, lexicon)


def _due_logs_query():
    max_seq = (
        select(RequestLogMessage.log_id, func.max(RequestLogMessage.seq).label("max_seq"))
        .group_by(RequestLogMessage.log_id)
        .subquery()
    )
    log_time = func.coalesce(RequestLog.updated_at, RequestLog.created_at)
    return (
        select(RequestLog)
        .outerjoin(ContentAuditScan, ContentAuditScan.log_id == RequestLog.id)
        .outerjoin(max_seq, max_seq.c.log_id == RequestLog.id)
        .where(
            or_(
                ContentAuditScan.log_id.is_(None),
                ContentAuditScan.last_scanned_at < log_time,
                ContentAuditScan.last_message_seq < func.coalesce(max_seq.c.max_seq, -1),
            )
        )
        .order_by(RequestLog.id.asc())
    ), max_seq


def list_lexicon_categories(directory: Path | None = None) -> list[str]:
    path = directory or lexicon_dir()
    return [file_path.stem[:64] for file_path in _lexicon_files(path)]


def count_remaining(db: Session) -> int:
    query, _ = _due_logs_query()
    counted = query.order_by(None)
    return db.scalar(select(func.count()).select_from(counted.subquery())) or 0


def _existing_keys(db: Session, log_id: int) -> set[tuple[int, int, str, str, int]]:
    rows = db.execute(
        select(
            ContentAuditFinding.log_id,
            ContentAuditFinding.message_seq,
            ContentAuditFinding.category,
            ContentAuditFinding.rule_key,
            ContentAuditFinding.start_offset,
        ).where(ContentAuditFinding.log_id == log_id)
    ).all()
    return {(row[0], row[1], row[2], row[3], row[4]) for row in rows}


def _write_hits(
    db: Session,
    log: RequestLog,
    message_seq: int,
    text: str,
    hits: Iterable[Hit],
    existing: set[tuple[int, int, str, str, int]],
) -> int:
    added = 0
    for hit in hits:
        key = finding_key(log.id, message_seq, hit)
        if key in existing:
            continue
        finding = ContentAuditFinding(
            log_id=log.id,
            message_seq=message_seq,
            category=hit.category,
            lexicon_category=hit.lexicon_category,
            rule_key=hit.rule_key,
            severity=hit.severity,
            excerpt=make_excerpt(text, hit.start, hit.end),
            start_offset=hit.start,
            end_offset=hit.end,
            api_key_id=log.api_key_id,
            api_key_name=log.api_key_name,
            account_name=log.account_name,
        )
        try:
            with db.begin_nested():
                db.add(finding)
                db.flush()
        except IntegrityError:
            continue
        existing.add(key)
        added += 1
    return added


def _scan_one_log(
    db: Session,
    log: RequestLog,
    lexicon: LexiconState,
    scan: ContentAuditScan | None,
) -> tuple[int, str, str | None, int]:
    last_seq = scan.last_message_seq if scan else -1
    added = 0
    error: str | None = None
    messages = db.scalars(
        select(RequestLogMessage)
        .where(RequestLogMessage.log_id == log.id)
        .order_by(RequestLogMessage.seq)
    ).all()
    max_seq = last_seq
    existing = _existing_keys(db, log.id)
    for row in messages:
        max_seq = max(max_seq, row.seq)
        if scan is not None and row.seq <= last_seq:
            continue
        try:
            decoded = decode_stored_message(row)
            text = extract_scan_text(decoded)
        except Exception as exc:
            error = f"消息 seq={row.seq} 解析失败：{exc}"
            continue
        added += _write_hits(db, log, row.seq, text, detect_all(text, lexicon), existing)
    should_scan_body = scan is None or (log.updated_at or log.created_at) > scan.last_scanned_at
    if should_scan_body and log.request_body:
        try:
            text = extract_request_body_text(log.request_body)
        except Exception as exc:
            error = error or f"请求正文解析失败：{exc}"
            text = ""
        if text:
            added += _write_hits(
                db, log, REQUEST_BODY_SEQ, text, detect_all(text, lexicon), existing
            )
    status = "error" if error else "ok"
    return added, status, error, max_seq


def run_scan_batch_sync(batch_size: int = BATCH_SIZE, lexicon_directory: Path | None = None) -> dict[str, Any]:
    lexicon = load_lexicon(lexicon_directory, force=lexicon_directory is not None)
    session = get_session_factory()()
    processed = 0
    new_findings = 0
    try:
        query, _ = _due_logs_query()
        logs = session.scalars(query.limit(max(1, batch_size))).unique().all()
        for log in logs:
            scan = session.get(ContentAuditScan, log.id)
            try:
                added, status, error, max_seq = _scan_one_log(session, log, lexicon, scan)
            except Exception as exc:
                if scan is None:
                    scan = ContentAuditScan(
                        log_id=log.id,
                        last_scanned_at=utcnow(),
                        last_message_seq=-1,
                        finding_count=0,
                        status="error",
                        error_message=str(exc),
                    )
                    session.add(scan)
                else:
                    scan.status = "error"
                    scan.error_message = str(exc)
                    scan.last_scanned_at = utcnow()
                session.commit()
                processed += 1
                continue
            now = utcnow()
            finding_count = session.scalar(
                select(func.count()).where(ContentAuditFinding.log_id == log.id)
            ) or 0
            if scan is None:
                session.add(
                    ContentAuditScan(
                        log_id=log.id,
                        last_scanned_at=now,
                        last_message_seq=max_seq,
                        finding_count=finding_count,
                        status=status,
                        error_message=error,
                    )
                )
            else:
                scan.last_scanned_at = now
                scan.last_message_seq = max_seq
                scan.finding_count = finding_count
                scan.status = status
                scan.error_message = error
            session.commit()
            processed += 1
            new_findings += added
        remaining = count_remaining(session)
        total_logs = session.scalar(select(func.count()).select_from(RequestLog)) or 0
        scanned_logs = session.scalar(select(func.count()).select_from(ContentAuditScan)) or 0
    finally:
        session.close()
    extra: dict[str, Any] = {
        "processed": processed,
        "new_findings": new_findings,
        "remaining": remaining,
        "lexicon_ok": lexicon.ok,
        "scanned_count": scanned_logs,
        "total_logs": total_logs,
    }
    if lexicon.ok:
        extra["message"] = f"已扫描 {processed} 条请求，新增 {new_findings} 条命中，剩余 {remaining} 条"
    else:
        extra["message"] = (
            f"词库未能加载，已跳过敏感词；已扫描 {processed} 条请求，新增 {new_findings} 条命中，剩余 {remaining} 条"
        )
        extra["error_message"] = lexicon.error_message or "词库未能加载，已跳过敏感词扫描"
    return extra


async def run_scan_batch(batch_size: int = BATCH_SIZE) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(run_scan_batch_sync, batch_size)


def progress_stats(db: Session | None = None) -> dict[str, Any]:
    own = db is None
    session = db or get_session_factory()()
    try:
        total_logs = session.scalar(select(func.count()).select_from(RequestLog)) or 0
        scanned_count = session.scalar(select(func.count()).select_from(ContentAuditScan)) or 0
        finding_count = session.scalar(select(func.count()).select_from(ContentAuditFinding)) or 0
        remaining = count_remaining(session)
        counts = dict(
            session.execute(
                select(ContentAuditFinding.category, func.count()).group_by(ContentAuditFinding.category)
            ).all()
        )
        return {
            "scanned_count": scanned_count,
            "total_logs": total_logs,
            "finding_count": finding_count,
            "remaining": remaining,
            "by_category": {
                "sensitive": int(counts.get(CATEGORY_SENSITIVE) or 0),
                "pii": int(counts.get(CATEGORY_PII) or 0),
                "secret": int(counts.get(CATEGORY_SECRET) or 0),
            },
        }
    finally:
        if own:
            session.close()
