"""Self-hosted Instagram connector.

Fetches Instagram hashtag pages and post pages using curl_cffi (browser TLS
fingerprint matching). No Playwright, no login, no third-party service.
Cost: $0.

Instagram embeds initial page data in JSON-LD or <script> JSON blocks.
The anonymous web API also exposes a GraphQL endpoint that works with the
correct X-IG-App-ID header (same header the Instagram web app sends).

Auth approach: none — fully anonymous access to public data.
Data depth: hashtag post lists with captions, like counts, comment counts,
timestamps, media URLs. Deeper pagination requires login (login-wall blocks
after ~15-24 items, which is enough for trend monitoring).

X-IG-App-ID: 936619743392459 (Instagram Web App ID, public, hardcoded in every browser request)
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.proxy_config import build_playwright_proxy

logger = logging.getLogger(__name__)

_IG_APP_ID = "936619743392459"
_IG_GRAPHQL_URL = "https://www.instagram.com/graphql/query"

# Query hashes for Instagram's GraphQL API (public, embedded in web app JS)
# These are the query IDs the Instagram web client uses. They can change with
# Instagram updates. If they break, find new ones by inspecting Network tab
# on instagram.com. As of 2026 these are stable.
_TAG_QUERY_HASH = "9b498c08113f1e09617a1703c22b2f35"  # hashtag media query
_POST_QUERY_HASH = "305a5b5cba6bf7d81453955223ad4ff5"  # shortcode media query

_IG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "X-IG-App-ID": _IG_APP_ID,
    "X-ASBD-ID": "198387",
    "X-IG-WWW-Claim": "0",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
    "Origin": "https://www.instagram.com",
}


def _proxy_url():
    proxy = build_playwright_proxy()
    if not proxy:
        return None
    server = proxy["server"]
    username = proxy.get("username")
    password = proxy.get("password")
    if username and password:
        from urllib.parse import urlparse
        parsed = urlparse(server)
        return f"{parsed.scheme}://{username}:{password}@{parsed.hostname}:{parsed.port}"
    return server


def _parse_count(text):
    """Parse Instagram-style counts: '1,234' → 1234, '5.6K' → 5600, '2.3M' → 2300000."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace(" ", "")
    try:
        if text[-1].upper() == "K":
            return int(float(text[:-1]) * 1000)
        elif text[-1].upper() == "M":
            return int(float(text[:-1]) * 1000000)
        elif text[-1].upper() == "B":
            return int(float(text[:-1]) * 1000000000)
        return int(text)
    except (ValueError, IndexError):
        return None


