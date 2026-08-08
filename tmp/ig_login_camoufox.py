"""Login to Instagram via Camoufox to clear checkpoint challenge, then save cookies."""
import json
import time
from pathlib import Path

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

username = os.getenv("BOUNTY_IG_USERNAME", "")
password = os.getenv("BOUNTY_IG_PASSWORD", "")

print(f"Logging into Instagram as @{username} via Camoufox...")

cookie_out = Path(__file__).resolve().parents[1] / "data" / "ig_cookies.json"

with Camoufox(headless=True) as browser:
    page = browser.new_page()

    # Go to Instagram login page
    page.goto("https://www.instagram.com/accounts/login/", timeout=30000, wait_until="domcontentloaded")
    time.sleep(4)

    # Enter username
    print("Entering username...")
    try:
        username_input = page.wait_for_selector('input[name="username"]', timeout=10000)
        username_input.fill(username)
        time.sleep(1)

        # Enter password
        pw_input = page.query_selector('input[name="password"]')
        if pw_input:
            pw_input.fill(password)
            time.sleep(1)
        else:
            pw_input = page.wait_for_selector('input[type="password"]', timeout=5000)
            pw_input.fill(password)

        # Click login button
        buttons = page.query_selector_all('button[type="submit"]')
        if not buttons:
            buttons = page.query_selector_all('button')
        for btn in buttons:
            text = btn.inner_text().strip().lower()
            if "log in" in text or "submit" in text:
                btn.click()
                break
        else:
            page.keyboard.press("Enter")

        print("Waiting for login to process...")
        time.sleep(8)

        # Check for "Save login info" popup
        save_buttons = page.query_selector_all('button')
        for btn in save_buttons:
            text = btn.inner_text().strip().lower()
            if "not now" in text or "save" in text:
                if "not now" in text:
                    btn.click()
                    time.sleep(2)
                    break

        # Check for checkpoint/challenge
        body_text = page.inner_text("body") if page.query_selector("body") else ""
        if "verify" in body_text.lower() or "challenge" in body_text.lower() or "security" in body_text.lower():
            print(f"Challenge detected: {body_text[:200]}")
            # If it's an email/SMS challenge, we can't solve it here
            # But Camoufox might pass the fingerprint check automatically

        # Extract cookies
        cookies = page.context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        # Check for session cookies
        has_session = "sessionid" in cookie_dict
        has_user_id = "ds_user_id" in cookie_dict

        if has_session:
            print(f"\nSUCCESS! Got sessionid ({len(cookie_dict['sessionid'])} chars)")
            print(f"Got ds_user_id: {has_user_id}")

            # Save cookies
            cookie_out.parent.mkdir(parents=True, exist_ok=True)
            cookie_out.write_text(json.dumps(cookie_dict, indent=2), encoding="utf-8")
            print(f"Cookies saved to {cookie_out}")
            print(f"Cookie names: {sorted(cookie_dict.keys())}")
        else:
            print("\nFAILED - no sessionid cookie")
            print(f"Available cookies: {sorted(cookie_dict.keys())}")
            print(f"Page content: {body_text[:300]}")

            # Check current URL
            print(f"Current URL: {page.url}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
