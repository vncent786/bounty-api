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
from urllib.parse import quote, urlparse

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
from social_scraper.owned_worker_lock import AsyncFileLock
from social_scraper.proxy_config import build_playwright_proxy

logger = logging.getLogger(__name__)

_IG_APP_ID = "936619743392459"
_IG_BASE = "https://www.instagram.com"
_COOKIE_PATH = Path(__file__).resolve().parents[2] / "data" / "ig_cookies.json"
_LOGIN_LOCK = asyncio.Lock()
_IG_LOCK_PATH = Path(__file__).resolve().parents[2] / "data" / "ig_session.lock"
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
        parsed = urlparse(server)
        return f"{parsed.scheme}://{username}:{password}@{parsed.hostname}:{parsed.port}"
    return server


def _ig_playwright_proxy():
    value = os.getenv("BOUNTY_IG_PROXY", "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError("ig_proxy_invalid")
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


class IGAuthError(RuntimeError):
    pass


class InstagramConnector(BaseConnector):
    """Instagram connector using authenticated web API via curl_cffi."""

    platform = "instagram"
    connector_name = "ig_auth_web"

    def __init__(self):
        self._session = None
        self._authed = False
        self._last_tag_payload = None

    def _get_session(self):
        if self._session is not None:
            return self._session

        if not CURL_CFFI_AVAILABLE:
            raise RuntimeError("curl_cffi_not_installed")

        impersonate = "chrome124"
        # Instagram sessions are IP-bound. Using a proxy that doesn't match
        # the session's origin IP triggers checkpoint/challenge. Connect direct
        # unless an IG-specific proxy is explicitly configured.
        proxy = os.getenv("BOUNTY_IG_PROXY", "").strip()
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

                    # Warm up: hit homepage to collect csrftoken + other cookies
                    # the server sets on the response. With a valid sessionid,
                    # this gives us a fully authenticated cookie set without login.
                    def _warmup():
                        session.get(
                            f"{_IG_BASE}/",
                            timeout=15,
                            headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                                              "Chrome/127.0.0.0 Safari/537.36",
                            },
                        )
                    await asyncio.to_thread(_warmup)

                    # Verify session is valid (not redirect to login)
                    csrf = None
                    for c in session.cookies.jar:
                        if c.name == "csrftoken":
                            csrf = c.value
                            break
                    if not csrf:
                        raise IGAuthError("ig_session_expired: no csrftoken after warmup")

                    account_id = None
                    for cookie in session.cookies.jar:
                        if cookie.name == "ds_user_id":
                            account_id = cookie.value
                            break
                    if not account_id:
                        raise IGAuthError(
                            "ig_session_expired: ds_user_id missing"
                        )

                    def _verify_login():
                        response = session.get(
                            f"{_IG_BASE}/api/v1/users/{account_id}/info/",
                            timeout=20,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/127.0.0.0 Safari/537.36"
                                ),
                                "X-IG-App-ID": _IG_APP_ID,
                                "X-CSRFToken": csrf,
                                "X-ASBD-ID": "198387",
                                "Referer": f"{_IG_BASE}/",
                            },
                        )
                        if response.status_code != 200:
                            raise IGAuthError(
                                f"ig_session_expired: user_info_http_{response.status_code}"
                            )
                        payload = response.json()
                        if not isinstance(payload.get("user"), dict):
                            raise IGAuthError(
                                "ig_session_expired: authenticated user missing"
                            )

                    await asyncio.to_thread(_verify_login)
                    self._authed = True
                    logger.info("IG: loaded stored cookies + warmed up session")
                    return
                except IGAuthError:
                    raise
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
                timeout=45,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"ig_http_{resp.status_code}")
            return resp.json()

        data = await asyncio.to_thread(_do_fetch)
        self._last_tag_payload = data

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
        user = media.get("user") or {}
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
        image_versions = (media.get("image_versions2") or {}).get("candidates", [])
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
            raw={"source_payload": media},
        )

    async def _browser_keyword_search(self, keyword: str, count: int):
        """Use Instagram's own keyword-search page and capture its GraphQL data."""
        if not PLAYWRIGHT_AVAILABLE:
            return [], []
        cookie_file = _cookie_path()
        if not cookie_file.exists():
            return [], []
        stored = json.loads(cookie_file.read_text(encoding="utf-8"))
        cookies = [
            {"name": name, "value": str(value), "domain": ".instagram.com", "path": "/"}
            for name, value in stored.items()
            if value is not None
        ]
        media_items = []
        raw_records = []
        seen = set()
        async with async_playwright() as playwright:
            launch_kwargs = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            proxy = _ig_playwright_proxy()
            if proxy:
                launch_kwargs["proxy"] = proxy
            browser = await playwright.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/127.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            async def on_response(response):
                if "/graphql/query" not in response.url or response.status != 200:
                    return
                try:
                    payload = await response.json()
                except Exception:
                    return
                extracted = self._extract_media(payload.get("data") or {})
                if not extracted:
                    return
                raw_records.append({
                    "source_id": f"instagram_keyword_graphql:{len(raw_records) + 1}",
                    "payload_format": "json",
                    "payload": payload,
                })
                for media in extracted:
                    identity = str(media.get("id") or media.get("pk") or media.get("code") or "")
                    if not identity or identity in seen:
                        continue
                    seen.add(identity)
                    media_items.append(media)

            page.on("response", on_response)
            try:
                await page.goto(
                    f"{_IG_BASE}/explore/search/keyword/?q={quote(keyword)}",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await page.wait_for_timeout(7000)
                for _ in range(3):
                    if len(media_items) >= count:
                        break
                    await page.evaluate("window.scrollBy(0, 1200)")
                    await page.wait_for_timeout(2000)
            finally:
                await context.close()
                await browser.close()
        return media_items[:count], raw_records

    def _request_headers(self, referer: str) -> dict:
        session = self._get_session()
        csrf = None
        for cookie in session.cookies.jar:
            if cookie.name == "csrftoken":
                csrf = cookie.value
                break
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            "X-IG-App-ID": _IG_APP_ID,
            "X-CSRFToken": csrf or "",
            "X-ASBD-ID": "198387",
            "Referer": referer,
        }

    async def _collect_media_comments(
        self, media_id: str, referer: str, max_comments: int
    ) -> list[dict]:
        session = self._get_session()
        headers = self._request_headers(referer)
        payloads = []
        cursor = None
        page_budget = max(1, (max_comments + 19) // 20)
        for _ in range(page_budget):
            params = {
                "can_support_threading": "true",
                "permalink_enabled": "false",
            }
            if cursor:
                params["min_id"] = cursor

            def fetch():
                self._throttle()
                response = session.get(
                    f"{_IG_BASE}/api/v1/media/{media_id}/comments/",
                    params=params,
                    headers=headers,
                    timeout=45,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"ig_comments_http_{response.status_code}")
                return response.json()

            payload = await asyncio.to_thread(fetch)
            payloads.append(payload)
            cursor = payload.get("next_min_id")
            if not cursor or not payload.get("has_more_comments"):
                break
        return payloads

    async def _collect_child_comments(
        self, media_id: str, parent_id: str, referer: str, limit: int
    ) -> list[dict]:
        session = self._get_session()
        headers = self._request_headers(referer)
        payloads = []
        cursor = None
        page_budget = max(1, (limit + 19) // 20)
        for _ in range(page_budget):
            params = {"sort_order": "popular"}
            if cursor:
                params["min_id"] = cursor

            def fetch():
                self._throttle()
                response = session.get(
                    f"{_IG_BASE}/api/v1/media/{media_id}/comments/{parent_id}/child_comments/",
                    params=params,
                    headers=headers,
                    timeout=45,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"ig_replies_http_{response.status_code}")
                return response.json()

            payload = await asyncio.to_thread(fetch)
            payloads.append(payload)
            cursor = payload.get("next_min_child_cursor")
            if not cursor or not payload.get("has_more_tail_child_comments"):
                break
        return payloads

    async def fetch_thread(
        self, post: SocialItem, max_comments: int, max_depth: int
    ) -> ThreadFetchResult:
        route = "instagram_authenticated_web_comments"
        if post.platform != "instagram" or not post.post_id or not post.url:
            return ThreadFetchResult(
                platform="instagram",
                root_post_external_id=post.post_id or "unknown",
                status="error",
                attempted_route=route,
                error_category="invalid_instagram_post",
                max_comments=max_comments,
                max_depth=max_depth,
            )
        if max_comments <= 0 or max_depth <= 0:
            return ThreadFetchResult(
                platform="instagram",
                root_post_external_id=post.post_id,
                status="empty",
                attempted_route=route,
                max_comments=max_comments,
                max_depth=max_depth,
            )
        try:
            async with AsyncFileLock(_IG_LOCK_PATH):
                await self._ensure_authed()
                root_payloads = await self._collect_media_comments(
                    post.post_id, post.url, max_comments
                )
                child_payloads = {}
                if max_depth >= 2:
                    root_budget_for_fetch = max(
                        1, max_comments - max(1, max_comments // 4)
                    )
                    eligible_parents = []
                    roots_seen = 0
                    for payload in root_payloads:
                        for comment in payload.get("comments") or []:
                            if roots_seen >= root_budget_for_fetch:
                                break
                            roots_seen += 1
                            child_count = comment.get("child_comment_count")
                            parent_id = str(
                                comment.get("pk") or comment.get("id") or ""
                            )
                            if (
                                isinstance(child_count, int)
                                and child_count > 0
                                and parent_id
                            ):
                                eligible_parents.append((parent_id, child_count))
                    reply_budget = max_comments - root_budget_for_fetch
                    for parent_id, child_count in eligible_parents[:5]:
                        if reply_budget <= 0:
                            break
                        limit = min(reply_budget, child_count)
                        payload_list = await self._collect_child_comments(
                            post.post_id, parent_id, post.url, limit
                        )
                        child_payloads[parent_id] = payload_list
                        returned = sum(
                            len(payload.get("child_comments") or [])
                            for payload in payload_list
                        )
                        reply_budget -= min(reply_budget, returned)
        except Exception as exc:
            error_text = str(exc).lower()
            error_category = "instagram_comments_unavailable"
            if any(marker in error_text for marker in (
                "401", "403", "checkpoint", "challenge", "session_expired"
            )):
                self._authed = False
                error_category = "ig_session_expired"
            elif "429" in error_text:
                error_category = "ig_rate_limited"
            return ThreadFetchResult(
                platform="instagram",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route=route,
                error_category=error_category,
                max_comments=max_comments,
                max_depth=max_depth,
                limitations=("Authenticated Instagram comment route failed.",),
            )

        if not root_payloads:
            return ThreadFetchResult(
                platform="instagram",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route=route,
                error_category="instagram_comments_not_returned",
                max_comments=max_comments,
                max_depth=max_depth,
            )
        records = []
        seen = set()
        reported_total = None
        source_has_more = False
        child_totals = {}
        root_budget = max_comments
        if max_depth >= 2:
            root_budget = max(1, max_comments - max(1, max_comments // 4))
        for payload in root_payloads:
            total = payload.get("comment_count")
            if isinstance(total, int):
                reported_total = max(reported_total or 0, total)
            source_has_more = source_has_more or bool(payload.get("has_more_comments"))
            for comment in payload.get("comments") or []:
                if len(records) >= root_budget:
                    break
                record = self._thread_record(
                    comment,
                    root_post_id=post.post_id,
                    parent_id=post.post_id,
                    depth=1,
                    post_url=post.url,
                )
                if record is None or record.external_id in seen:
                    continue
                seen.add(record.external_id)
                records.append(record)
                child_count = comment.get("child_comment_count")
                if isinstance(child_count, int) and child_count > 0:
                    child_totals[record.external_id] = child_count

        returned_children = {}
        root_ids = {record.external_id for record in records if record.depth == 1}
        if max_depth >= 2 and len(records) < max_comments:
            for parent_id, payload_list in child_payloads.items():
                if parent_id not in root_ids:
                    continue
                for payload in payload_list:
                    source_has_more = source_has_more or bool(
                        payload.get("has_more_tail_child_comments")
                    )
                    for comment in payload.get("child_comments") or []:
                        if len(records) >= max_comments:
                            break
                        record = self._thread_record(
                            comment,
                            root_post_id=post.post_id,
                            parent_id=parent_id,
                            depth=2,
                            post_url=post.url,
                        )
                        if record is None or record.external_id in seen:
                            continue
                        seen.add(record.external_id)
                        records.append(record)
                        returned_children[parent_id] = returned_children.get(parent_id, 0) + 1

        root_returned = len([record for record in records if record.depth == 1])
        omitted_children = any(
            returned_children.get(parent_id, 0) < total
            for parent_id, total in child_totals.items()
        )
        if reported_total is None and isinstance(post.comments, int):
            reported_total = post.comments
        unknown_total = reported_total is None
        truncated = (
            unknown_total
            or source_has_more
            or omitted_children
            or (reported_total is not None and reported_total > root_returned)
            or len(records) >= max_comments
        )
        status = (
            "empty"
            if not records and reported_total == 0 and not truncated
            else "partial" if truncated else "complete"
        )
        return ThreadFetchResult(
            platform="instagram",
            root_post_external_id=post.post_id,
            status=status,
            records=tuple(records),
            truncated=truncated,
            attempted_route=route,
            platform_reported_total=reported_total,
            max_comments=max_comments,
            max_depth=max_depth,
            limitations=(
                "Instagram web returns ranked comments; bounded reads may omit lower-ranked comments and replies.",
            ),
        )

    @staticmethod
    def _thread_record(
        comment: dict,
        *,
        root_post_id: str,
        parent_id: str,
        depth: int,
        post_url: str,
    ) -> ThreadRecord | None:
        comment_id = str(comment.get("pk") or comment.get("id") or "")
        if not comment_id:
            return None
        user = comment.get("user") or {}
        timestamp = (
            comment.get("created_at_utc")
            or comment.get("created_at")
            or comment.get("created_time")
        )
        published_at = None
        if timestamp:
            try:
                published_at = datetime.fromtimestamp(
                    int(timestamp), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                pass
        likes = comment.get("comment_like_count")
        if likes is None:
            likes = comment.get("like_count")
        if not isinstance(likes, int) or isinstance(likes, bool):
            likes = None
        return ThreadRecord(
            platform="instagram",
            external_id=comment_id,
            record_type="comment" if depth == 1 else "reply",
            parent_external_id=parent_id,
            root_post_external_id=root_post_id,
            depth=depth,
            text=comment.get("text"),
            author_external_id=str(user.get("pk") or user.get("id") or "") or None,
            author_username=user.get("username"),
            url=f"{post_url}?comment_id={comment_id}",
            published_at=published_at,
            likes=likes,
            raw=comment,
        )

    async def search(self, keyword: str, count: int = 20, time_filter: str = "",
                     sort: str = "", region: str = "") -> ConnectorResult:
        """Search Instagram's authenticated keyword page, then hashtag fallback."""
        start = time.time()
        keyword = str(keyword or "").strip()
        if not keyword:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="error", items_requested=count, error="ig_empty_query",
                ),
            )

        try:
            async with AsyncFileLock(_IG_LOCK_PATH):
                await self._ensure_authed()
                route = "keyword_browser_graphql"
                media_count = None
                raw_records = []
                browser_error = None
                try:
                    media_items, raw_records = await self._browser_keyword_search(
                        keyword, count
                    )
                except Exception as exc:
                    browser_error = type(exc).__name__
                    media_items = []

                if not media_items:
                    tag = re.sub(r"[^A-Za-z0-9]", "", keyword).lower()
                    if not tag:
                        raise RuntimeError("ig_empty_tag")
                    route = "hashtag_web_info"
                    media_items, media_count = await self._fetch_tag_data(tag, count)
                    if isinstance(self._last_tag_payload, dict):
                        raw_records.append({
                            "source_id": f"instagram_hashtag_web_info:{tag}",
                            "payload_format": "json",
                            "payload": self._last_tag_payload,
                        })

            items = []
            for media in media_items[:count]:
                try:
                    item = self._media_to_item(media)
                except (TypeError, ValueError):
                    continue
                if item.post_id:
                    items.append(item)
            if time_filter and items:
                from datetime import timedelta

                now = datetime.now(timezone.utc)
                cutoff_map = {"1day": 1, "week": 7, "month": 30, "halfyear": 180}
                days = cutoff_map.get(time_filter)
                if days:
                    cutoff = now - timedelta(days=days)
                    items = [
                        item for item in items
                        if not item.created_at
                        or datetime.fromisoformat(
                            item.created_at.replace("Z", "+00:00")
                        ) >= cutoff
                    ]
            if sort == "latest":
                items.sort(key=lambda item: item.created_at or "", reverse=True)
            elif sort == "hot":
                items.sort(
                    key=lambda item: (item.likes or 0) + (item.comments or 0),
                    reverse=True,
                )

            latency_ms = int((time.time() - start) * 1000)
            coverage = {
                "route": route,
                "query": keyword,
                "tag_media_count": media_count,
            }
            if browser_error:
                coverage["keyword_browser_error"] = browser_error
            return ConnectorResult(
                items=items,
                health=SourceHealth(
                    platform="instagram", connector=self.connector_name,
                    status="ok" if items else "partial",
                    items_returned=len(items), items_requested=count,
                    latency_ms=latency_ms,
                    coverage=coverage,
                ),
                raw_records=raw_records,
            )

        except (IGAuthError, RuntimeError) as exc:
            error_str = str(exc)
            if "ig_http_429" in error_str:
                error_code = "ig_rate_limited"
            elif "ig_http_401" in error_str or "ig_http_403" in error_str:
                self._authed = False
                error_code = "ig_session_expired"
            elif "ig_session_expired" in error_str:
                error_code = "ig_session_expired"
            elif "ig_credentials_missing" in error_str:
                error_code = "ig_credentials_missing"
            elif "ig_empty" in error_str:
                error_code = error_str
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
        try:
            async with AsyncFileLock(_IG_LOCK_PATH):
                await self._ensure_authed()
        except IGAuthError:
            return SourceHealth(
                platform="instagram",
                connector=self.connector_name,
                status="error",
                error="ig_session_expired",
                coverage={"network_check": True},
            )
        return SourceHealth(
            platform="instagram",
            connector=self.connector_name,
            status="ok",
            coverage={
                "auth": "session_cookies",
                "auth_verified": True,
                "network_check": True,
                "search": "keyword_graphql+hashtag_fallback",
                "depth": "root_comments+child_comments",
                "playwright_available": PLAYWRIGHT_AVAILABLE,
            },
        )
