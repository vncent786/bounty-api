"""Official X API connector for reproducible scheduled collection.

Recent Search is the canonical incremental route. Full-Archive Search is used
only when explicitly enabled for month/halfyear backfills. Grok X Search is not
implemented here because agentic relevance search has no exhaustive pagination
contract and therefore cannot be the observation system of record.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth


_RECENT_ENDPOINT = "/2/tweets/search/recent"
_ARCHIVE_ENDPOINT = "/2/tweets/search/all"


class XOfficialConnector(BaseConnector):
    platform = "x"
    connector_name = "x_official_api"

    def __init__(
        self,
        *,
        bearer_token: Optional[str] = None,
        base_url: Optional[str] = None,
        transport=None,
    ):
        self._bearer_token = bearer_token
        self._base_url = (base_url or os.getenv("BOUNTY_X_API_BASE_URL") or "https://api.x.com").rstrip("/")
        self._transport = transport

    def _token(self) -> str:
        return (self._bearer_token or os.getenv("BOUNTY_X_BEARER_TOKEN") or "").strip()

    @staticmethod
    def _window(time_filter: str, now: Optional[datetime] = None):
        now = now or datetime.now(timezone.utc)
        durations = {
            "1day": timedelta(days=1),
            "week": timedelta(days=7),
            "month": timedelta(days=30),
            "halfyear": timedelta(days=180),
        }
        duration = durations.get(time_filter)
        if duration is None:
            return None, None
        # X requires RFC3339 UTC timestamps. Leave a small buffer at both
        # boundaries so the recent-search week never exceeds its seven-day cap
        # and the newest seconds have time to enter the index.
        buffer = timedelta(seconds=15)
        end = now - buffer
        start = now - duration + buffer
        return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _error_code(status_code: int) -> str:
        return {
            401: "x_auth_expired",
            403: "x_forbidden",
            429: "x_rate_limited",
        }.get(status_code, "x_error")

    @staticmethod
    def _parse_item(tweet: dict, users: dict, media: dict) -> Optional[SocialItem]:
        tweet_id = str(tweet.get("id") or "")
        if not tweet_id:
            return None
        author = users.get(str(tweet.get("author_id") or ""), {})
        username = str(author.get("username") or "")
        public_metrics = tweet.get("public_metrics") or {}
        author_metrics = author.get("public_metrics") or {}
        attachments = tweet.get("attachments") or {}
        media_items = [
            media[key] for key in attachments.get("media_keys") or [] if key in media
        ]
        media_urls = [
            str(item.get("url") or item.get("preview_image_url"))
            for item in media_items
            if item.get("url") or item.get("preview_image_url")
        ]
        media_type = str(media_items[0].get("type") or "text") if media_items else "text"
        entities = tweet.get("entities") or {}
        hashtags = [
            str(item.get("tag") or "")
            for item in entities.get("hashtags") or []
            if item.get("tag")
        ]
        mentions = [
            str(item.get("username") or "")
            for item in entities.get("mentions") or []
            if item.get("username")
        ]
        repost_count = public_metrics.get("repost_count")
        if repost_count is None:
            repost_count = public_metrics.get("retweet_count")
        edit_history = (
            tweet.get("edit_history_post_ids")
            or tweet.get("edit_history_tweet_ids")
            or []
        )
        return SocialItem(
            platform="x",
            post_id=tweet_id,
            url=(
                f"https://x.com/{username}/status/{tweet_id}"
                if username else f"https://x.com/i/status/{tweet_id}"
            ),
            author_username=username,
            author_display_name=str(author.get("name") or ""),
            author_profile_url=f"https://x.com/{username}" if username else "",
            author_follower_count=author_metrics.get("followers_count"),
            text=str(tweet.get("text") or ""),
            created_at=tweet.get("created_at"),
            likes=public_metrics.get("like_count"),
            comments=public_metrics.get("reply_count"),
            shares=repost_count,
            reposts=repost_count,
            bookmarks=public_metrics.get("bookmark_count"),
            views=public_metrics.get("impression_count"),
            media_type=media_type,
            thumbnail_url=media_urls[0] if media_urls else None,
            media_urls=media_urls,
            hashtags=hashtags,
            mentions=mentions,
            language=tweet.get("lang"),
            raw={
                "source_kind": "official_x_api",
                "edit_history_post_ids": list(edit_history),
                "conversation_id": tweet.get("conversation_id"),
            },
        )

    async def search(
        self,
        keyword: str,
        count: int = 20,
        time_filter: str = "",
        sort: str = "",
        region: str = "",
    ) -> ConnectorResult:
        start_clock = time.time()
        token = self._token()
        if not token:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    error="x_credentials_missing",
                ),
            )

        use_archive = time_filter in {"month", "halfyear"}
        if use_archive and os.getenv("BOUNTY_X_ENABLE_FULL_ARCHIVE", "") != "1":
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="skipped",
                    items_requested=count,
                    error="x_full_archive_disabled",
                    coverage={"requested_time_filter": time_filter},
                ),
            )

        endpoint = _ARCHIVE_ENDPOINT if use_archive else _RECENT_ENDPOINT
        start_time, end_time = self._window(time_filter)
        target_count = max(1, int(count))
        params = {
            "query": keyword,
            "max_results": max(10, min(target_count, 100)),
            "tweet.fields": "id,text,author_id,created_at,public_metrics,lang,entities,attachments,conversation_id,edit_history_tweet_ids",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "id,name,username,public_metrics",
            "media.fields": "media_key,type,url,preview_image_url,public_metrics",
        }
        if sort == "latest":
            params["sort_order"] = "recency"
        elif sort == "hot":
            params["sort_order"] = "relevancy"
        if start_time:
            params["start_time"] = start_time
            params["end_time"] = end_time

        items = []
        seen_ids = set()
        raw_records = []
        payload_errors = []
        page_count = 0
        next_token = None
        last_result_count = 0
        max_pages = max(1, int(os.getenv("BOUNTY_X_MAX_PAGES", "100")))

        try:
            async with httpx.AsyncClient(timeout=60, transport=self._transport) as client:
                while page_count < max_pages and len(items) < target_count:
                    page_params = dict(params)
                    if next_token:
                        page_params["next_token"] = next_token
                    response = await client.get(
                        f"{self._base_url}{endpoint}",
                        params=page_params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if response.status_code >= 400:
                        error = self._error_code(response.status_code)
                        return ConnectorResult(
                            items=items,
                            health=SourceHealth(
                                platform="x",
                                connector=self.connector_name,
                                status="partial" if items else "error",
                                items_returned=len(items),
                                items_requested=target_count,
                                latency_ms=int((time.time() - start_clock) * 1000),
                                error=error,
                                coverage={
                                    "endpoint": endpoint,
                                    "http_status": response.status_code,
                                    "pages_completed": page_count,
                                },
                            ),
                            raw_records=raw_records,
                        )
                    payload = response.json()
                    page_count += 1
                    raw_records.append({
                        "source_id": f"{endpoint}:{keyword}:{start_time or 'recent'}:page:{page_count}",
                        "payload_format": "json",
                        "payload": payload,
                    })
                    payload_errors.extend(payload.get("errors") or [])
                    includes = payload.get("includes") or {}
                    users = {
                        str(item.get("id")): item
                        for item in includes.get("users") or []
                    }
                    media = {
                        str(item.get("media_key")): item
                        for item in includes.get("media") or []
                    }
                    for tweet in payload.get("data") or []:
                        item = self._parse_item(tweet, users, media)
                        if item is None or item.post_id in seen_ids:
                            continue
                        seen_ids.add(item.post_id)
                        items.append(item)
                        if len(items) >= target_count:
                            break
                    meta = payload.get("meta") or {}
                    last_result_count = meta.get("result_count") or 0
                    next_token = meta.get("next_token")
                    if not next_token:
                        break
        except (httpx.HTTPError, ValueError, TypeError):
            return ConnectorResult(
                items=items,
                health=SourceHealth(
                    platform="x",
                    connector=self.connector_name,
                    status="partial" if items else "error",
                    items_returned=len(items),
                    items_requested=target_count,
                    latency_ms=int((time.time() - start_clock) * 1000),
                    error="x_error",
                    coverage={"endpoint": endpoint, "pages_completed": page_count},
                ),
                raw_records=raw_records,
            )

        page_cap_reached = bool(next_token and page_count >= max_pages and len(items) < target_count)
        coverage = {
            "endpoint": endpoint,
            "time_filter": time_filter or "recent_default",
            "last_page_result_count": last_result_count,
            "pages_completed": page_count,
            "window_exhausted": not bool(next_token),
            "requested_limit_reached": len(items) >= target_count,
            "page_cap_reached": page_cap_reached,
            "payload_error_count": len(payload_errors),
            "requested_region": region or None,
            "region_filter_applied": False,
        }
        health_status = "partial" if payload_errors or page_cap_reached else "ok"
        health_error = "x_partial_response" if payload_errors else (
            "x_page_cap_reached" if page_cap_reached else None
        )
        return ConnectorResult(
            items=items[:target_count],
            health=SourceHealth(
                platform="x",
                connector=self.connector_name,
                status=health_status,
                items_returned=min(len(items), target_count),
                items_requested=target_count,
                latency_ms=int((time.time() - start_clock) * 1000),
                error=health_error,
                coverage=coverage,
            ),
            raw_records=raw_records,
        )

    async def health_check(self) -> SourceHealth:
        if not self._token():
            return SourceHealth(
                platform="x",
                connector=self.connector_name,
                status="error",
                error="x_credentials_missing",
            )
        return SourceHealth(
            platform="x",
            connector=self.connector_name,
            status="partial",
            coverage={"check": "credentials_present_only", "network_request": False},
        )
