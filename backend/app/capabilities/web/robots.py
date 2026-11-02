from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from app.config import get_settings

from .errors import WebError
from .fetch import fetch

_CACHE: dict[str, tuple[float, RobotFileParser | None]] = {}
_TTL_SECONDS = 3600


async def allowed(url: str) -> bool:
    settings = get_settings()
    if not settings.web_robots_enabled:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return True
    origin = f"{parsed.scheme}://{parsed.netloc}"
    now = time.time()
    cached = _CACHE.get(origin)
    if cached and cached[0] > now:
        parser = cached[1]
    else:
        parser = await _load(origin)
        _CACHE[origin] = (now + _TTL_SECONDS, parser)
    if parser is None:
        return True
    return parser.can_fetch(settings.resolved_web_user_agent, url)


async def _load(origin: str) -> RobotFileParser | None:
    try:
        result = await fetch(
            f"{origin}/robots.txt",
            max_bytes=512 * 1024,
            timeout=5,
            max_redirects=2,
        )
    except WebError:
        return None
    text = result.body.decode("utf-8", errors="ignore")
    if not text.strip():
        return None
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser


def clear_cache() -> None:
    _CACHE.clear()
