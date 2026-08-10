"""Reddit depth connector using bounded Camoufox worker processes.

PullPush remains the fast global keyword-discovery route. Camoufox provides
allowlisted subreddit feeds and exact canonical post/comment hydration.
"""

import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.proxy_config import build_playwright_proxy

try:
    from camoufox.sync_api import Camoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    Camoufox = None
    CAMOUFOX_AVAILABLE = False


SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
POST_PATH_RE = re.compile(
    r"^/r/([A-Za-z0-9_]{2,21})/comments/([A-Za-z0-9]+)/([A-Za-z0-9_-]+)/?$"
)
_ALLOWED_BROWSER_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "redd.it",
    "redditstatic.com",
    "redditmedia.com",
}
_CAMOUFOX_GATES = weakref.WeakKeyDictionary()


class CamoufoxBusyError(RuntimeError):
    pass


class CamoufoxTimeoutError(RuntimeError):
    pass


class CamoufoxChallengeError(RuntimeError):
    pass


def is_reddit_challenge_page(title: str, body_text: str) -> bool:
    combined = f"{title} {body_text}".lower()
    markers = (
        "please wait for verification",
        "blocked by network security",
        "whoa there, pardner",
    )
    return any(marker in combined for marker in markers)


def _gate_for_current_loop():
    loop = asyncio.get_running_loop()
    gate = _CAMOUFOX_GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(1)
        _CAMOUFOX_GATES[loop] = gate
    return gate


def _allowed_browser_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme in {"data", "blob"}:
        return True
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in _ALLOWED_BROWSER_HOSTS)


def _install_egress_guard(page):
    def guard(route):
        if _allowed_browser_url(route.request.url):
            route.continue_()
        else:
            route.abort()
    page.route("**/*", guard)


def _camoufox_launch_options():
    options = {"headless": True}
    proxy = build_playwright_proxy()
    reddit_proxy_server = os.getenv("BOUNTY_REDDIT_PROXY_SERVER", "").strip()
    if reddit_proxy_server:
        proxy = dict(proxy or {})
        proxy["server"] = reddit_proxy_server
    if proxy:
        options["proxy"] = proxy
    return options


def validate_reddit_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"reddit.com", "www.reddit.com"}
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Only public HTTPS Reddit URLs are supported")
    try:
        if parsed.port not in {None, 443}:
            raise ValueError("Only the standard HTTPS port is supported")
    except ValueError as exc:
        raise ValueError("Invalid Reddit URL port") from exc
    if unquote(parsed.path) != parsed.path:
        raise ValueError("Encoded Reddit paths are not supported")
    match = POST_PATH_RE.fullmatch(parsed.path)
    if not match:
        raise ValueError("A canonical Reddit post URL is required")
    subreddit, post_id, slug = match.groups()
    return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/{slug}/"


def validate_hydrated_post_identity(canonical_url: str, rendered_permalink: str, title: str) -> bool:
    if not rendered_permalink or not title.strip():
        raise RuntimeError("Reddit post identity is missing from the rendered page")
    if not rendered_permalink.startswith("http"):
        rendered_permalink = f"https://www.reddit.com{rendered_permalink}"
    try:
        rendered_canonical = validate_reddit_url(rendered_permalink)
    except ValueError as exc:
        raise RuntimeError("Rendered Reddit post identity is invalid") from exc
    expected = POST_PATH_RE.fullmatch(urlsplit(canonical_url).path)
    rendered = POST_PATH_RE.fullmatch(urlsplit(rendered_canonical).path)
    if expected.group(1).lower() != rendered.group(1).lower() or expected.group(2) != rendered.group(2):
        raise RuntimeError("Rendered Reddit post does not match the requested post")
    return True


def _parse_created(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _matches_keyword(title: str, keyword: str) -> bool:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]+", keyword)]
    haystack = title.lower()
    if not keyword.strip():
        return True
    if not terms:
        return False
    return all(re.search(rf"\b{re.escape(term)}\b", haystack) for term in terms)


