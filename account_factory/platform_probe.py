"""
Quick probe: what signup options do TikTok, Reddit, Discord actually offer?
Goal: find platforms that still allow email-only web signup (no phone required).
"""

import asyncio
import os
import time
from datetime import datetime
from playwright.async_api import async_playwright

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "probe_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

PROBES = [
    {
        "name": "tiktok",
        "url": "https://www.tiktok.com/signup",
        "actions": [
            # Look for email input option
        ],
    },
    {
        "name": "reddit",
        "url": "https://www.reddit.com/register/",
        "actions": [],
    },
    {
        "name": "discord",
        "url": "https://discord.com/register",
        "actions": [],
    },
    {
        "name": "instagram",
        "url": "https://www.instagram.com/accounts/emailsignup/",
        "actions": [],
    },
    {
        "name": "youtube_google",
        "url": "https://accounts.google.com/signup",
        "actions": [],
    },
]


async def probe_platform(playwright, config):
    """Visit signup page, capture what's available."""
    name = config["name"]
    url = config["url"]
    result = {
        "platform": name,
        "url": url,
        "email_field": False,
        "phone_field": False,
        "phone_required": False,
        "captcha": False,
        "page_text_snippet": "",
        "form_inputs": [],
        "buttons": [],
        "screenshot": None,
        "error": None,
    }

    browser = None
    page = None
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        await page.goto(url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)  # Let JS render

        # Capture all input fields
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                input_type = await inp.get_attribute("type") or "text"
                input_name = await inp.get_attribute("name") or ""
                input_placeholder = await inp.get_attribute("placeholder") or ""
                input_autocomplete = await inp.get_attribute("autocomplete") or ""
                result["form_inputs"].append(
                    {
                        "type": input_type,
                        "name": input_name,
                        "placeholder": input_placeholder,
                        "autocomplete": input_autocomplete,
                    }
                )
                if "email" in input_type or "email" in input_name or "email" in input_placeholder or "email" in input_autocomplete:
                    result["email_field"] = True
                if "phone" in input_type or "phone" in input_name or "phone" in input_placeholder or "phone" in input_autocomplete or "tel" in input_type:
                    result["phone_field"] = True
            except:
                pass

        # Capture all buttons
        buttons = await page.query_selector_all("button, [role='button'], a[href]")
        for btn in buttons[:20]:
            try:
                text = (await btn.inner_text()).strip()
                if text and len(text) < 80:
                    result["buttons"].append(text)
            except:
                pass

        # Capture page text
        body_text = await page.inner_text("body")
        result["page_text_snippet"] = body_text[:800].strip()

        # Check for phone requirement signals
        phone_signals = [
            "phone number is required",
            "verify your phone",
            "enter your phone",
            "phone verification",
            "verify with phone",
        ]
        text_lower = body_text.lower()
        for signal in phone_signals:
            if signal in text_lower:
                result["phone_required"] = True
                break

        # Check for captcha
        captcha_signals = ["captcha", "recaptcha", "hcaptcha", "arkose", "funcaptcha", "geetest"]
        for signal in captcha_signals:
            if signal in text_lower or signal in page.content.__doc__.lower() if page.content.__doc__ else False:
                result["captcha"] = True
                break

        # Check iframes for captcha
        frames = page.frames
        for frame in frames:
            try:
                frame_url = frame.url.lower()
                for signal in captcha_signals:
                    if signal in frame_url:
                        result["captcha"] = True
                        break
            except:
                pass

        # Screenshot
        screenshot_path = os.path.join(SCREENSHOT_DIR, f"{name}_signup.png")
        await page.screenshot(path=screenshot_path, full_page=False)
        result["screenshot"] = screenshot_path

    except Exception as e:
        result["error"] = str(e)
        if page:
            try:
                screenshot_path = os.path.join(SCREENSHOT_DIR, f"{name}_error.png")
                await page.screenshot(path=screenshot_path, full_page=False)
                result["screenshot"] = screenshot_path
            except:
                pass
    finally:
        if browser:
            await browser.close()

    return result


async def main():
    import json

    print("=" * 70)
    print("PLATFORM SIGNUP PROBE")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)

    async with async_playwright() as pw:
        results = []
        for config in PROBES:
            print(f"\n--- Probing {config['name']} ---")
            result = await probe_platform(pw, config)
            results.append(result)

            status = "ERROR" if result["error"] else "OK"
            email = "EMAIL" if result["email_field"] else "no-email"
            phone = "PHONE" if result["phone_field"] else "no-phone"
            captcha = "CAPTCHA" if result["captcha"] else "no-captcha"

            print(f"  Status: {status} | {email} | {phone} | {captcha}")
            if result["error"]:
                print(f"  Error: {result['error'][:200]}")
            if result["form_inputs"]:
                print(f"  Inputs: {[i['type'] + ':' + i['name'] for i in result['form_inputs'][:8]]}")
            if result["buttons"]:
                print(f"  Buttons: {result['buttons'][:8]}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        if r["error"]:
            verdict = "BLOCKED"
        elif r["email_field"] and not r["phone_required"]:
            verdict = "GOOD CANDIDATE"
        elif r["email_field"] and r["phone_required"]:
            verdict = "PHONE REQUIRED"
        elif r["phone_field"] and not r["email_field"]:
            verdict = "PHONE ONLY"
        else:
            verdict = "UNCLEAR"

        print(f"  {r['platform']:20s} → {verdict}")
        if r["email_field"]:
            print(f"    ✅ Email field present")
        if r["phone_field"]:
            print(f"    ⚠️  Phone field present")
        if r["phone_required"]:
            print(f"    ❌ Phone verification required")
        if r["captcha"]:
            print(f"    🔒 Captcha detected")
        if r["error"]:
            print(f"    ❌ {r['error'][:150]}")

    print("\n" + json.dumps(results, indent=2, default=str)[:5000])


if __name__ == "__main__":
    asyncio.run(main())
