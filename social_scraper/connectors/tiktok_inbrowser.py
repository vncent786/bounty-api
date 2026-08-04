"""
TikTok In-Browser Connector — execute fetch() inside TikTok's page context.

The browser loads TikTok's real JS, which patches fetch/XHR with signing
middleware (webmssdk.js). When we call fetch() from page.evaluate(),
TikTok's own code signs the request. No Evil0ctal, no manual X-Bogus.

This is the most resilient approach: TikTok updates their signing, and
we automatically get the new version because we use their own JS.
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

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Shared browser context — keep alive across searches for speed
_browser = None
_context = None
_page = None
_last_use = 0
CONTEXT_TTL = 120  # recycle context after 2 min idle


async def _ensure_browser():
    """Get or create a shared browser page."""
    global _browser, _context, _page, _last_use

    now = time.time()
    if _page and (now - _last_use) < CONTEXT_TTL:
        _last_use = now
        return _page

    # Recycle
    await _cleanup()

    p = await async_playwright().start()

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

    _browser = await p.chromium.launch(**launch_kwargs)
    _context = await _browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    _page = await _context.new_page()

    # Visit TikTok to load signing middleware
    await _page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=60000)
    await _page.wait_for_timeout(8000)

    _last_use = now
    return _page


async def _cleanup():
    global _browser, _context, _page
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    _browser = None
    _context = None
    _page = None


class TikTokInBrowserConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "in_browser"

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
            page = await _ensure_browser()

            # Build search URL params
            sort_param = {"": 0, "hot": 1, "latest": 2}.get(sort, 0)
            time_param = {"": 0, "1day": 1, "week": 7, "month": 30}.get(time_filter, 0)

            search_url = (
                "https://www.tiktok.com/api/search/item/full/"
                f"?keyword={quote(keyword)}"
                f"&offset=0&count={min(count, 12)}"
                "&search_id="
                f"&sort_type={sort_param}"
                f"&publish_time={time_param}"
                "&aid=1988"
                "&app_language=en"
                "&app_name=tiktok_web"
                "&browser_language=en-US"
                "&browser_name=Mozilla"
                "&browser_online=true"
                "&browser_platform=Win32"
                "&browser_version=120.0.0.0"
                "&channel=tiktok_web"
                "&cookie_enabled=true"
                "&device_id=7385712345678901234"
                "&device_platform=web_pc"
                "&focus_state=true"
                "&from_page=search"
                "&history_len=4"
                "&is_full=1"
                "&os=windows"
                "&priority_region="
                "&region=US"
                "&screen_height=1080"
                "&screen_width=1920"
                "&tz_name=America/New_York"
                '&web_search_code={"htm_refresh_tk":"0.0"}'
            )

            # Execute fetch() inside the page — TikTok's signing
            # middleware will intercept and sign it automatically
            result = await page.evaluate("""
                async (url) => {
                    try {
                        const resp = await fetch(url, {
                            method: 'GET',
                            headers: {
                                'Accept': 'application/json, text/plain, */*',
                            },
                            credentials: 'include',
                        });
                        const text = await resp.text();
                        return {
                            status: resp.status,
                            body: text,
                            bodyLen: text.length,
                        };
                    } catch(e) {
                        return { error: e.message };
                    }
                }
            """, search_url)

            if result.get("error"):
                raise Exception(f"fetch error: {result['error']}")
            if result.get("status") != 200:
                raise Exception(f"HTTP {result['status']}")
            if result.get("bodyLen", 0) < 10:
                raise Exception("Empty body — signing middleware did not sign the request")

            data = json.loads(result["body"])

            raw_items = data.get("data", []) or data.get("itemList", []) or data.get("aweme_list", [])
            for raw in raw_items:
                item = self._parse_aweme(raw)
                if item:
                    items.append(item)

        except Exception as e:
            error = str(e)
            # Reset session on error
            await _cleanup()

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

    def _parse_aweme(self, raw: dict) -> SocialItem:
        aweme = raw.get("item") or raw.get("aweme_info") or raw
        aweme_id = str(aweme.get("id") or aweme.get("aweme_id") or "")
        if not aweme_id:
            return None

        desc = aweme.get("desc", "")
        stats = aweme.get("statistics", {}) or aweme.get("stats", {})
        author = aweme.get("author", {})
        video = aweme.get("video", {})

        created_at = None
        ct = aweme.get("create_time") or aweme.get("createTime")
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
        for tag in aweme.get("text_extra", []) or aweme.get("challenges", []):
            name = tag.get("hashtag_name") or tag.get("title")
            if name:
                hashtags.append(name)

        author_handle = author.get("unique_id") or author.get("uniqueId") or author.get("nickname", "")
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
            views=stats.get("play_count") or stats.get("playCount"),
            likes=stats.get("digg_count") or stats.get("diggCount"),
            comments=stats.get("comment_count") or stats.get("commentCount"),
            shares=stats.get("share_count") or stats.get("shareCount"),
            collects=stats.get("collect_count") or stats.get("collectCount"),
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

    async def close(self):
        await _cleanup()
