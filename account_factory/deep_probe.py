"""
Deep probe: actually interact with TikTok and Instagram signup flows.
TikTok: click "Use phone or email" -> see what's behind it.
Instagram: fill in form fields -> see what captcha/phone/verification steps appear.
"""

import asyncio
import os
import random
import string
import time
from datetime import datetime
from playwright.async_api import async_playwright

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "deep_probe_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def random_email():
    name = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"testuser{name}@agentmail.to"


def random_name():
    first = random.choice(["James", "Sarah", "Mike", "Emma", "Alex", "Lisa", "David", "Anna"])
    last = random.choice(["Smith", "Chen", "Brown", "Lee", "Wilson", "Garcia", "Park", "Wong"])
    return first, last


async def probe_tiktok(playwright):
    """Click through TikTok signup: 'Use phone or email' -> see email flow."""
    print("\n" + "=" * 60)
    print("TIKTOK DEEP PROBE")
    print("=" * 60)

    result = {"steps": [], "screenshots": [], "error": None}
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    try:
        # Step 1: Go to signup page
        print("[1] Navigating to TikTok signup...")
        await page.goto("https://www.tiktok.com/signup", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_step1_landing.png"))
        result["screenshots"].append("tiktok_step1_landing.png")
        result["steps"].append("Landed on signup page")

        # Step 2: Click "Use phone or email"
        print("[2] Looking for 'Use phone or email' button...")
        
        # Try multiple selectors
        phone_email_selectors = [
            'a:has-text("phone or email")',
            'div:has-text("Use phone or email")',
            '[data-e2e="use-phone-or-email"]',
            'text="Use phone or email"',
            'a[href*="email"]',
            'div[class*="channel"]',
        ]
        
        clicked = False
        for sel in phone_email_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    text = await el.inner_text()
                    print(f"  Found: '{text.strip()[:60]}'")
                    await el.click()
                    clicked = True
                    break
            except:
                continue
        
        if not clicked:
            # Try link elements
            links = await page.query_selector_all("a, div[role='button'], button")
            for link in links:
                try:
                    text = await link.inner_text()
                    if "phone or email" in text.lower() or "email" in text.lower():
                        print(f"  Found by scan: '{text.strip()[:60]}'")
                        await link.click()
                        clicked = True
                        break
                except:
                    continue

        if not clicked:
            result["steps"].append("Could not find 'Use phone or email' button")
            print("  Could not find the button!")
            # dump page content for debug
            body = await page.inner_text("body")
            print(f"  Page text: {body[:500]}")
            return result

        await asyncio.sleep(3)
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_step2_after_click.png"))
        result["screenshots"].append("tiktok_step2_after_click.png")
        result["steps"].append(f"Clicked 'phone or email' - now on next screen")

        # Step 3: Analyze the new screen
        print("[3] Analyzing form after click...")
        
        # Check URL
        current_url = page.url
        print(f"  Current URL: {current_url}")
        result["steps"].append(f"URL: {current_url}")

        # Get all inputs
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                inp_type = await inp.get_attribute("type") or "text"
                inp_name = await inp.get_attribute("name") or ""
                inp_placeholder = await inp.get_attribute("placeholder") or ""
                print(f"  Input: type={inp_type}, name={inp_name}, placeholder={inp_placeholder}")
                result["steps"].append(f"Input: type={inp_type}, name={inp_name}, ph={inp_placeholder}")
            except:
                pass

        # Get page text
        body_text = await page.inner_text("body")
        print(f"  Body text snippet: {body_text[:500]}")
        result["steps"].append(f"Body: {body_text[:300]}")

        # Check for email/phone toggle
        email_toggles = await page.query_selector_all("a, span, div, button")
        for toggle in email_toggles[:30]:
            try:
                text = (await toggle.inner_text()).strip().lower()
                if text == "email" or text == "phone":
                    print(f"  Toggle found: '{text}'")
                    result["steps"].append(f"Toggle: '{text}'")
            except:
                pass

        # Step 4: If email field exists, try entering email
        email_input = None
        for inp in inputs:
            try:
                inp_type = await inp.get_attribute("type") or ""
                inp_name = (await inp.get_attribute("name") or "").lower()
                inp_placeholder = (await inp.get_attribute("placeholder") or "").lower()
                if "email" in inp_type or "email" in inp_name or "email" in inp_placeholder:
                    email_input = inp
                    break
            except:
                continue

        if email_input:
            email = random_email()
            print(f"[4] Entering email: {email}")
            await email_input.fill(email)
            await asyncio.sleep(1)
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_step4_email_entered.png"))
            result["screenshots"].append("tiktok_step4_email_entered.png")
            result["steps"].append(f"Entered email: {email}")

            # Try clicking Next/Send code
            next_selectors = [
                'button:has-text("Next")',
                'button:has-text("Send code")',
                'button:has-text("Continue")',
                'button[type="submit"]',
                'div[class*="button"]:has-text("Send")',
            ]
            for sel in next_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        print(f"  Clicking: {sel}")
                        await btn.click()
                        await asyncio.sleep(3)
                        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_step5_after_next.png"))
                        result["screenshots"].append("tiktok_step5_after_next.png")
                        result["steps"].append(f"Clicked next/continue")
                        
                        # What's on screen now?
                        body2 = await page.inner_text("body")
                        print(f"  After next: {body2[:400]}")
                        result["steps"].append(f"After next body: {body2[:200]}")
                        break
                except:
                    continue
        else:
            # Maybe it's a phone field that needs switching to email
            print("[4] No email field found directly - checking for email/phone switch")
            # Look for a tab or link to switch to email
            switches = await page.query_selector_all("a, span, div, button, label")
            for sw in switches:
                try:
                    text = (await sw.inner_text()).strip().lower()
                    if text in ["email", "switch to email", "sign up with email"]:
                        print(f"  Found email switch: '{text}'")
                        await sw.click()
                        await asyncio.sleep(2)
                        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_step4b_email_mode.png"))
                        result["screenshots"].append("tiktok_step4b_email_mode.png")
                        result["steps"].append(f"Switched to email mode")
                        break
                except:
                    pass

        # Final state
        final_inputs = await page.query_selector_all("input")
        final_text = await page.inner_text("body")
        print(f"\n[5] Final state:")
        print(f"  Inputs: {len(final_inputs)}")
        print(f"  Text snippet: {final_text[:400]}")
        
        # Check for captcha
        frames = page.frames
        for f in frames:
            url = f.url.lower()
            if "captcha" in url or "recaptcha" in url or "hcaptcha" in url or "arkose" in url or "geetest" in url:
                print(f"  CAPTCHA frame detected: {f.url}")
                result["steps"].append(f"CAPTCHA: {f.url}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tiktok_error.png"))
        except:
            pass
    finally:
        await browser.close()
    
    return result


