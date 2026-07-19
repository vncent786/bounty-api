"""Quick Instagram probe — same approach as TikTok."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

QUERY = "dopamine detox"
OUT = "social_scraper/probe_instagram_results.json"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def probe():
    out = {"query": QUERY, "fetched_at": now_iso(), "approaches": []}
    with sync_playwright() as pw:
        # Approach 1: Instagram explore/tags
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        captured = []
        def on_resp(r):
            if "/graphql/" in r.url or "/api/v1/" in r.url or "/tags/" in r.url or "/explore/" in r.url:
                try:
                    body = r.text()
                    captured.append({"url": r.url[:200], "status": r.status, "len": len(body), "snippet": body[:300]})
                except Exception as e:
                    captured.append({"url": r.url[:200], "err": str(e)[:100]})
        page.on("response", on_resp)

        for label, url in [
            ("tags_page", f"https://www.instagram.com/explore/tags/{QUERY.replace(' ','')}/"),
            ("explore_page", "https://www.instagram.com/explore/"),
            ("search", f"https://www.instagram.com/explore/search/keyword/?q={QUERY.replace(' ','+')}"),
        ]:
            res = {"approach": label, "url": url, "status": "error", "error": None}
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3500)
                res["page_title"] = page.title()
                body_text = (page.inner_text("body") or "")[:600]
                res["body_snippet"] = body_text
                captcha_markers = ["captcha", "verify you", "are you a robot", "checking your browser", "sign up", "log in"]
                res["login_wall"] = ("log in" in body_text.lower() and "sign up" in body_text.lower())
                try:
                    page.screenshot(path=f"social_scraper/probe_ig_{label}.png", full_page=False)
                    res["screenshot"] = f"social_scraper/probe_ig_{label}.png"
                except Exception as e:
                    res["screenshot_error"] = str(e)[:100]
                res["status"] = "ok" if not res["login_wall"] else "blocked_login_wall"
                if res["login_wall"]:
                    res["error"] = "login wall — Instagram requires auth for explore/search"
            except Exception as e:
                res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            out["approaches"].append(res)
        out["xhr_captured"] = captured[:5]
        browser.close()
    return out

if __name__ == "__main__":
    r = probe()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2, ensure_ascii=False)
    print(json.dumps(r, indent=2, ensure_ascii=False)[:3000])
    print(f"\n[written to {OUT}]")