def normalize_feed_posts(
    raw_posts: list[dict],
    keyword: str = "",
    count: int = 20,
    allowed_subreddits: Optional[list[str]] = None,
) -> list[SocialItem]:
    items = []
    seen_post_ids = set()
    for raw in raw_posts:
        title = str(raw.get("title") or "").strip()
        permalink = str(raw.get("permalink") or "").strip()
        if not title or not permalink or not _matches_keyword(title, keyword):
            continue
        try:
            if not permalink.startswith("http"):
                permalink = f"https://www.reddit.com{permalink}"
            canonical = validate_reddit_url(permalink)
        except ValueError:
            continue
        path_match = POST_PATH_RE.fullmatch(urlsplit(canonical).path)
        path_subreddit = path_match.group(1)
        if allowed_subreddits and path_subreddit.lower() not in {
            value.lower() for value in allowed_subreddits
        }:
            continue
        post_id = path_match.group(2)
        if post_id in seen_post_ids:
            continue
        seen_post_ids.add(post_id)
        subreddit = path_subreddit
        author = str(raw.get("author") or "")
        items.append(SocialItem(
            platform="reddit",
            post_id=post_id,
            url=canonical,
            author_username=author,
            author_profile_url=f"https://www.reddit.com/user/{author}" if author else "",
            text=title,
            created_at=_parse_created(raw.get("created")),
            likes=raw.get("score") if isinstance(raw.get("score"), int) else None,
            comments=raw.get("comments") if isinstance(raw.get("comments"), int) else None,
            media_type="text",
            raw={"subreddit": subreddit, "flair": raw.get("flair"), "feed": raw.get("feed")},
        ))
        if len(items) >= count:
            break
    return items


def _feed_sorts(sort: str):
    if sort in {"latest", "new"}:
        return ["new"]
    if sort == "hot":
        return ["hot"]
    if sort == "rising":
        return ["rising"]
    return ["rising", "new"]


