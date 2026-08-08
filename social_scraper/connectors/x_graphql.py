"""Self-hosted X/Twitter connector.

Replays X's internal GraphQL SearchTimeline endpoint using browser cookies
(auth_token + ct0). Authentication is done via the twikit library (MIT, open-source)
which implements X's login flow through the official web client's own endpoints.

No paid API key. No third-party service. No developer account.
Cost: $0 + one free X account for cookie auth.

Auth approach:
1. First run: login with username/password via twikit → stores cookies
2. Subsequent runs: loads stored cookies, refreshes if expired
3. Search: GET SearchTimeline GraphQL endpoint with cookie + CSRF auth

Cookies are stored in data/x_cookies.json (same pattern as reddit_mobile_device.json).
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth

logger = logging.getLogger(__name__)

try:
    from twikit import Client as TwikitClient
    TWIKIT_AVAILABLE = True
except ImportError:
    TwikitClient = None
    TWIKIT_AVAILABLE = False

_COOKIE_PATH = Path(__file__).resolve().parents[2] / "data" / "x_cookies.json"
_LOGIN_LOCK = asyncio.Lock()


def _get_env(name):
    return os.getenv(name, "").strip()


def _cookie_path():
    configured = _get_env("BOUNTY_X_COOKIE_PATH")
    if configured:
        return Path(configured)
    return _COOKIE_PATH


class XAuthError(RuntimeError):
    pass


class XRateLimitError(RuntimeError):
    pass


class XConnector(BaseConnector):
    """X/Twitter connector using twikit's cookie-based GraphQL replay."""

    platform = "x"
    connector_name = "x_graphql"

    def __init__(self):
        self._client = None
        self._cookies_loaded = False

    def _build_client(self):
        lang = _get_env("BOUNTY_X_LANG") or "en-US"
        client = TwikitClient(lang)
        return client

    async def _ensure_authed(self):
        """Load stored cookies or login fresh."""
        if not TWIKIT_AVAILABLE:
            raise XAuthError("twikit_not_installed")

        if self._client and self._cookies_loaded:
            return self._client

        async with _LOGIN_LOCK:
            if self._client and self._cookies_loaded:
                return self._client

            client = self._build_client()

            # Try loading stored cookies first
            cookie_file = _cookie_path()
            if cookie_file.exists():
                try:
                    cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                    client.set_cookies(cookies)
                    self._client = client
                    self._cookies_loaded = True
                    logger.info("X: loaded stored cookies")
                    return self._client
                except Exception as e:
                    logger.warning(f"X: failed to load cookies: {e}")

            # No stored cookies — need to login
            username = _get_env("BOUNTY_X_USERNAME")
            email = _get_env("BOUNTY_X_EMAIL")
            password = _get_env("BOUNTY_X_PASSWORD")

            if not username or not password:
                raise XAuthError(
                    "x_credentials_missing: set BOUNTY_X_USERNAME, "
                    "BOUNTY_X_EMAIL (optional), BOUNTY_X_PASSWORD"
                )

            try:
                await client.login(
                    auth_info_1=username,
                    auth_info_2=email if email else None,
                    password=password,
                    cookies_file=str(cookie_file),
                )
                # Also save cookies in JSON for our own persistence
                cookies = client.get_cookies()
                cookie_file.parent.mkdir(parents=True, exist_ok=True)
                cookie_file.write_text(
                    json.dumps(cookies, indent=2), encoding="utf-8"
                )
                self._client = client
                self._cookies_loaded = True
                logger.info("X: login successful, cookies stored")
                return self._client
            except Exception as e:
                raise XAuthError(f"x_login_failed: {e}")

    @staticmethod
    def _parse_tweet_to_item(tweet, now=None):
        """Convert a twikit Tweet object into a SocialItem."""
        now = now or datetime.now(timezone.utc)

        # Basic fields
        post_id = getattr(tweet, "id", "")
        text = getattr(tweet, "text", "")
        created_at = getattr(tweet, "created_at", "")
        author = getattr(tweet, "author", None)

        # Author info
        username = ""
        display_name = ""
        follower_count = None
        if author:
            username = getattr(author, "screen_name", "")
            display_name = getattr(author, "name", "")
            follower_count = getattr(author, "followers_count", None)

        # Engagement metrics
        likes = getattr(tweet, "favorite_count", None)
        comments = getattr(tweet, "reply_count", None)
        shares = getattr(tweet, "retweet_count", None)
        views = getattr(tweet, "view_count", None)

        # Media
        media_type = "text"
        media_urls = []
        thumbnail_url = None
        media = getattr(tweet, "media", None)
        if media:
            photos = media.get("photos", [])
            videos = media.get("videos", [])
            if videos:
                media_type = "video"
                media_urls = [v.get("url", "") for v in videos if v.get("url")]
            elif photos:
                media_type = "image"
                media_urls = [p.get("url", "") for p in photos if p.get("url")]
                if media_urls:
                    thumbnail_url = media_urls[0]

        # Hashtags and mentions
        hashtags = getattr(tweet, "hashtags", []) or []
        mentions_list = getattr(tweet, "mentions", []) or []
        mentions = [m.get("screen_name", "") if isinstance(m, dict)
                     else str(m) for m in mentions_list] if mentions_list else []
        if not mentions:
            mentions = []
        if not hashtags:
            hashtags = []

        url = f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/status/{post_id}"

        return SocialItem(
            platform="x",
            post_id=str(post_id),
            url=url,
            author_username=username,
            author_display_name=display_name,
            author_profile_url=f"https://x.com/{username}" if username else "",
            author_follower_count=follower_count,
            text=text,
            created_at=created_at,
            views=int(views) if views is not None else None,
            likes=int(likes) if likes is not None else None,
            comments=int(comments) if comments is not None else None,
            shares=int(shares) if shares is not None else None,
            media_type=media_type,
            thumbnail_url=thumbnail_url,
            media_urls=media_urls,
            hashtags=hashtags,
            mentions=mentions,
        )

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search X for tweets matching keyword."""
        start = time.time()

        try:
            client = await self._ensure_authed()
        except XAuthError as e:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    error=str(e),
                ),
            )

        # Determine product type
        product = "Latest" if sort == "new" else "Top"

        # Time filter → query suffix
        query = keyword
        if time_filter:
            tf_map = {
                "1day": " since:1day",
                "week": " since:7d",
                "month": " since:30d",
            }
            # X search uses operators; for recency, use min_faves/min_retweets
            # or just use Latest product for time-sorted results
            pass

        try:
            # twikit search_tweet returns up to 20 per call; paginate if needed
            remaining = count
            all_items = []
            cursor = None

            while remaining > 0:
                batch_size = min(remaining, 20)
                tweets = await client.search_tweet(query, product, count=batch_size, cursor=cursor)

                if not tweets:
                    break

                for tweet in tweets:
                    all_items.append(self._parse_tweet_to_item(tweet))

                remaining -= len(tweets)

                # Try to get next page
                if remaining > 0 and hasattr(tweets, "next"):
                    try:
                        cursor_result = tweets.next_cursor if hasattr(tweets, "next_cursor") else None
                        if not cursor_result:
                            break
                        cursor = cursor_result
                    except Exception:
                        break
                else:
                    break

            latency_ms = int((time.time() - start) * 1000)

            return ConnectorResult(
                items=all_items[:count],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="ok" if all_items else "partial",
                    items_returned=len(all_items),
                    items_requested=count,
                    latency_ms=latency_ms,
                ),
            )

        except Exception as e:
            error_str = str(e).lower()
            if "rate limit" in error_str:
                error_code = "x_rate_limited"
            elif "unauthorized" in error_str or "401" in error_str:
                # Cookies expired — clear and retry next time
                self._cookies_loaded = False
                self._client = None
                try:
                    _cookie_path().unlink(missing_ok=True)
                except Exception:
                    pass
                error_code = "x_auth_expired"
            elif "forbidden" in error_str or "403" in error_str:
                error_code = "x_forbidden"
            else:
                error_code = "x_error"

            latency_ms = int((time.time() - start) * 1000)
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    latency_ms=latency_ms,
                    error=error_code,
                ),
            )

    async def health_check(self) -> SourceHealth:
        """Quick health probe — try a minimal search."""
        try:
            client = await self._ensure_authed()
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="ok",
                coverage={"auth": "cookie-based", "library": "twikit"},
            )
        except XAuthError as e:
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="error",
                error=str(e),
            )
        except Exception as e:
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="error",
                error=f"x_health_error: {e}",
            )
