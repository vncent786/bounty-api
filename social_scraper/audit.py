"""
Social source audit harness for Bounty/Cairn/Social Arb.

Purpose: test which social data routes actually work for a query, without
silent failures. This is the foundation for a durable social signal engine.

Usage:
  python -m social_scraper.audit --query "dopamine detox" --limit 10

Current sources:
  - Reddit via PullPush.io (no auth)
  - YouTube via yt-dlp if installed, fallback to YouTube RSS search
  - GitHub OSS candidate inventory for platform scraper repos

Each source returns a health block, not just items.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


USER_AGENT = "BountySocialAudit/0.1 (+https://bountyapi.com)"


@dataclass
class SourceResult:
    source: str
    status: str  # ok | partial | error
    method: str
    query: str
    count: int
    items: List[Dict[str, Any]]
    fetched_at: str
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, timeout: int = 20, headers: Optional[Dict[str, str]] = None) -> Any:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def http_text(url: str, timeout: int = 20, headers: Optional[Dict[str, str]] = None) -> str:
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ── Reddit ───────────────────────────────────────────────────────────────────

def scan_reddit_pullpush(query: str, limit: int = 10) -> SourceResult:
    """Search public Reddit submissions through PullPush.io."""
    fetched_at = now_iso()
    params = urllib.parse.urlencode({"q": query, "size": limit, "sort": "desc", "sort_type": "created_utc"})
    url = f"https://api.pullpush.io/reddit/search/submission/?{params}"
    try:
        data = http_json(url, timeout=25)
        raw_items = data.get("data", []) if isinstance(data, dict) else []
        items = []
        for r in raw_items[:limit]:
            permalink = r.get("permalink") or ""
            if permalink and permalink.startswith("/"):
                permalink = "https://www.reddit.com" + permalink
            items.append({
                "platform": "reddit",
                "id": r.get("id"),
                "url": permalink,
                "author": r.get("author"),
                "author_url": f"https://www.reddit.com/user/{r.get('author')}" if r.get("author") else None,
                "text": normalize_text((r.get("title") or "") + " " + (r.get("selftext") or "")),
                "created_at": r.get("created_utc"),
                "engagement": {
                    "score": r.get("score"),
                    "comments": r.get("num_comments"),
                },
                "query": query,
                "raw": {
                    "subreddit": r.get("subreddit"),
                    "title": r.get("title"),
                },
            })
        return SourceResult("reddit", "ok", "pullpush", query, len(items), items, fetched_at, meta={"url": url})
    except Exception as e:
        return SourceResult("reddit", "error", "pullpush", query, 0, [], fetched_at, error=str(e)[:300], meta={"url": url})


def scan_reddit_rss(query: str, limit: int = 10, subreddits: Optional[List[str]] = None) -> SourceResult:
    """Fallback Reddit search via subreddit Atom RSS. No OAuth, but scoped to selected subreddits."""
    fetched_at = now_iso()
    subreddits = subreddits or ["NoFap", "pornfree", "selfimprovement", "productivity", "DecidingToBeBetter"]
    items: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    for sub in subreddits:
        params = urllib.parse.urlencode({"q": query, "restrict_sr": "on", "sort": "new"})
        url = f"https://www.reddit.com/r/{urllib.parse.quote(sub)}/search.rss?{params}"
        try:
            xml_text = http_text(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 BountySocialAudit"})
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("atom:entry", ns):
                if len(items) >= limit:
                    break
                title = normalize_text(entry.findtext("atom:title", default="", namespaces=ns))
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href") if link_el is not None else None
                author_el = entry.find("atom:author/atom:name", ns)
                author = author_el.text if author_el is not None else None
                items.append({
                    "platform": "reddit",
                    "id": entry.findtext("atom:id", default=None, namespaces=ns),
                    "url": link,
                    "author": author,
                    "author_url": f"https://www.reddit.com/user/{author}" if author else None,
                    "text": title,
                    "created_at": entry.findtext("atom:updated", default=None, namespaces=ns),
                    "engagement": {},
                    "query": query,
                    "raw": {"subreddit": sub, "method": "subreddit_search_rss"},
                })
        except Exception as e:
            errors[sub] = str(e)[:200]
        if len(items) >= limit:
            break
        time.sleep(0.5)

    if items:
        status = "partial" if errors else "ok"
        return SourceResult("reddit_rss", status, "subreddit_search_rss", query, len(items), items, fetched_at, error=json.dumps(errors) if errors else None, meta={"subreddits": subreddits})
    return SourceResult("reddit_rss", "error", "subreddit_search_rss", query, 0, [], fetched_at, error=json.dumps(errors) if errors else "No RSS results", meta={"subreddits": subreddits})


# ── YouTube ──────────────────────────────────────────────────────────────────

def scan_youtube_ytdlp(query: str, limit: int = 10) -> SourceResult:
    """Search YouTube using yt-dlp if installed."""
    fetched_at = now_iso()
    try:
        import yt_dlp  # type: ignore
    except Exception as e:
        return SourceResult("youtube", "error", "yt-dlp", query, 0, [], fetched_at, error=f"yt-dlp not available: {e}")

    try:
        opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = (info or {}).get("entries", []) if isinstance(info, dict) else []
        items = []
        for e in entries[:limit]:
            url = e.get("url") or e.get("webpage_url") or e.get("id")
            if url and not str(url).startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            items.append({
                "platform": "youtube",
                "id": e.get("id"),
                "url": url,
                "author": e.get("channel") or e.get("uploader"),
                "author_url": e.get("channel_url"),
                "text": normalize_text(e.get("title")),
                "created_at": e.get("upload_date"),
                "engagement": {
                    "views": e.get("view_count"),
                    "likes": e.get("like_count"),
                    "comments": e.get("comment_count"),
                },
                "query": query,
                "raw": {"duration": e.get("duration")},
            })
        return SourceResult("youtube", "ok", "yt-dlp", query, len(items), items, fetched_at)
    except Exception as e:
        return SourceResult("youtube", "error", "yt-dlp", query, 0, [], fetched_at, error=str(e)[:300])


def scan_youtube_rss(query: str, limit: int = 10) -> SourceResult:
    """Fallback YouTube public RSS-ish search through Google News video search is weak but no deps."""
    fetched_at = now_iso()
    # YouTube has no official unauthenticated search RSS. Use Google News RSS scoped to YouTube URLs as fallback.
    q = f"site:youtube.com/watch {query}"
    params = urllib.parse.urlencode({"q": q, "hl": "en", "gl": "US", "ceid": "US:en"})
    url = f"https://news.google.com/rss/search?{params}"
    try:
        xml_text = http_text(url, timeout=20)
        root = ET.fromstring(xml_text)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = normalize_text(item.findtext("title"))
            link = item.findtext("link")
            pub_date = item.findtext("pubDate")
            items.append({
                "platform": "youtube",
                "id": None,
                "url": link,
                "author": None,
                "author_url": None,
                "text": title,
                "created_at": pub_date,
                "engagement": {},
                "query": query,
                "raw": {"fallback": "google_news_rss_youtube_site_search"},
            })
        status = "ok" if items else "partial"
        return SourceResult("youtube", status, "google_news_rss_fallback", query, len(items), items, fetched_at, meta={"url": url})
    except Exception as e:
        return SourceResult("youtube", "error", "google_news_rss_fallback", query, 0, [], fetched_at, error=str(e)[:300], meta={"url": url})


# ── GitHub OSS Candidate Inventory ───────────────────────────────────────────

GITHUB_QUERIES = {
    "tiktok": "tiktok scraper stars:>100",
    "instagram": "instagram scraper stars:>100",
    "xiaohongshu": "xiaohongshu scraper OR rednote stars:>20",
    "douyin": "douyin scraper stars:>100",
}


def scan_github_candidates(platform: str, limit: int = 8) -> SourceResult:
    """Search GitHub for scraper repos. Useful for choosing integration candidates."""
    fetched_at = now_iso()
    query = GITHUB_QUERIES.get(platform, f"{platform} scraper stars:>50")
    params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": limit})
    url = f"https://api.github.com/search/repositories?{params}"
    try:
        data = http_json(url, timeout=25, headers={"Accept": "application/vnd.github+json"})
        items = []
        for r in data.get("items", [])[:limit]:
            items.append({
                "platform": "github",
                "id": r.get("full_name"),
                "url": r.get("html_url"),
                "author": r.get("owner", {}).get("login"),
                "author_url": r.get("owner", {}).get("html_url"),
                "text": normalize_text(r.get("description")),
                "created_at": r.get("created_at"),
                "engagement": {
                    "stars": r.get("stargazers_count"),
                    "forks": r.get("forks_count"),
                    "open_issues": r.get("open_issues_count"),
                },
                "query": query,
                "raw": {
                    "updated_at": r.get("updated_at"),
                    "language": r.get("language"),
                },
            })
        return SourceResult(f"github_{platform}", "ok", "github_search_api", query, len(items), items, fetched_at, meta={"url": url})
    except Exception as e:
        return SourceResult(f"github_{platform}", "error", "github_search_api", query, 0, [], fetched_at, error=str(e)[:300], meta={"url": url})


# ── Orchestration ────────────────────────────────────────────────────────────

def audit(query: str, limit: int = 10, include_github: bool = True) -> Dict[str, Any]:
    results: List[SourceResult] = []

    # Real content sources
    reddit = scan_reddit_pullpush(query, limit=limit)
    results.append(reddit)
    if reddit.status == "error":
        results.append(scan_reddit_rss(query, limit=limit))

    yt = scan_youtube_ytdlp(query, limit=limit)
    if yt.status == "error":
        # Keep the failed yt-dlp result AND add fallback, so health is explicit.
        results.append(yt)
        results.append(scan_youtube_rss(query, limit=limit))
    else:
        results.append(yt)

    # Instagram via Playwright tags page (free, no auth, login-wall limited)
    try:
        from .instagram import scan_instagram_tag
        ig = scan_instagram_tag(query, limit=limit)
        results.append(ig)
    except Exception as e:
        results.append(SourceResult("instagram", "error", "playwright_tags_page", query, 0, [], now_iso(), error=f"import/run failed: {e}"))

    # Integration-candidate inventory
    if include_github:
        for platform in ["tiktok", "instagram", "xiaohongshu", "douyin"]:
            results.append(scan_github_candidates(platform, limit=8))
            time.sleep(1.0)  # be polite to GitHub unauthenticated API

    sources = {r.source: asdict(r) for r in results}
    ok = sum(1 for r in results if r.status == "ok")
    failed = sum(1 for r in results if r.status == "error")
    partial = sum(1 for r in results if r.status == "partial")
    total_items = sum(r.count for r in results if not r.source.startswith("github_"))

    return {
        "query": query,
        "fetched_at": now_iso(),
        "summary": {
            "sources_total": len(results),
            "sources_ok": ok,
            "sources_partial": partial,
            "sources_failed": failed,
            "content_items": total_items,
        },
        "sources": sources,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit social scraping sources for a query")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Items per source")
    parser.add_argument("--no-github", action="store_true", help="Skip GitHub OSS candidate inventory")
    parser.add_argument("--output", help="Write JSON result to file")
    args = parser.parse_args(argv)

    result = audit(args.query, limit=args.limit, include_github=not args.no_github)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
