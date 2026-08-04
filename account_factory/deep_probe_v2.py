"""
Deep probe v2: Properly interact with TikTok and Instagram signup.
Fixes from v1:
- TikTok: use data-e2e attributes and proper React click handling
- Instagram: fill fields by aria-label/placeholder (not DOM order), handle birthday dropdowns
"""

import asyncio
import os
import random
import string
import json
from datetime import datetime
from playwright.async_api import async_playwright

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "deep_probe_v2_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def random_email():
    name = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"testuser{name}@agentmail.to"


def random_name():
    first = random.choice(["James", "Sarah", "Mike", "Emma", "Alex", "Lisa", "David", "Anna"])
    last = random.choice(["Smith", "Chen", "Brown", "Lee", "Wilson", "Garcia", "Park", "Wong"])
    return first, last


async def screenshot(page, name):
    path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
    await page.screenshot(path=path)
    print(f"  📸 {path}")
    return path


async def probe_tiktok(playwright):
    """Click through TikTok: 'Use phone or email' → switch to email → see form."""
    print("\n" + "=" * 60)
    print("TIKTOK DEEP PROBE v2")
    print("=" * 60)

    steps = []
    error = None
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
        print("[1] Navigating to TikTok signup...")
        await page.goto("https://www.tiktok.com/signup", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        await screenshot(page, "tiktok_01_landing")
        steps.append("Landed on signup page")

        # TikTok React buttons: use text-based locator with exact match
        print("[2] Clicking 'Use phone or email'...")
        clicked = False

        # Strategy 1: TikTok-specific data attributes
        for attr in ["data-e2e", "data-testid"]:
            els = await page.query_selector_all(f"[{attr}]")
            for el in els:
                val = await el.get_attribute(attr) or ""
                text = ""
                try:
                    text = (await el.inner_text()).strip()
                except:
                    pass
                if "phone" in val.lower() or "email" in val.lower() or "phone or email" in text.lower():
                    print(f"  Found by {attr}={val}: '{text[:50]}'")
                    await el.click()
                    clicked = True
                    break
            if clicked:
                break

        # Strategy 2: Find the exact text element and click it
        if not clicked:
            # Use get_by_text with exact match
            try:
                el = page.get_by_text("Use phone or email", exact=True)
                if await el.count() > 0:
                    print("  Found by exact text match")
                    await el.first.click()
                    clicked = True
            except:
                pass

        # Strategy 3: Scan all clickable elements for the text
        if not clicked:
            all_els = await page.query_selector_all("a, button, div[role='button'], div[tabindex]")
            for el in all_els:
                try:
                    text = (await el.inner_text()).strip()
                    if text.lower() == "use phone or email":
                        print(f"  Found by element scan: '{text}'")
                        await el.click()
                        clicked = True
                        break
                except:
                    continue

        if not clicked:
            steps.append("FAILED: Could not find 'Use phone or email' button")
            print("  Could not find button! Dumping page...")
            html = await page.content()
            print(f"  HTML length: {len(html)}")
            print(f"  HTML snippet: {html[:2000]}")
        else:
            await asyncio.sleep(3)
            await screenshot(page, "tiktok_02_after_click")
            current_url = page.url
            print(f"  URL after click: {current_url}")
            steps.append(f"Clicked 'phone or email', URL: {current_url}")

            # Analyze new page
            inputs = await page.query_selector_all("input")
            print(f"  Found {len(inputs)} inputs")
            for i, inp in enumerate(inputs):
                try:
                    itype = await inp.get_attribute("type") or "text"
                    iname = await inp.get_attribute("name") or ""
                    iph = await inp.get_attribute("placeholder") or ""
                    iaria = await inp.get_attribute("aria-label") or ""
                    print(f"    [{i}] type={itype}, name={iname}, ph={iph}, aria={iaria}")
                    steps.append(f"Input[{i}]: type={itype}, name={iname}, ph={iph}, aria={iaria}")
                except:
                    pass

            # Check for email/phone tabs
            body_text = await page.inner_text("body")
            print(f"  Body text: {body_text[:600]}")
            steps.append(f"Body: {body_text[:300]}")

            # Look for Email/Phone toggle
            toggles = await page.query_selector_all("a, span, div, button, label")
            for toggle in toggles[:50]:
                try:
                    text = (await toggle.inner_text()).strip().lower()
                    if text in ["email", "sign up with email", "use email"]:
                        print(f"  Found email toggle: '{text}'")
                        steps.append(f"Email toggle: '{text}'")
                except:
                    pass

            # If there's an email input or toggle, try interacting
            email_toggle = None
            for sel in [
                'text="Email"',
                'a:has-text("Email")',
                'div:has-text("Email"):not(:has(*))',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        email_toggle = el
                        break
                except:
                    continue

            if email_toggle:
                print("[3] Switching to email mode...")
                await email_toggle.click()
                await asyncio.sleep(2)
                await screenshot(page, "tiktok_03_email_mode")
                steps.append("Switched to email mode")

                # Now check inputs again
                inputs2 = await page.query_selector_all("input")
                for i, inp in enumerate(inputs2):
                    try:
                        itype = await inp.get_attribute("type") or "text"
                        iph = await inp.get_attribute("placeholder") or ""
                        iaria = await inp.get_attribute("aria-label") or ""
                        print(f"    [{i}] type={itype}, ph={iph}, aria={iaria}")
                        steps.append(f"Email-mode Input[{i}]: type={itype}, ph={iph}, aria={iaria}")
                    except:
                        pass

                # Try entering email
                email = random_email()
                email_in = None
                for inp in inputs2:
                    try:
                        itype = (await inp.get_attribute("type") or "").lower()
                        iph = (await inp.get_attribute("placeholder") or "").lower()
                        iaria = (await inp.get_attribute("aria-label") or "").lower()
                        iname = (await inp.get_attribute("name") or "").lower()
                        if "email" in itype or "email" in iph or "email" in iaria or "email" in iname:
                            email_in = inp
                            break
                    except:
                        continue

                if email_in:
                    print(f"[4] Entering email: {email}")
                    await email_in.fill(email)
                    await asyncio.sleep(1)
                    await screenshot(page, "tiktok_04_email_entered")
                    steps.append(f"Entered email: {email}")

                    # Click send code / next
                    for btn_text in ["Send code", "Next", "Continue", "Get code", "Send"]:
                        try:
                            btn = page.get_by_role("button", name=btn_text)
                            if await btn.count() > 0:
                                print(f"  Clicking '{btn_text}'")
                                await btn.first.click()
                                await asyncio.sleep(4)
                                await screenshot(page, "tiktok_05_after_send")
                                after_text = await page.inner_text("body")
                                print(f"  After send: {after_text[:400]}")
                                steps.append(f"Clicked '{btn_text}', after: {after_text[:200]}")
                                break
                        except:
                            continue

                    # Check for captcha
                    frames = page.frames
                    for f in frames:
                        furl = f.url.lower()
                        if any(c in furl for c in ["captcha", "recaptcha", "hcaptcha", "arkose", "geetest", "verify"]):
                            print(f"  CAPTCHA: {f.url}")
                            steps.append(f"CAPTCHA frame: {f.url}")

    except Exception as e:
        error = str(e)
        print(f"  ERROR: {e}")
    finally:
        await browser.close()

    return {"platform": "tiktok", "steps": steps, "error": error}


async def probe_instagram(playwright):
    """Fill Instagram form by field label, handle birthday, submit."""
    print("\n" + "=" * 60)
    print("INSTAGRAM DEEP PROBE v2")
    print("=" * 60)

    steps = []
    error = None
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
        first, last = random_name()
        email = random_email()
        password = "Tx9$kL2#mP7"
        username = f"{first.lower()}{last.lower()}{random.randint(100,999)}"

        print(f"  Identity: {first} {last}, email={email}, user={username}")

        print("[1] Navigating to Instagram signup...")
        await page.goto("https://www.instagram.com/accounts/emailsignup/", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(4)
        await screenshot(page, "ig_01_landing")
        steps.append("Landed")

        # Fill by field identifier, not DOM index
        # Instagram uses aria-label or placeholder for field identification

        print("[2] Filling email/phone field...")
        # Field 1: "Mobile number or email"
        email_filled = False
        for sel in [
            'input[aria-label*="Mobile"]',
            'input[aria-label*="email"]',
            'input[placeholder*="Mobile"]',
            'input[type="text"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.fill(email)
                    email_filled = True
                    print(f"  Email filled via: {sel}")
                    break
            except:
                continue

        await asyncio.sleep(0.5)

        print("[3] Filling password field...")
        # Field 2: Password
        try:
            pw_el = page.locator('input[type="password"]').first
            if await pw_el.is_visible(timeout=2000):
                await pw_el.fill(password)
                print("  Password filled")
        except:
            pass

        await asyncio.sleep(0.5)

        print("[4] Filling full name field...")
        # Field 3: Full name - look for it by aria-label
        name_filled = False
        for sel in [
            'input[aria-label*="name"]',
            'input[placeholder*="name"]',
            'input[autocomplete*="name"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.fill(f"{first} {last}")
                    name_filled = True
                    print(f"  Name filled via: {sel}")
                    break
            except:
                continue
        
        if not name_filled:
            # Instagram name field might be 3rd text input (after email and password)
            text_inputs = await page.query_selector_all('input[type="text"]')
            if len(text_inputs) >= 2:
                await text_inputs[1].fill(f"{first} {last}")
                name_filled = True
                print("  Name filled via 2nd text input")

        await asyncio.sleep(0.5)

        print("[5] Filling username field...")
        # Field 4: Username
        username_filled = False
        for sel in [
            'input[aria-label*="Username"]',
            'input[placeholder*="Username"]',
            'input[type="search"]',
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.fill(username)
                    username_filled = True
                    print(f"  Username filled via: {sel}")
                    break
            except:
                continue

        await asyncio.sleep(1)
        await screenshot(page, "ig_02_fields_filled")
        steps.append(f"Filled: email={email}, name={first} {last}, user={username}")

        print("[6] Filling birthday dropdowns...")
        # Birthday: Month, Day, Year dropdowns
        # Try select elements
        selects = await page.query_selector_all("select")
        print(f"  Found {len(selects)} select elements")
        
        if len(selects) >= 3:
            # Month
            await selects[0].select_option(label="January")
            await asyncio.sleep(0.3)
            # Day  
            await selects[1].select_option(label="15")
            await asyncio.sleep(0.3)
            # Year
            await selects[2].select_option(label="1995")
            await asyncio.sleep(0.3)
            print("  Birthday filled: Jan 15, 1995")
            steps.append("Birthday: Jan 15, 1995")
        else:
            # Maybe custom dropdowns (not <select>)
            # Try clicking birthday area and selecting from dropdown
            print(f"  Only {len(selects)} selects found - checking for custom dropdowns")
            
            # Look for birthday-related clickable elements
            bd_els = await page.query_selector_all("div, span, button")
            for el in bd_els:
                try:
                    text = (await el.inner_text()).strip()
                    if text in ["Month", "Day", "Year"]:
                        print(f"  Found birthday label: '{text}'")
                except:
                    pass
            
            # Try scrolling down to find more fields
            await page.evaluate("window.scrollTo(0, 500)")
            await asyncio.sleep(0.5)
            selects2 = await page.query_selector_all("select")
            print(f"  After scroll: {len(selects2)} selects")

        await screenshot(page, "ig_03_birthday_filled")
        steps.append("Birthday filled (or attempted)")

        print("[7] Looking for Submit button...")
        submit_clicked = False
        for sel in [
            'button:has-text("Submit")',
            'button:has-text("Sign up")',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button[type="submit"]',
            'div[role="button"]:has-text("Submit")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    print(f"  Clicking: {sel}")
                    await btn.click()
                    submit_clicked = True
                    break
            except:
                continue

        if submit_clicked:
            await asyncio.sleep(5)
            await screenshot(page, "ig_04_after_submit")
            after_url = page.url
            after_text = await page.inner_text("body")
            print(f"  After submit URL: {after_url}")
            print(f"  After submit text: {after_text[:500]}")
            steps.append(f"Submitted, URL: {after_url}")
            steps.append(f"After submit body: {after_text[:300]}")

            # Check for verification step
            if "code" in after_text.lower() or "we sent" in after_text.lower() or "verify" in after_text.lower():
                print("  -> EMAIL VERIFICATION step detected!")
                steps.append("EMAIL VERIFICATION step detected")
            
            if "phone" in after_text.lower() or "sms" in after_text.lower():
                print("  -> PHONE verification detected")
                steps.append("Phone verification detected")

            if "captcha" in after_text.lower():
                print("  -> CAPTCHA detected")
                steps.append("Captcha detected")
        else:
            print("  No submit button found!")
            # Try scrolling
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)
            await screenshot(page, "ig_04_scrolled_bottom")
            # Check again
            all_btns = await page.query_selector_all("button, div[role='button']")
            for btn in all_btns:
                try:
                    text = (await btn.inner_text()).strip()
                    if "submit" in text.lower() or "sign up" in text.lower():
                        print(f"  Found after scroll: '{text}'")
                except:
                    pass

    except Exception as e:
        error = str(e)
        print(f"  ERROR: {e}")
    finally:
        await browser.close()

    return {"platform": "instagram", "steps": steps, "error": error, "identity": {"email": email, "username": username, "password": password}}


async def main():
    print(f"Deep probe v2 started: {datetime.now().isoformat()}")

    async with async_playwright() as pw:
        tiktok = await probe_tiktok(pw)
        instagram = await probe_instagram(pw)

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    for result in [tiktok, instagram]:
        print(f"\n{'─' * 40}")
        print(f"  Platform: {result['platform']}")
        if result.get("error"):
            print(f"  Error: {result['error']}")
        for s in result["steps"]:
            print(f"  • {s}")


if __name__ == "__main__":
    asyncio.run(main())
