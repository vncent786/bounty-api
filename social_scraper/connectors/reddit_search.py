"""Reddit post discovery through the Brave Search API.

This is a credentialed fallback for PullPush keyword discovery. It only accepts
canonical Reddit post URLs and deliberately does not invent engagement fields
that a web-search result cannot verify.
"""

import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth


BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
USER_AGENT = "BountySocialAPI/1.0 (+https://bountyapi.com)"
_REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"}
_POST_PATH = re.compile(
    r"^/r/(?P<subreddit>[A-Za-z0-9_]+)/comments/(?P<post_id>[A-Za-z0-9]+)/(?P<slug>[^/?#]*)/?$",
    re.IGNORECASE,
)
_FRESHNESS = {"1day": "pd", "week": "pw", "month": "pm"}


class RedditSearchConnector(BaseConnector):
    platform = "reddit"
    connector_name = "brave_reddit_discovery"

    def __init__(self, api_key: str = "", fetch_json: Optional[Callable] = None):
        self.api_key = (api_key or "").strip()
        self._fetch_json_override = fetch_json

    @staticmethod
    def _canonical_post(url: str):
        try:
            parsed = urllib.parse.urlsplit(url)
        except (TypeError, ValueError):
            return None
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _REDDIT_HOSTS:
            return None
        match = _POST_PATH.match(parsed.path)
        if not match:
            return None
        subreddit = match.group("subreddit")
        post_id = match.group("post_id").lower()
        slug = match.group("slug")
        canonical = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
        if slug:
            canonical += f"{slug}/"
        return canonical, subreddit, post_id

    def _fetch_json(self, request):
        if self._fetch_json_override:
            return self._fetch_json_override(request)
        with urllib.request.urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="replace"))

    async def search(
        self,
        keyword: str,
        count: int = 20,
        time_filter: str = "",
        sort: str = "",
        region: str = "",
    ) -> ConnectorResult:
        start = time.monotonic()
        items = []
        error = None

        if not self.api_key:
            error = "missing_api_key"
        else:
            params = {
                "q": f"site:reddit.com {keyword}",
                "count": min(max(count * 2, 10), 20),
                "safesearch": "moderate",
                "extra_snippets": "false",
            }
            freshness = _FRESHNESS.get(time_filter)
            if time_filter == "halfyear":
                today = datetime.now(timezone.utc).date()
                freshness = f"{today - timedelta(days=180)}to{today}"
            if freshness:
                params["freshness"] = freshness
            if region and re.fullmatch(r"[A-Za-z]{2}", region):
                params["country"] = region.upper()
            request = urllib.request.Request(
                BRAVE_SEARCH_URL + "?" + urllib.parse.urlencode(params),
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                    "X-Subscription-Token": self.api_key,
                },
            )
            try:
                payload = await asyncio.to_thread(self._fetch_json, request)
                seen = set()
                for result in (payload.get("web", {}) or {}).get("results", []):
                    canonical = self._canonical_post(result.get("url", ""))
                    if not canonical:
                        continue
                    url, subreddit, post_id = canonical
                    if post_id in seen:
                        continue
                    seen.add(post_id)
                    items.append(SocialItem(
                        platform=self.platform,
                        post_id=post_id,
                        url=url,
                        text=result.get("description", "") or result.get("title", ""),
                        media_type="text",
                        raw={
                            "subreddit": subreddit,
                            "title": result.get("title", ""),
                            "discovery_url": result.get("url", ""),
                        },
                    ))
                    if len(items) >= count:
                        break
            except Exception as exc:
                error = str(exc)

        latency = int((time.monotonic() - start) * 1000)
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="ok" if len(items) >= count else ("error" if error and not items else "partial"),
                items_returned=len(items),
                items_requested=count,
                latency_ms=latency,
                error=error,
            ),
        )

    async def health_check(self) -> SourceHealth:
        if not self.api_key:
            return SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="error",
                error="missing_api_key",
            )
        return (await self.search("technology", count=1, time_filter="week")).health
