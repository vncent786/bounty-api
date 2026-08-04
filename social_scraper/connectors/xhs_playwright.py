"""
Xiaohongshu (RedNote) Connector — Playwright-based.

Loads the XHS search page, intercepts API responses, extracts notes.
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


class XHSPlaywrightConnector(BaseConnector):
    platform = "xiaohongshu"
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
                    if "/api/sns/web/v1/search/notes" in url or "/api/sns/web/v1/search_result" in url:
                        if response.status == 200:
                            try:
                                body = await response.json()
                                api_responses.append(body)
                            except Exception:
                                pass
                    # Also intercept homefeed which sometimes loads on search pages
                    if "/api/sns/web/v1/feed" in url and "search" in response.url.lower():
                        if response.status == 200:
                            try:
                                body = await response.json()
                                api_responses.append(body)
                            except Exception:
                                pass

                page.on("response", handle_response)

                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&source=web_search_result_notes"
                await page.goto(search_url, wait_until="networkidle", timeout=30000)

                for _ in range(min(count // 10 + 1, 3)):
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await asyncio.sleep(1)

                for resp_data in api_responses:
                    raw_items = []
                    if isinstance(resp_data.get("data"), dict):
                        raw_items = resp_data["data"].get("items", [])
                    elif isinstance(resp_data.get("data"), list):
                        raw_items = resp_data["data"]
                    elif isinstance(resp_data.get("items"), list):
                        raw_items = resp_data["items"]

                    for raw in raw_items:
                        item = self._parse_item(raw)
                        if item:
                            items.append(item)

                # Fallback: if no API intercepted, try DOM scraping
                if not items:
                    dom_items = await self._scrape_dom(page, keyword)
                    items.extend(dom_items)

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

    async def _scrape_dom(self, page, keyword: str) -> list:
        """Fallback DOM scraping if API interception fails."""
        items = []
        try:
            cards = await page.query_selector_all("section.note-item, div.note-item, [data-note-id]")
            for card in cards[:20]:
                try:
                    note_id = await card.get_attribute("data-note-id") or ""
                    link = await card.query_selector("a")
                    href = await link.get_attribute("href") if link else ""
                    title_el = await card.query_selector(".title, .note-title, span")
                    title = await title_el.inner_text() if title_el else ""

                    if note_id or href:
                        items.append(SocialItem(
                            platform=self.platform,
                            post_id=note_id,
                            url=f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else href,
                            text=title,
                            media_type="image",
                            region="CN",
                        ))
                except Exception:
                    pass
        except Exception:
            pass
        return items

    def _parse_item(self, raw: dict) -> SocialItem:
        """Parse XHS note from API response."""
        note = raw.get("note_card") or raw.get("model_type") and raw or raw
        note_id = str(raw.get("id") or raw.get("note_id") or note.get("note_id") or "")

        if not note_id and not note.get("display_title"):
            return None

        title = note.get("display_title", "")
        desc = note.get("desc", "")
        text = title or desc

        # Author
        user = note.get("user", {}) or raw.get("user", {})
        author_nickname = user.get("nickname", "")
        author_id = user.get("user_id") or user.get("userid", "")

        # Engagement
        interact = note.get("interact_info", {})

        # Media
        media_type = "image"
        if note.get("type") == "video":
            media_type = "video"

        cover = None
        cover_data = note.get("cover", {})
        if isinstance(cover_data, dict):
            cover_info = cover_data.get("info_list", [])
            if cover_info:
                cover = cover_info[0].get("url")

        # Tags
        hashtags = []
        for tag in note.get("tag_list", []):
            name = tag.get("name") or tag.get("title")
            if name:
                hashtags.append(name)

        url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""

        return SocialItem(
            platform=self.platform,
            post_id=note_id,
            url=url,
            author_username=str(author_id),
            author_display_name=author_nickname,
            author_profile_url=f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else "",
            text=text,
            likes=int(interact.get("liked_count", 0)) if interact.get("liked_count") else None,
            comments=int(interact.get("comment_count", 0)) if interact.get("comment_count") else None,
            collects=int(interact.get("collected_count", 0)) if interact.get("collected_count") else None,
            media_type=media_type,
            thumbnail_url=cover,
            hashtags=hashtags,
            region="CN",
            raw={"note_id": note_id},
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
