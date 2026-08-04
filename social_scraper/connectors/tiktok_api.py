"""
TikTok Connector — API-direct using X-Bogus signing.

This is the same approach Scrape Creators / TikHub use:
call TikTok's private API directly with proper request signing.
No browser, no Playwright. Just signed HTTP requests.

~50KB per request instead of 5-10MB for Playwright.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)
from social_scraper.proxy_config import build_playwright_proxy

# Path to Evil0ctal's signing engine
_engine_path = os.path.join(os.path.dirname(__file__), "..", "..", "crawlers")
_engine_path = os.path.abspath(_engine_path)
if _engine_path not in sys.path:
    sys.path.insert(0, _engine_path)

import httpx
from crawlers.tiktok.web.endpoints import TikTokAPIEndpoints
from crawlers.tiktok.web.utils import BogusManager


# Default TikTok browser headers
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_COOKIE = "ttwid=1%7C4zrPCqm8J3F0wWwL3y4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r4r%7C1784532303%7C8a3c0e1a2b3c4d5e6f7a8b9c0d1e2f3a"


def _build_httpx_proxy():
    """Convert Playwright proxy format to httpx format."""
    pw_proxy = build_playwright_proxy()
    if not pw_proxy:
        return None
    server = pw_proxy["server"]
    # httpx proxy format: http://username:password@host:port
    if pw_proxy.get("username") and pw_proxy.get("password"):
        # Insert auth into URL
        parts = server.split("://")
        if len(parts) == 2:
            server = f"{parts[0]}://{pw_proxy['username']}:{pw_proxy['password']}@{parts[1]}"
    return server


class TikTokAPIDirectConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "api_direct"

    async def search(self, keyword: str, count: int = 12, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        try:
            results = await self._fetch_search(keyword, count, time_filter, sort)
            raw_items = results.get("data", []) if isinstance(results, dict) else []
            for raw in raw_items:
                item = self._parse_aweme(raw)
                if item:
                    items.append(item)
        except Exception as e:
            error = str(e)

        latency = int((time.time() - start) * 1000)
        return ConnectorResult(
            items=items[:count],
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items),
                items_requested=count,
                latency_ms=latency,
                error=error,
            ),
        )

    async def _fetch_search(self, keyword: str, count: int, time_filter: str, sort: str) -> dict:
        """Call TikTok search API directly with X-Bogus signing."""

        # Map filters
        sort_map = {"": 0, "hot": 1, "latest": 2}
        time_map = {"": 0, "1day": 1, "week": 7, "month": 30, "quarter": 90, "halfyear": 180}

        params = {
            "keyword": keyword,
            "offset": 0,
            "count": min(count, 12),
            "search_id": "",
            "sort_type": sort_map.get(sort, 0),
            "publish_time": time_map.get(time_filter, 0),
            "WebIdLastTime": "1784532303",
            "aid": "1988",
            "app_language": "en",
            "app_name": "tiktok_web",
            "browser_language": "en-US",
            "browser_name": "Mozilla",
            "browser_online": "true",
            "browser_platform": "Win32",
            "browser_version": "120.0.0.0",
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "device_id": "7385712345678901234",
            "device_platform": "web_pc",
            "focus_state": "true",
            "from_page": "search",
            "history_len": "4",
            "is_full": "1",
            "os": "windows",
            "priority_region": "",
            "region": "US",
            "screen_height": "1080",
            "screen_width": "1920",
            "tz_name": "America/Chicago",
            "web_search_code": '{"htm_refresh_tk":"0.0"}',
        }

        # Sign with X-Bogus
        endpoint = BogusManager.model_2_endpoint(
            TikTokAPIEndpoints.SEARCH_ITEM, params, DEFAULT_UA
        )

        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": f"https://www.tiktok.com/search?q={quote(keyword)}",
            "Cookie": DEFAULT_COOKIE,
            "Accept": "application/json, text/plain, */*",
        }

        proxy_url = _build_httpx_proxy()
        client_kwargs = {"headers": headers, "timeout": 15.0}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(endpoint)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")
            data = resp.json()
            return data

    def _parse_aweme(self, raw: dict) -> SocialItem:
        """Parse TikTok aweme item into SocialItem."""
        aweme = raw.get("item") or raw.get("aweme_info") or raw
        aweme_id = str(aweme.get("id") or aweme.get("aweme_id") or "")

        if not aweme_id:
            return None

        desc = aweme.get("desc", "")
        stats = aweme.get("statistics", {})
        author = aweme.get("author", {})
        video = aweme.get("video", {})

        created_at = None
        ct = aweme.get("create_time")
        if ct:
            try:
                created_at = datetime.fromtimestamp(int(ct), tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass

        # Get thumbnail
        thumbnail = None
        cover = video.get("cover") or video.get("origin_cover") or {}
        if isinstance(cover, dict):
            url_list = cover.get("url_list", [])
            if url_list:
                thumbnail = url_list[0]

        # Get hashtags
        hashtags = []
        for tag in aweme.get("text_extra", []):
            name = tag.get("hashtag_name") or tag.get("sec_uid")
            if name and tag.get("hashtag_name"):
                hashtags.append(name)

        author_handle = author.get("unique_id") or author.get("nickname", "")
        author_name = author.get("nickname", "")
        sec_uid = author.get("sec_uid", "")

        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=f"https://www.tiktok.com/@{author_handle}/video/{aweme_id}" if author_handle else "",
            author_username=author_handle,
            author_display_name=author_name,
            author_profile_url=f"https://www.tiktok.com/@{author_handle}" if author_handle else "",
            author_avatar_url=author.get("avatar_thumb", {}).get("url_list", [None])[0] if author.get("avatar_thumb") else None,
            text=desc,
            created_at=created_at,
            views=stats.get("play_count"),
            likes=stats.get("digg_count"),
            comments=stats.get("comment_count"),
            shares=stats.get("share_count"),
            collects=stats.get("collect_count"),
            media_type="video",
            thumbnail_url=thumbnail,
            hashtags=hashtags,
            region=aweme.get("region", "US"),
            duration_seconds=video.get("duration", 0) / 1000 if video.get("duration") else None,
            raw={"aweme_id": aweme_id},
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
