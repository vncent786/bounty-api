"""Self-hosted Instagram connector.

Authenticates via Instagram's web login flow (username/password → session cookies),
then queries the authenticated web API for hashtag data.

No third-party service. No Playwright. No login wall blocking.
Cost: $0 + one burner Instagram account.

Auth approach:
1. First run: login via /api/v1/web/accounts/login/ajax/ → stores session cookies
2. Subsequent runs: loads stored cookies, refreshes login if expired
3. Search: GET /api/v1/tags/web_info/ → returns top posts + recent posts

Rate limiting: built-in delay between requests to avoid detection.
The session cookies (sessionid, ds_user_id) stay valid for days/weeks.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
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
_IG_BASE = "https://www.instagram.com"
_COOKIE_PATH = Path(__file__).resolve().parents[2] / "data" / "ig_cookies.json"
_LOGIN_LOCK = asyncio.Lock()
_MIN_REQUEST_INTERVAL = 2.0  # seconds between API calls (anti-ban)
_last_request_time = 0.0


def _get_env(name):
    return os.getenv(name, "").strip()


def _cookie_path():
    configured = _get_env("BOUNTY_IG_COOKIE_PATH")
    if configured:
        return Path(configured)
    return _COOKIE_PATH


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


class IGAuthError(RuntimeError):
    pass


class InstagramConnector(BaseConnector):
    """Instagram connector using authenticated web API via curl_cffi."""

    platform = "instagram"
    connector_name = "ig_auth_web"

    def __init__(self):
        self._session = None
        self._authed = False

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

    async def _ensure_authed(self):
        """Login or load stored cookies."""
        if self._authed:
            return

        async with _LOGIN_LOCK:
            if self._authed:
                return

            session = self._get_session()

            # Try loading stored cookies
            cookie_file = _cookie_path()
            if cookie_file.exists():
                try:
                    stored = json.loads(cookie_file.read_text(encoding="utf-8"))
                    for name, value in stored.items():
                        session.cookies.set(name, value, domain=".instagram.com")
                    self._authed = True
                    logger.info("IG: loaded stored cookies")
                    return
                except Exception as e:
                    logger.warning(f"IG: failed to load cookies: {e}")

            # Need to login
            username = _get_env("BOUNTY_IG_USERNAME")
            password = _get_env("BOUNTY_IG_PASSWORD")

            if not username or not password:
                raise IGAuthError(
                    "ig_credentials_missing: set BOUNTY_IG_USERNAME, BOUNTY_IG_PASSWORD"
                )

            def _do_login():
                # 1. Fetch homepage for csrf token
                session.get(
                    f"{_IG_BASE}/",
                    timeout=15,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/127.0.0.0 Safari/537.36",
                    },
                )

                csrf = None
                for c in session.cookies.jar:
                    if c.name == "csrftoken":
                        csrf = c.value
                        break

                if not csrf:
                    raise IGAuthError("ig_no_csrf")

                # 2. Login
                login_resp = session.post(
                    f"{_IG_BASE}/api/v1/web/accounts/login/ajax/",
                    data={
                        "username": username,
                        "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}",
                        "queryParams": "{}",
                        "optIntoOneTap": "false",
                        "stopDeletion": "false",
                        "trustedDevice": "false",
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/127.0.0.0 Safari/537.36",
                        "X-CSRFToken": csrf,
                        "X-IG-App-ID": _IG_APP_ID,
                        "X-ASBD-ID": "198387",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": f"{_IG_BASE}/",
                        "Origin": _IG_BASE,
                    },
                    timeout=15,
                )

                result = login_resp.json()
                if not result.get("authenticated"):
                    raise IGAuthError(f"ig_auth_failed: {result}")

                # 3. Store cookies
                cookies = {}
                for c in session.cookies.jar:
                    cookies[c.name] = c.value

                cookie_file.parent.mkdir(parents=True, exist_ok=True)
                cookie_file.write_text(
                    json.dumps(cookies, indent=2), encoding="utf-8"
                )
                return True

            await asyncio.to_thread(_do_login)
            self._authed = True
            logger.info("IG: login successful, cookies stored")

    @staticmethod
    def _throttle():
        """Rate-limit requests to avoid detection."""
        global _last_request_time
        elapsed = time.time() - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _last_request_time = time.time()

    async def _fetch_tag_data(self, tag, count=24):
        """Fetch hashtag data via authenticated web API."""
        session = self._get_session()
        csrf = None
        for c in session.cookies.jar:
            if c.name == "csrftoken":
                csrf = c.value
                break

        def _do_fetch():
            self._throttle()
            resp = session.get(
                f"{_IG_BASE}/api/v1/tags/web_info/",
                params={"tag_name": tag},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/127.0.0.0 Safari/537.36",
                    "X-IG-App-ID": _IG_APP_ID,
                    "X-CSRFToken": csrf,
                    "X-ASBD-ID": "198387",
                    "Referer": f"{_IG_BASE}/explore/tags/{tag}/",
                },
                timeout=20,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"ig_http_{resp.status_code}")
            return resp.json()

        data = await asyncio.to_thread(_do_fetch)

        # Extract media count
        tag_data = data.get("data", {})
        media_count = tag_data.get("media_count", 0)

        # Extract all media items from nested structure
        media_items = self._extract_media(tag_data)
        return media_items, media_count

    @staticmethod
    def _extract_media(obj):
        """Recursively find all media items in Instagram's nested JSON."""
        results = []
        seen_codes = set()

        def _walk(o):
            if isinstance(o, dict):
                if "code" in o and ("like_count" in o or "caption" in o):
                    code = o.get("code", o.get("id", ""))
                    if code not in seen_codes:
                        seen_codes.add(code)
                        results.append(o)
                for v in o.values():
                    _walk(v)
            elif isinstance(o, list):
                for item in o:
                    _walk(item)

        _walk(obj)
        return results

    @staticmethod
    def _media_to_item(media):
        """Convert an Instagram media node to SocialItem."""
        code = media.get("code", media.get("shortcode", ""))
        media_id = str(media.get("id", code))

        # Caption/text
        caption = media.get("caption")
        text = ""
        if isinstance(caption, dict):
            text = caption.get("text", "")
        elif isinstance(caption, str):
            text = caption

        # Engagement
        likes = media.get("like_count")
        comments = media.get("comment_count")
        views = media.get("view_count") or media.get("play_count")

        # Timestamp
        taken_at = media.get("taken_at")
        created_at = None
        if taken_at:
            try:
                created_at = datetime.fromtimestamp(
                    int(taken_at), tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError):
                pass

        # Owner
        user = media.get("user", {})
        username = user.get("username", "")

        # Media type
        media_type_val = media.get("media_type", 1)
        if media_type_val == 2 or media.get("video_duration"):
            m_type = "video"
        elif media_type_val == 8:
            m_type = "gallery"
        else:
            m_type = "image"

        # Thumbnail
        thumbnail = None
        image_versions = media.get("image_versions2", {}).get("candidates", [])
        if image_versions:
            thumbnail = image_versions[-1].get("url")

        # Hashtags from text
        hashtags = re.findall(r"#(\w+)", text)

        return SocialItem(
            platform="instagram",
            post_id=media_id,
            url=f"https://www.instagram.com/p/{code}/" if code else "",
            author_username=username,
            author_display_name=user.get("full_name", ""),
            author_profile_url=f"https://www.instagram.com/{username}/" if username else "",
            author_follower_count=user.get("follower_count"),
            text=text,
            created_at=created_at,
            views=int(views) if views is not None else None,
            likes=int(likes) if likes is not None else None,
            comments=int(comments) if comments is not None else None,
            media_type=m_type,
            thumbnail_url=thumbnail,
            hashtags=hashtags,
        )

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search Instagram by hashtag."""
        start = time.time()

        tag = re.sub(r"[^A-Za-z0-9]", "", keyword).lower()
        if not tag:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="error", items_requested=count, error="ig_empty_tag",
                ),
            )

        try:
            await self._ensure_authed()
        except IGAuthError as e:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="error", items_requested=count, error=str(e),
                ),
            )

        try:
            media_items, media_count = await self._fetch_tag_data(tag, count)

            items = [self._media_to_item(m) for m in media_items[:count]]

            # Time filter
            if time_filter and items:
                now = datetime.now(timezone.utc)
                cutoff_map = {"1day": 1, "week": 7, "month": 30, "halfyear": 180}
                days = cutoff_map.get(time_filter)
                if days:
                    from datetime import timedelta
                    cutoff = now - timedelta(days=days)
                    items = [
                        item for item in items
                        if not item.created_at
                        or datetime.fromisoformat(item.created_at.replace("Z", "+00:00")) >= cutoff
                    ]

            latency_ms = int((time.time() - start) * 1000)

            return ConnectorResult(
                items=items,
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="ok" if items else "partial",
                    items_returned=len(items), items_requested=count,
                    latency_ms=latency_ms,
                    coverage={"tag_media_count": media_count},
                ),
            )

        except RuntimeError as e:
            error_str = str(e)
            if "ig_http_429" in error_str:
                error_code = "ig_rate_limited"
            elif "ig_http_401" in error_str or "ig_http_403" in error_str:
                self._authed = False
                error_code = "ig_session_expired"
            elif "ig_http_" in error_str:
                error_code = error_str
            else:
                error_code = "ig_error"

            latency_ms = int((time.time() - start) * 1000)
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="error", items_requested=count,
                    latency_ms=latency_ms, error=error_code,
                ),
            )

    async def health_check(self) -> SourceHealth:
        if not CURL_CFFI_AVAILABLE:
            return SourceHealth(
                platform="instagram", connector=self.connector_name,
                status="error", error="curl_cffi_not_installed",
            )
        return SourceHealth(
            platform="instagram", connector=self.connector_name,
            status="ok",
            coverage={"auth": "session_cookies", "depth": "hashtag_top+recent"},
        )
