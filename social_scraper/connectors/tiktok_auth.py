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
from urllib.parse import parse_qs, quote, urlsplit

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
from social_scraper.owned_worker_lock import AsyncFileLock

try:
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


PROFILE = str(PROJECT / ".browser_profiles" / "tiktok_real")
EXTENSION = str(PROJECT / ".browser_profiles" / "tiktok_proxy_ext")
_PROFILE_LOCK_PATH = PROJECT / ".browser_profiles" / "tiktok_real.lock"


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

                // Preserve an exact machine-readable timestamp when TikTok exposes one.
                const timeEl = el.querySelector('time[datetime]');
                const createdAt = timeEl ? (timeEl.getAttribute('datetime') || '') : '';

                items.push({
                    id: videoId,
                    url: href.startsWith('http') ? href : 'https://www.tiktok.com' + href,
                    author: authorMatch ? authorMatch[1] : authorName,
                    author_display: authorName,
                    caption: caption,
                    views_text: viewsText,
                    likes_text: likesText,
                    thumbnail: thumbnail,
                    created_at: createdAt,
                });
            } catch (e) {}
        }
    }

    return items;
}
"""


def _root_record_budget(max_comments: int, max_depth: int) -> int:
    maximum = max(0, int(max_comments))
    if max_depth < 2:
        return maximum
    return max(1, (maximum * 2) // 3)


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


def _created_sort_key(item: SocialItem) -> float:
    value = str(item.created_at or "").strip()
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _dom_social_items(dom_items: list[dict]) -> list[SocialItem]:
    """Parse every captured DOM card; caller applies dedup/sort/count afterward."""
    values = []
    for item in dom_items:
        values.append(SocialItem(
            platform="tiktok",
            post_id=str(item.get("id") or ""),
            url=str(item.get("url") or ""),
            author_username=str(item.get("author") or ""),
            author_display_name=str(item.get("author_display") or ""),
            text=str(item.get("caption") or ""),
            created_at=item.get("created_at") or None,
            views=parse_count(item.get("views_text")),
            likes=parse_count(item.get("likes_text")),
            thumbnail_url=item.get("thumbnail") or None,
            media_type="video",
        ))
    return values


def _finalize_items(
    items: list[SocialItem], *, count: int, sort: str,
) -> tuple[list[SocialItem], int]:
    """Deduplicate and sort the full captured page before applying caller count."""
    parsed_count = len(items)
    deduped: dict[str, SocialItem] = {}
    for item in items:
        key = str(item.post_id or item.url or "").strip()
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None or _created_sort_key(item) > _created_sort_key(existing):
            deduped[key] = item
    values = list(deduped.values())
    if sort == "latest":
        values.sort(key=_created_sort_key, reverse=True)
    elif sort == "hot":
        values.sort(
            key=lambda item: (
                int(item.likes or 0) + int(item.comments or 0),
                int(item.views or 0),
            ),
            reverse=True,
        )
    return values[:max(0, int(count))], parsed_count


class TikTokAuthConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "authenticated"

    async def search(self, keyword: str, count: int = 12, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        async with AsyncFileLock(_PROFILE_LOCK_PATH):
            return await self._search_unlocked(
                keyword, count, time_filter, sort, region
            )

    async def _search_unlocked(
        self, keyword: str, count: int = 12, time_filter: str = "",
        sort: str = "", region: str = "",
    ) -> ConnectorResult:
        start = time.time()
        items = []
        raw_records = []
        error = None
        exception_type = None
        api_payload_count = 0
        dom_item_count = 0
        page_state = "starting"

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
                    api_payload_count += 1
                    raw_records.append({
                        "source_id": f"tiktok_search_api:{len(raw_records) + 1}",
                        "payload_format": "json",
                        "payload": data,
                    })
                    raw_items = data.get("data", []) or data.get("itemList", [])
                    for raw in raw_items:
                        item = self._parse_api_item(raw)
                        if item:
                            items.append(item)
                except Exception:
                    pass

            if items:
                page_state = "api_results"

            # Strategy 2: Fall back to DOM extraction
            if not items:
                dom_items = await page.evaluate(_EXTRACT_JS)
                dom_item_count = len(dom_items)
                raw_records.append({
                    "source_id": "tiktok_search_dom:1",
                    "payload_format": "json",
                    "payload": {"items": dom_items},
                })
                items.extend(_dom_social_items(dom_items))
                if items:
                    page_state = "dom_results"
                else:
                    flags = await page.evaluate("""
                        () => {
                            const text = (document.body?.innerText || '').toLowerCase();
                            return {
                                login: location.pathname.startsWith('/login'),
                                challenge: text.includes('verify to continue') || text.includes('captcha'),
                                noResults: text.includes('no results'),
                            };
                        }
                    """)
                    if flags.get("login"):
                        page_state = "auth_required"
                        error = "tiktok_auth_required"
                    elif flags.get("challenge"):
                        page_state = "verification_challenge"
                        error = "tiktok_verification_challenge"
                    elif flags.get("noResults"):
                        page_state = "query_empty"
                        error = "tiktok_query_empty"
                    else:
                        page_state = "empty_response"
                        error = "tiktok_empty_response"

        except Exception as e:
            exception_type = type(e).__name__
            message = str(e).casefold()
            if isinstance(e, (TimeoutError, asyncio.TimeoutError)) or "timeout" in message:
                error = "tiktok_timeout"
            elif "profile" in message and ("use" in message or "lock" in message):
                error = "tiktok_profile_busy"
            elif any(value in message for value in ("proxy", "net::", "connection")):
                error = "tiktok_network_error"
            else:
                error = "tiktok_error"
            page_state = "exception"
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
        final_items, parsed_item_count = _finalize_items(
            items, count=count, sort=sort
        )
        health_status = (
            "ok"
            if final_items
            else "partial" if error == "tiktok_query_empty" else "error"
        )
        return ConnectorResult(
            items=final_items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status=health_status,
                items_returned=len(final_items),
                items_requested=count,
                latency_ms=latency,
                error=error,
                coverage={
                    "page_state": page_state,
                    "api_responses_seen": len(api_responses),
                    "api_payloads_parsed": api_payload_count,
                    "dom_items_seen": dom_item_count,
                    "parsed_items_before_dedup": parsed_item_count,
                    "returned_after_sort": len(final_items),
                    "sort_requested": sort or None,
                    "time_filter_requested": time_filter or None,
                    "exception_type": exception_type,
                },
            ),
            raw_records=raw_records,
        )

    async def fetch_thread(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        if post.platform != "tiktok" or not post.post_id or not post.url:
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=post.post_id or "unknown",
                status="error",
                attempted_route="tiktok_authenticated_browser_comments",
                error_category="invalid_tiktok_post",
                max_comments=max_comments,
                max_depth=max_depth,
            )
        if max_comments <= 0 or max_depth <= 0:
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=post.post_id,
                status="empty",
                attempted_route="tiktok_authenticated_browser_comments",
                max_comments=max_comments,
                max_depth=max_depth,
            )
        try:
            async with AsyncFileLock(_PROFILE_LOCK_PATH):
                collected = await self._collect_thread_payloads(
                    post, max_comments, max_depth
                )
        except Exception:
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route="tiktok_authenticated_browser_comments",
                error_category="tiktok_comments_unavailable",
                max_comments=max_comments,
                max_depth=max_depth,
                limitations=("Authenticated TikTok browser comment route failed.",),
            )

        root_payloads = collected.get("root_payloads") or []
        reply_payloads = collected.get("reply_payloads") or []
        if not root_payloads:
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route="tiktok_authenticated_browser_comments",
                error_category="tiktok_comments_not_returned",
                max_comments=max_comments,
                max_depth=max_depth,
                limitations=tuple(collected.get("limitations") or ()),
            )

        records = []
        seen = set()
        reported_total = None
        source_has_more = False
        reply_totals = {}
        root_budget = _root_record_budget(max_comments, max_depth)
        for payload in root_payloads:
            total = payload.get("total")
            if isinstance(total, int):
                reported_total = max(reported_total or 0, total)
            source_has_more = source_has_more or bool(payload.get("has_more"))
            for comment in payload.get("comments") or []:
                if len(records) >= root_budget:
                    break
                record = self._thread_record(
                    comment,
                    root_post_id=post.post_id,
                    parent_id=post.post_id,
                    depth=1,
                )
                if record is None or record.external_id in seen:
                    continue
                seen.add(record.external_id)
                records.append(record)
                reply_total = comment.get("reply_comment_total")
                if isinstance(reply_total, int) and reply_total > 0:
                    reply_totals[record.external_id] = reply_total

        returned_replies = {}
        root_ids = {record.external_id for record in records if record.depth == 1}
        if max_depth >= 2 and len(records) < max_comments:
            for reply_entry in reply_payloads:
                parent_id = str(reply_entry.get("parent_comment_id") or "")
                if not parent_id or parent_id not in root_ids:
                    continue
                payload = reply_entry.get("payload") or {}
                source_has_more = source_has_more or bool(payload.get("has_more"))
                for comment in payload.get("comments") or []:
                    if len(records) >= max_comments:
                        break
                    record = self._thread_record(
                        comment,
                        root_post_id=post.post_id,
                        parent_id=parent_id or post.post_id,
                        depth=2,
                    )
                    if record is None or record.external_id in seen:
                        continue
                    seen.add(record.external_id)
                    records.append(record)
                    returned_replies[parent_id] = returned_replies.get(parent_id, 0) + 1

        omitted_replies = any(
            returned_replies.get(parent_id, 0) < total
            for parent_id, total in reply_totals.items()
        )
        unknown_total = reported_total is None
        truncated = (
            unknown_total
            or source_has_more
            or omitted_replies
            or (reported_total is not None and reported_total > len([
                record for record in records if record.depth == 1
            ]))
            or len(records) >= max_comments
        )
        status = (
            "empty"
            if not records and reported_total == 0 and not truncated
            else "partial" if truncated else "complete"
        )
        return ThreadFetchResult(
            platform="tiktok",
            root_post_external_id=post.post_id,
            status=status,
            records=tuple(records),
            truncated=truncated,
            attempted_route="tiktok_authenticated_browser_comments",
            platform_reported_total=reported_total,
            max_comments=max_comments,
            max_depth=max_depth,
            limitations=tuple(collected.get("limitations") or ()),
        )

    async def _collect_thread_payloads(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> dict:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("playwright_not_installed")
        root_payloads = []
        reply_payloads = []
        seen_root_cursors = set()
        seen_reply_keys = set()
        first_root = asyncio.Event()

        playwright = await async_playwright().start()
        context = None
        try:
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

            async def on_response(response):
                if "/api/comment/list" not in response.url or response.status != 200:
                    return
                try:
                    payload = await response.json()
                except Exception:
                    return
                query = parse_qs(urlsplit(response.url).query)
                if "/reply/" in response.url:
                    parent_id = str((query.get("comment_id") or [""])[0])
                    key = (parent_id, payload.get("cursor"))
                    if key in seen_reply_keys:
                        return
                    seen_reply_keys.add(key)
                    reply_payloads.append({
                        "parent_comment_id": parent_id,
                        "payload": payload,
                    })
                else:
                    key = payload.get("cursor")
                    if key in seen_root_cursors:
                        return
                    seen_root_cursors.add(key)
                    root_payloads.append(payload)
                    first_root.set()

            page.on("response", on_response)
            await page.goto(post.url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(10000)
            comments_button = page.locator('[data-e2e="comment-icon"]:visible').first
            if await comments_button.count():
                await comments_button.click(force=True)
            await asyncio.wait_for(first_root.wait(), timeout=25)
            await page.wait_for_timeout(2500)

            root_budget = _root_record_budget(max_comments, max_depth)
            page_budget = max(1, (root_budget + 19) // 20)
            for _ in range(page_budget - 1):
                scrolled = await page.evaluate("""
                    () => {
                        let node = document.querySelector('[data-e2e="comment-level-1"]');
                        while (node) {
                            if (node.scrollHeight > node.clientHeight + 20) {
                                node.scrollTop = node.scrollHeight;
                                return true;
                            }
                            node = node.parentElement;
                        }
                        return false;
                    }
                """)
                if not scrolled:
                    break
                before = len(root_payloads)
                await page.wait_for_timeout(3500)
                if len(root_payloads) == before:
                    break

            if max_depth >= 2:
                reply_buttons = page.locator('text=/View .* repl/i')
                reply_parent_budget = max(5, min(8, max_comments // 8))
                button_count = min(
                    await reply_buttons.count(), reply_parent_budget
                )
                for index in range(button_count):
                    try:
                        await reply_buttons.nth(index).click(force=True)
                        await page.wait_for_timeout(1800)
                    except Exception:
                        continue
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            await playwright.stop()

        return {
            "root_payloads": root_payloads,
            "reply_payloads": reply_payloads,
            "limitations": [
                "TikTok web returns ranked comments; bounded reads may omit lower-ranked comments and replies."
            ],
        }

    @staticmethod
    def _thread_record(
        comment: dict, *, root_post_id: str, parent_id: str, depth: int
    ) -> ThreadRecord | None:
        comment_id = str(comment.get("cid") or comment.get("id") or "")
        if not comment_id:
            return None
        user = comment.get("user") or {}
        timestamp = comment.get("create_time") or comment.get("createTime")
        published_at = None
        if timestamp:
            try:
                published_at = datetime.fromtimestamp(
                    int(timestamp), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                pass
        likes = comment.get("digg_count")
        if not isinstance(likes, int) or isinstance(likes, bool):
            likes = None
        return ThreadRecord(
            platform="tiktok",
            external_id=comment_id,
            record_type="comment" if depth == 1 else "reply",
            parent_external_id=parent_id,
            root_post_external_id=root_post_id,
            depth=depth,
            text=comment.get("text"),
            author_external_id=str(
                user.get("uid") or user.get("sec_uid") or user.get("id") or ""
            ) or None,
            author_username=(
                user.get("unique_id") or user.get("uniqueId") or user.get("nickname")
            ),
            url=f"https://www.tiktok.com/i18n/share/comment/{comment_id}",
            published_at=published_at,
            likes=likes,
            raw=comment,
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
            raw={"aweme_id": aweme_id, "source_payload": aweme},
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
