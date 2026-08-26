"""Owned X collector using X's authenticated web GraphQL interface via Scweet.

This is the low-marginal-cost route. It uses one of Bounty's logged-in X
accounts, browser-derived cookies, client-transaction-id generation, conservative
per-account budgets, and persistent account cooldown state. It is intentionally
separate from the paid official X API connector.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
from social_scraper.owned_worker_lock import AsyncFileLock

logger = logging.getLogger(__name__)

try:
    from Scweet import Scweet, ScweetConfig
    SCWEET_AVAILABLE = True
except ImportError:
    Scweet = None
    ScweetConfig = None
    SCWEET_AVAILABLE = False


class XConnector(BaseConnector):
    """X/Twitter connector using owned authenticated web GraphQL access."""

    platform = "x"
    connector_name = "x_scweet"
    manages_timeout = True

    def __init__(self, *, sleep_fn=None, clock=None):
        self._client = None
        self._sleep = sleep_fn or asyncio.sleep
        self._clock = clock or time.time
        self._daily_request_limit = 300

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            return max(0.0, float(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default

    def _get_auth_token(self):
        return os.getenv("BOUNTY_X_AUTH_TOKEN", "").strip()

    def _db_path(self) -> str:
        return (
            os.getenv("BOUNTY_X_SCWEET_DB", "data/x_scweet_state.db").strip()
            or "data/x_scweet_state.db"
        )

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not SCWEET_AVAILABLE:
            raise RuntimeError("scweet_not_installed")
        auth_token = self._get_auth_token()
        if not auth_token:
            raise RuntimeError("x_credentials_missing: set BOUNTY_X_AUTH_TOKEN")

        db_path = self._db_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._daily_request_limit = self._env_int(
            "BOUNTY_X_DAILY_REQUEST_LIMIT", 300
        )
        config = ScweetConfig(
            db_path=db_path,
            proxy=os.getenv("BOUNTY_X_PROXY", "").strip() or None,
            concurrency=1,
            daily_requests_limit=self._daily_request_limit,
            daily_tweets_limit=self._env_int("BOUNTY_X_DAILY_TWEETS_LIMIT", 4000),
            requests_per_min=self._env_int("BOUNTY_X_REQUESTS_PER_MIN", 5),
            min_delay_s=float(os.getenv("BOUNTY_X_MIN_DELAY_SECONDS", "3")),
            max_account_switches=0,
            manifest_scrape_on_init=False,
            manifest_update_on_init=False,
            proxy_check_on_lease=False,
        )
        self._client = Scweet(
            auth_token=auth_token,
            db_path=db_path,
            config=config,
        )
        return self._client

    @staticmethod
    def _time_bounds(time_filter: str):
        days = {"1day": 1, "week": 7, "month": 30, "halfyear": 180}.get(
            time_filter, 30
        )
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).date().isoformat()
        # X's `until:` operator is exclusive, so tomorrow includes all of today.
        until = (now + timedelta(days=1)).date().isoformat()
        return since, until

    @staticmethod
    def _error_code(exc: Exception) -> str:
        text = str(exc).lower()
        if "daily_limit" in text:
            return "x_daily_budget_exhausted"
        if "no eligible accounts" in text:
            return "x_account_pool_unavailable"
        if "rate limit" in text or "429" in text:
            return "x_rate_limited"
        if "unauthorized" in text or "401" in text:
            return "x_auth_expired"
        if "forbidden" in text or "403" in text:
            return "x_forbidden"
        if "transaction" in text:
            return "x_transaction_id_failed"
        return "x_error"

    def _cooldown_wait_seconds(
        self,
        client,
        error_code: str,
        *,
        already_waited: float,
    ) -> float | None:
        if error_code not in {"x_rate_limited", "x_account_pool_unavailable"}:
            return None
        max_wait = self._env_float("BOUNTY_X_MAX_WAIT_SECONDS", 20 * 60)
        remaining_wait = max_wait - already_waited
        if remaining_wait <= 0:
            return None
        try:
            accounts = client.db.list_accounts(
                limit=200,
                include_cookies=False,
                reveal_secrets=False,
            )
        except Exception:
            accounts = []
        active = [
            account for account in accounts
            if int(account.get("status") or 0) == 1
            and int(account.get("daily_requests") or 0) < self._daily_request_limit
        ]
        if accounts and not active:
            return None
        now = float(self._clock())
        future = [
            float(account.get("available_til") or 0.0)
            for account in active
            if float(account.get("available_til") or 0.0) > now
        ]
        if future:
            wait_seconds = max(1.0, min(future) - now + 1.0)
        elif active and any(account.get("busy") for account in active):
            wait_seconds = 5.0
        else:
            wait_seconds = self._env_float(
                "BOUNTY_X_RETRY_DEFAULT_SECONDS", 2 * 60
            )
        if wait_seconds > remaining_wait:
            return None
        return float(wait_seconds)

    async def search(
        self,
        keyword: str,
        count: int = 20,
        time_filter: str = "",
        sort: str = "",
        region: str = "",
    ) -> ConnectorResult:
        start = time.time()
        try:
            client = self._ensure_client()
        except RuntimeError as exc:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    error=str(exc),
                ),
            )

        since, until = self._time_bounds(time_filter)
        display_type = "Latest" if sort == "latest" else "Top"
        lock_path = Path(f"{self._db_path()}.lock")
        retry_count = 0
        waited_seconds = 0.0
        max_retry_attempts = self._env_int("BOUNTY_X_MAX_RETRY_ATTEMPTS", 4)
        while True:
            try:
                def run_search():
                    return client.search(
                        keyword,
                        since=since,
                        until=until,
                        display_type=display_type,
                        limit=count,
                        # Restart the bounded query after cooldown. Scweet's cursor
                        # resume returns only later pages, which would omit records
                        # collected by a failed prior attempt from this call's result.
                        resume=False,
                    )

                async with AsyncFileLock(lock_path):
                    raw_tweets = await asyncio.to_thread(run_search)
                break
            except Exception as exc:
                error_code = self._error_code(exc)
                wait_seconds = self._cooldown_wait_seconds(
                    client,
                    error_code,
                    already_waited=waited_seconds,
                )
                if retry_count >= max_retry_attempts or wait_seconds is None:
                    return ConnectorResult(
                        items=[],
                        health=SourceHealth(
                            platform="x",
                            connector=self.connector_name,
                            status="error",
                            items_requested=count,
                            latency_ms=int((time.time() - start) * 1000),
                            error=error_code,
                            coverage={
                                "route": "x_web_graphql_scweet",
                                "retry_count": retry_count,
                                "waited_seconds": waited_seconds,
                                "retried_after_cooldown": retry_count > 0,
                            },
                        ),
                    )
                retry_count += 1
                waited_seconds += wait_seconds
                logger.info(
                    "X collector cooling down for %.1fs before retry %s/%s",
                    wait_seconds,
                    retry_count,
                    max_retry_attempts,
                )
                await self._sleep(wait_seconds)

        items = []
        for tweet_data in raw_tweets:
            item = self._parse_tweet(tweet_data)
            if item is not None:
                items.append(item)
            if len(items) >= count:
                break
        requested_limit_reached = len(raw_tweets) > count or len(items) >= count
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="ok",
                items_returned=len(items),
                items_requested=count,
                latency_ms=int((time.time() - start) * 1000),
                coverage={
                    "route": "x_web_graphql_scweet",
                    "auth": "owned_account_cookie",
                    "display_type": display_type,
                    "since": since,
                    "provider_returned": len(raw_tweets),
                    "requested_limit_reached": requested_limit_reached,
                    "region_filter_applied": False,
                    "retry_count": retry_count,
                    "waited_seconds": waited_seconds,
                    "retried_after_cooldown": retry_count > 0,
                },
            ),
            raw_records=[{
                "source_id": f"x_scweet_search:{keyword}:{since or 'unbounded'}",
                "payload_format": "json",
                "payload": {"tweets": raw_tweets[:count]},
            }],
        )

    @staticmethod
    def _safe_int(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_tweet(cls, tweet_data):
        if not isinstance(tweet_data, dict):
            return None
        tweet_id = str(tweet_data.get("tweet_id") or "")
        if not tweet_id:
            return None
        raw_source = tweet_data.get("raw") or {}
        legacy = raw_source.get("legacy") or {}
        user = tweet_data.get("user") or {}
        core = raw_source.get("core") or {}
        user_result = ((core.get("user_results") or {}).get("result") or {})
        username = str(user.get("screen_name") or "")
        display_name = str(user.get("name") or "")
        views = tweet_data.get("views")
        if views is None:
            views = (raw_source.get("views") or {}).get("count")
        url = str(tweet_data.get("tweet_url") or "")
        if not url:
            url = (
                f"https://x.com/{username}/status/{tweet_id}"
                if username else f"https://x.com/i/status/{tweet_id}"
            )
        raw = dict(raw_source)
        raw["source_payload"] = tweet_data
        raw["author_id"] = str(
            legacy.get("user_id_str") or user_result.get("rest_id") or ""
        ) or None
        return SocialItem(
            platform="x",
            post_id=tweet_id,
            url=url,
            author_username=username,
            author_display_name=display_name,
            author_profile_url=f"https://x.com/{username}" if username else "",
            text=str(tweet_data.get("text") or legacy.get("full_text") or ""),
            created_at=tweet_data.get("timestamp") or legacy.get("created_at"),
            views=cls._safe_int(views),
            likes=cls._safe_int(tweet_data.get("likes") if tweet_data.get("likes") is not None else legacy.get("favorite_count")),
            comments=cls._safe_int(tweet_data.get("comments") if tweet_data.get("comments") is not None else legacy.get("reply_count")),
            shares=cls._safe_int(tweet_data.get("retweets") if tweet_data.get("retweets") is not None else legacy.get("retweet_count")),
            reposts=cls._safe_int(tweet_data.get("retweets") if tweet_data.get("retweets") is not None else legacy.get("retweet_count")),
            bookmarks=cls._safe_int(legacy.get("bookmark_count")),
            language=legacy.get("lang"),
            media_type="text",
            raw=raw,
        )

    async def fetch_thread(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        route = "x_scweet_conversation_search"
        if post.platform != "x" or not post.post_id:
            return ThreadFetchResult(
                platform="x",
                root_post_external_id=post.post_id or "unknown",
                status="error",
                attempted_route=route,
                error_category="invalid_x_post",
                max_comments=max_comments,
                max_depth=max_depth,
            )
        if max_comments <= 0 or max_depth <= 0:
            return ThreadFetchResult(
                platform="x",
                root_post_external_id=post.post_id,
                status="empty",
                attempted_route=route,
                max_comments=max_comments,
                max_depth=max_depth,
            )

        legacy = post.raw.get("legacy") or {}
        conversation_id = str(legacy.get("conversation_id_str") or post.post_id)
        search_result = await self.search(
            f"conversation_id:{conversation_id}",
            count=max_comments + 10,
            sort="latest",
        )
        if not search_result.items and search_result.health.status == "error":
            return ThreadFetchResult(
                platform="x",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route=route,
                error_category=search_result.health.error or "x_replies_unavailable",
                max_comments=max_comments,
                max_depth=max_depth,
            )

        candidates = [
            item for item in search_result.items if item.post_id != post.post_id
        ]
        by_id = {item.post_id: item for item in candidates}
        parent_by_id = {}
        for item in candidates:
            item_legacy = item.raw.get("legacy") or {}
            parent_id = str(item_legacy.get("in_reply_to_status_id_str") or "")
            parent_by_id[item.post_id] = parent_id

        depth_cache = {}

        def depth_for(item_id, visiting=None):
            if item_id in depth_cache:
                return depth_cache[item_id]
            visiting = set(visiting or ())
            if item_id in visiting:
                depth_cache[item_id] = None
                return None
            visiting.add(item_id)
            parent_id = parent_by_id.get(item_id, "")
            if parent_id == post.post_id:
                depth = 1
            elif parent_id in by_id:
                parent_depth = depth_for(parent_id, visiting)
                depth = None if parent_depth is None else parent_depth + 1
            else:
                depth = None
            depth_cache[item_id] = depth
            return depth

        records = []
        unknown_depth = False
        for item in candidates:
            depth = depth_for(item.post_id)
            if depth is None:
                unknown_depth = True
                continue
            if depth > max_depth or len(records) >= max_comments:
                continue
            records.append(ThreadRecord(
                platform="x",
                external_id=item.post_id,
                record_type="comment" if depth == 1 else "reply",
                parent_external_id=parent_by_id[item.post_id],
                root_post_external_id=post.post_id,
                depth=depth,
                text=item.text,
                author_external_id=str(item.raw.get("author_id") or "") or None,
                author_username=item.author_username or None,
                url=item.url or None,
                published_at=item.created_at,
                likes=item.likes,
                raw=item.raw,
            ))

        reported_total = post.comments if isinstance(post.comments, int) else None
        direct_replies = len([record for record in records if record.depth == 1])
        unknown_total = reported_total is None
        truncated = (
            unknown_total
            or unknown_depth
            or bool(search_result.health.coverage.get("requested_limit_reached"))
            or len(candidates) > len(records)
            or len(records) >= max_comments
            or (reported_total is not None and reported_total > direct_replies)
        )
        status = (
            "empty"
            if not records and reported_total == 0 and not truncated
            else "partial" if truncated else "complete"
        )
        return ThreadFetchResult(
            platform="x",
            root_post_external_id=post.post_id,
            status=status,
            records=tuple(records),
            truncated=truncated,
            attempted_route=route,
            error_category=(
                search_result.health.error
                if search_result.health.status == "partial" else None
            ),
            platform_reported_total=reported_total,
            max_comments=max_comments,
            max_depth=max_depth,
            limitations=(
                "X web search is ranked and bounded; deleted, protected, withheld, or omitted replies remain unavailable.",
            ),
        )

    async def health_check(self) -> SourceHealth:
        try:
            client = self._ensure_client()
            summary = None
            try:
                summary = client.db.accounts_summary()
            except Exception:
                pass
            eligible = summary.get("eligible") if isinstance(summary, dict) else None
            status = "ok" if eligible is None or eligible > 0 else "partial"
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status=status,
                coverage={
                    "auth": "owned_account_cookie",
                    "library": "scweet",
                    "route": "x_web_graphql_scweet",
                    "network_check": False,
                    "eligible_accounts": eligible,
                    "account_blocked_counts": (
                        summary.get("blocked_counts")
                        if isinstance(summary, dict) else None
                    ),
                },
            )
        except RuntimeError as exc:
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="error",
                error=str(exc),
            )
