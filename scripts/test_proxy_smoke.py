"""Smoke-test Bounty proxy config with Playwright.

Usage:
  BOUNTY_PROXY_SERVER=http://host:port \
  BOUNTY_PROXY_USERNAME=... \
  BOUNTY_PROXY_PASSWORD=... \
  python scripts/test_proxy_smoke.py

The script prints only redacted proxy status and public IP metadata.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright
from social_scraper.proxy_config import build_playwright_proxy, proxy_health_summary


async def main():
    proxy = build_playwright_proxy()
    print("proxy_config:", json.dumps(proxy_health_summary(), indent=2))

    async with async_playwright() as p:
        launch_kwargs = {"headless": True}
        if proxy:
            launch_kwargs["proxy"] = proxy

        browser = await p.chromium.launch(**launch_kwargs)
        page = await browser.new_page()
        await page.goto("https://api.ipify.org?format=json", timeout=30000)
        ipify = await page.inner_text("body")
        print("ipify:", ipify)

        # ipinfo can rate-limit but is useful for country/org metadata.
        try:
            await page.goto("https://ipinfo.io/json", timeout=30000)
            print("ipinfo:", await page.inner_text("body"))
        except Exception as exc:
            print("ipinfo_error:", str(exc))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
