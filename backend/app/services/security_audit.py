"""安全中心的事件判定、地点匹配与异步落库。

设计约束（见 .monkeycode/specs/2026-10-20-security-center/design.md）：

- 判定必须零阻塞：中间件只做内存计算并入队，绝不在请求路径上查库或外呼。
- 落库离开热路径：归属地解析（网络 I/O）与写库都在 worker 内，写库用独立短事务，
  不把写事务跨到网络 I/O 期间（避免 SQLite ``database is locked``）。
- 代理接口（/v1、/anthropic、/mcp 等）由 SDK 调用，非浏览器 UA 属正常，不参与判定。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import jwt

from app.config import get_settings
from app.crypto import encrypt_secret
from app.db import get_session_factory
from app.models import SecurityEvent, SecuritySettings
from app.services.client_meta import client_ip as resolve_client_ip
from app.services.client_meta import (
    lookup_ip_location,
    parse_client_name,
    parse_client_version,
    parse_device_type,
)
from app.services.headers_redact import dump_headers

# 管理控制台与公开 Web 的 API 前缀；只有这些路径参与异常判定
CONSOLE_API_PREFIXES = ("/api/admin/", "/api/public/", "/api/voice/")

_BROWSER_TOKEN = re.compile(
    r"Mozilla/5\.0.*(Chrome/|Firefox/|Safari/|Edg/|Version/|Mobile/)", re.I
)

# 命令行客户端 / 扫描器：命中即 critical
SCANNER_PATTERNS = (
    "curl/",
    "wget/",
    "python-requests",
    "python-httpx",
    "httpx/",
    "aiohttp/",
    "go-http-client",
    "okhttp",
    "java/",
    "node-fetch",
    "undici",
    "axios/",
    "sqlmap",
    "nikto",
    "nmap",
    "nuclei",
    "masscan",
    "zgrab",
    "feroxbuster",
    "ffuf",
    "dirbuster",
    "gobuster",
)

# headless / 自动化 UA 令牌（仅在 UA 伪装成浏览器时判定，避免误伤正常 SDK）
AUTOMATION_UA_TOKENS = (
    "headlesschrome",
    "headless",
    "phantomjs",
    "electron/",
    "puppeteer",
    "playwright",
    "selenium",
    "webdriver",
)

_MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}
_ADMIN_SUFFIXES = ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区", "省", "市")

_QUEUE_MAX = 2000
_LOCATION_ENTRIES_TTL = 60


@dataclass(slots=True)
class JudgeResult:
    is_console: bool
    source: str
    client_ip: str | None
    user_agent: str | None
    client_name: str | None
    risk_tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PendingEvent:
    event_type: str
    severity: str
    source: str
    headers: dict[str, str] = field(default_factory=dict)
    peer: str | None = None
    method: str | None = None
    path: str | None = None
    status: int | None = None
    risk_tags: list[str] = field(default_factory=list)
    actor: str | None = None
    attempted_password: str | None = None
    beacon: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None
    dedupe: bool = True


_QUEUE: deque[PendingEvent] = deque(maxlen=_QUEUE_MAX)
_LAST_EMIT: dict[tuple, float] = {}
_THROTTLE_LOCK = threading.Lock()
_AUTH_FAILURES: dict[str, list[float]] = {}
_FAIL_LOCK = threading.Lock()
_BEACON_HITS: dict[str, list[float]] = {}
_LOCATION_CACHE: tuple[float, list[dict], list[ipaddress._BaseNetwork], bool] | None = None


def looks_like_browser(user_agent: str | None) -> bool:
    return bool(user_agent) and bool(_BROWSER_TOKEN.search(user_agent))


_SUBRESOURCE_DESTS = frozenset(
    {
        "audio",
        "embed",
        "font",
        "frame",
        "iframe",
        "image",
        "manifest",
        "object",
        "paintworklet",
        "script",
        "serviceworker",
        "sharedworker",
        "style",
        "track",
        "video",
        "worker",
        "xslt",
    }
)


def is_subresource_request(headers: dict[str, str]) -> bool:
    """是否为浏览器子资源加载（图片/字体/脚本/iframe 等）。

    这类请求由浏览器直接发起，既无法携带自定义头，其 404 也只说明资源缺失，
    不该按「缺少前端标识」或「路径探测」告警。
    """
    return (headers.get("sec-fetch-dest") or "").strip().lower() in _SUBRESOURCE_DESTS


def is_xhr_request(headers: dict[str, str]) -> bool:
    """是否为 XHR / fetch 这类程序化请求。

    只有这类请求才要求前端标识：导航、图片/字体等子资源、文件下载链接天然不会带
    自定义头，如果也要求标识会把正常使用误判成异常（图片访问、上传回跳等）。

    判定优先用 Fetch Metadata：`Sec-Fetch-Dest: empty` 正是 fetch()/XHR 的特征，
    而 `image` / `document` / `script` 等说明是子资源或页面导航。
    """
    if (headers.get("x-requested-with") or "").strip().lower() == "xmlhttprequest":
        return True
    dest = (headers.get("sec-fetch-dest") or "").strip().lower()
    if dest:
        return dest == "empty"
    # 没有 Fetch Metadata 的客户端（旧浏览器 / 命令行）：退回按 Accept、Content-Type 判断
    accept = (headers.get("accept") or "").lower()
    content_type = (headers.get("content-type") or "").lower()
    return "application/json" in accept or "application/json" in content_type


def is_console_path(path: str) -> bool:
    return path.startswith(CONSOLE_API_PREFIXES)


def _origin_mismatch(headers: dict[str, str]) -> bool:
    origin = headers.get("origin")
    if not origin:
        return False
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0]
    allowed = {headers.get("host", "")}
    base = get_settings().app_base_url
    if base:
        allowed.add(base.split("://", 1)[-1].split("/", 1)[0])
    allowed.discard("")
    return origin_host not in allowed


def judge_request(path: str, headers: dict[str, str], peer: str | None) -> JudgeResult:
    """对单次请求做纯内存判定，不查库、不外呼。"""
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    ip = resolve_client_ip(lowered, peer=peer)
    user_agent = lowered.get("user-agent")
    client_name = parse_client_name(user_agent or "")
    if not is_console_path(path):
        # 非控制台路径不判定，代理接口直接归为 api 来源
        return JudgeResult(False, "api", ip, user_agent, client_name)

    tags: list[str] = []
    has_marker = lowered.get("x-gw-client") == "web"
    # 只对 XHR / fetch 要求前端标识；导航、图片等子资源不要求
    if not has_marker and is_xhr_request(lowered):
        tags.append("missing_marker")
    if not looks_like_browser(user_agent):
        tags.append("non_browser_ua")
    # 带前端标识的请求即同源 SPA（跨站请求无法携带自定义头且会被预检拦截），
    # 因此只在缺少标识时比对 Origin，避免反向代理改写 Host 造成的误报。
    if not has_marker and _origin_mismatch(lowered):
        tags.append("origin_mismatch")
    if any(token in (user_agent or "").lower() for token in SCANNER_PATTERNS):
        tags.append("scanner_ua")
    return JudgeResult(True, "direct", ip, user_agent, client_name, tags)


def automation_signals(
    user_agent: str | None,
    headers: dict[str, str] | None = None,
    beacon: dict[str, Any] | None = None,
) -> list[str]:
    lowered = {str(k).lower() for k in (headers or {})}
    signals: list[str] = []
    ua = (user_agent or "").lower()
    if looks_like_browser(user_agent):
        if any(token in ua for token in AUTOMATION_UA_TOKENS):
            signals.append("headless_ua")
        if "accept-language" not in lowered:
            signals.append("missing_accept_language")
    if beacon is not None:
        if beacon.get("webdriver"):
            signals.append("webdriver")
        if not beacon.get("screen_w") or not beacon.get("screen_h"):
            signals.append("zero_screen")
    return signals


# ---- 常用地点匹配 ---------------------------------------------------------------


@dataclass(slots=True)
class LocationTokens:
    country: str = ""
    province: str = ""
    city: str = ""


def normalize_admin(token: str) -> str:
    text = (token or "").strip()
    for suffix in _ADMIN_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _is_province_token(token: str) -> bool:
    return token.endswith(("省", "自治区", "特别行政区"))


def _is_municipality(token: str) -> bool:
    return token in _MUNICIPALITIES


def normalize_location(text: str | None) -> LocationTokens | None:
    """把归属地字符串归一化为「国家 / 省 / 市」三级令牌。

    先按行政后缀识别（省/自治区/特别行政区、市、直辖市），缺后缀的部分再按
    位置兜底：首个无标记段视为国家，其余依次补省与市。这样 ``中国 广东 广州市``
    与 ``中国 广东省 广州市`` 都能解析成 ``(中国, 广东, 广州)``。
    """
    parts = [part for part in (text or "").split() if part]
    if not parts:
        return None
    uniq: list[str] = []
    for part in parts:
        if not uniq or uniq[-1] != part:
            uniq.append(part)

    tokens = LocationTokens()
    plain: list[str] = []
    for part in uniq:
        norm = normalize_admin(part)
        if _is_municipality(norm):
            tokens.province = tokens.province or norm
            tokens.city = tokens.city or norm
            continue
        if _is_province_token(part):
            tokens.province = tokens.province or norm
            continue
        if part.endswith("市"):
            tokens.city = tokens.city or norm
            continue
        plain.append(norm)

    # 无标记段按位置兜底：首个是国家，其余补省/市
    rest = plain[1:] if plain else []
    if plain:
        tokens.country = plain[0]
    if not tokens.province and rest:
        tokens.province = rest[0]
    if not tokens.city and tokens.province:
        if _is_municipality(tokens.province):
            tokens.city = tokens.province
        elif rest and normalize_admin(rest[-1]) != tokens.province:
            tokens.city = rest[-1]
    return tokens


def _entry_tokens(entry: dict[str, Any]) -> LocationTokens:
    return LocationTokens(
        country=normalize_admin(str(entry.get("country") or "")),
        province=normalize_admin(str(entry.get("province") or "")),
        city=normalize_admin(str(entry.get("city") or "")),
    )


def match_location(tokens: LocationTokens, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in entries:
        if not entry.get("enabled", True):
            continue
        rule = _entry_tokens(entry)
        if not (rule.country or rule.province or rule.city):
            continue
        if rule.country and rule.country != tokens.country:
            continue
        if rule.province and rule.province != tokens.province:
            continue
        if rule.city and rule.city != tokens.city:
            continue
        return entry
    return None


def _parse_allowlist(allowlist: list[str]) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for item in allowlist:
        text = str(item).strip()
        if not text:
            continue
        candidate = text if "/" in text else (f"{text}/32" if ":" not in text else f"{text}/128")
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return networks


def _is_public(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (parsed.is_private or parsed.is_loopback or parsed.is_reserved or parsed.is_link_local)


def _in_allowlist(ip: str | None, networks: list[ipaddress._BaseNetwork]) -> bool:
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def classify_location(
    ip: str | None,
    location: str | None,
    entries: list[dict[str, Any]],
    allowlist: list[ipaddress._BaseNetwork],
    enabled: bool,
) -> tuple[list[str], bool | None, str]:
    if not enabled:
        return [], None, ""
    if _in_allowlist(ip, allowlist):
        return [], True, "ip_allowlist"
    if not _is_public(ip):
        return ["local_network"], None, ""
    tokens = normalize_location(location)
    if tokens is None or not tokens.city:
        return ["location_unknown"], None, ""
    if not entries:
        return ["unfamiliar_location"], False, ""
    hit = match_location(tokens, entries)
    if hit is not None:
        return [], True, str(hit.get("raw") or "")
    return ["unfamiliar_location"], False, ""


def load_trusted_locations() -> tuple[list[dict], list[ipaddress._BaseNetwork], bool]:
    """读取常用地点与 IP 白名单（带 60s 进程内缓存）。"""
    global _LOCATION_CACHE
    now = time.monotonic()
    cached = _LOCATION_CACHE
    if cached is not None and now - cached[0] < _LOCATION_ENTRIES_TTL:
        return cached[1], cached[2], cached[3]
    entries: list[dict] = []
    networks: list[ipaddress._BaseNetwork] = []
    enabled = True
    session = get_session_factory()()
    try:
        row = session.get(SecuritySettings, 1)
        if row is not None:
            enabled = bool(row.location_check_enabled)
            entries = _load_json_list(row.trusted_locations_json)
            networks = _parse_allowlist(_load_json_list(row.ip_allowlist_json))
    finally:
        session.close()
    _LOCATION_CACHE = (now, entries, networks, enabled)
    return entries, networks, enabled


def invalidate_trusted_locations() -> None:
    global _LOCATION_CACHE
    _LOCATION_CACHE = None


def _load_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


# ---- 入队与节流 -----------------------------------------------------------------


def _throttled(event: PendingEvent) -> bool:
    if not event.dedupe:
        return False
    key = (event.event_type, event.path, event.status, tuple(event.risk_tags))
    window = get_settings().security_dedupe_window_seconds
    now = time.monotonic()
    # 生产者在事件循环与线程池两处，节流表需要加锁避免 check-then-set 竞争
    with _THROTTLE_LOCK:
        last = _LAST_EMIT.get(key)
        if last is not None and now - last < window:
            return True
        _LAST_EMIT[key] = now
        if len(_LAST_EMIT) > 10000:
            _LAST_EMIT.clear()
            _LAST_EMIT[key] = now
    return False


def _enqueue(event: PendingEvent) -> None:
    if _throttled(event):
        return
    # deque 的 append 在 GIL 下对生产者线程安全；满时自动丢弃最旧的一条。
    _QUEUE.append(event)


def _actor_from_headers(headers: dict[str, str] | None) -> str | None:
    """从 Bearer token 解析操作账号；仅在已标记的请求上调用，避免热路径开销。"""
    if not headers:
        return None
    raw = None
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            raw = str(value)
            break
    if not raw or not raw.lower().startswith("bearer "):
        return None
    token = raw.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            get_settings().app_secret_key,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def submit_request_event(
    judge: JudgeResult,
    status: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    peer: str | None = None,
) -> None:
    if not judge.is_console:
        return
    tags = list(judge.risk_tags)
    # 子资源（图片/字体/iframe 等）的 404 只是资源缺失，不代表有人在探测接口
    if status == 404 and not is_subresource_request(headers or {}):
        tags.append("probe_path")
    if not tags:
        return
    severity = "critical" if "scanner_ua" in tags else "warn"
    _enqueue(
        PendingEvent(
            event_type="suspicious_request",
            severity=severity,
            source=judge.source,
            headers=dict(headers or {}),
            peer=peer,
            method=method,
            path=path,
            status=status,
            risk_tags=tags,
            actor=_actor_from_headers(headers),
            detail={"client_ip": judge.client_ip, "user_agent": judge.user_agent},
        )
    )


def enqueue_login_event(
    event_type: str,
    username: str | None,
    meta: dict[str, str | None],
    *,
    severity: str = "info",
    attempted_password: str | None = None,
    headers: dict[str, str] | None = None,
    status: int | None = None,
    path: str = "/api/admin/login",
    method: str = "POST",
) -> None:
    _enqueue(
        PendingEvent(
            event_type=event_type,
            severity=severity,
            source="web",
            path=path,
            actor=username,
            attempted_password=attempted_password,
            headers=dict(headers or {}),
            method=method,
            status=status,
            detail={"client_ip": meta.get("client_ip"), "user_agent": meta.get("user_agent")},
            dedupe=False,
        )
    )


def enqueue_beacon_event(
    payload: dict[str, Any],
    admin: str,
    meta: dict[str, str | None],
    *,
    signals: list[str],
    headers: dict[str, str] | None = None,
    status: int | None = 204,
) -> None:
    tags = ["automation"] if signals else []
    _enqueue(
        PendingEvent(
            event_type="frontend_beacon",
            severity="warn" if signals else "info",
            source="web",
            path=payload.get("path") or "/",
            actor=admin,
            beacon=payload,
            risk_tags=tags,
            headers=dict(headers or {}),
            method="POST",
            status=status,
            detail={"client_ip": meta.get("client_ip"), "user_agent": meta.get("user_agent"), "signals": signals},
        )
    )


def allow_beacon(username: str | None) -> bool:
    """单管理员固定窗口埋点限流；超限返回 False（调用方静默丢弃）。"""
    if not username:
        return True
    limit = get_settings().security_beacon_rate_limit
    if limit <= 0:
        return True
    now = time.monotonic()
    with _FAIL_LOCK:
        stamps = [stamp for stamp in _BEACON_HITS.get(username, []) if now - stamp < 60]
        if len(stamps) >= limit:
            _BEACON_HITS[username] = stamps
            return False
        stamps.append(now)
        _BEACON_HITS[username] = stamps
    return True


def record_auth_failure(ip: str | None) -> None:
    if not ip:
        return
    settings = get_settings()
    window = settings.security_auth_fail_window_seconds
    threshold = settings.security_auth_fail_threshold
    now = time.monotonic()
    with _FAIL_LOCK:
        stamps = [stamp for stamp in _AUTH_FAILURES.get(ip, []) if now - stamp < window]
        stamps.append(now)
        _AUTH_FAILURES[ip] = stamps
        burst = len(stamps) >= threshold
    if burst:
        _enqueue(
            PendingEvent(
                event_type="auth_fail_burst",
                severity="critical",
                source="direct",
                path="/api/admin/login",
                method="POST",
                status=401,
                risk_tags=["auth_fail_burst"],
                detail={"client_ip": ip, "count": len(stamps)},
            )
        )


async def audit_worker() -> None:
    while True:
        if not _QUEUE:
            await asyncio.sleep(0.5)
            continue
        try:
            event = _QUEUE.popleft()
        except IndexError:
            continue
        try:
            await _process(event)
        except Exception as error:  # noqa: BLE001 - 单条失败不能中断循环
            print(f"[security] write failed: {error}")


async def _process(event: PendingEvent) -> None:
    ip = (event.detail or {}).get("client_ip") or event.peer
    if not ip:
        ip = resolve_client_ip(event.headers, peer=event.peer)
    location = await lookup_ip_location(ip) if ip else None
    user_agent = (event.headers or {}).get("user-agent") or (event.detail or {}).get("user_agent")
    client_name = parse_client_name(user_agent or "")
    client_version = parse_client_version(user_agent or "")
    device_type = parse_device_type(user_agent or "")

    detail = dict(event.detail or {})
    tags = list(event.risk_tags)
    trusted: bool | None = None
    rule = ""
    if event.event_type in {"login_success", "login_failure", "login_locked", "suspicious_request", "auth_fail_burst"}:
        entries, networks, enabled = load_trusted_locations()
        loc_tags, trusted, rule = classify_location(ip, location, entries, networks, enabled)
        tags.extend(tag for tag in loc_tags if tag not in tags)
    if event.event_type in {"suspicious_request", "frontend_beacon"}:
        signals = automation_signals(user_agent, event.headers, event.beacon)
        if signals:
            if "automation" not in tags:
                tags.append("automation")
            detail["automation_signals"] = signals
            if event.severity == "info":
                event.severity = "warn"

    _write_event(event, ip, location, user_agent, client_name, client_version, device_type, tags, trusted, rule, detail)


def _outcome(status: int | None) -> str | None:
    if status is None:
        return None
    if 200 <= status < 400:
        return "success"
    if status in (401, 403, 429):
        return "rejected"
    return "failed"


def _write_event(
    event: PendingEvent,
    ip: str | None,
    location: str | None,
    user_agent: str | None,
    client_name: str | None,
    client_version: str | None,
    device_type: str | None,
    tags: list[str],
    trusted: bool | None,
    rule: str,
    detail: dict[str, Any],
) -> None:
    encrypted = None
    if event.attempted_password:
        limit = get_settings().security_max_password_len
        encrypted = encrypt_secret(event.attempted_password[:limit], get_settings().app_secret_key)
    session = get_session_factory()()
    try:
        session.add(
            SecurityEvent(
                event_type=event.event_type,
                severity=event.severity,
                source=event.source,
                actor=event.actor,
                attempted_password_encrypted=encrypted,
                client_ip=ip,
                ip_location=location,
                location_trusted=trusted,
                location_rule=rule or None,
                user_agent=user_agent,
                client_name=client_name,
                client_version=client_version,
                device_type=device_type,
                request_headers=dump_headers(event.headers),
                method=event.method,
                path=event.path,
                status_code=event.status,
                outcome=_outcome(event.status),
                risk_tags=json.dumps(tags, ensure_ascii=False),
                detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
            )
        )
        session.commit()
    finally:
        session.close()


def reset_security_state() -> None:
    """测试辅助：清空内存节流、失败计数与地点缓存。"""
    global _LOCATION_CACHE
    with _THROTTLE_LOCK:
        _LAST_EMIT.clear()
    _QUEUE.clear()
    with _FAIL_LOCK:
        _AUTH_FAILURES.clear()
        _BEACON_HITS.clear()
    _LOCATION_CACHE = None


class SecurityAuditMiddleware:
    """纯 ASGI 中间件：判定控制台请求的风险标签并入队，不改写 body、不阻塞响应。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not get_settings().security_audit_enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not is_console_path(path):
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        peer = (scope.get("client") or (None, None))[0]
        judge = judge_request(path, headers, peer)
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                submit_request_event(
                    judge,
                    status,
                    scope.get("method", ""),
                    path,
                    headers=headers,
                    peer=peer,
                )
            except Exception as error:  # noqa: BLE001 - 审计失败不影响请求
                print(f"[security] enqueue failed: {error}")
