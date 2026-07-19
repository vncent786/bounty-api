"""
Honest probe: can we get TikTok search results without a paid API?

Tests three approaches in order:
1. TikTok public search page via Playwright (no login)
2. TikTok internal search API via Playwright request interception
3. Direct curl to TikTok search API (expected: blocked without signature)

Reports EXACTLY what worked and what failed. No silent failures.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

QUERY = "dopamine detox"
OUTFILE = "social_scraper/probe_tiktok_results.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def probe_search_page(pw):
    """Approach 1: load tiktok.com/search and read rendered DOM."""
    out = {"approach": "search_page_dom", "status": "error", "items": [], "error": None}
    browser = None
    try:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        url = f"https://www.tiktok.com/search?q={QUERY}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)  # let JS hydrate

        # Common selectors for search result items
        selectors_tried = [
            'div[data-e2e="search_video-item"]',
            'div[data-e2e="search-search-item"]',
            'a[href*="/video/"]',
            'div[class*="DivItemContainer"]',
            'div[class*="SearchVideo"]',
        ]
        found = []
        for sel in selectors_tried:
            items = page.query_selector_all(sel)
            if items:
                out["selector_used"] = sel
                for it in items[:10]:
                    try:
                        href = it.get_attribute("href") or ""
                        text = (it.inner_text() or "").replace("\n", " ").strip()[:300]
                        found.append({"href": href, "text": text})
                    except Exception as e:
                        found.append({"error": str(e)[:150]})
                break

        # Take screenshot for evidence regardless
        try:
            page.screenshot(path="social_scraper/probe_tiktok_search_page.png", full_page=False)
            out["screenshot"] = "social_scraper/probe_tiktok_search_page.png"
        except Exception as e:
            out["screenshot_error"] = str(e)[:150]

        # Capture page title + any visible "are you human" markers
        out["page_title"] = page.title()
        body_text = (page.inner_text("body") or "")[:1000]
        out["body_snippet"] = body_text
        captcha_markers = ["captcha", "verify you are human", "are you a robot", "checking your browser"]
        out["captcha_detected"] = any(m in body_text.lower() for m in captcha_markers)

        if found:
            out["status"] = "ok"
            out["items"] = found
            out["count"] = len(found)
        else:
            out["status"] = "partial"
            out["error"] = "page loaded but no items matched selectors"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
    return out


def probe_api_via_request(pw):
    """Approach 2: intercept the XHR call TikTok makes to its search API."""
    out = {"approach": "api_intercept", "status": "error", "items": [], "error": None}
    browser = None
    try:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        captured = []

        def on_response(resp):
            url = resp.url
            if "/api/search/" in url or "/search/general/" in url or "/search/item/full/" in url:
                try:
                    body = resp.text()
                    captured.append({"url": url[:200], "status": resp.status, "len": len(body), "snippet": body[:800]})
                except Exception as e:
                    captured.append({"url": url[:200], "err": str(e)[:150]})

        page.on("response", on_response)
        try:
            page.goto(f"https://www.tiktok.com/search?q={QUERY}", wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeout:
            out["note"] = "navigation timed out, but responses may have been captured"
        page.wait_for_timeout(5000)

        out["captured_calls"] = captured[:5]
        if any(c.get("status") == 200 and c.get("len", 0) > 1000 for c in captured):
            out["status"] = "ok"
            out["note"] = "captured search XHR with 200 status — reverse-engineerable"
        elif captured:
            out["status"] = "partial"
            out["error"] = f"captured {len(captured)} calls but none had 200 + body"
        else:
            out["status"] = "error"
            out["error"] = "no search XHR captured — page may be captcha-blocked"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
    return out


def main():
    results = {
        "query": QUERY,
        "fetched_at": now_iso(),
        "approaches": [],
    }
    with sync_playwright() as pw:
        results["approaches"].append(probe_search_page(pw))
        results["approaches"].append(probe_api_via_request(pw))

    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(json.dumps(results, indent=2, ensure_ascii=False)[:4000])
    print(f"\n[written to {OUTFILE}]")


if __name__ == "__main__":
    sys.exit(main() or 0)