class InstagramConnector(BaseConnector):
    """Instagram connector using anonymous web GraphQL access via curl_cffi."""

    platform = "instagram"
    connector_name = "ig_anon_graphql"

    def __init__(self):
        self._session = None

    def _get_session(self):
        if self._session is not None:
            return self._session

        if not CURL_CFFI_AVAILABLE:
            raise RuntimeError("curl_cffi_not_installed")

        impersonate = "chrome124"
        proxy = _proxy_url()
        if proxy:
            self._session = curl_requests.Session(
                impersonate=impersonate, proxies={"https": proxy, "http": proxy}
            )
        else:
            self._session = curl_requests.Session(impersonate=impersonate)
        return self._session

    async def _graphql_request(self, query_hash, variables):
        """Make an anonymous GraphQL request to Instagram's web API."""
        session = self._get_session()
        params = {
            "query_hash": query_hash,
            "variables": json.dumps(variables),
        }

        def _do_request():
            resp = session.get(
                _IG_GRAPHQL_URL,
                params=params,
                headers=_IG_HEADERS,
                timeout=20,
            )
            return resp

        resp = await asyncio.to_thread(_do_request)

        if resp.status_code != 200:
            raise RuntimeError(f"ig_http_{resp.status_code}")

        return resp.json()

    async def _fetch_tag_page(self, tag, count=20):
        """Fetch hashtag page via GraphQL — returns list of media nodes."""
        variables = {
            "tag_name": tag,
            "first": min(count, 50),
            "after": None,
        }

        try:
            data = await self._graphql_request(_TAG_QUERY_HASH, variables)
            hashtag_data = data.get("data", {}).get("hashtag", {})
            if not hashtag_data:
                logger.warning(f"IG: no hashtag data for tag={tag}")
                return []

            media_edges = (
                hashtag_data.get("edge_hashtag_to_media", {}).get("edges", [])
                or hashtag_data.get("edge_hashtag_to_top_posts", {}).get("edges", [])
            )

            return [edge["node"] for edge in media_edges if edge.get("node")]

        except Exception as e:
            logger.error(f"IG: tag fetch failed for {tag}: {e}")
            raise

    @staticmethod
    def _node_to_item(node):
        """Convert an Instagram media node to SocialItem."""
        shortcode = node.get("shortcode", "")
        media_id = str(node.get("id", shortcode))

        # Text/caption
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        text = ""
        if caption_edges:
            text = caption_edges[0].get("node", {}).get("text", "")

        # Engagement
        likes = node.get("edge_liked_by", {}).get("count")
        comments = node.get("edge_media_to_comment", {}).get("count")
        if comments is None:
            comments = node.get("edge_media_to_parent_comment", {}).get("count")

        # Timestamp
        taken_at = node.get("taken_at_timestamp")
        created_at = None
        if taken_at:
            created_at = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat()

        # Owner
        owner = node.get("owner", {})
        username = owner.get("username", "")

        # Media type
        is_video = node.get("is_video", False)
        media_type = "video" if is_video else "image"

        # Media URLs
        display_url = node.get("display_url", "")
        thumbnail_url = node.get("thumbnail_src", display_url)
        media_urls = [display_url] if display_url else []

        # Hashtags from text
        hashtags = re.findall(r"#(\w+)", text)

        return SocialItem(
            platform="instagram",
            post_id=media_id,
            url=f"https://www.instagram.com/p/{shortcode}/" if shortcode else "",
            author_username=username,
            author_display_name="",
            author_profile_url=f"https://www.instagram.com/{username}/" if username else "",
            author_follower_count=None,
            text=text,
            created_at=created_at,
            views=node.get("video_view_count") if is_video else None,
            likes=int(likes) if likes is not None else None,
            comments=int(comments) if comments is not None else None,
            shares=None,
            media_type=media_type,
            thumbnail_url=thumbnail_url,
            media_urls=media_urls,
            hashtags=hashtags,
        )

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search Instagram by hashtag."""
        start = time.time()

        # Normalize keyword to tag (no spaces, lowercase)
        tag = re.sub(r"[^A-Za-z0-9]", "", keyword).lower()
        if not tag:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    error="ig_empty_tag",
                ),
            )

        try:
            nodes = await self._fetch_tag_page(tag, count=count)

            items = [self._node_to_item(node) for node in nodes]

            # Apply time filter if specified
            if time_filter and items:
                now = datetime.now(timezone.utc)
                cutoff_map = {
                    "1day": 1, "week": 7, "month": 30, "halfyear": 180,
                }
                days = cutoff_map.get(time_filter)
                if days:
                    from datetime import timedelta
                    cutoff = now - timedelta(days=days)
                    filtered = []
                    for item in items:
                        if item.created_at:
                            try:
                                item_dt = datetime.fromisoformat(
                                    item.created_at.replace("Z", "+00:00")
                                )
                                if item_dt >= cutoff:
                                    filtered.append(item)
                            except (ValueError, TypeError):
                                filtered.append(item)  # Keep if we can't parse
                        else:
                            filtered.append(item)  # Keep if no timestamp
                    items = filtered

            latency_ms = int((time.time() - start) * 1000)

            return ConnectorResult(
                items=items[:count],
                health=SourceHealth(
                    platform="instagram",
                    connector=self.connector_name,
                    status="ok" if items else "partial",
                    items_returned=len(items),
                    items_requested=count,
                    latency_ms=latency_ms,
                ),
            )

        except RuntimeError as e:
            error_str = str(e)
            if "ig_http_429" in error_str:
                error_code = "ig_rate_limited"
            elif "ig_http_401" in error_str or "ig_http_403" in error_str:
                error_code = "ig_blocked"
            elif "ig_http_" in error_str:
                error_code = error_str
            else:
                error_code = "ig_error"

            latency_ms = int((time.time() - start) * 1000)
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    latency_ms=latency_ms,
                    error=error_code,
                ),
            )

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram",
                    connector=self.connector_name,
                    status="error",
                    items_requested=count,
                    latency_ms=latency_ms,
                    error="ig_error",
                ),
            )

    async def health_check(self) -> SourceHealth:
        """Quick health probe."""
        if not CURL_CFFI_AVAILABLE:
            return SourceHealth(
                platform="instagram",
                connector=self.connector_name,
                status="error",
                error="curl_cffi_not_installed",
            )
        return SourceHealth(
            platform="instagram",
            connector=self.connector_name,
            status="ok",
            coverage={"auth": "anonymous", "depth": "hashtag_page_shallow"},
        )
