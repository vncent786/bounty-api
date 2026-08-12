"""Owned current Reddit collection through the official Android client's token flow.

The access mechanism is derived from current Redlib source. It mints an
installed-client bearer token and reads normal oauth.reddit.com JSON listings.
No developer key or external data provider is involved.
"""

import asyncio
import base64
import json
import math
import os
import re
import threading
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.connectors.reddit_camoufox import POST_PATH_RE, SUBREDDIT_RE, validate_reddit_url
from social_scraper.proxy_config import build_playwright_proxy

ANDROID_CLIENT_ID = "ohXpoqrZYub1kg"
ANDROID_APP_VERSION = "Version 2024.47.0/Build 2029755"
TOKEN_URL = "https://www.reddit.com/auth/v2/oauth/access-token/loid"
OAUTH_ORIGIN = "https://oauth.reddit.com"
_MAX_RESPONSE_BYTES = 2_000_000
_LOOKBACK = {
    "1day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
    "halfyear": timedelta(days=180),
}
_MOBILE_GATES = weakref.WeakKeyDictionary()
_DEVICE_LOCK = threading.Lock()
_MOBILE_SYNC_LOCK = threading.Lock()


class RedditMobileRateLimitError(RuntimeError):
    pass


class RedditMobileAuthError(RuntimeError):
    pass


def _gate_for_current_loop():
    loop = asyncio.get_running_loop()
    gate = _MOBILE_GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(1)
        _MOBILE_GATES[loop] = gate
    return gate


def _default_device_path():
    configured = os.getenv("BOUNTY_REDDIT_DEVICE_STATE", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "reddit_mobile_device.json"


def load_or_create_device_id(path=None):
    path = Path(path or _default_device_path())
    with _DEVICE_LOCK:
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get("device_id")
                return str(uuid.UUID(value))
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                pass
        value = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"device_id": value}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return value


def _proxy_url():
    proxy = build_playwright_proxy()
    if not proxy:
        return None
    server = proxy["server"]
    username = proxy.get("username")
    password = proxy.get("password")
    if username:
        scheme, rest = server.split("://", 1)
        return f"{scheme}://{username}:{password or ''}@{rest}"
    return server


def _safe_iso_timestamp(value, now):
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed < datetime(2005, 1, 1, tzinfo=timezone.utc) or parsed > now + timedelta(days=1):
        return None
    return parsed.isoformat()


def _metric(value, nonnegative=False):
    if type(value) is not int:
        return None
    if nonnegative and value < 0:
        return None
    return value


def parse_mobile_post(post, allowed_subreddits, now=None):
    if not isinstance(post, dict):
        return None
    now = now or datetime.now(timezone.utc)
    post_id = str(post.get("id") or "").lower()
    name = str(post.get("name") or "").lower()
    subreddit = str(post.get("subreddit") or "")
    permalink = str(post.get("permalink") or "")
    if (
        not post_id
        or name != f"t3_{post_id}"
        or subreddit.lower() not in {value.lower() for value in allowed_subreddits}
        or permalink.startswith("http")
    ):
        return None
    try:
        canonical = validate_reddit_url(f"https://www.reddit.com{permalink}")
    except ValueError:
        return None
    match = POST_PATH_RE.fullmatch(urlsplit(canonical).path)
    if match.group(1).lower() != subreddit.lower() or match.group(2).lower() != post_id:
        return None

    author = str(post.get("author") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", author):
        author = ""
    title = str(post.get("title") or "").strip()
    body = str(post.get("selftext") or "").strip()
    if not title:
        return None
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
        platform="reddit",
        post_id=post_id,
        url=canonical,
        author_username=author,
        author_profile_url=f"https://www.reddit.com/user/{author}" if author else "",
        text=f"{title}\n\n{body}" if body else title,
        created_at=_safe_iso_timestamp(post.get("created_utc"), now),
        likes=_metric(post.get("score")),
        comments=_metric(post.get("num_comments"), nonnegative=True),
        media_type=media_type,
        raw={
            "subreddit": subreddit,
            "title": title,
            "flair": post.get("link_flair_text"),
            "source_kind": "current_oauth_listing",
            "edited": post.get("edited") if type(post.get("edited")) in {bool, float, int} else None,
        },
    )


