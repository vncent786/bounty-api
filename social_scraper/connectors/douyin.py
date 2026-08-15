"""
Douyin Connector — self-hosted using Evil0ctal's a_bogus signing engine.

Provides keyword search on Douyin (抖音) without any paid API dependency.
The signing algorithms (a_bogus, x_bogus) are vendored from Evil0ctal's
Apache-2.0-licensed Douyin_TikTok_Download_API.
"""

import asyncio
import sys
import os
import time
from urllib.parse import urlencode

# Add the vendored crawler engine to path
_engine_path = os.path.join(os.path.dirname(__file__), "..", "..", "crawlers")
_engine_path = os.path.abspath(_engine_path)
if _engine_path not in sys.path:
    sys.path.insert(0, _engine_path)

from crawlers.base_crawler import BaseCrawler
from crawlers.douyin.web.endpoints import DouyinAPIEndpoints
from crawlers.douyin.web.utils import BogusManager

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)

import yaml

# Load config
_config_path = os.path.join(_engine_path, "douyin", "web", "config.yaml")
with open(_config_path, "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

_dy_config = _config["TokenManager"]["douyin"]


class DouyinConnector(BaseConnector):
    platform = "douyin"
    connector_name = "owned_evil0ctal"

    async def _get_headers(self):
        headers_cfg = _dy_config["headers"]
        return {
            "headers": {
                "Accept-Language": headers_cfg["Accept-Language"],
                "User-Agent": headers_cfg["User-Agent"],
                "Referer": headers_cfg["Referer"],
                "Cookie": headers_cfg.get("Cookie", ""),
            },
            "proxies": {
                "http://": _dy_config["proxies"]["http"] or None,
                "https://": _dy_config["proxies"]["https"] or None,
            },
        }

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        try:
            kwargs = await self._get_headers()
            base_crawler = BaseCrawler(
                proxies=kwargs["proxies"],
                crawler_headers=kwargs["headers"],
            )

            # Build search params for Douyin general search
            params = {
                "keyword": keyword,
                "search_channel": "aweme_general",
                "sort_type": 0,       # 0=general, 1=latest, 2=hottest
                "publish_time": 0,    # 0=all, 1=1day, 7=week, 180=halfyear
                "offset": 0,
                "count": min(count, 20),  # Douyin caps at ~20 per page
                "search_source": "normal_search",
                "query_correct_type": "1",
                "is_filter_search": 0,
                "from_page_id": 0,
                "msToken": "",
            }

            # Apply sort
            if sort == "latest":
                params["sort_type"] = 1
            elif sort == "hot":
                params["sort_type"] = 2

            # Apply time filter
            if time_filter == "1day":
                params["publish_time"] = 1
                params["is_filter_search"] = 1
            elif time_filter == "week":
                params["publish_time"] = 7
                params["is_filter_search"] = 1
            elif time_filter == "halfyear":
                params["publish_time"] = 180
                params["is_filter_search"] = 1

            async with base_crawler as crawler:
                # Sign with a_bogus
                a_bogus = BogusManager.ab_model_2_endpoint(
                    params, kwargs["headers"]["User-Agent"]
                )
                endpoint = (
                    f"{DouyinAPIEndpoints.GENERAL_SEARCH}"
                    f"?{urlencode(params)}&a_bogus={a_bogus}"
                )

                response = await crawler.fetch_get_json(endpoint)

            # Parse response
            raw_items = []
            if isinstance(response, dict):
                raw_items = response.get("data", [])

            for raw in raw_items:
                try:
                    item = self._parse_item(raw)
                    if item:
                        items.append(item)
                except Exception:
                    pass  # skip malformed items

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
        """Parse a raw Douyin search result into normalized SocialItem."""
        aweme = raw.get("aweme_info") or raw.get("video") or raw
        if not aweme or not aweme.get("aweme_id"):
            return None

        aweme_id = aweme.get("aweme_id", "")
        desc = aweme.get("desc", "")
        create_time = aweme.get("create_time")

        # Author
        author = aweme.get("author", {})
        author_sec_uid = author.get("sec_uid", "")
        author_nickname = author.get("nickname", "")
        author_unique_id = author.get("unique_id", "")

        # Engagement
        statistics = aweme.get("statistics", {})
        collect_count = statistics.get("collect_count")
        engagement_sources = (
            {
                "collects": "collect_count",
                "bookmarks": "collect_count",
            }
            if collect_count is not None
            else {}
        )

        # Media
        video = aweme.get("video", {})
        media_type = "video" if video else "image"
        cover = None
        if video:
            cover_url_data = video.get("cover", {})
            cover = cover_url_data.get("url_list", [None])[0] if cover_url_data else None

        # Hashtags
        hashtags = []
        text_extra = aweme.get("text_extra", [])
        for te in text_extra:
            if te.get("hashtag_name"):
                hashtags.append(te["hashtag_name"])

        # URL
        if author_unique_id:
            url = f"https://www.douyin.com/video/{aweme_id}"
        else:
            url = f"https://www.douyin.com/video/{aweme_id}"

        # Created at
        created_at = None
        if create_time:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()

        return SocialItem(
            platform=self.platform,
            post_id=aweme_id,
            url=url,
            author_username=author_unique_id or author_sec_uid[:20],
            author_display_name=author_nickname,
            author_profile_url=f"https://www.douyin.com/user/{author_sec_uid}" if author_sec_uid else "",
            text=desc,
            created_at=created_at,
            views=statistics.get("play_count"),
            likes=statistics.get("digg_count"),
            comments=statistics.get("comment_count"),
            shares=statistics.get("share_count"),
            collects=collect_count,
            bookmarks=collect_count,
            media_type=media_type,
            thumbnail_url=cover,
            hashtags=hashtags,
            region="CN",
            raw={
                "aweme_id": aweme_id,
                "engagement_sources": engagement_sources,
            },
        )

    async def health_check(self) -> SourceHealth:
        """Quick probe — try a minimal search."""
        result = await self.search(keyword="test", count=1)
        return result.health
