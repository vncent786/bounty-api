"""
TikTok Hybrid Connector — browser session harvest + API-direct.

Phase 1: Launch real Chrome through Geonode proxy, visit tiktok.com,
         harvest real cookies (ttwid, msToken, session cookies).
Phase 2: Use those cookies + Evil0ctal X-Bogus signing to call
         TikTok's search API directly via httpx.

This is how production TikTok scrapers work:
- Browser establishes trust (real fingerprint, cookies, tokens)
- API calls reuse that trust with proper signing
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

# Evil0ctal signing engine
_engine_path = os.path.join(os.path.dirname(__file__), "..", "..", "crawlers")
_engine_path = os.path.abspath(_engine_path)
if _engine_path not in sys.path:
    sys.path.insert(0, _engine_path)

import httpx
from crawlers.tiktok.web.endpoints import TikTokAPIEndpoints
from crawlers.tiktok.web.utils import BogusManager

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Session cache: cookies + UA, harvested once then reused
_session_cache = {
    "cookies": None,
    "user_agent": None,
    "harvested_at": 0,
}
SESSION_TTL = 300  # re-harvest every 5 minutes


def _build_httpx_proxy():
    pw_proxy = build_playwright_proxy()
    if not pw_proxy:
        return None
    server = pw_proxy["server"]
    if pw_proxy.get("username") and pw_proxy.get("password"):
        parts = server.split("://")
        if len(parts) == 2:
            server = f"{parts[0]}://{pw_proxy['username']}:{pw_proxy['password']}@{parts[1]}"
    return server


async def _harvest_session() -> dict:
    """Launch Chrome, visit TikTok, harvest cookies + UA."""
    async with async_playwright() as p:
        launch_kwargs = {
            "channel": "chrome",
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
            ],
        }
        proxy = build_playwright_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        # Visit homepage first — lets TikTok set ttwid, msToken, etc.
        await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        # Extract cookies
        cookies = await context.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        # Extract UA
        user_agent = await page.evaluate("navigator.userAgent")

        await browser.close()

    return {"cookies": cookie_str, "user_agent": user_agent}


async def _get_session() -> dict:
    """Get cached session or harvest a new one."""
    now = time.time()
    if (
        _session_cache["cookies"]
        and (now - _session_cache["harvested_at"]) < SESSION_TTL
    ):
        return _session_cache

    session = await _harvest_session()
    _session_cache.update(session)
    _session_cache["harvested_at"] = now
    return session


class TikTokHybridConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "hybrid"

    async def search(self, keyword: str, count: int = 12, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        if not PLAYWRIGHT_AVAILABLE:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform,
                    connector=self.connector_name,
                    status="error",
                    error="Playwright not installed",
                ),
            )

        try:
            session = await _get_session()
            results = await self._fetch_search(keyword, count, time_filter, sort, session)
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

    async def _fetch_search(self, keyword: str, count: int, time_filter: str,
                            sort: str, session: dict) -> dict:
        sort_map = {"": 0, "hot": 1, "latest": 2}
        time_map = {"": 0, "1day": 1, "week": 7, "month": 30, "quarter": 90, "halfyear": 180}

        ua = session["user_agent"]
        cookie_str = session["cookies"]

        params = {
            "keyword": keyword,
            "offset": 0,
            "count": min(count, 12),
            "search_id": "",
            "sort_type": sort_map.get(sort, 0),
            "publish_time": time_map.get(time_filter, 0),
            "WebIdLastTime": str(int(time.time())),
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
            "tz_name": "America/New_York",
            "web_search_code": '{"htm_refresh_tk":"0.0"}',
        }

        # Sign with X-Bogus using the REAL user agent from the browser session
        endpoint = BogusManager.model_2_endpoint(
            TikTokAPIEndpoints.SEARCH_ITEM, params, ua
        )

        headers = {
            "User-Agent": ua,
            "Referer": f"https://www.tiktok.com/search?q={quote(keyword)}",
            "Cookie": cookie_str,
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
            body = resp.text
            if not body or len(body) < 10:
                raise Exception("Empty response body — TikTok rejected the session")
            data = resp.json()
            return data

    def _parse_aweme(self, raw: dict) -> SocialItem:
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

        thumbnail = None
        cover = video.get("cover") or video.get("origin_cover") or {}
        if isinstance(cover, dict):
            url_list = cover.get("url_list", [])
            if url_list:
                thumbnail = url_list[0]

        hashtags = []
        for tag in aweme.get("text_extra", []):
            name = tag.get("hashtag_name")
            if name:
                hashtags.append(name)

        author_handle = author.get("unique_id") or author.get("nickname", "")
        author_name = author.get("nickname", "")

        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=f"https://www.tiktok.com/@{author_handle}/video/{aweme_id}" if author_handle else "",
            author_username=author_handle,
            author_display_name=author_name,
            author_profile_url=f"https://www.tiktok.com/@{author_handle}" if author_handle else "",
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
