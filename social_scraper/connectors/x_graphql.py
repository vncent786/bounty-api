"""Self-hosted X/Twitter connector using Scweet as the underlying engine.

Scweet (MIT, open-source) replays X's internal GraphQL SearchTimeline endpoint
using browser cookies (auth_token + ct0) with proper client-transaction-id signing.
Same pattern as yt-dlp for YouTube — open-source library handles the platform's
anti-bot measures, we wrap it in our BaseConnector interface.

Cost: $0 + one free X account for cookie auth.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth

logger = logging.getLogger(__name__)

try:
    from Scweet import Scweet
    SCWEET_AVAILABLE = True
except ImportError:
    Scweet = None
    SCWEET_AVAILABLE = False


class XConnector(BaseConnector):
    """X/Twitter connector using Scweet (cookie-based GraphQL replay)."""

    platform = "x"
    connector_name = "x_scweet"

    def __init__(self):
        self._client = None

    def _get_auth_token(self):
        return os.getenv("BOUNTY_X_AUTH_TOKEN", "").strip()

    def _ensure_client(self):
        if self._client is not None:
            return self._client

        if not SCWEET_AVAILABLE:
            raise RuntimeError("scweet_not_installed")

        auth_token = self._get_auth_token()
        if not auth_token:
            raise RuntimeError(
                "x_credentials_missing: set BOUNTY_X_AUTH_TOKEN"
            )

        self._client = Scweet(auth_token=auth_token)
        return self._client

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search X for tweets matching keyword."""
        start = time.time()

        try:
            client = self._ensure_client()
        except RuntimeError as e:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x", connector=self.connector_name,
                    status="error", items_requested=count, error=str(e),
                ),
            )

        try:
            # Scweet search is sync — run in thread
            def _do_search():
                return client.search(keyword, limit=count)

            raw_tweets = await asyncio.to_thread(_do_search)

            items = []
            for tweet_data in raw_tweets:
                item = self._parse_tweet(tweet_data)
                if item:
                    items.append(item)

            latency_ms = int((time.time() - start) * 1000)

            return ConnectorResult(
                items=items[:count],
                health=SourceHealth(
                    platform="x", connector=self.connector_name,
                    status="ok" if items else "partial",
                    items_returned=len(items), items_requested=count,
                    latency_ms=latency_ms,
                ),
            )

        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str:
                error_code = "x_rate_limited"
            elif "unauthorized" in error_str or "401" in error_str:
                error_code = "x_auth_expired"
            elif "forbidden" in error_str or "403" in error_str:
                error_code = "x_forbidden"
            else:
                error_code = "x_error"

            latency_ms = int((time.time() - start) * 1000)
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x", connector=self.connector_name,
                    status="error", items_requested=count,
                    latency_ms=latency_ms, error=error_code,
                ),
            )

    @staticmethod
    def _parse_tweet(tweet_data):
        """Parse a Scweet tweet dict into a SocialItem."""
        if not isinstance(tweet_data, dict):
            return None

        tweet_id = str(tweet_data.get("tweet_id", ""))
        if not tweet_id:
            return None

        text = tweet_data.get("text", "")
        timestamp = tweet_data.get("timestamp", "")
        user = tweet_data.get("user", {})

        username = user.get("screen_name", "")
        display_name = user.get("name", "")

        url = f"https://x.com/{username}/status/{tweet_id}" if username else f"https://x.com/i/status/{tweet_id}"

        # Engagement (Scweet returns these in the tweet dict or nested)
        likes = tweet_data.get("likes") or tweet_data.get("favorite_count")
        comments = tweet_data.get("comments") or tweet_data.get("reply_count")
        shares = tweet_data.get("retweets") or tweet_data.get("retweet_count")
        views = tweet_data.get("views")

        return SocialItem(
            platform="x",
            post_id=tweet_id,
            url=url,
            author_username=username,
            author_display_name=display_name,
            author_profile_url=f"https://x.com/{username}" if username else "",
            text=text,
            created_at=timestamp,
            views=int(views) if views is not None else None,
            likes=int(likes) if likes is not None else None,
            comments=int(comments) if comments is not None else None,
            shares=int(shares) if shares is not None else None,
        )

    async def health_check(self) -> SourceHealth:
        try:
            self._ensure_client()
            return SourceHealth(
                platform="x", connector=self.connector_name,
                status="ok",
                coverage={"auth": "auth_token_cookie", "library": "scweet"},
            )
        except RuntimeError as e:
            return SourceHealth(
                platform="x", connector=self.connector_name,
                status="error", error=str(e),
            )
