"""
Reddit Connector — uses PullPush.io (free, no auth).

Wraps the existing Reddit logic into the new connector interface.
"""

import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from social_scraper.base import (
    BaseConnector, ConnectorResult, SocialItem, SourceHealth,
)

USER_AGENT = "BountySocialAPI/1.0 (+https://bountyapi.com)"


class RedditConnector(BaseConnector):
    platform = "reddit"
    connector_name = "pullpush_free"

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        start = time.time()
        items = []
        error = None

        try:
            params = {
                "q": keyword,
                "size": min(count, 100),
                "sort": "score" if sort == "hot" else "desc",
            }
            lookback_days = {
                "1day": 1,
                "week": 7,
                "month": 30,
                "halfyear": 180,
            }.get(time_filter)
            if lookback_days:
                params["after"] = int(time.time() - lookback_days * 86400)

            url = "https://api.pullpush.io/reddit/search/submission/?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

            import asyncio
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: self._fetch_json(req))

            raw_posts = data.get("data", [])
            for post in raw_posts[:count]:
                item = self._parse_post(post)
                if item:
                    items.append(item)

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

    def _fetch_json(self, req):
        with urllib.request.urlopen(req, timeout=20) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(resp.read().decode(charset, errors="replace"))

    def _parse_post(self, post: dict) -> SocialItem:
        permalink = post.get("permalink", "")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"

        created = post.get("created_utc")
        created_at = None
        if created:
            created_at = datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat()

        return SocialItem(
            platform=self.platform,
            post_id=post.get("id", ""),
            url=permalink,
            author_username=post.get("author", ""),
            author_profile_url=f"https://www.reddit.com/user/{post.get('author', '')}" if post.get("author") else "",
            text=post.get("selftext", "") or post.get("title", ""),
            created_at=created_at,
            likes=post.get("score"),
            comments=post.get("num_comments"),
            media_type="text",
            hashtags=post.get("link_flair_text", "").split() if post.get("link_flair_text") else [],
            raw={"subreddit": post.get("subreddit"), "title": post.get("title")},
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
