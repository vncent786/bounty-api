"""
Douyin Connector — Playwright-based.

Same approach as TikTok: load real page, intercept API responses.
The browser handles a_bogus signing natively.
"""

import asyncio
import json
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


class DouyinPlaywrightConnector(BaseConnector):
    platform = "douyin"
    connector_name = "playwright"

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        if not PLAYWRIGHT_AVAILABLE:
            return ConnectorResult(items=[], health=SourceHealth(
                platform=self.platform, connector=self.connector_name,
                status="error", error="Playwright not installed",
            ))

        try:
            async with async_playwright() as p:
                launch_kwargs = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                proxy = build_playwright_proxy()
                if proxy:
                    launch_kwargs["proxy"] = proxy

                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="zh-CN",
                )
                page = await context.new_page()

                api_responses = []
                async def handle_response(response):
                    url = response.url
                    if "/aweme/v1/web/search/" in url or "/aweme/v1/web/general/search/" in url:
                        if response.status == 200:
                            try:
                                body = await response.json()
                                api_responses.append(body)
                            except Exception:
                                pass

                page.on("response", handle_response)

                search_url = f"https://www.douyin.com/search/{quote(keyword)}"
                await page.goto(search_url, wait_until="networkidle", timeout=30000)

                for _ in range(min(count // 10 + 1, 3)):
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await asyncio.sleep(1)

                for resp_data in api_responses:
                    raw_items = resp_data.get("data", [])
                    if isinstance(raw_items, list):
                        for raw in raw_items:
                            aweme = raw.get("aweme_info") or raw.get("video") or raw
                            item = self._parse_item(aweme)
                            if item:
                                items.append(item)

                await browser.close()

        except Exception as e:
            error = str(e)

        latency = int((time.time() - start) * 1000)
        return ConnectorResult(
            items=items[:count],
            health=SourceHealth(
                platform=self.platform, connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items), items_requested=count,
                latency_ms=latency, error=error,
            ),
        )

    def _parse_item(self, aweme: dict) -> SocialItem:
        aweme_id = str(aweme.get("aweme_id") or aweme.get("id") or "")
        if not aweme_id:
            return None

        desc = aweme.get("desc", "")
        create_time = aweme.get("create_time")
        author = aweme.get("author", {})
        stats = aweme.get("statistics", {})
        video = aweme.get("video", {})
        collect_count = stats.get("collect_count")
        engagement_sources = (
            {
                "collects": "collect_count",
                "bookmarks": "collect_count",
            }
            if collect_count is not None
            else {}
        )

        cover = None
        if video:
            cover_data = video.get("cover", {})
            if isinstance(cover_data, dict):
                urls = cover_data.get("url_list", [])
                if urls:
                    cover = urls[0]

        hashtags = []
        for te in aweme.get("text_extra", []):
            if te.get("hashtag_name"):
                hashtags.append(te["hashtag_name"])

        created_at = None
        if create_time:
            try:
                created_at = datetime.fromtimestamp(
                    int(create_time), tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError):
                pass

        sec_uid = author.get("sec_uid", "")
        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=f"https://www.douyin.com/video/{aweme_id}",
            author_username=author.get("unique_id", ""),
            author_display_name=author.get("nickname", ""),
            author_profile_url=f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
            text=desc,
            created_at=created_at,
            views=stats.get("play_count"),
            likes=stats.get("digg_count"),
            comments=stats.get("comment_count"),
            shares=stats.get("share_count"),
            collects=collect_count,
            bookmarks=collect_count,
            media_type="video" if video else "text",
            thumbnail_url=cover,
            hashtags=hashtags,
            region="CN",
            raw={
                "aweme_id": aweme_id,
                "engagement_sources": engagement_sources,
            },
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
