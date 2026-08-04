"""
Open a VISIBLE persistent TikTok browser for one-time login.
Session persists in .browser_profiles/tiktok for reuse.
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

PROJECT = Path(__file__).resolve().parents[1]
PROFILE = PROJECT / ".browser_profiles" / "tiktok"
load_dotenv(PROJECT / ".env")


def proxy_config():
    server = os.getenv("BOUNTY_PROXY_SERVER", "").strip()
    if not server:
        return None
    config = {"server": server}
    username = os.getenv("BOUNTY_PROXY_USERNAME", "").strip()
    password = os.getenv("BOUNTY_PROXY_PASSWORD", "").strip()
    if username:
        config["username"] = username
    if password:
        config["password"] = password
    return config


async def logged_in(context):
    names = {c["name"] for c in await context.cookies("https://www.tiktok.com")}
    return bool(names & {"sessionid", "sessionid_ss", "sid_tt"})


async def main():
    PROFILE.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=False,
            proxy=proxy_config(),
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1280, "height": 800},
            no_viewport=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )
        await Stealth().apply_stealth_async(context)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.tiktok.com/login/phone-or-email/email",
                        wait_until="domcontentloaded", timeout=60_000)
        print("=== TikTok login window is open on your screen ===", flush=True)
        print("Log in with your TikTok account.", flush=True)
        print("The script will auto-detect when login succeeds.", flush=True)

        announced = False
        while True:
            await asyncio.sleep(3)
            if not announced and await logged_in(context):
                print("=== LOGIN DETECTED ===", flush=True)
                announced = True
                # Navigate to search to confirm it works
                await page.goto("https://www.tiktok.com/search?q=AI%20tools",
                                wait_until="domcontentloaded", timeout=60_000)
                await asyncio.sleep(10)
                body = await page.locator("body").inner_text()
                if "Log in to search" in body:
                    print("WARNING: Still seeing login wall after auth.", flush=True)
                else:
                    print("SUCCESS: Search page loaded without login wall.", flush=True)
                break
            if announced:
                break

        print("Waiting 5 more seconds then closing...", flush=True)
        await asyncio.sleep(5)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
