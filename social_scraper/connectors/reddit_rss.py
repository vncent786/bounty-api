"""Low-rate Reddit discovery through global search and scoped Atom feeds.

RSS is a discovery ledger, not an engagement source. It provides canonical post
identity and feed timestamps but no score or comment-count observations.
"""

import asyncio
import base64
import html
import os
import re
import time
import weakref
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.connectors.reddit_camoufox import POST_PATH_RE, SUBREDDIT_RE, validate_reddit_url
from social_scraper.proxy_config import build_playwright_proxy

ATOM_NS = "http://www.w3.org/2005/Atom"
USER_AGENT = "BountySocialAPI/1.0 (+https://bountyapi.com)"
_MAX_RESPONSE_BYTES = 2_000_000
_RSS_GATES = weakref.WeakKeyDictionary()


class RedditRSSRateLimitError(RuntimeError):
    pass


class RedditRSSResponseError(RuntimeError):
    pass


def _gate_for_current_loop():
    loop = asyncio.get_running_loop()
    gate = _RSS_GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(1)
        _RSS_GATES[loop] = gate
    return gate


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _plain_text(value):
    if not value:
        return ""
    return BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)[:10000]


def _matches_keyword(text, keyword):
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]+", keyword)]
    if not keyword.strip():
        return True
    if not terms:
        return False
    lowered = text.lower()
    return all(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms)


def parse_reddit_atom(
    payload: bytes, allowed_subreddits: list[str], keyword="", count=20, cutoff=None
):
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RedditRSSResponseError("RSS response exceeded size limit")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RedditRSSResponseError("RSS response was not valid XML") from exc
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise RedditRSSResponseError("RSS response was not an Atom feed")

    allowed = {value.lower() for value in allowed_subreddits}
    items = []
    seen = set()
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        thing_id = (entry.findtext(f"{{{ATOM_NS}}}id") or "").strip().lower()
        post_id = thing_id[3:] if thing_id.startswith("t3_") else ""
        title = (entry.findtext(f"{{{ATOM_NS}}}title") or "").strip()
        content_node = entry.find(f"{{{ATOM_NS}}}content")
        body = _plain_text(content_node.text if content_node is not None else "")
        if not post_id or not title or not _matches_keyword(f"{title} {body}", keyword):
            continue

        link = next(
            (
                node.attrib.get("href", "").strip()
                for node in entry.findall(f"{{{ATOM_NS}}}link")
                if node.attrib.get("href")
            ),
            "",
        )
        try:
            canonical = validate_reddit_url(link)
        except ValueError:
            continue
        match = POST_PATH_RE.fullmatch(urlsplit(canonical).path)
        path_subreddit, path_post_id = match.group(1), match.group(2).lower()
        if path_post_id != post_id or (
            allowed and path_subreddit.lower() not in allowed
        ):
            continue
        if post_id in seen:
            continue

        category = entry.find(f"{{{ATOM_NS}}}category")
        category_term = (category.attrib.get("term", "") if category is not None else "").strip()
        if category_term and category_term.lower() != path_subreddit.lower():
            continue

        author_node = entry.find(f"{{{ATOM_NS}}}author")
        author = ""
        if author_node is not None:
            author = (author_node.findtext(f"{{{ATOM_NS}}}name") or "").strip()
            author = re.sub(r"^/?u/", "", author)
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,20}", author):
            author = ""

        published = _parse_iso(entry.findtext(f"{{{ATOM_NS}}}published"))
        updated = _parse_iso(entry.findtext(f"{{{ATOM_NS}}}updated"))
        source_time = published or updated
        if cutoff and (
            not source_time or datetime.fromisoformat(source_time) < cutoff
        ):
            continue
        seen.add(post_id)
        items.append(SocialItem(
            platform="reddit",
            post_id=post_id,
            url=canonical,
            author_username=author,
            author_profile_url=f"https://www.reddit.com/user/{author}" if author else "",
            text=f"{title}\n\n{body}" if body else title,
            created_at=published,
            likes=None,
            comments=None,
            media_type="unknown",
            raw={
                "subreddit": path_subreddit,
                "title": title,
                "source_kind": "feed",
                "source_updated_at": updated,
                "source_timestamp_kind": "atom_published" if published else "atom_updated_only",
            },
        ))
        if len(items) >= count:
            break
    return items


def _httpx_proxy_url():
    proxy = build_playwright_proxy()
    server = os.getenv("BOUNTY_REDDIT_PROXY_SERVER", "").strip()
    if not proxy and not server:
        return None
    proxy = dict(proxy or {})
    if server:
        proxy["server"] = server
    parsed = httpx.URL(proxy["server"])
    return parsed.copy_with(
        username=proxy.get("username") or None,
        password=proxy.get("password") or None,
    )