def _cutoff_for(time_filter: str):
    duration = {
        "1day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }.get(time_filter)
    return datetime.now(timezone.utc) - duration if duration else None


def scan_reddit_feeds(subreddits, keyword, count, time_filter="", sort=""):
    if not CAMOUFOX_AVAILABLE:
        raise RuntimeError("Camoufox is not installed")
    raw_posts = []
    cutoff = _cutoff_for(time_filter)
    with Camoufox(**_camoufox_launch_options()) as browser:
        page = browser.new_page()
        _install_egress_guard(page)
        for subreddit in subreddits:
            if not SUBREDDIT_RE.fullmatch(subreddit):
                continue
            for feed in _feed_sorts(sort):
                page.goto(
                    f"https://www.reddit.com/r/{subreddit}/{feed}/",
                    timeout=45000,
                    wait_until="domcontentloaded",
                )
                if not _allowed_browser_url(page.url):
                    raise RuntimeError("Reddit redirected outside the allowed host set")
                time.sleep(5)
                if is_reddit_challenge_page(page.title(), page.locator("body").inner_text()[:500]):
                    raise RuntimeError("Reddit verification challenge")
                for _ in range(3):
                    page.mouse.wheel(0, 2500)
                    time.sleep(1.2)
                rows = page.eval_on_selector_all(
                    "shreddit-post",
                    """els => els.map(e => ({
                        title: e.getAttribute('post-title') || '',
                        permalink: e.getAttribute('permalink') || e.getAttribute('content-href') || '',
                        score: (() => { const v = e.getAttribute('score') || ''; return /^\\d+$/.test(v) ? Number(v) : null; })(),
                        comments: (() => { const v = e.getAttribute('comment-count') || ''; return /^\\d+$/.test(v) ? Number(v) : null; })(),
                        created: e.getAttribute('created-timestamp') || '',
                        author: e.getAttribute('author') || '',
                        flair: (e.querySelector('[slot="post-flair"]')?.innerText || '').trim()
                    }))""",
                )
                for row in rows:
                    row["requested_subreddit"] = subreddit
                    row["feed"] = feed
                    created = _parse_created(row.get("created"))
                    if cutoff:
                        if not created or datetime.fromisoformat(created) < cutoff:
                            continue
                    raw_posts.append(row)
    if sort in {"hot", "rising"}:
        raw_posts.sort(key=lambda row: (row.get("score") or -1, row.get("created") or ""), reverse=True)
    else:
        raw_posts.sort(key=lambda row: row.get("created") or "", reverse=True)
    return raw_posts


def hydrate_reddit_post(url: str, comment_limit: int = 20):
    canonical = validate_reddit_url(url)
    if not CAMOUFOX_AVAILABLE:
        raise RuntimeError("Camoufox is not installed")
    with Camoufox(**_camoufox_launch_options()) as browser:
        page = browser.new_page()
        _install_egress_guard(page)
        page.goto(canonical, timeout=45000, wait_until="domcontentloaded")
        final_url = validate_reddit_url(page.url)
        if final_url != canonical:
            raise RuntimeError("Reddit redirected away from the canonical post")
        time.sleep(5)
        if is_reddit_challenge_page(page.title(), page.locator("body").inner_text()[:500]):
            raise RuntimeError("Reddit verification challenge")
        post_root = page.locator("shreddit-post").first
        if not post_root.count():
            raise RuntimeError("Reddit post root is missing from the rendered page")
        rendered_permalink = post_root.get_attribute("permalink") or ""
        title = (post_root.get_attribute("post-title") or "").strip()
        if not title:
            title_locator = post_root.locator("[slot='title'], h1").first
            title = (title_locator.text_content() or "").strip() if title_locator.count() else ""
        validate_hydrated_post_identity(canonical, rendered_permalink, title)
        body_locator = post_root.locator(
            "div[data-slot='text-body'], [slot='text-body'] .md"
        ).first
        body = body_locator.text_content() if body_locator.count() else ""
        platform_total_text = post_root.get_attribute("comment-count") or ""
        platform_reported_total = (
            int(platform_total_text) if platform_total_text.isdigit() else None
        )
        comments = page.eval_on_selector_all(
            "shreddit-comment, [data-testid='comment']",
            """(els, limit) => els.slice(0, limit).map(e => {
                const clone = e.cloneNode(true);
                clone.querySelectorAll('shreddit-comment, [data-testid="comment"]').forEach(n => n.remove());
                const body = clone.querySelector('[slot="comment"], .md');
                const scoreText = e.getAttribute('score') || '';
                const depthText = e.getAttribute('depth') || '0';
                const permalink = e.getAttribute('permalink') || '';
                return {
                    id: e.getAttribute('thingid') || e.getAttribute('comment-id') || e.id || '',
                    parent_id: e.getAttribute('parentid') || e.getAttribute('parent-id') || '',
                    depth: /^\\d+$/.test(depthText) ? Number(depthText) + 1 : 1,
                    author: e.getAttribute('author') || '',
                    score: /^\\d+$/.test(scoreText) ? Number(scoreText) : null,
                    url: permalink ? (permalink.startsWith('http') ? permalink : `https://www.reddit.com${permalink}`) : '',
                    text: ((body?.innerText || '')).trim().slice(0, 4000)
                };
            }).filter(c => c.text)""",
            comment_limit,
        )
        more_comments_present = bool(page.locator(
            "shreddit-load-more-comments, [data-testid*='load-more-comment']"
        ).count())
        comments_complete = (
            platform_reported_total is not None
            and len(comments) >= platform_reported_total
            and not more_comments_present
        )
        return {
            "url": canonical,
            "title": (title or "").strip(),
            "body": (body or "").strip(),
            "comments": comments,
            "comments_requested": comment_limit,
            "comments_returned": len(comments),
            "platform_reported_total": platform_reported_total,
            "comments_complete": comments_complete,
            "truncation_reason": None if comments_complete else "initial_render_only",
        }


def run_camoufox_worker(operation: str, payload: dict, timeout_seconds: float):
    command = [sys.executable, "-m", "social_scraper.connectors.reddit_camoufox_worker"]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps({"operation": operation, "payload": payload}),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CamoufoxTimeoutError("Camoufox worker timed out") from exc
    marker = "RESULT_JSON:"
    result_line = next(
        (line[len(marker):] for line in reversed(completed.stdout.splitlines()) if line.startswith(marker)),
        None,
    )
    if result_line is None:
        raise RuntimeError("Camoufox worker failed")
    result = json.loads(result_line)
    if not result.get("ok"):
        if result.get("error") == "camoufox_verification_challenge":
            raise CamoufoxChallengeError("Reddit verification challenge")
        raise RuntimeError("Camoufox worker failed")
    if completed.returncode != 0:
        raise RuntimeError("Camoufox worker failed")
    return result["data"]


class RedditCamoufoxConnector(BaseConnector):
    platform = "reddit"
    connector_name = "camoufox_depth"
    requires_options = True

    def __init__(
        self,
        subreddits: Optional[list[str]] = None,
        scan_fn: Optional[Callable] = None,
        post_fn: Optional[Callable] = None,
        operation_timeout_seconds: float = 45.0,
        queue_timeout_seconds: float = 3.0,
    ):
        configured = os.getenv("BOUNTY_REDDIT_SUBREDDITS", "")
        env_subreddits = [value.strip() for value in configured.split(",") if value.strip()]
        selected = subreddits if subreddits is not None else env_subreddits
        self.subreddits = list(dict.fromkeys(value for value in selected if SUBREDDIT_RE.fullmatch(value)))[:50]
        self.scan_fn = scan_fn or scan_reddit_feeds
        self.post_fn = post_fn or hydrate_reddit_post
        self.operation_timeout_seconds = operation_timeout_seconds
        self.queue_timeout_seconds = queue_timeout_seconds

    async def _run_bounded(self, operation, fn, args, payload):
        gate = _gate_for_current_loop()
        try:
            await asyncio.wait_for(gate.acquire(), timeout=self.queue_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise CamoufoxBusyError("Camoufox worker is busy") from exc
        operation_task = None
        release_delegated = False

        def release_when_finished(task):
            gate.release()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.exception()

        try:
            if fn in {scan_reddit_feeds, hydrate_reddit_post}:
                operation_task = asyncio.create_task(asyncio.to_thread(
                    run_camoufox_worker,
                    operation,
                    payload,
                    self.operation_timeout_seconds,
                ))
            else:
                operation_task = asyncio.create_task(asyncio.to_thread(fn, *args))
            operation_task.add_done_callback(release_when_finished)
            release_delegated = True

            if fn in {scan_reddit_feeds, hydrate_reddit_post}:
                return await asyncio.shield(operation_task)
            return await asyncio.wait_for(
                asyncio.shield(operation_task),
                timeout=self.operation_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise CamoufoxTimeoutError("Camoufox operation timed out") from exc
        finally:
            if not release_delegated:
                gate.release()

    async def _collect(self, subreddits, keyword, count, time_filter, sort):
        started = time.time()
        try:
            raw = await self._run_bounded(
                "feed",
                self.scan_fn,
                (subreddits, keyword, count, time_filter, sort),
                {
                    "subreddits": subreddits,
                    "keyword": keyword,
                    "count": count,
                    "time_filter": time_filter,
                    "sort": sort,
                },
            )
            items = normalize_feed_posts(
                raw,
                keyword=keyword,
                count=count,
                allowed_subreddits=subreddits,
            )
            error = None
        except CamoufoxTimeoutError:
            items, error = [], "camoufox_timeout"
        except CamoufoxBusyError:
            items, error = [], "camoufox_busy"
        except CamoufoxChallengeError:
            items, error = [], "camoufox_verification_challenge"
        except Exception:
            items, error = [], "camoufox_collection_failed"
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items),
                items_requested=count,
                latency_ms=int((time.time() - started) * 1000),
                error=error,
            ),
        )

    def can_handle_options(self, options):
        subreddits = options.get("subreddits") if isinstance(options, dict) else None
        return (
            isinstance(subreddits, list)
            and 1 <= len(subreddits) <= 5
            and all(
                isinstance(value, str) and SUBREDDIT_RE.fullmatch(value)
                for value in subreddits
            )
        )

    async def search_with_options(
        self, keyword, count=20, time_filter="", sort="", region="", options=None
    ):
        requested = list(dict.fromkeys((options or {}).get("subreddits", [])))
        allowed = {value.lower() for value in self.subreddits}
        if any(value.lower() not in allowed for value in requested):
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform,
                    connector=self.connector_name,
                    status="skipped",
                    items_requested=count,
                    error="subreddit_scope_not_allowed",
                    coverage={
                        "global_coverage": False,
                        "requested_subreddits": requested,
                        "searched_subreddits": [],
                        "feeds": _feed_sorts(sort),
                    },
                ),
            )
        result = await self._collect(requested, keyword, count, time_filter, sort)
        result.health.coverage = {
            "global_coverage": False,
            "requested_subreddits": requested,
            "searched_subreddits": requested if result.health.status != "error" else [],
            "feeds": _feed_sorts(sort),
        }
        return result

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        if not self.subreddits:
            return ConnectorResult(
                items=[],
                health=SourceHealth(
                    platform=self.platform,
                    connector=self.connector_name,
                    status="partial",
                    items_requested=count,
                    error="subreddit_scope_not_configured",
                ),
            )
        return await self._collect(self.subreddits, keyword, count, time_filter, sort)

    async def get_feed(self, subreddit, keyword="", count=20, time_filter="", sort=""):
        if not SUBREDDIT_RE.fullmatch(subreddit):
            raise ValueError("Invalid subreddit name")
        if subreddit.lower() not in {value.lower() for value in self.subreddits}:
            raise ValueError("Subreddit is not in the configured allowlist")
        return await self._collect([subreddit], keyword, count, time_filter, sort)

    async def get_post(self, url: str, comment_limit: int = 20):
        canonical = validate_reddit_url(url)
        subreddit = POST_PATH_RE.fullmatch(urlsplit(canonical).path).group(1)
        if subreddit.lower() not in {value.lower() for value in self.subreddits}:
            raise ValueError("Subreddit is not in the configured allowlist")
        return await self._run_bounded(
            "post",
            self.post_fn,
            (canonical, comment_limit),
            {"url": canonical, "comment_limit": comment_limit},
        )

    async def health_check(self):
        if not CAMOUFOX_AVAILABLE:
            status, error = "error", "camoufox_not_installed"
        elif not self.subreddits:
            status, error = "partial", "subreddit_scope_not_configured"
        else:
            status, error = "ok", None
        return SourceHealth(
            platform=self.platform,
            connector=self.connector_name,
            status=status,
            error=error,
        )
