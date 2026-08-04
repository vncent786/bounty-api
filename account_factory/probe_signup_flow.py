"""
Probe Twitter's signup flow with different strategies.
Quick test: mobile emulation vs desktop, "Create account" button vs direct URL.
"""

import os
import sys
import json
import asyncio
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright


async def probe_desktop_create_account():
    """Try clicking 'Create account' from x.com homepage."""
    print("\n=== STRATEGY 1: Desktop — Create Account button ===")
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="en-US",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = await context.new_page()
        
        # Go to homepage
        await page.goto("https://x.com", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        await page.screenshot(path="/tmp/probe_01_homepage.png")
        
        # Look for "Create account" button
        page_text = await page.evaluate("() => document.body.innerText")
        has_create = "create" in page_text.lower() or "sign up" in page_text.lower()
        print(f"  Homepage has 'Create account': {has_create}")
        
        # Find and click it
        for selector in [
            'a:has-text("Sign up")',
            'div[role="link"]:has-text("Sign up")',
            'a:has-text("Create")',
            '[data-testid="signupButton"]',
            'a[href*="signup"]',
            'div[role="link"]:has-text("Create account")',
        ]:
            try:
                el = await page.wait_for_selector(selector, state="visible", timeout=3000)
                if el:
                    print(f"  Found: {selector}")
                    await el.click()
                    await asyncio.sleep(3)
                    break
            except:
                continue
        
        await page.screenshot(path="/tmp/probe_02_after_create.png")
        url_after = page.url
        text_after = await page.evaluate("() => document.body.innerText.slice(0, 1000)")
        print(f"  URL after click: {url_after}")
        print(f"  Page text snippet: {text_after[:500]}")
        
        await browser.close()
        return url_after, text_after


async def probe_mobile_signup():
    """Try signup with iPhone emulation."""
    print("\n=== STRATEGY 2: Mobile (iPhone) — Signup flow ===")
    
    async with async_playwright() as pw:
        iphone = pw.devices["iPhone 13 Pro"]
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            **iphone,
            locale="en-US",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = await context.new_page()
        
        await page.goto("https://x.com/i/flow/signup", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await page.screenshot(path="/tmp/probe_03_mobile_signup.png")
        
        url = page.url
        text = await page.evaluate("() => document.body.innerText.slice(0, 1500)")
        print(f"  URL: {url}")
        print(f"  Mobile page text: {text[:800]}")
        
        # Look for email input
        inputs = await page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('input')).map(i => ({
                    type: i.type,
                    name: i.name,
                    id: i.id,
                    placeholder: i.placeholder,
                    autocomplete: i.autocomplete,
                    value: i.value,
                }));
            }
        """)
        print(f"  Inputs found: {json.dumps(inputs, indent=2)}")
        
        # Look for buttons
        buttons = await page.evaluate("""
            () => {
                return Array.from(document.querySelectorAll('button, div[role="button"]')).map(b => ({
                    text: b.innerText?.slice(0, 50),
                    testid: b.getAttribute('data-testid'),
                    tag: b.tagName,
                })).filter(b => b.text);
            }
        """)
        print(f"  Buttons found: {json.dumps(buttons[:10], indent=2)}")
        
        # Try entering email if we find the input
        for inp in inputs:
            if inp.get("type") == "text" or "email" in (inp.get("name", "") + inp.get("autocomplete", "")):
                print(f"  Trying to use input: {inp['name']}")
                await page.fill(f'input[name="{inp["name"]}"]', f"testuser{random.randint(1000,9999)}@agentmail.to")
                await asyncio.sleep(1)
                
                # Try pressing Enter
                await page.keyboard.press("Enter")
                await asyncio.sleep(4)
                
                await page.screenshot(path="/tmp/probe_04_mobile_after_email.png")
                text_after = await page.evaluate("() => document.body.innerText.slice(0, 1000)")
                url_after = page.url
                print(f"  After email entry — URL: {url_after}")
                print(f"  After email entry — Text: {text_after[:500]}")
                break
        
        await browser.close()
        return url, text


async def probe_signup_api():
    """Check what API calls the signup page makes."""
    print("\n=== STRATEGY 3: Network analysis ===")
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        api_calls = []
        
        def on_request(request):
            url = request.url
            if "x.com" in url and any(k in url for k in ["flow", "signup", "onboarding", "account", "create"]):
                api_calls.append({
                    "url": url,
                    "method": request.method,
                    "headers": dict(list(request.headers.items())[:5]),
                })
        
        page.on("request", on_request)
        
        await page.goto("https://x.com/i/flow/signup", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)
        
        print(f"  Captured {len(api_calls)} relevant API calls:")
        for call in api_calls[:15]:
            print(f"    {call['method']} {call['url'][:120]}")
        
        await browser.close()


async def main():
    await probe_desktop_create_account()
    await probe_mobile_signup()
    await probe_signup_api()


if __name__ == "__main__":
    asyncio.run(main())