class RedditRSSConnector(BaseConnector):
    platform = "reddit"
    connector_name = "reddit_atom_scoped"
    requires_options = False

    def __init__(self, fetch_feed: Optional[Callable] = None, max_subreddits=5, clock=None):
        self.fetch_feed = fetch_feed
        self.max_subreddits = max_subreddits
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def can_handle_options(self, options):
        if not options:
            return True
        requested = options.get("subreddits") if isinstance(options, dict) else None
        return (
            isinstance(requested, list)
            and 1 <= len(requested) <= self.max_subreddits
            and all(isinstance(value, str) and SUBREDDIT_RE.fullmatch(value) for value in requested)
        )

    @staticmethod
    def _feed_url(subreddits):
        return f"https://www.reddit.com/r/{'+'.join(subreddits)}/new/.rss"

    @staticmethod
    def _search_url(keyword, time_filter):
        params = {"q": str(keyword), "sort": "new"}
        period = {
            "1day": "day",
            "week": "week",
            "month": "month",
            "halfyear": "year",
        }.get(time_filter)
        if period:
            params["t"] = period
        return f"https://www.reddit.com/search.rss?{urlencode(params)}"

    async def _fetch(self, url):
        if self.fetch_feed:
            return await asyncio.to_thread(self.fetch_feed, url)
        timeout = httpx.Timeout(35.0, connect=10.0, read=30.0, write=5.0, pool=5.0)
        async with _gate_for_current_loop():
            async with httpx.AsyncClient(
                proxy=_httpx_proxy_url(),
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers={"Accept": "application/atom+xml, application/xml", "User-Agent": USER_AGENT},
                ) as response:
                    if response.status_code == 429:
                        raise RedditRSSRateLimitError("Reddit RSS rate limited")
                    if response.status_code != 200:
                        raise RedditRSSResponseError("Reddit RSS unavailable")
                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_RESPONSE_BYTES:
                            raise RedditRSSResponseError("RSS response exceeded size limit")
                        chunks.append(chunk)
        return b"".join(chunks)

    async def search_with_options(
        self, keyword, count=20, time_filter="", sort="", region="", options=None
    ):
        started = time.monotonic()
        requested = list(dict.fromkeys((options or {}).get("subreddits", [])))
        raw_records = []
        coverage = {
            "kind": "combined_atom_new_feed",
            "requested_subreddits": requested,
            "observed_subreddits": [],
            "global_coverage": False,
            "window_limited": True,
            "engagement_available": False,
            "source_kind": "feed",
        }
        if time_filter not in {"", "1day", "week", "month", "halfyear"}:
            error = "unsupported_time_filter"
            items = []
        elif sort not in {"", "latest"}:
            error = "unsupported_sort"
            items = []
        else:
            try:
                payload = await self._fetch(self._feed_url(requested))
                lookback = {
                    "1day": timedelta(days=1),
                    "week": timedelta(days=7),
                    "month": timedelta(days=30),
                    "halfyear": timedelta(days=180),
                }.get(time_filter)
                cutoff = self.clock() - lookback if lookback else None
                items = parse_reddit_atom(
                    payload, requested, keyword, count, cutoff=cutoff
                )
                raw_records = [{
                    "source_id": self._feed_url(requested),
                    "payload_format": "bytes_base64",
                    "payload": base64.b64encode(payload).decode("ascii"),
                }]
                coverage["observed_subreddits"] = sorted({
                    item.raw["subreddit"] for item in items
                }, key=str.lower)
                error = None
            except RedditRSSRateLimitError:
                items, error = [], "reddit_rss_rate_limited"
            except Exception:
                items, error = [], "reddit_rss_unavailable"
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items),
                items_requested=count,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=error,
                coverage=coverage,
            ),
            raw_records=raw_records,
        )

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        started = time.monotonic()
        url = self._search_url(keyword, time_filter)
        coverage = {
            "kind": "global_atom_search",
            "global_query": True,
            "global_coverage": False,
            "window_limited": True,
            "engagement_available": False,
            "source_kind": "feed",
        }
        raw_records = []
        if time_filter not in {"", "1day", "week", "month", "halfyear"}:
            items, error = [], "unsupported_time_filter"
        elif sort not in {"", "latest"}:
            items, error = [], "unsupported_sort"
        else:
            try:
                payload = await self._fetch(url)
                lookback = {
                    "1day": timedelta(days=1),
                    "week": timedelta(days=7),
                    "month": timedelta(days=30),
                    "halfyear": timedelta(days=180),
                }.get(time_filter)
                cutoff = self.clock() - lookback if lookback else None
                items = parse_reddit_atom(
                    payload, [], keyword, count, cutoff=cutoff
                )
                raw_records = [{
                    "source_id": url,
                    "payload_format": "bytes_base64",
                    "payload": base64.b64encode(payload).decode("ascii"),
                }]
                coverage["observed_subreddits"] = sorted({
                    item.raw["subreddit"] for item in items
                }, key=str.lower)
                error = None
            except RedditRSSRateLimitError:
                items, error = [], "reddit_rss_rate_limited"
            except Exception:
                items, error = [], "reddit_rss_unavailable"
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status="ok" if items else ("error" if error else "partial"),
                items_returned=len(items),
                items_requested=count,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=error,
                coverage=coverage,
            ),
            raw_records=raw_records,
        )

    async def health_check(self):
        return SourceHealth(
            platform=self.platform,
            connector=self.connector_name,
            status="ok",
            coverage={"source_kind": "feed", "global_coverage": False},
        )
