"""Use Camoufox to login to X and extract auth_token cookie.

Camoufox bypasses X's anti-bot detection. Once we have auth_token,
we can use it with curl_cffi for all subsequent API calls (fast, no browser needed).
"""
import json
import time
from pathlib import Path

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

username = os.getenv("BOUNTY_X_USERNAME", "")
email = os.getenv("BOUNTY_X_EMAIL", "")
password = os.getenv("BOUNTY_X_PASSWORD", "")

print(f"Logging in as @{username}...")

cookie_out = Path(__file__).resolve().parents[1] / "data" / "x_cookies.json"

with Camoufox(headless=True) as browser:
    page = browser.new_page()

    # Go to X login
    page.goto("https://x.com/i/flow/login", timeout=45000, wait_until="domcontentloaded")
    time.sleep(5)

    # Enter username
    print("Entering username...")
    try:
        username_input = page.wait_for_selector('input[autocomplete="username"]', timeout=10000)
        username_input.fill(username)
        time.sleep(1)

        # Click Next
        next_buttons = page.query_selector_all('button[role="button"]')
        for btn in next_buttons:
            text = btn.inner_text().strip().lower()
            if "next" in text:
                btn.click()
                break
        time.sleep(3)
    except Exception as e:
        print(f"Username step error: {e}")
        # Try alternate selector
        try:
            inputs = page.query_selector_all('input[type="text"]')
            if inputs:
                inputs[0].fill(username)
                time.sleep(1)
                # Find and click next
                page.keyboard.press("Enter")
                time.sleep(3)
        except Exception as e2:
            print(f"Alt username: {e2}")

    # Check for unusual activity / email verification step
    # X sometimes asks for email/phone confirmation
    print("Checking for verification step...")
    body_text = page.inner_text("body") if page.query_selector("body") else ""

    if "verify your identity" in body_text.lower() or "phone number or email" in body_text.lower() or "enter your phone number or email" in body_text.lower():
        print("Email verification step detected. Entering email...")
        try:
            email_input = page.wait_for_selector('input[data-testid="ocfEnterText1"]', timeout=5000)
            if email and email_input:
                email_input.fill(email)
                time.sleep(1)
                # Click Next
                next_btn = page.query_selector('button[data-testid="ocfEnterTextNextButton"]')
                if next_btn:
                    next_btn.click()
                time.sleep(3)
        except Exception as e:
            print(f"Email verification: {e}")
            # Try text input
            try:
                text_inputs = page.query_selector_all('input[type="text"]')
                if text_inputs and email:
                    text_inputs[-1].fill(email)
                    time.sleep(1)
                    page.keyboard.press("Enter")
                    time.sleep(3)
            except:
                pass

    # Enter password
    print("Entering password...")
    try:
        pw_input = page.wait_for_selector('input[type="password"]', timeout=10000)
        pw_input.fill(password)
        time.sleep(1)

        # Click Log in
        login_btn = page.query_selector('button[data-testid="LoginForm_Login_Button"]')
        if login_btn:
            login_btn.click()
        else:
            # Try text match
            buttons = page.query_selector_all('button[role="button"]')
            for btn in buttons:
                if "log in" in btn.inner_text().strip().lower():
                    btn.click()
                    break
        time.sleep(5)
    except Exception as e:
        print(f"Password step: {e}")

    # Wait for login to complete
    print("Waiting for login to complete...")
    time.sleep(5)

    # Check if we're logged in by looking for cookies
    cookies = page.context.cookies()
    cookie_dict = {c["name"]: c["value"] for c in cookies}

    auth_token = cookie_dict.get("auth_token", "")
    ct0 = cookie_dict.get("ct0", "")

    if auth_token:
        print(f"\nSUCCESS! Got auth_token ({len(auth_token)} chars)")
        print(f"Got ct0: {bool(ct0)} ({len(ct0)} chars)")

        # Save cookies for our connector
        cookie_out.parent.mkdir(parents=True, exist_ok=True)
        cookie_out.write_text(json.dumps(cookie_dict, indent=2), encoding="utf-8")
        print(f"Cookies saved to {cookie_out}")

        # Verify by checking if home page loads
        page.goto("https://x.com/home", timeout=20000, wait_until="domcontentloaded")
        time.sleep(3)
        title = page.title()
        print(f"Home page title: {title}")
        body = page.inner_text("body")[:200] if page.query_selector("body") else ""
        if "home" in body.lower() or "what's happening" in body.lower():
            print("Login confirmed - home page loaded")
        else:
            print(f"Home page content: {body[:100]}")
    else:
        print("\nFAILED - no auth_token cookie found")
        print(f"Available cookies: {list(cookie_dict.keys())}")
        # Check for error messages
        body = page.inner_text("body")[:500] if page.query_selector("body") else ""
        print(f"Page content: {body[:300]}")
