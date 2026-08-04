"""Scoped Reddit discovery through the free Arctic Shift archive API.

Arctic Shift keyword search requires a subreddit or author. This connector
therefore searches only an explicitly configured subreddit universe and must
never be represented as global Reddit coverage.
"""

import asyncio
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import weakref
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth


ARCTIC_SEARCH_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
USER_AGENT = "BountySocialAPI/1.0 (+https://bountyapi.com)"
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
POST_PATH_RE = re.compile(
    r"^/r/(?P<subreddit>[A-Za-z0-9_]{2,21})/comments/(?P<post_id>[A-Za-z0-9]+)/(?P<slug>[A-Za-z0-9_-]+)/?$",
    re.IGNORECASE,
)
_LOOKBACK_DAYS = {"1day": 1, "week": 7, "month": 30, "halfyear": 180}
_ARCTIC_GATES = weakref.WeakKeyDictionary()
_MAX_RESPONSE_BYTES = 2_000_000


class ArcticRateLimitError(RuntimeError):
    pass


def _gate_for_current_loop():
    loop = asyncio.get_running_loop()
    gate = _ARCTIC_GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(2)
        _ARCTIC_GATES[loop] = gate
    return gate


class RedditArcticConnector(BaseConnector):
    platform = "reddit"
    connector_name = "arctic_shift_scoped"

    def __init__(
        self,
        subreddits: Optional[list[str]] = None,
        fetch_json: Optional[Callable] = None,
        clock: Optional[Callable] = None,
        max_subreddits: int = 5,
    ):
        configured = os.getenv("BOUNTY_REDDIT_SUBREDDITS", "")
        selected = subreddits if subreddits is not None else configured.split(",")
        valid = [
            value.strip()
            for value in selected
            if isinstance(value, str) and SUBREDDIT_RE.fullmatch(value.strip())
        ]
        self.subreddits = list(dict.fromkeys(value.lower() for value in valid))[:max_subreddits]
        self._display_names = {value.lower(): value for value in valid}
        self._fetch_json_override = fetch_json
        self.clock = clock or time.time
        self.max_subreddits = max_subreddits

    def can_handle_options(self, options):
        if not options:
            return bool(self.subreddits)
        requested = options.get("subreddits", [])
        return bool([
            value for value in requested
            if isinstance(value, str) and SUBREDDIT_RE.fullmatch(value.strip())
        ])

    async def search_with_options(
        self, keyword, count, time_filter, sort, region, options
    ):
        scoped = RedditArcticConnector(
            subreddits=options.get("subreddits", []),
            fetch_json=self._fetch_json_override,
            clock=self.clock,
            max_subreddits=self.max_subreddits,
        )
        return await scoped.search(
            keyword,
            count,
            options.get("time_filter", time_filter),
            options.get("sort", sort),
            region,
        )

    def _coverage(self, successful_subreddits=None):
        requested = [
            self._display_names.get(value, value) for value in self.subreddits
        ]
        return {
            "kind": "configured_subreddits",
            "requested_subreddits": requested,
            "successful_subreddits": successful_subreddits or [],
            "global_coverage": False,
            "source_kind": "archive",
        }

    async def _fetch_json_async(self, client, url):
        async with _gate_for_current_loop():
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            ) as response:
                if response.status_code == 429:
                    raise ArcticRateLimitError("Arctic Shift rate limited")
                response.raise_for_status()
                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _MAX_RESPONSE_BYTES:
                        raise RuntimeError("Arctic Shift response exceeded size limit")
                    chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8", errors="replace"))

    async def _fetch_subreddit(self, client, subreddit, keyword, per_subreddit, after):
        params = {
            "subreddit": self._display_names.get(subreddit, subreddit),
            "query": keyword,
            "limit": per_subreddit,
            "sort": "desc",
        }
        if after is not None:
            params["after"] = after
        request = urllib.request.Request(
            ARCTIC_SEARCH_URL + "?" + urllib.parse.urlencode(params),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        if self._fetch_json_override:
            payload = await asyncio.to_thread(self._fetch_json_override, request)
        else:
            payload = await self._fetch_json_async(client, request.full_url)
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError("Arctic Shift query failed")
        data = payload.get("data", []) or []
        if not isinstance(data, list):
            raise RuntimeError("Arctic Shift returned invalid data")
        return data

    def _archive_time(self, value):
        if value is None or isinstance(value, bool):
            return None
        try:
            numeric = float(value)
            if not math.isfinite(numeric):
                return None
            if numeric < 1_104_537_600 or numeric > self.clock() + 86400:
                return None
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _metric(value, nonnegative=False):
        if type(value) is not int:
            return None
        if nonnegative and value < 0:
            return None
        return value

    def _parse_post(self, post):
        if not isinstance(post, dict):
            return None
        post_id = str(post.get("id") or "").lower()
        subreddit = str(post.get("subreddit") or "")
        permalink = str(post.get("permalink") or "")
        if not post_id or subreddit.lower() not in self.subreddits or permalink.startswith("http"):
            return None
        match = POST_PATH_RE.fullmatch(permalink)
        if not match:
            return None
        if (
            match.group("subreddit").lower() != subreddit.lower()
            or match.group("post_id").lower() != post_id
        ):
            return None
        canonical = (
            f"https://www.reddit.com/r/{match.group('subreddit')}/comments/"
            f"{post_id}/{match.group('slug')}/"
        )
        created_at = self._archive_time(post.get("created_utc"))
        observed_at = self._archive_time(post.get("retrieved_on"))
        if created_at and observed_at and observed_at < created_at:
            observed_at = None
        score = self._metric(post.get("score")) if observed_at else None
        comments = self._metric(post.get("num_comments"), nonnegative=True) if observed_at else None
        author = str(post.get("author") or "")
        valid_author = author if re.fullmatch(r"[A-Za-z0-9_-]{3,20}", author) else ""
        title = str(post.get("title") or "")
        body = str(post.get("selftext") or "")
        if post.get("is_self") is True:
            media_type = "text"
        elif post.get("is_gallery") is True:
            media_type = "gallery"
        elif post.get("post_hint") == "image":
            media_type = "image"
        elif post.get("is_video") is True or post.get("post_hint") == "hosted:video":
            media_type = "video"
        else:
            media_type = "unknown"
        return SocialItem(
            platform=self.platform,
            post_id=post_id,
            url=canonical,
            author_username=valid_author,
            author_profile_url=(
                f"https://www.reddit.com/user/{valid_author}" if valid_author else ""
            ),
            text=f"{title}\n\n{body}" if body else title,
            created_at=created_at,
            likes=score,
            comments=comments,
            media_type=media_type,
            raw={
                "subreddit": subreddit,
                "title": title,
                "flair": post.get("link_flair_text"),
                "source_observed_at": observed_at,
                "source_kind": "archive",
            },
        )

    def _error_result(self, count, error):
        return ConnectorResult(
            items=[],
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="error",
                items_requested=count,
                error=error,
                coverage=self._coverage(),
            ),
        )

    async def search(
        self,
        keyword: str,
        count: int = 20,
        time_filter: str = "",
        sort: str = "",
        region: str = "",
    ) -> ConnectorResult:
        started = time.monotonic()
        if not self.subreddits:
            return self._error_result(count, "missing_subreddit_scope")
        if time_filter not in {"", *_LOOKBACK_DAYS}:
            return self._error_result(count, "unsupported_time_filter")
        if sort not in {"", "latest"}:
            return self._error_result(count, "unsupported_sort")

        days = _LOOKBACK_DAYS.get(time_filter)
        after = int(self.clock() - days * 86400) if days else None
        per_subreddit = min(
            max(((count + len(self.subreddits) - 1) // len(self.subreddits)) * 2, 5),
            100,
        )
        if self._fetch_json_override:
            responses = await asyncio.gather(
                *(
                    self._fetch_subreddit(None, subreddit, keyword, per_subreddit, after)
                    for subreddit in self.subreddits
                ),
                return_exceptions=True,
            )
        else:
            timeout = httpx.Timeout(10.0, connect=3.0, read=8.0, write=5.0, pool=3.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                responses = await asyncio.gather(
                    *(
                        self._fetch_subreddit(client, subreddit, keyword, per_subreddit, after)
                        for subreddit in self.subreddits
                    ),
                    return_exceptions=True,
                )

        failures = sum(isinstance(response, Exception) for response in responses)
        successful_subreddits = [
            self._display_names.get(subreddit, subreddit)
            for subreddit, response in zip(self.subreddits, responses)
            if not isinstance(response, Exception)
        ]
        seen = set()
        items = []
        for response in responses:
            if isinstance(response, Exception):
                continue
            for post in response:
                item = self._parse_post(post)
                if item is None or item.post_id in seen:
                    continue
                seen.add(item.post_id)
                items.append(item)
        items.sort(key=lambda item: item.created_at or "", reverse=True)
        items = items[:count]

        if failures == len(responses):
            rate_limited = any(isinstance(response, ArcticRateLimitError) for response in responses)
            status = "error"
            error = "arctic_shift_rate_limited" if rate_limited else "arctic_shift_unavailable"
        elif failures or len(items) < count:
            status, error = "partial", None
        else:
            status, error = "ok", None
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status=status,
                items_returned=len(items),
                items_requested=count,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=error,
                coverage=self._coverage(successful_subreddits),
            ),
        )

    async def health_check(self) -> SourceHealth:
        if not self.subreddits:
            return SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="skipped",
                coverage=self._coverage(),
            )
        return (await self.search("test", count=1, time_filter="month")).health