def _matches_keyword(item, keyword):
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]+", keyword)]
    if not keyword.strip():
        return True
    if not terms:
        return False
    text = item.text.lower()
    return all(re.search(rf"\b{re.escape(term)}\b", text) for term in terms)


class RedditMobileConnector(BaseConnector):
    platform = "reddit"
    connector_name = "reddit_mobile_owned"
    requires_options = True
    manages_timeout = True

    def __init__(
        self,
        request_fn: Optional[Callable] = None,
        device_path=None,
        clock: Optional[Callable] = None,
        max_subreddits=5,
    ):
        self.request_fn = request_fn
        self.device_path = device_path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_subreddits = max_subreddits
        self._token = None
        self._token_expires_at = 0.0
        self._oauth_headers = {}
        self._token_proxy_identity = None

    def can_handle_options(self, options):
        requested = options.get("subreddits") if isinstance(options, dict) else None
        return (
            CURL_CFFI_AVAILABLE or self.request_fn is not None
        ) and (
            isinstance(requested, list)
            and 1 <= len(requested) <= self.max_subreddits
            and all(isinstance(value, str) and SUBREDDIT_RE.fullmatch(value) for value in requested)
        )

    def _device_headers(self):
        device_id = load_or_create_device_id(self.device_path)
        user_agent = f"Reddit/{ANDROID_APP_VERSION}/Android 13"
        return {
            "User-Agent": user_agent,
            "x-reddit-retry": "algo=no-retries",
            "x-reddit-compression": "1",
            "x-reddit-qos": "10.000",
            "x-reddit-media-codecs": "available-codecs=video/avc, video/hevc, video/x-vnd.on2.vp9",
            "Content-Type": "application/json; charset=UTF-8",
            "client-vendor-id": device_id,
            "X-Reddit-Device-Id": device_id,
        }

    def _request(self, session, method, url, **kwargs):
        if self.request_fn:
            return self.request_fn(method, url, **kwargs)
        return session.request(method, url, **kwargs)

    @staticmethod
    def _json_response(response):
        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Reddit response exceeded size limit")
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            raise RuntimeError("Reddit response was not JSON")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Reddit returned invalid JSON")
        return payload

    def _authenticate(self, session, headers, proxies):
        auth_headers = dict(headers)
        basic = base64.b64encode(f"{ANDROID_CLIENT_ID}:".encode()).decode()
        auth_headers["Authorization"] = f"Basic {basic}"
        response = self._request(
            session,
            "POST",
            TOKEN_URL,
            headers=auth_headers,
            json={"scopes": ["read"]},
            proxies=proxies,
            timeout=25,
        )
        if response.status_code == 429:
            raise RedditMobileRateLimitError("Reddit mobile token rate limited")
        if response.status_code != 200:
            raise RedditMobileAuthError("Reddit mobile token failed")
        payload = self._json_response(response)
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or len(token) < 20 or type(expires_in) not in {int, float}:
            raise RedditMobileAuthError("Reddit mobile token invalid")
        self._token = token
        self._token_expires_at = time.monotonic() + max(0, float(expires_in) - 300)
        self._oauth_headers = {
            key: value
            for key, value in {
                "x-reddit-loid": response.headers.get("x-reddit-loid"),
                "x-reddit-session": response.headers.get("x-reddit-session"),
            }.items()
            if value
        }

    def _invalidate_token(self):
        self._token = None
        self._token_expires_at = 0.0
        self._oauth_headers = {}
        self._token_proxy_identity = None

    def _ensure_token(self, session, headers, proxies):
        proxy_identity = tuple(sorted((proxies or {}).items()))
        if self._token_proxy_identity != proxy_identity:
            self._invalidate_token()
        if not self._token or time.monotonic() >= self._token_expires_at:
            self._authenticate(session, headers, proxies)
            self._token_proxy_identity = proxy_identity

    def _fetch_listing(self, session, subreddit, limit, headers, proxies):
        oauth_headers = dict(headers)
        oauth_headers.update(self._oauth_headers)
        oauth_headers["Authorization"] = f"Bearer {self._token}"
        response = self._request(
            session,
            "GET",
            f"{OAUTH_ORIGIN}/r/{subreddit}/new",
            params={"limit": limit, "raw_json": 1},
            headers=oauth_headers,
            proxies=proxies,
            timeout=25,
        )
        if response.status_code == 429:
            raise RedditMobileRateLimitError("Reddit mobile listing rate limited")
        if response.status_code == 401:
            raise RedditMobileAuthError("Reddit mobile token rejected")
        if response.status_code != 200:
            raise RuntimeError("Reddit mobile listing failed")
        payload = self._json_response(response)
        if payload.get("kind") != "Listing":
            raise RuntimeError("Reddit mobile response was not a listing")
        children = payload.get("data", {}).get("children", [])
        if not isinstance(children, list):
            raise RuntimeError("Reddit mobile listing children invalid")
        return (
            [child.get("data") for child in children if isinstance(child, dict)],
            datetime.now(timezone.utc).isoformat(),
        )

    def _search_sync_unlocked(self, requested, keyword, count, time_filter):
        if self.request_fn:
            session = object()
        else:
            session = curl_requests.Session(impersonate="chrome")
        proxy = _proxy_url()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        headers = self._device_headers()
        try:
            self._ensure_token(session, headers, proxies)
            per_subreddit = min(max(((count + len(requested) - 1) // len(requested)) * 3, 10), 100)
            responses = []
            failures = []
            refreshed_after_401 = False
            for index, subreddit in enumerate(requested):
                if index:
                    time.sleep(0.25)
                try:
                    try:
                        posts, fetched_at = self._fetch_listing(
                            session, subreddit, per_subreddit, headers, proxies
                        )
                    except RedditMobileAuthError:
                        if refreshed_after_401:
                            raise
                        self._invalidate_token()
                        self._ensure_token(session, headers, proxies)
                        refreshed_after_401 = True
                        posts, fetched_at = self._fetch_listing(
                            session, subreddit, per_subreddit, headers, proxies
                        )
                    responses.append((subreddit, posts, fetched_at))
                except Exception as exc:
                    failures.append((subreddit, exc))
            now = self.clock()
            cutoff = now - _LOOKBACK[time_filter] if time_filter in _LOOKBACK else None
            items = []
            seen = set()
            for _, posts, fetched_at in responses:
                for post in posts:
                    item = parse_mobile_post(post, requested, now=now)
                    if item is None or item.post_id in seen or not _matches_keyword(item, keyword):
                        continue
                    if cutoff and (
                        not item.created_at
                        or datetime.fromisoformat(item.created_at) < cutoff
                    ):
                        continue
                    item.raw["source_observed_at"] = fetched_at
                    seen.add(item.post_id)
                    items.append(item)
            items.sort(key=lambda item: item.created_at or "", reverse=True)
            return items[:count], responses, failures
        finally:
            if not self.request_fn:
                session.close()

    def _search_sync(self, requested, keyword, count, time_filter):
        with _MOBILE_SYNC_LOCK:
            return self._search_sync_unlocked(requested, keyword, count, time_filter)

    async def search_with_options(
        self, keyword, count=20, time_filter="", sort="", region="", options=None
    ):
        started = time.monotonic()
        requested = list(dict.fromkeys((options or {}).get("subreddits", [])))
        raw_records = []
        if time_filter not in {"", *_LOOKBACK}:
            items, responses, failures, error = [], [], [], "unsupported_time_filter"
        elif sort not in {"", "latest"}:
            items, responses, failures, error = [], [], [], "unsupported_sort"
        elif not CURL_CFFI_AVAILABLE and self.request_fn is None:
            items, responses, failures, error = [], [], [], "reddit_mobile_not_installed"
        else:
            try:
                async with _gate_for_current_loop():
                    items, responses, failures = await asyncio.to_thread(
                        self._search_sync, requested, keyword, count, time_filter
                    )
                raw_records = [
                    {
                        "source_id": str(post.get("name") or post.get("id") or ""),
                        "payload_format": "json",
                        "payload": post,
                        "fetched_at": fetched_at,
                    }
                    for _, posts, fetched_at in responses
                    for post in posts
                    if isinstance(post, dict)
                ]
                if failures and not responses:
                    failure_types = {type(exc) for _, exc in failures}
                    if RedditMobileRateLimitError in failure_types:
                        error = "reddit_mobile_rate_limited"
                    elif RedditMobileAuthError in failure_types:
                        error = "reddit_mobile_auth_failed"
                    else:
                        error = "reddit_mobile_unavailable"
                else:
                    error = None
            except RedditMobileRateLimitError:
                items, responses, failures, error = [], [], [], "reddit_mobile_rate_limited"
            except RedditMobileAuthError:
                items, responses, failures, error = [], [], [], "reddit_mobile_auth_failed"
            except Exception:
                items, responses, failures, error = [], [], [], "reddit_mobile_unavailable"
        successful = [subreddit for subreddit, _, _ in responses]
        failed = [subreddit for subreddit, _ in failures]
        if error:
            status = "error"
        elif failures or not items:
            status = "partial"
        else:
            status = "ok"
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
                coverage={
                    "kind": "owned_mobile_oauth_new_listings",
                    "requested_subreddits": requested,
                    "successful_subreddits": successful,
                    "failed_subreddits": failed,
                    "global_coverage": False,
                    "engagement_available": True,
                    "source_kind": "current_oauth_listing",
                },
            ),
            raw_records=raw_records,
        )

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        """Auto-discover relevant subreddits from the keyword, then search within them.

        This makes the connector work for ANY keyword without manual subreddit
        configuration. Uses the same OAuth token to call /subreddits/search.
        """
        if not CURL_CFFI_AVAILABLE and self.request_fn is None:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="error", items_requested=count,
                    error="reddit_mobile_not_installed",
                ),
            )
        if not keyword.strip():
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="skipped", items_requested=count,
                    error="empty_keyword",
                ),
            )

        started = time.monotonic()
        try:
            async with _gate_for_current_loop():
                subreddits = await asyncio.to_thread(self._discover_subreddits, keyword)
        except Exception:
            subreddits = []

        if not subreddits:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform, connector=self.connector_name,
                    status="partial", items_requested=count,
                    error="no_subreddits_found",
                    coverage={"global_coverage": False, "auto_discovery": True},
                ),
            )

        # Now search within the discovered subreddits
        return await self.search_with_options(
            keyword, count=count, time_filter=time_filter, sort=sort,
            region=region, options={"subreddits": subreddits[:self.max_subreddits]},
        )

    def _discover_subreddits(self, keyword):
        """Search for subreddits matching the keyword using the mobile OAuth token."""
        if self.request_fn:
            session = object()
        else:
            session = curl_requests.Session(impersonate="chrome")
        proxy = _proxy_url()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        headers = self._device_headers()
        try:
            self._ensure_token(session, headers, proxies)
            oauth_headers = dict(headers)
            oauth_headers.update(self._oauth_headers)
            oauth_headers["Authorization"] = f"Bearer {self._token}"
            response = self._request(
                session, "GET",
                f"{OAUTH_ORIGIN}/subreddits/search",
                params={"q": keyword, "limit": 10, "sort": "relevance", "raw_json": 1},
                headers=oauth_headers, proxies=proxies, timeout=25,
            )
            if response.status_code != 200:
                return []
            payload = self._json_response(response)
            children = payload.get("data", {}).get("children", [])
            if not isinstance(children, list):
                return []
            subreddits = []
            for child in children:
                if not isinstance(child, dict):
                    continue
                data = child.get("data") or {}
                display_name = str(data.get("display_name") or "").strip()
                if display_name and SUBREDDIT_RE.fullmatch(display_name):
                    subscribers = data.get("subscribers") or 0
                    subreddits.append((display_name, subscribers))
            # Sort by subscriber count descending — bigger communities = more content
            subreddits.sort(key=lambda pair: pair[1], reverse=True)
            return [name for name, _ in subreddits[:self.max_subreddits]]
        except Exception:
            return []
        finally:
            if not self.request_fn:
                session.close()

    async def health_check(self):
        return SourceHealth(
            platform=self.platform,
            connector=self.connector_name,
            status="ok" if CURL_CFFI_AVAILABLE else "error",
            error=None if CURL_CFFI_AVAILABLE else "reddit_mobile_not_installed",
            coverage={"global_coverage": False, "source_kind": "current_oauth_listing"},
        )
