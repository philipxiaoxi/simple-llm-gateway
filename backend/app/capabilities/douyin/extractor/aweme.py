from __future__ import annotations

from typing import Any

from app.capabilities.douyin.errors import DouyinError

from .base import ExtractedMedia, ExtractedWork


def _first_url(container: Any) -> str | None:
    if not isinstance(container, dict):
        return None
    urls = container.get("url_list")
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, str) and item.startswith("http"):
                return item
    return None


def _clean_watermark(url: str) -> str:
    return url.replace("/playwm/", "/play/").replace("playwm", "play")


def _dimensions(node: Any) -> tuple[int | None, int | None]:
    if not isinstance(node, dict):
        return None, None
    width = node.get("width")
    height = node.get("height")
    return (
        int(width) if isinstance(width, int | float) else None,
        int(height) if isinstance(height, int | float) else None,
    )


def find_aweme(node: Any) -> dict[str, Any] | None:
    """在任意嵌套 JSON 中定位作品对象（含 aweme_id 且带 video/images/image_post_info）。"""
    if isinstance(node, dict):
        if node.get("aweme_id") and (
            "video" in node or "images" in node or "image_post_info" in node
        ):
            return node
        for value in node.values():
            found = find_aweme(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_aweme(item)
            if found is not None:
                return found
    return None


def build_work(aweme: dict[str, Any], extractor: str) -> ExtractedWork:
    author = aweme.get("author") or {}
    video = aweme.get("video") or {}
    images = aweme.get("images") or []
    if not images:
        image_post = aweme.get("image_post_info") or {}
        images = image_post.get("images") or []

    media: list[ExtractedMedia] = []
    duration_ms = 0

    if isinstance(images, list) and images:
        for image in images:
            url = _first_url(image)
            if not url:
                continue
            width, height = _dimensions(image)
            media.append(ExtractedMedia(kind="image", url=url, width=width, height=height))
        kind = "gallery"
    else:
        play = (
            video.get("play_addr")
            or video.get("play_addr_h264")
            or video.get("download_addr")
            or {}
        )
        url = _first_url(play)
        if not url:
            for bit in video.get("bit_rate") or []:
                url = _first_url(bit.get("play_addr"))
                if url:
                    break
        if not url:
            raise DouyinError("未能解析视频地址", status_code=422, error_type="content_unavailable")
        url = _clean_watermark(url)
        width, height = _dimensions(video)
        duration_ms = int(video.get("duration") or 0)
        media.append(
            ExtractedMedia(kind="video", url=url, width=width, height=height, duration_ms=duration_ms)
        )
        kind = "video"

    if not media:
        raise DouyinError("未解析到可下载媒体", status_code=422, error_type="content_unavailable")

    music = aweme.get("music") or {}
    music_url = _first_url(music.get("play_url"))
    if music_url:
        media.append(ExtractedMedia(kind="audio", url=music_url, duration_ms=duration_ms))

    cover = (
        _first_url(video.get("cover"))
        or _first_url(video.get("origin_cover"))
        or (_first_url(images[0]) if images else None)
        or ""
    )
    return ExtractedWork(
        extractor=extractor,
        aweme_id=str(aweme.get("aweme_id") or ""),
        title=str(aweme.get("desc") or ""),
        author_name=str(author.get("nickname") or ""),
        author_id=str(author.get("uid") or author.get("sec_uid") or ""),
        cover_url=cover,
        duration_ms=duration_ms,
        kind=kind,
        media=media,
    )
