"""
TikTok Connector — Playwright-based.

Loads the real TikTok search page in a headless browser, intercepts
the API responses, and extracts structured data. No X-Bogus needed
because the browser handles all signing natively.

This is how production TikTok scrapers actually work.
"""

import asyncio
import json
import time
import os
from datetime import datetime, timezone

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)
from social_scraper.proxy_config import build_playwright_proxy

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


async def _navigate_search_page(page, search_url: str) -> None:
    """Wait for the document, not network idle; TikTok keeps connections open."""
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)


class TikTokPlaywrightConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "playwright"

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
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
                    locale="en-US",
                )

                page = await context.new_page()

                # Intercept API responses
                api_responses = []

                async def handle_response(response):
                    url = response.url
                    if "/api/search/" in url and response.status == 200:
                        try:
                            body = await response.json()
                            api_responses.append(body)
                        except Exception:
                            pass

                page.on("response", handle_response)

                # Navigate to search page
                search_url = f"https://www.tiktok.com/search?q={keyword}"
                if sort == "latest":
                    search_url += "&type=video&t=1"
                elif sort == "hot":
                    search_url += "&type=video"

                await _navigate_search_page(page, search_url)

                # Scroll to trigger more results
                for _ in range(min(count // 10 + 1, 3)):
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await asyncio.sleep(1)

                # Parse intercepted API responses
                for resp_data in api_responses:
                    raw_items = self._extract_items(resp_data)
                    for raw in raw_items:
                        item = self._parse_item(raw)
                        if item:
                            items.append(item)

                await browser.close()

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

    def _extract_items(self, response_data: dict) -> list:
        """Extract item list from various TikTok API response shapes."""
        # Shape 1: data[] with type:item
        if isinstance(response_data.get("data"), list):
            items = []
            for entry in response_data["data"]:
                if isinstance(entry, dict):
                    if entry.get("type") == "item" or "item" in entry:
                        items.append(entry.get("item", entry))
                    elif "aweme_info" in entry:
                        items.append(entry["aweme_info"])
                    elif "id" in entry or "aweme_id" in entry:
                        items.append(entry)
            if items:
                return items

        # Shape 2: itemList[]
        if isinstance(response_data.get("itemList"), list):
            return response_data["itemList"]

        # Shape 3: aweme_list[]
        if isinstance(response_data.get("aweme_list"), list):
            return response_data["aweme_list"]

        return []

    def _parse_item(self, raw: dict) -> SocialItem:
        """Parse raw TikTok item into normalized SocialItem."""
        aweme = raw

        aweme_id = str(aweme.get("id") or aweme.get("aweme_id") or "")
        if not aweme_id:
            return None

        desc = aweme.get("desc", "")
        create_time = aweme.get("createTime") or aweme.get("create_time")

        # Author
        author = aweme.get("author", {})
        author_sec_uid = author.get("secUid") or author.get("sec_uid", "")
        author_nickname = author.get("nickname", "")
        author_unique_id = author.get("uniqueId") or author.get("unique_id", "")

        # Engagement
        stats = aweme.get("stats", {}) or aweme.get("statistics", {})
        if "collectCount" in stats:
            collect_count = stats.get("collectCount")
            collect_source = "collectCount"
        else:
            collect_count = stats.get("collect_count")
            collect_source = "collect_count"
        engagement_sources = (
            {"collects": collect_source, "bookmarks": collect_source}
            if collect_count is not None
            else {}
        )

        # Media
        video = aweme.get("video", {})
        media_type = "video" if video else "text"
        cover = None
        if video:
            cover_data = video.get("cover", {})
            if isinstance(cover_data, dict):
                cover = cover_data.get("url") or (
                    cover_data.get("url_list", [None])[0]
                    if cover_data.get("url_list") else None
                )

        # Hashtags
        hashtags = []
        challenges = aweme.get("challenges", []) or aweme.get("textExtra", [])
        for ch in challenges:
            name = ch.get("title") or ch.get("hashtagName") or ch.get("hashtag_name")
            if name:
                hashtags.append(name)

        # URL
        if author_unique_id:
            url = f"https://www.tiktok.com/@{author_unique_id}/video/{aweme_id}"
        else:
            url = f"https://www.tiktok.com/video/{aweme_id}"

        # Timestamp
        created_at = None
        if create_time:
            try:
                created_at = datetime.fromtimestamp(
                    int(create_time), tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError):
                pass

        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=url,
            author_username=author_unique_id,
            author_display_name=author_nickname,
            author_profile_url=f"https://www.tiktok.com/@{author_unique_id}" if author_unique_id else "",
            author_follower_count=author.get("followerCount") or author.get("follower_count"),
            text=desc,
            created_at=created_at,
            views=stats.get("playCount") or stats.get("play_count"),
            likes=stats.get("diggCount") or stats.get("digg_count"),
            comments=stats.get("commentCount") or stats.get("comment_count"),
            shares=stats.get("shareCount") or stats.get("share_count"),
            collects=collect_count,
            bookmarks=collect_count,
            media_type=media_type,
            thumbnail_url=cover,
            hashtags=hashtags,
            region=aweme.get("region"),
            raw={
                "aweme_id": aweme_id,
                "engagement_sources": engagement_sources,
            },
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
