"""
TikTok production scraper — authenticated Chrome profile + DOM extraction.

Works because:
1. One-time login via real Chrome (no automation flags)
2. Session cookies persist in .browser_profiles/tiktok_real
3. Proxy extension handles Geonode auth
4. Stealth masks Playwright automation
5. DOM extraction is more resilient than API interception
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)

try:
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


PROFILE = str(PROJECT / ".browser_profiles" / "tiktok_real")
EXTENSION = str(PROJECT / ".browser_profiles" / "tiktok_proxy_ext")


# JS that extracts all video data from TikTok's DOM
_EXTRACT_JS = """
() => {
    const items = [];

    // TikTok renders search results as <div data-e2e="search_video-item">
    // or within article/a tags containing video links
    const selectors = [
        '[data-e2e="search_video-item"]',
        '[data-e2e="search_search-item"]',
        'div[class*="DivItemContainer"]',
        'a[href*="/video/"]',
    ];

    const seen = new Set();

    for (const sel of selectors) {
        const elements = document.querySelectorAll(sel);
        for (const el of elements) {
            try {
                // Get video link
                const link = el.matches('a[href*="/video/"]')
                    ? el
                    : el.querySelector('a[href*="/video/"]');

                if (!link) continue;
                const href = link.getAttribute('href') || '';
                const match = href.match('/video/(\\d+)');
                if (!match) continue;

                const videoId = match[1];
                if (seen.has(videoId)) continue;
                seen.add(videoId);

                // Get author from href
                const authorMatch = href.match('/@([^/]+)/video/');

                // Get caption text
                const captionEl = el.querySelector(
                    '[class*="Title"], [class*="Caption"], [class*="Desc"], ' +
                    'a[class*="Title"], div[class*="title"]'
                );
                let caption = '';
                if (captionEl) caption = captionEl.textContent.trim();

                // Get views/likes
                const statsEls = el.querySelectorAll(
                    '[class*="Count"], [class*="Views"], [class*="Likes"], ' +
                    'strong, span[class*="text"]'
                );
                let viewsText = '';
                let likesText = '';
                for (const s of statsEls) {
                    const txt = s.textContent.trim();
                    if (txt.match(/[.\d]+[KMB]?/)) {
                        if (!viewsText) viewsText = txt;
                        else if (!likesText) likesText = txt;
                    }
                }

                // Get author display name
                const authorEl = el.querySelector(
                    '[class*="Author"], [class*="Nickname"], [class*="User"]'
                );
                let authorName = '';
                if (authorEl) authorName = authorEl.textContent.trim();

                // Get thumbnail
                const img = el.querySelector('img');
                let thumbnail = '';
                if (img) thumbnail = img.getAttribute('src') || '';

                items.push({
                    id: videoId,
                    url: href.startsWith('http') ? href : 'https://www.tiktok.com' + href,
                    author: authorMatch ? authorMatch[1] : authorName,
                    author_display: authorName,
                    caption: caption,
                    views_text: viewsText,
                    likes_text: likesText,
                    thumbnail: thumbnail,
                });
            } catch (e) {}
        }
    }

    return items;
}
"""


def parse_count(text: str) -> int | None:
    """Convert '186.1K', '1.2M', '54.2K' to integers."""
    if not text:
        return None
    text = text.strip().replace(',', '').replace(' ', '')
    multipliers = {'K': 1000, 'M': 1_000_000, 'B': 1_000_000_000}
    try:
        if text[-1] in multipliers:
            return int(float(text[:-1]) * multipliers[text[-1]])
        return int(float(text))
    except (ValueError, IndexError):
        return None


class TikTokAuthConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "authenticated"

    async def search(self, keyword: str, count: int = 12, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        if not PLAYWRIGHT_AVAILABLE:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="error", error="Playwright not installed",
                ),
            )

        # Also intercept API responses as a secondary data source
        api_responses = []

        playwright = None
        context = None
        try:
            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                PROFILE,
                channel="chrome",
                headless=False,
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1280, "height": 800},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=-32000,-32000",
                    f"--load-extension={EXTENSION}",
                    f"--disable-extensions-except={EXTENSION}",
                ],
            )
            await Stealth().apply_stealth_async(context)

            page = context.pages[0] if context.pages else await context.new_page()

            # Intercept API responses for richer data
            async def on_response(resp):
                url = resp.url
                if "/api/search/" in url and resp.status == 200:
                    try:
                        text = await resp.text()
                        if text and len(text) > 100:
                            api_responses.append(text)
                    except Exception:
                        pass

            page.on("response", on_response)

            # Build search URL
            search_url = f"https://www.tiktok.com/search?q={quote(keyword)}"
            if sort == "latest":
                search_url += "&type=video&t=1"
            elif sort == "hot":
                search_url += "&type=video"

            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(12000)

            # Scroll to load more
            for _ in range(max(count // 6, 2)):
                await page.evaluate("window.scrollBy(0, 800)")
                await page.wait_for_timeout(2500)

            # Strategy 1: Try API interception first (richest data)
            for raw_text in api_responses:
                try:
                    data = json.loads(raw_text)
                    raw_items = data.get("data", []) or data.get("itemList", [])
                    for raw in raw_items:
                        item = self._parse_api_item(raw)
                        if item:
                            items.append(item)
                except Exception:
                    pass

            # Strategy 2: Fall back to DOM extraction
            if not items:
                dom_items = await page.evaluate(_EXTRACT_JS)
                for d in dom_items[:count]:
                    items.append(SocialItem(
                        platform=self.platform,
                        post_id=d.get("id", ""),
                        url=d.get("url", ""),
                        author_username=d.get("author", ""),
                        author_display_name=d.get("author_display", ""),
                        text=d.get("caption", ""),
                        views=parse_count(d.get("views_text")),
                        likes=parse_count(d.get("likes_text")),
                        thumbnail_url=d.get("thumbnail"),
                        media_type="video",
                    ))

        except Exception as e:
            error = str(e)
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass

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

    def _parse_api_item(self, raw: dict) -> SocialItem | None:
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

        author_handle = author.get("unique_id") or author.get("uniqueId", "")

        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=f"https://www.tiktok.com/@{author_handle}/video/{aweme_id}" if author_handle else "",
            author_username=author_handle,
            author_display_name=author.get("nickname", ""),
            author_profile_url=f"https://www.tiktok.com/@{author_handle}" if author_handle else "",
            text=desc,
            created_at=created_at,
            views=stats.get("play_count") or stats.get("playCount"),
            likes=stats.get("digg_count") or stats.get("diggCount"),
            comments=stats.get("comment_count") or stats.get("commentCount"),
            shares=stats.get("share_count") or stats.get("shareCount"),
            media_type="video" if video else "text",
            thumbnail_url=thumbnail,
            hashtags=hashtags,
            region=aweme.get("region", ""),
            raw={"aweme_id": aweme_id},
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
