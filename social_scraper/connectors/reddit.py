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
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord


def _reddit_external_id(value) -> str:
    value = str(value or "")
    return value.split("_", 1)[1] if "_" in value else value


def parse_reddit_json_thread(
    *, post_id: str, payload: list, max_comments: int, max_depth: int
) -> ThreadFetchResult:
    reported_total = None
    if payload and isinstance(payload[0], dict):
        posts = payload[0].get("data", {}).get("children", [])
        if posts:
            value = posts[0].get("data", {}).get("num_comments")
            reported_total = value if isinstance(value, int) else None
    records = []
    truncated = False

    def walk(children, depth):
        nonlocal truncated
        for child in children or []:
            if not isinstance(child, dict):
                continue
            if child.get("kind") == "more":
                if (child.get("data") or {}).get("count", 0):
                    truncated = True
                continue
            if child.get("kind") != "t1":
                continue
            data = child.get("data") or {}
            external_id = str(data.get("id") or "")
            if not external_id:
                continue
            if depth > max_depth or len(records) >= max_comments:
                truncated = True
                continue
            parent = _reddit_external_id(data.get("parent_id")) or post_id
            timestamp = data.get("created_utc")
            published_at = None
            if isinstance(timestamp, (int, float)):
                published_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            permalink = str(data.get("permalink") or "")
            if permalink and not permalink.startswith("http"):
                permalink = f"https://www.reddit.com{permalink}"
            records.append(ThreadRecord(
                platform="reddit", external_id=external_id,
                record_type="comment" if depth == 1 else "reply",
                parent_external_id=parent, root_post_external_id=post_id,
                depth=depth,
                text=data.get("body") if isinstance(data.get("body"), str) else None,
                author_external_id=(
                    str(data.get("author_fullname")) if data.get("author_fullname") else None
                ),
                author_username=(str(data.get("author")) if data.get("author") else None),
                url=permalink or None, published_at=published_at,
                likes=data.get("score") if isinstance(data.get("score"), int) else None,
                raw=data,
            ))
            replies = data.get("replies")
            if isinstance(replies, dict):
                walk(replies.get("data", {}).get("children", []), depth + 1)

    comment_children = []
    if len(payload) > 1 and isinstance(payload[1], dict):
        comment_children = payload[1].get("data", {}).get("children", [])
    walk(comment_children, 1)
    if isinstance(reported_total, int) and reported_total > len(records):
        truncated = True
    status = "empty" if not records and reported_total == 0 else "partial" if truncated else "complete"
    return ThreadFetchResult(
        platform="reddit", root_post_external_id=post_id, status=status,
        records=tuple(records), truncated=truncated,
        attempted_route="reddit_json", platform_reported_total=reported_total,
        max_comments=max_comments, max_depth=max_depth,
        limitations=(("More comments or deeper replies were not retrieved.",) if truncated else ()),
    )


def parse_reddit_hydrated_thread(
    *, post_id: str, hydrated: dict, max_comments: int, max_depth: int
) -> ThreadFetchResult:
    records = []
    truncated = bool(hydrated.get("truncation_reason"))
    for raw in hydrated.get("comments", []):
        external_id = _reddit_external_id(raw.get("id"))
        if not external_id:
            continue
        raw_depth = raw.get("depth")
        depth = raw_depth if isinstance(raw_depth, int) and raw_depth >= 1 else 1
        if depth > max_depth or len(records) >= max_comments:
            truncated = True
            continue
        parent = _reddit_external_id(raw.get("parent_id")) or post_id
        records.append(ThreadRecord(
            platform="reddit", external_id=external_id,
            record_type="comment" if depth == 1 else "reply",
            parent_external_id=parent, root_post_external_id=post_id,
            depth=depth,
            text=raw.get("text") if isinstance(raw.get("text"), str) else None,
            author_username=(str(raw.get("author")) if raw.get("author") else None),
            url=raw.get("url") if isinstance(raw.get("url"), str) else None,
            likes=raw.get("score") if isinstance(raw.get("score"), int) else None,
            raw=raw,
        ))
    reported_total = hydrated.get("platform_reported_total")
    if not isinstance(reported_total, int):
        reported_total = None
    if reported_total is not None and reported_total > len(records):
        truncated = True
    status = "empty" if not records and reported_total == 0 else "partial" if truncated else "complete"
    return ThreadFetchResult(
        platform="reddit", root_post_external_id=post_id, status=status,
        records=tuple(records), truncated=truncated,
        attempted_route="camoufox_rendered_thread",
        platform_reported_total=reported_total,
        max_comments=max_comments, max_depth=max_depth,
        limitations=((str(hydrated.get("truncation_reason")),) if truncated else ()),
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

    async def fetch_thread(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        if post.platform != "reddit" or not post.post_id or not post.url:
            return ThreadFetchResult(
                platform="reddit", root_post_external_id=post.post_id or "unknown",
                status="error", attempted_route="reddit_json",
                error_category="invalid_reddit_post", max_comments=max_comments,
                max_depth=max_depth,
            )
        try:
            from social_scraper.connectors.reddit_camoufox import validate_reddit_url
            canonical = validate_reddit_url(post.url)
        except Exception:
            return ThreadFetchResult(
                platform="reddit", root_post_external_id=post.post_id,
                status="error", attempted_route="reddit_json",
                error_category="invalid_reddit_post", max_comments=max_comments,
                max_depth=max_depth,
            )
        if max_comments <= 0 or max_depth <= 0:
            return ThreadFetchResult(
                platform="reddit", root_post_external_id=post.post_id,
                status="empty", attempted_route="reddit_json",
                max_comments=max_comments, max_depth=max_depth,
            )
        params = urllib.parse.urlencode({
            "raw_json": 1, "limit": min(max_comments, 100), "depth": max_depth,
            "sort": "confidence",
        })
        json_url = f"{canonical.rstrip('/')}.json?{params}"
        request = urllib.request.Request(json_url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json",
        })
        try:
            loop = asyncio.get_event_loop()
            payload = await loop.run_in_executor(None, lambda: self._fetch_json(request))
            if not isinstance(payload, list):
                raise ValueError("Reddit thread response was not a listing pair")
            return parse_reddit_json_thread(
                post_id=post.post_id, payload=payload,
                max_comments=max_comments, max_depth=max_depth,
            )
        except Exception:
            try:
                return await self._camoufox_fallback(post, max_comments, max_depth)
            except Exception:
                return ThreadFetchResult(
                    platform="reddit", root_post_external_id=post.post_id,
                    status="unavailable", attempted_route="reddit_json+camoufox",
                    error_category="reddit_thread_routes_failed",
                    max_comments=max_comments, max_depth=max_depth,
                    limitations=("Reddit JSON and rendered-page routes were unavailable.",),
                )

    async def _camoufox_fallback(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        from social_scraper.connectors.reddit_camoufox import hydrate_reddit_post

        hydrated = await asyncio.to_thread(hydrate_reddit_post, post.url, max_comments)
        return parse_reddit_hydrated_thread(
            post_id=post.post_id, hydrated=hydrated,
            max_comments=max_comments, max_depth=max_depth,
        )

    async def health_check(self) -> SourceHealth:
        result = await self.search(keyword="test", count=1)
        return result.health