async def probe_instagram(playwright):
    """Fill Instagram signup form and see what happens."""
    print("\n" + "=" * 60)
    print("INSTAGRAM DEEP PROBE")
    print("=" * 60)

    result = {"steps": [], "screenshots": [], "error": None}
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = await context.new_page()

    try:
        # Step 1: Go to signup
        print("[1] Navigating to Instagram signup...")
        await page.goto("https://www.instagram.com/accounts/emailsignup/", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(4)
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "ig_step1_landing.png"))
        result["screenshots"].append("ig_step1_landing.png")
        result["steps"].append("Landed on signup page")

        # Step 2: Identify form fields
        print("[2] Scanning form fields...")
        inputs = await page.query_selector_all("input")
        fields = []
        for i, inp in enumerate(inputs):
            try:
                inp_type = await inp.get_attribute("type") or "text"
                inp_name = await inp.get_attribute("name") or ""
                inp_placeholder = await inp.get_attribute("placeholder") or ""
                inp_aria = await inp.get_attribute("aria-label") or ""
                inp_autocap = await inp.get_attribute("autocomplete") or ""
                desc = f"type={inp_type}, name={inp_name}, ph={inp_placeholder}, aria={inp_aria}, auto={inp_autocap}"
                print(f"  [{i}] {desc}")
                fields.append({"index": i, "type": inp_type, "name": inp_name, "placeholder": inp_placeholder, "aria": inp_aria, "autocomplete": inp_autocap})
            except:
                pass

        # Instagram signup fields are typically:
        # [0] Mobile number or email
        # [1] Full name
        # [2] Username
        # [3] Password
        # But they may be split across steps

        first, last = random_name()
        email = random_email()
        password = "Tx9$kL2#mP!7"
        username = f"{first.lower()}{last.lower()}{random.randint(100,999)}"

        print(f"  Generated: email={email}, name={first} {last}, user={username}")

        # Step 3: Fill first field (email or phone)
        if len(fields) > 0:
            print(f"[3] Filling first field (email)...")
            f0 = fields[0]
            inp_el = page.locator("input").nth(f0["index"])
            await inp_el.fill(email)
            await asyncio.sleep(0.5)

        # Step 4: Fill remaining visible fields
        if len(fields) > 1:
            # Full name
            print(f"[4] Filling full name...")
            inp_el = page.locator("input").nth(1)
            await inp_el.fill(f"{first} {last}")
            await asyncio.sleep(0.5)

        if len(fields) > 2:
            # Username
            print(f"[5] Filling username...")
            inp_el = page.locator("input").nth(2)
            await inp_el.fill(username)
            await asyncio.sleep(0.5)

        if len(fields) > 3:
            # Password
            print(f"[6] Filling password...")
            inp_el = page.locator("input").nth(3)
            await inp_el.fill(password)
            await asyncio.sleep(0.5)

        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "ig_step2_filled.png"))
        result["screenshots"].append("ig_step2_filled.png")
        result["steps"].append(f"Filled: email={email}, name={first} {last}, user={username}")

        # Step 5: Submit
        print("[7] Looking for submit button...")
        submit_selectors = [
            'button:has-text("Submit")',
            'button:has-text("Sign up")',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button[type="submit"]',
        ]
        for sel in submit_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    print(f"  Clicking: {sel}")
                    await btn.click()
                    await asyncio.sleep(5)
                    await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "ig_step3_after_submit.png"))
                    result["screenshots"].append("ig_step3_after_submit.png")
                    result["steps"].append(f"Submitted form")
                    break
            except:
                continue

        # Step 6: Analyze what happened after submit
        body_text = await page.inner_text("body")
        print(f"\n[8] After submit:")
        print(f"  URL: {page.url}")
        print(f"  Body snippet: {body_text[:500]}")
        result["steps"].append(f"After submit URL: {page.url}")
        result["steps"].append(f"After submit body: {body_text[:300]}")

        # Check for birthday step (Instagram often asks)
        birthday_signals = ["birthday", "date of birth", "month", "day", "year"]
        if any(sig in body_text.lower() for sig in birthday_signals):
            print("  -> Birthday step detected!")
            result["steps"].append("Birthday step detected")
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "ig_step4_birthday.png"))
            result["screenshots"].append("ig_step4_birthday.png")

        # Check for captcha
        captcha_signals = ["captcha", "verify", "puzzle", "slider"]
        if any(sig in body_text.lower() for sig in captcha_signals):
            print("  -> Captcha detected!")
            result["steps"].append("Captcha detected")

        # Check for phone verification
        phone_signals = ["phone", "sms", "verify your number", "phone number"]
        if any(sig in body_text.lower() for sig in phone_signals):
            print("  -> Phone verification detected!")
            result["steps"].append("Phone verification detected")

        # Check for email verification
        email_signals = ["enter the code", "verification code", "check your email", "we sent"]
        if any(sig in body_text.lower() for sig in email_signals):
            print("  -> Email verification step detected!")
            result["steps"].append("Email verification step detected")

        # Check captcha frames
        frames = page.frames
        for f in frames:
            url = f.url.lower()
            if any(c in url for c in ["captcha", "recaptcha", "hcaptcha", "arkose"]):
                print(f"  -> Captcha frame: {f.url}")
                result["steps"].append(f"Captcha frame: {f.url}")

    except Exception as e:
        result["error"] = str(e)
        print(f"  ERROR: {e}")
        try:
            await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "ig_error.png"))
        except:
            pass
    finally:
        await browser.close()

    return result


async def main():
    import json

    print(f"Deep probe started: {datetime.now().isoformat()}")

    async with async_playwright() as pw:
        tiktok_result = await probe_tiktok(pw)
        instagram_result = await probe_instagram(pw)

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    
    for name, result in [("TikTok", tiktok_result), ("Instagram", instagram_result)]:
        print(f"\n--- {name} ---")
        if result["error"]:
            print(f"  ERROR: {result['error']}")
        for step in result["steps"]:
            print(f"  • {step}")
        print(f"  Screenshots: {result['screenshots']}")


if __name__ == "__main__":
    asyncio.run(main())
