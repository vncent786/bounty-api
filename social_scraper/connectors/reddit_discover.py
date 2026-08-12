"""Auto-discover relevant subreddits for any keyword using Reddit's mobile OAuth.

This solves the "no subreddit scope" problem: when a user searches for
"circle internet group" or "budgeting apps", we need to find which
communities discuss that topic before the scoped connectors can search.

Uses the same Android-client OAuth mechanism as reddit_mobile.py.
No developer API key required.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Optional

from social_scraper.connectors.reddit_mobile import (
    CURL_CFFI_AVAILABLE, ANDROID_CLIENT_ID, TOKEN_URL, OAUTH_ORIGIN,
    ANDROID_APP_VERSION, load_or_create_device_id,
    RedditMobileAuthError, RedditMobileRateLimitError,
    SUBREDDIT_RE,
)

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL = 3600  # 1 hour
_MAX_RESULTS = 10


def _proxy_config():
    """Read proxy config from env, return (proxies_dict, identity_tuple)."""
    proxy_url = os.getenv("BOUNTY_REDDIT_PROXY_SERVER", "").strip() or os.getenv("BOUNTY_PROXY_SERVER", "").strip()
    proxy_user = os.getenv("BOUNTY_REDDIT_PROXY_USERNAME", "").strip() or os.getenv("BOUNTY_PROXY_USERNAME", "").strip()
    proxy_pass = os.getenv("BOUNTY_REDDIT_PROXY_PASSWORD", "").strip() or os.getenv("BOUNTY_PROXY_PASSWORD", "").strip()
    if not proxy_url:
        return None, ()
    if proxy_user:
        # Insert credentials into URL
        scheme = "http://" if "://" not in proxy_url else proxy_url.split("://")[0] + "://"
        host = proxy_url.split("://")[-1]
        proxy_url = f"{scheme}{proxy_user}:{proxy_pass}@{host}"
    return {"http": proxy_url, "https": proxy_url}, (proxy_url,)


def discover_subreddits(keyword: str, max_results: int = 5) -> list[str]:
    """Find subreddits relevant to a keyword using Reddit's mobile OAuth.

    Returns a list of subreddit names (lowercase, validated).
    Cached for 1 hour per keyword.
    """
    key = keyword.strip().lower()
    if not key:
        return []

    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1][:max_results]

    subs = _search_subreddits(key)[:_MAX_RESULTS]
    _CACHE[key] = (now, subs)
    logger.info("Discovered %d subreddits for %r: %s", len(subs), keyword, subs[:5])
    return subs[:max_results]


def _search_subreddits(keyword: str) -> list[str]:
    """Call Reddit's subreddit search endpoint via mobile OAuth."""
    if not CURL_CFFI_AVAILABLE:
        logger.warning("curl-cffi not available, cannot discover subreddits")
        return []

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return []

    proxies, proxy_identity = _proxy_config()
    device_id = load_or_create_device_id()

    headers = {
        "User-Agent": f"Reddit/{ANDROID_APP_VERSION}/Android 13",
        "x-reddit-retry": "algo=no-retries",
        "x-reddit-compression": "1",
        "x-reddit-qos": "10.000",
        "Content-Type": "application/json; charset=UTF-8",
        "client-vendor-id": device_id,
        "X-Reddit-Device-Id": device_id,
    }

    session = cffi_requests.Session()
    try:
        # Step 1: Get OAuth token
        auth_headers = dict(headers)
        basic = base64.b64encode(f"{ANDROID_CLIENT_ID}:".encode()).decode()
        auth_headers["Authorization"] = f"Basic {basic}"

        token_resp = session.request(
            "POST", TOKEN_URL,
            headers=auth_headers,
            json={"scopes": ["read"]},
            proxies=proxies,
            timeout=20,
        )
        if token_resp.status_code == 429:
            logger.warning("Reddit subreddit discovery rate limited")
            return []
        if token_resp.status_code != 200:
            logger.warning("Reddit token failed for discovery: %s", token_resp.status_code)
            return []

        token_data = token_resp.json()
        token = token_data.get("access_token")
        if not token:
            return []

        # Step 2: Search subreddits
        oauth_headers = dict(headers)
        oauth_headers["Authorization"] = f"Bearer {token}"
        oauth_headers["x-reddit-loid"] = token_resp.headers.get("x-reddit-loid", "")
        oauth_headers["x-reddit-session"] = token_resp.headers.get("x-reddit-session", "")

        search_url = f"{OAUTH_ORIGIN}/subreddits/search"
        search_resp = session.request(
            "GET", search_url,
            headers=oauth_headers,
            params={"q": keyword, "limit": _MAX_RESULTS, "show": "all"},
            proxies=proxies,
            timeout=20,
        )
        if search_resp.status_code != 200:
            logger.warning("Reddit subreddit search failed: %s", search_resp.status_code)
            return []

        data = search_resp.json()
        children = data.get("data", {}).get("children", [])
        subs = []
        for child in children:
            if not isinstance(child, dict):
                continue
            sub_data = child.get("data", {})
            display_name = str(sub_data.get("display_name", "")).strip()
            if display_name and SUBREDDIT_RE.fullmatch(display_name):
                subscribers = sub_data.get("subscribers", 0)
                # Prefer communities with actual members
                if isinstance(subscribers, int) and subscribers > 0:
                    subs.append(display_name.lower())

        return list(dict.fromkeys(subs))

    except (RedditMobileAuthError, RedditMobileRateLimitError) as exc:
        logger.warning("Reddit discovery auth error: %s", exc)
        return []
    except Exception as exc:
        logger.warning("Reddit discovery failed: %s", exc)
        return []
    finally:
        session.close()
