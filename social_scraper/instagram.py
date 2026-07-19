"""
Instagram source connector for the social audit harness.

Strategy: load /explore/tags/{tag}/ in Playwright, parse the top creators + captions
that Instagram renders server-side BEFORE the login wall blocks deep scrolling.

What we get free:
  - Total post count for hashtag (e.g., "56K reels")
  - Top creators (handle, follower count)
  - Top post captions + hashtags
  - Thumbnail URLs (limited)

What we DON'T get free:
  - Deep pagination (login wall after ~5-15 items)
  - Comments
  - Engagement counts (likes/views per post)

That's still useful trend intel: who the dominant creators are, what language they use,
and how saturated a hashtag is. For deeper data, fall back to paid Apify/TikHub.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .audit import SourceResult, now_iso, normalize_text


def scan_instagram_tag(query: str, limit: int = 10) -> SourceResult:
    """
    Scrape Instagram hashtag landing page. Free, no auth.

    `query` is normalized: spaces stripped, lowercased. e.g. "dopamine detox" -> "dopaminedetox".
    """
    fetched_at = now_iso()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return SourceResult("instagram", "error", "playwright_tags_page", query, 0, [], fetched_at, error=f"playwright not available: {e}")

    tag = re.sub(r"[^A-Za-z0-9]", "", query).lower()
    if not tag:
        return SourceResult("instagram", "error", "playwright_tags_page", query, 0, [], fetched_at, error="empty tag after normalization")

    url = f"https://www.instagram.com/explore/tags/{tag}/"
    items: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {"url": url, "tag": tag}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)
                body_text = page.inner_text("body") or ""
                title = page.title() or ""
                meta["page_title"] = title

                # Parse total post count from title (e.g., "Dopaminedetox • 56K reels on Instagram")
                m = re.search(r"([\d.,KMB]+)\s+(?:reels|posts)", title, re.I)
                if m:
                    meta["total_posts_label"] = m.group(1)

                # Detect login wall (still get top items before it blocks scrolling)
                meta["login_wall"] = "log in" in body_text.lower() and "sign up" in body_text.lower()

                # Extract creator handles and follower counts from body text patterns.
                # Pattern observed: "username\n874K" (handle on one line, follower count on next).
                lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]
                for i, ln in enumerate(lines):
                    # Follower count pattern: digits + K/M/B
                    if re.fullmatch(r"[\d.,]+[KMB]?", ln) and i > 0:
                        handle = lines[i - 1]
                        # Strip Instagram's "..." truncation marker
                        handle = handle.rstrip(".").rstrip("…")
                        # Plausible IG handle: letters/digits/underscores/dots, no spaces
                        if re.fullmatch(r"[A-Za-z0-9._]{2,30}", handle) and handle.lower() not in {"log", "sign", "explore", "search", "tagged"}:
                            items.append({
                                "platform": "instagram",
                                "id": f"{tag}:{handle}",
                                "url": f"https://www.instagram.com/{handle}/",
                                "author": handle,
                                "author_url": f"https://www.instagram.com/{handle}/",
                                "text": "",
                                "created_at": None,
                                "engagement": {"followers_label": ln},
                                "query": query,
                                "raw": {"tag": tag, "source": "tags_page_top_creator"},
                            })

                # Extract captions: lines that contain hashtags (strong signal of real caption text)
                caption_lines = []
                for ln in lines:
                    if "#" in ln and len(ln) > 15 and len(ln) < 400:
                        # skip pure nav spam
                        if not any(skip in ln.lower() for skip in ["log in", "sign up", "see more", "next", "explore"]):
                            caption_lines.append(ln)

                # Attach captions as separate text items if we found any
                for cap in caption_lines[:limit]:
                    items.append({
                        "platform": "instagram",
                        "id": f"{tag}:caption:{hash(cap) & 0xFFFFFFFF}",
                        "url": url,
                        "author": None,
                        "author_url": None,
                        "text": normalize_text(cap),
                        "created_at": None,
                        "engagement": {},
                        "query": query,
                        "raw": {"tag": tag, "source": "tags_page_caption"},
                    })

                # Truncate to limit
                items = items[:limit]
            finally:
                browser.close()

        status = "ok" if items else ("partial" if not meta.get("login_wall") else "error")
        err = None
        if status == "partial":
            err = "page loaded but no creators or captions extracted"
        elif status == "error":
            err = "login wall blocked all data extraction"
        return SourceResult(
            source="instagram",
            status=status,
            method="playwright_tags_page",
            query=query,
            count=len(items),
            items=items,
            fetched_at=fetched_at,
            error=err,
            meta=meta,
        )
    except Exception as e:
        return SourceResult(
            source="instagram",
            status="error",
            method="playwright_tags_page",
            query=query,
            count=0,
            items=[],
            fetched_at=fetched_at,
            error=f"{type(e).__name__}: {str(e)[:300]}",
            meta=meta,
        )
