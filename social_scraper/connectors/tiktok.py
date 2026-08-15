"""
TikTok Connector — self-hosted using Evil0ctal's X-Bogus signing engine.

Provides keyword search on TikTok without any paid API dependency.
"""

import asyncio
import sys
import os
import time
import json
from urllib.parse import urlencode, quote

_engine_path = os.path.join(os.path.dirname(__file__), "..", "..", "crawlers")
_engine_path = os.path.abspath(_engine_path)
if _engine_path not in sys.path:
    sys.path.insert(0, _engine_path)

from crawlers.base_crawler import BaseCrawler
from crawlers.tiktok.web.endpoints import TikTokAPIEndpoints
from crawlers.tiktok.web.utils import BogusManager as TikTokBogusManager

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)

import yaml

_config_path = os.path.join(_engine_path, "tiktok", "web", "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

_tt_config = _config["TokenManager"]["tiktok"]

# TikTok search endpoints (not in the OSS endpoints.py, but known and working)
TIKTOK_SEARCH_GENERAL = f"{TikTokAPIEndpoints.TIKTOK_DOMAIN}/api/search/general/full/"
TIKTOK_SEARCH_VIDEO = f"{TikTokAPIEndpoints.TIKTOK_DOMAIN}/api/search/item/full/"


class TikTokConnector(BaseConnector):
    platform = "tiktok"
    connector_name = "owned_evil0ctal"

    async def _get_headers(self):
        headers_cfg = _tt_config["headers"]
        return {
            "headers": {
                "Accept-Language": headers_cfg.get("Accept-Language", "en-US,en;q=0.9"),
                "User-Agent": headers_cfg.get("User-Agent", ""),
                "Referer": headers_cfg.get("Referer", "https://www.tiktok.com/"),
                "Cookie": headers_cfg.get("Cookie", ""),
            },
            "proxies": {
                "http://": _tt_config["proxies"]["http"] or None,
                "https://": _tt_config["proxies"]["https"] or None,
            },
        }

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "US") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        try:
            kwargs = await self._get_headers()
            base_crawler = BaseCrawler(
                proxies=kwargs["proxies"],
                crawler_headers=kwargs["headers"],
            )

            # Build TikTok search params
            params = {
                "keyword": keyword,
                "offset": 0,
                "count": min(count, 20),
                "search_id": "",
                "use_account_context": "true",
                "from_page": "search",
                "web_search_code": json.dumps({
                    "search_id_param": "",
                    "search_keyword": keyword,
                    "search_source": "normal_search"
                }),
                "query_source": "search_sug",
                "sort_type": 0,  # 0=relevance, 1=latest
                "publish_time": 0,  # 0=all
                "visibility_filter_public": "true",
                "region": region or "US",
            }

            if sort == "latest":
                params["sort_type"] = 1

            if time_filter == "1day":
                params["publish_time"] = 86400
            elif time_filter == "week":
                params["publish_time"] = 604800
            elif time_filter == "month":
                params["publish_time"] = 2592000

            async with base_crawler as crawler:
                # Sign with X-Bogus (TikTok uses X-Bogus, not a_bogus)
                response = await crawler.fetch_get_json(
                    TikTokBogusManager.model_2_endpoint(
                        TIKTOK_SEARCH_VIDEO,
                        params,
                        kwargs["headers"]["User-Agent"],
                    )
                )

            # Parse response
            raw_items = []
            if isinstance(response, dict):
                raw_items = response.get("data", [])
                if not raw_items:
                    raw_items = response.get("aweme_list", [])
                    if not raw_items and response.get("itemList"):
                        raw_items = response.get("itemList", [])

            for raw in raw_items:
                try:
                    item = self._parse_item(raw)
                    if item:
                        items.append(item)
                except Exception:
                    pass

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

    def _parse_item(self, raw: dict) -> SocialItem:
        """Parse raw TikTok search result into normalized SocialItem."""
        aweme = raw.get("aweme_info") or raw.get("item") or raw
        if not aweme or not aweme.get("id") and not aweme.get("aweme_id"):
            return None

        aweme_id = aweme.get("id") or aweme.get("aweme_id", "")
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
        media_type = "video" if video else "image"
        cover = None
        if video:
            cover_data = video.get("cover", {})
            if isinstance(cover_data, dict):
                cover = cover_data.get("url") or (
                    cover_data.get("url_list", [None])[0] if cover_data.get("url_list") else None
                )
            elif isinstance(cover_data, str):
                cover = cover_data

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

        created_at = None
        if create_time:
            from datetime import datetime, timezone
            try:
                created_at = datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass

        return SocialItem(
            platform=self.platform,
            post_id=str(aweme_id),
            url=url,
            author_username=author_unique_id,
            author_display_name=author_nickname,
            author_profile_url=f"https://www.tiktok.com/@{author_unique_id}" if author_unique_id else "",
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
            region=aweme.get("region") or "US",
            raw={
                "aweme_id": str(aweme_id),
                "engagement_sources": engagement_sources,
            },
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
