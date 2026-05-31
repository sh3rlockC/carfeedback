from __future__ import annotations

import re
from html import unescape
from typing import Any, Literal

import requests


PlatformName = Literal["autohome", "dongchedi"]
_PLATFORMS = {"autohome", "dongchedi"}
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def canonical_series_url(platform: str, series_id: str) -> str:
    if platform == "autohome":
        return f"https://k.autohome.com.cn/{series_id}/"
    if platform == "dongchedi":
        return f"https://www.dongchedi.com/auto/series/{series_id}"
    raise ValueError(f"unsupported platform: {platform}")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _html_text_window(html: str) -> str:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = title_match.group(1) if title_match else ""
    compact = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    compact = re.sub(r"<style[\s\S]*?</style>", " ", compact, flags=re.IGNORECASE)
    compact = re.sub(r"<[^>]+>", " ", compact)
    return unescape(f"{title} {compact[:4000]}")


def validate_series_id(
    *,
    query: str,
    platform: PlatformName,
    series_id: str,
    url: str | None = None,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    normalized_query = query.strip()
    normalized_series_id = series_id.strip()
    target_url = url.strip() if url and url.strip() else canonical_series_url(platform, normalized_series_id)

    if platform not in _PLATFORMS:
        return _result(normalized_query, platform, normalized_series_id, target_url, "invalid", "不支持的平台")
    if not normalized_series_id.isdigit():
        return _result(normalized_query, platform, normalized_series_id, target_url, "invalid", "车系编号必须为纯数字")

    try:
        response = requests.get(
            target_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout_seconds,
            allow_redirects=True,
        )
    except requests.RequestException:
        return _result(normalized_query, platform, normalized_series_id, target_url, "unverified", "页面暂时无法验证，请人工确认后继续")

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {403, 408, 429} or status_code >= 500:
        return _result(normalized_query, platform, normalized_series_id, target_url, "unverified", "页面暂时无法验证，请人工确认后继续")
    if status_code >= 400:
        return _result(normalized_query, platform, normalized_series_id, target_url, "mismatch", "车系页面无法打开或不存在")

    text_window = _html_text_window(str(getattr(response, "text", "") or ""))
    if _normalize_text(normalized_query) and _normalize_text(normalized_query) in _normalize_text(text_window):
        return _result(normalized_query, platform, normalized_series_id, target_url, "matched", "车系页面标题或正文匹配车型名")

    return _result(normalized_query, platform, normalized_series_id, target_url, "mismatch", "车系页面内容未匹配车型名")


def _result(query: str, platform: str, series_id: str, url: str, status: str, message: str) -> dict[str, Any]:
    return {
        "query": query,
        "platform": platform,
        "series_id": series_id,
        "url": url,
        "status": status,
        "can_create": status in {"matched", "unverified", "mismatch"},
        "cacheable": status == "matched",
        "requires_confirmation": status in {"unverified", "mismatch"},
        "message": message,
    }
