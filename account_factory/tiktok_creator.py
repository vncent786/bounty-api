"""
TikTok Account Creation Pipeline

Automated TikTok account creation: AgentMail (email) → Playwright (signup) →
email verification code → working account credentials.

Flow:
  1. Create AgentMail inbox
  2. Navigate to TikTok signup
  3. Click "Use phone or email" → switch to email mode
  4. Fill birthday dropdowns (Month/Day/Year)
  5. Enter email + password
  6. Click "Send code" → TikTok sends 6-digit code to email
  7. Poll AgentMail for the code
  8. Enter code + check TOS
  9. Click Next → account created
  10. Handle any post-signup steps (skip profile setup, etc.)
  11. Extract session cookies

Requirements:
  pip install agentmail playwright httpx
  playwright install chromium

Environment:
  AGENTMAIL_API_KEY - from agentmail.to

Usage:
  python -m account_factory.tiktok_creator --count 1
  python -m account_factory.tiktok_creator --count 1 --no-headless
"""

import os
import re
import sys
import json
import time
import random
import string
import asyncio
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger("tiktok_factory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── Believable identity generation ──────────────────────────────────────────

FIRST_NAMES_WESTERN = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
    "Lucas", "Isabella", "Mason", "Mia", "Logan", "Charlotte", "Alex",
    "Amelia", "Jack", "Harper", "Ryan", "Ella", "Connor", "Luna", "Tyler",
    "Grace", "Brandon", "Chloe", "Nathan", "Lily", "Dylan", "Zoe",
    "Kevin", "Nina", "Marcus", "Ruby", "Oscar", "Iris", "Felix", "Mila",
    "Leo", "Maya", "Theo", "Aria", "Hugo", "Stella", "Milo", "Nora",
    "Ezra", "Hazel", "Axel", "Ivy", "Dean", "Wren", "Cole", "Sage",
]

FIRST_NAMES_ASIAN = [
    "Wei", "Min", "Jia", "Hao", "Lin", "Yan", "Jun", "Xuan",
    "Kai", "Mei", "Ren", "Xin", "Bo", "Yui", "Haru", "Sora",
    "Jin", "Aiko", "Ryo", "Mika", "Daiki", "Nana", "Kenta", "Rina",
    "Anh", "Linh", "Minh", "Huong", "Duc", "Thao",
]

LAST_NAMES_WESTERN = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor",
    "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White",
    "Harris", "Clark", "Lewis", "Walker", "Hall", "Young", "King",
    "Wright", "Lopez", "Hill", "Green", "Adams", "Baker", "Carter",
    "Turner", "Parker", "Evans", "Edwards", "Collins", "Stewart",
]

LAST_NAMES_ASIAN = [
    "Chen", "Wang", "Li", "Zhang", "Liu", "Yang", "Huang", "Wu",
    "Tanaka", "Suzuki", "Sato", "Takahashi", "Kim", "Park", "Choi",
    "Nguyen", "Tran", "Le", "Pham", "Hoang", "Lim", "Ong", "Wong",
    "Goh", "Chua", "Koh", "Ang",
]


def generate_identity() -> dict:
    """Generate a believable, human-looking identity."""
    # 70% western, 30% asian (realistic for SG/global mix)
    if random.random() < 0.3:
        first = random.choice(FIRST_NAMES_ASIAN)
        last = random.choice(LAST_NAMES_ASIAN + LAST_NAMES_WESTERN)
    else:
        first = random.choice(FIRST_NAMES_WESTERN)
        last = random.choice(LAST_NAMES_WESTERN)

    # Username patterns that look human
    f = first.lower()
    l = last.lower()
    patterns = [
        f"{f}_{l}{random.randint(1, 999)}",          # emma_smith42
        f"{f}{l}{random.randint(10, 999)}",            # emmasmith123
        f"{f[0]}{l}{random.randint(10, 9999)}",        # esmith456
        f"{f}.{l}{random.randint(1, 99)}",             # emma.smith7
        f"{f}{random.randint(1000, 99999)}",           # emma2847
        f"its{f}_{l}",                                 # itsemma_smith
        f"{f}_{l}{random.choice(['x', 'xo', 'tv', '__'])}",
        f"real_{f}{random.randint(1, 99)}",            # real_emma23
        f"{f}{l}{random.randint(1000, 9999)}",
        f"{f}{random.choice(['_', '.'])}{l}{random.randint(100, 9999)}",
    ]
    username = random.choice(patterns)
    # Clean up any broken patterns
    username = re.sub(r'[^\w.]', '', username)
    if len(username) < 4:
        username = f"{f}{l}{random.randint(100, 9999)}"

    # Birthday: 1992-2005 range (age 21-34), believable
    year = random.randint(1992, 2005)
    month = random.randint(1, 12)
    day = random.randint(1, 28)  # safe for all months

    return {
        "first_name": first,
        "last_name": last,
        "username": username,
        "display_name": f"{first} {last}",
        "password": _generate_password(),
        "birth_year": year,
        "birth_month": month,
        "birth_day": day,
    }


def _generate_password(length: int = 14) -> str:
    """Generate a strong password with mixed chars."""
    lower = random.choices(string.ascii_lowercase, k=4)
    upper = random.choices(string.ascii_uppercase, k=3)
    digits = random.choices(string.digits, k=3)
    special = random.choices("!@#$%&*", k=2)
    rest = random.choices(string.ascii_letters + string.digits, k=length - 12)
    pool = lower + upper + digits + special + rest
    random.shuffle(pool)
    return ''.join(pool)


# ── Email layer (AgentMail) ──────────────────────────────────────────────────

class EmailManager:
    """Create and read AgentMail inboxes for email verification."""

    def __init__(self):
        from agentmail import AgentMail
        api_key = os.environ.get("AGENTMAIL_API_KEY")
        if not api_key:
            raise ValueError("AGENTMAIL_API_KEY not set")
        self.client = AgentMail(api_key=api_key)

    def create_inbox(self) -> dict:
        """Create a new email inbox, cleaning up old ones first (free tier limit)."""
        try:
            existing = self.client.inboxes.list()
            for ib in existing.inboxes:
                self.client.inboxes.delete(inbox_id=ib.inbox_id)
        except Exception as e:
            logger.warning(f"Cleanup error (non-fatal): {e}")

        inbox = self.client.inboxes.create()
        return {
            "inbox_id": inbox.inbox_id,
            "email": inbox.email,
        }

    def wait_for_code(self, inbox_id: str, timeout: int = 120) -> Optional[str]:
        """
        Poll for TikTok verification email and extract the 6-digit code.
        Returns the code string or None if timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                messages = self.client.inboxes.messages.list(
                    inbox_id=inbox_id, limit=5
                )
                msg_list = []
                if hasattr(messages, 'messages'):
                    msg_list = messages.messages
                elif hasattr(messages, 'data'):
                    msg_list = messages.data

                for msg in msg_list:
                    sender = str(getattr(msg, 'from_', '') or '')
                    subject = str(getattr(msg, 'subject', '') or '')
                    combined = (sender + subject).lower()

                    if any(k in combined for k in ['tiktok', 'verification', 'code', 'verify', 'email']):
                        # Get full message
                        msg_id = getattr(msg, 'message_id', None) or getattr(msg, 'id', None)
                        full_msg = self.client.inboxes.messages.get(
                            inbox_id=inbox_id, message_id=msg_id
                        )
                        body = ""
                        if hasattr(full_msg, 'text'):
                            body = str(full_msg.text or '')
                        elif hasattr(full_msg, 'html'):
                            body = str(full_msg.html or '')

                        code = self._extract_code(subject + " " + body)
                        if code:
                            return code
            except Exception as e:
                logger.warning(f"Poll error: {e}")
            time.sleep(4)

        logger.error(f"Timeout waiting for TikTok code (inbox: {inbox_id})")
        return None

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """Extract a 6-digit verification code from email text."""
        patterns = [
            r'(?:code|verification|verify)[^0-9]*(\d{6})',
            r'(\d{6})[^0-9]*(?:code|verification)',
            r'\b(\d{6})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def delete_inbox(self, inbox_id: str):
        try:
            self.client.inboxes.delete(inbox_id=inbox_id)
        except Exception:
            pass


# ── TikTok Signup Bot ────────────────────────────────────────────────────────

class TikTokSignupBot:
    """Playwright bot for TikTok email signup flow."""

    SIGNUP_URL = "https://www.tiktok.com/signup"

    # Human-like delays
    SHORT_DELAY = (0.3, 0.8)
    MED_DELAY = (0.8, 1.8)
    LONG_DELAY = (1.5, 3.0)

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None

    async def _delay(self, range_tuple=None):
        """Random human-like delay."""
        lo, hi = range_tuple or self.SHORT_DELAY
        await asyncio.sleep(random.uniform(lo, hi))

    async def _type_human(self, selector, text):
        """Type text character by character with human-like timing."""
        await self.page.locator(selector).click()
        await self._delay(self.SHORT_DELAY)
        for char in text:
            await self.page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.03, 0.12))
        await self._delay(self.SHORT_DELAY)

    async def launch(self):
        """Launch browser with anti-detection measures."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Asia/Singapore",
        )

        # Anti-detection: hide webdriver flag
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        """)

        self.page = await self.context.new_page()
        return self.page

    async def _screenshot(self, name: str):
        """Save debug screenshot."""
        screenshot_dir = Path("tiktok_screenshots")
        screenshot_dir.mkdir(exist_ok=True)
        path = screenshot_dir / f"{name}.png"
        try:
            await self.page.screenshot(path=str(path))
            logger.info(f"Screenshot: {path}")
        except:
            pass

    async def _select_birthday(self, month: int, day: int, year: int):
        """
        Fill TikTok birthday dropdowns.
        TikTok uses custom React dropdowns - click to open, then select option.
        Also try native <select> as fallback.
        """
        # Month names for matching
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        month_name = month_names[month - 1]

        # Strategy 1: Native <select> elements
        selects = await self.page.query_selector_all("select")
        if len(selects) >= 3:
            logger.info(f"Found {len(selects)} native selects, trying...")
            try:
                await selects[0].select_option(value=str(month))
                await self._delay(self.SHORT_DELAY)
                await selects[1].select_option(value=str(day))
                await self._delay(self.SHORT_DELAY)
                await selects[2].select_option(value=str(year))
                await self._delay(self.SHORT_DELAY)
                logger.info(f"Birthday set via native select: {month_name} {day}, {year}")
                return True
            except Exception as e:
                logger.warning(f"Native select failed: {e}")

        # Strategy 2: Custom dropdowns (TikTok React components)
        # Look for dropdown containers with Month/Day/Year labels
        for label_text, value in [("Month", month_name), ("Day", str(day)), ("Year", str(year))]:
            try:
                # Find the dropdown by its label
                # TikTok dropdowns typically have a wrapper div with the label text
                dropdown_found = False

                # Try data-e2e attributes
                for attr_val in ["birthday_month", "birthday_day", "birthday_year"]:
                    if label_text.lower() in attr_val:
                        el = self.page.locator(f'[data-e2e="{attr_val}"]')
                        if await el.count() > 0:
                            await el.first.click()
                            await self._delay(self.SHORT_DELAY)
                            # Select the value from the opened dropdown
                            option = self.page.locator(f'[data-e2e="option-{value}"], div:has-text("{value}")').first
                            if await option.is_visible(timeout=2000):
                                await option.click()
                                dropdown_found = True
                                break

                if dropdown_found:
                    continue

                # Try clicking elements containing the label text
                containers = await self.page.query_selector_all(
                    'div[class*="select"], div[class*="dropdown"], div[role="listbox"], div[role="combobox"]'
                )
                for container in containers:
                    try:
                        text = await container.inner_text()
                        if label_text.lower() in text.lower():
                            await container.click()
                            await self._delay(self.SHORT_DELAY)
                            # Find the option in the opened dropdown
                            options = await self.page.query_selector_all(
                                'div[class*="option"], div[role="option"], li'
                            )
                            for opt in options:
                                opt_text = (await opt.inner_text()).strip()
                                if opt_text == value:
                                    await opt.click()
                                    dropdown_found = True
                                    break
                            if dropdown_found:
                                break
                    except:
                        continue

                if not dropdown_found:
                    # Last resort: try div elements with exact label text
                    label_els = await self.page.query_selector_all("div, span")
                    for el in label_els:
                        try:
                            t = (await el.inner_text()).strip()
                            if t == label_text:
                                # Click the parent or sibling that's the dropdown trigger
                                parent = await el.evaluate_handle("el => el.parentElement")
                                await parent.click()
                                await self._delay(self.SHORT_DELAY)
                                # Find option
                                opts = await self.page.query_selector_all('div[class*="option"], div[role="option"]')
                                for opt in opts:
                                    ot = (await opt.inner_text()).strip()
                                    if ot == value:
                                        await opt.click()
                                        dropdown_found = True
                                        break
                                if dropdown_found:
                                    break
                        except:
                            continue

            except Exception as e:
                logger.warning(f"Custom dropdown for {label_text} failed: {e}")

        # Verify something was selected
        await self._delay(self.SHORT_DELAY)
        return dropdown_found

    async def signup(self, identity: dict, email: str) -> dict:
        """
        Execute the full TikTok signup flow.
        Returns dict with success status and details.
        """
        result = {
            "success": False,
            "email": email,
            "username": identity["username"],
            "password": identity["password"],
            "display_name": identity["display_name"],
            "error": None,
            "cookies": [],
            "debug": [],
        }

        try:
            # Step 1: Navigate to signup page
            logger.info("Navigating to TikTok signup...")
            await self.page.goto(self.SIGNUP_URL, wait_until="networkidle", timeout=30000)
            await self._delay(self.LONG_DELAY)
            await self._screenshot("01_landing")
            result["debug"].append(f"Landed on: {self.page.url}")

            # Step 2: Click "Use phone or email"
            logger.info("Clicking 'Use phone or email'...")
            clicked = False
            # TikTok uses data-e2e="channel-item" for signup method buttons
            els = await self.page.query_selector_all('[data-e2e="channel-item"]')
            for el in els:
                text = (await el.inner_text()).strip().lower()
                if "phone or email" in text:
                    await el.click()
                    clicked = True
                    break

            if not clicked:
                # Fallback: text-based search
                el = self.page.get_by_text("Use phone or email", exact=True)
                if await el.count() > 0:
                    await el.first.click()
                    clicked = True

            if not clicked:
                result["error"] = "Could not find 'Use phone or email' button"
                return result

            await self._delay(self.LONG_DELAY)
            await self._screenshot("02_phone_or_email")
            result["debug"].append("Clicked 'Use phone or email'")

            # Step 3: Switch to email mode (default is phone)
            logger.info("Switching to email signup...")
            switched = False
            for sel in [
                'a:has-text("Sign up with email")',
                'div:has-text("Sign up with email")',
                '[data-e2e*="email"]',
                'text="Sign up with email"',
            ]:
                try:
                    el = self.page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        switched = True
                        break
                except:
                    continue

            if not switched:
                # Scan all links
                links = await self.page.query_selector_all("a, div[role='link'], span")
                for link in links:
                    try:
                        t = (await link.inner_text()).strip().lower()
                        if "email" in t and "sign up" in t:
                            await link.click()
                            switched = True
                            break
                    except:
                        continue

            if not switched:
                result["error"] = "Could not switch to email signup"
                result["debug"].append("Failed to find email toggle")
                body = await self.page.inner_text("body")
                result["debug"].append(f"Body: {body[:300]}")
                return result

            await self._delay(self.MED_DELAY)
            await self._screenshot("03_email_mode")
            result["debug"].append("Switched to email mode")

            # Step 4: Fill birthday dropdowns
            logger.info(f"Setting birthday: {identity['birth_month']}/{identity['birth_day']}/{identity['birth_year']}...")
            birthday_set = await self._select_birthday(
                identity["birth_month"],
                identity["birth_day"],
                identity["birth_year"],
            )
            await self._screenshot("04_birthday")
            if birthday_set:
                result["debug"].append("Birthday set")
            else:
                result["debug"].append("Birthday dropdown issue (may still work)")
                logger.warning("Birthday selection uncertain - continuing anyway")

            await self._delay(self.MED_DELAY)

            # Step 5: Enter email
            logger.info(f"Entering email: {email}")
            email_input = None
            inputs = await self.page.query_selector_all('input[type="text"], input[type="email"]')
            for inp in inputs:
                try:
                    ph = (await inp.get_attribute("placeholder") or "").lower()
                    if "email" in ph:
                        email_input = inp
                        break
                except:
                    continue

            if email_input:
                await email_input.click()
                await self._delay(self.SHORT_DELAY)
                for char in email:
                    await self.page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.03, 0.1))
                result["debug"].append(f"Email entered: {email}")
            else:
                result["error"] = "Could not find email input"
                return result

            # Dismiss TikTok's email-domain autocomplete dropdown before moving on.
            # If left open, it intercepts the click on the password field.
            await self.page.keyboard.press("Escape")
            await self._delay(self.SHORT_DELAY)
            await self.page.keyboard.press("Tab")
            await self._delay(self.SHORT_DELAY)

            # Step 6: Enter password
            logger.info("Entering password...")
            pw_input = None
            pw_inputs = await self.page.query_selector_all('input[type="password"]')
            if pw_inputs:
                pw_input = pw_inputs[0]

            if pw_input:
                await pw_input.click()
                await self._delay(self.SHORT_DELAY)
                for char in identity["password"]:
                    await self.page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.03, 0.1))
                result["debug"].append("Password entered")
            else:
                result["error"] = "Could not find password input"
                return result

            await self._delay(self.MED_DELAY)
            await self._screenshot("05_credentials")

            # Step 7: Click "Send code"
            logger.info("Clicking 'Send code'...")

            # Blur active fields and dismiss any autocomplete/popover first.
            await self.page.keyboard.press("Escape")
            await self._delay(self.SHORT_DELAY)

            send_clicked = False
            send_candidates = []
            for sel in [
                '[data-e2e="send-code-button"]',
                'button:has-text("Send code")',
                'div[role="button"]:has-text("Send code")',
                'span:has-text("Send code")',
                'a:has-text("Send code")',
            ]:
                try:
                    loc = self.page.locator(sel)
                    count = await loc.count()
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible(timeout=1000):
                            send_candidates.append((sel, el))
                except Exception:
                    continue

            # Most reliable fallback: click geometrically to the right of the code input.
            # TikTok's Send code control is sometimes a styled div/span that Playwright's
            # text locators don't expose cleanly in headless mode, but its layout is stable.
            try:
                code_inputs = await self.page.query_selector_all('input[type="text"]')
                for code_inp in code_inputs:
                    ph = (await code_inp.get_attribute("placeholder") or "").lower()
                    if "code" in ph or "digit" in ph:
                        box = await code_inp.bounding_box()
                        if box:
                            x = box["x"] + box["width"] + 55
                            y = box["y"] + box["height"] / 2
                            logger.info(f"Trying Send code geometry click at ({x:.0f}, {y:.0f})")
                            await self.page.mouse.click(x, y)
                            send_clicked = True
                            break
            except Exception as e:
                logger.warning(f"Geometry send click failed: {e}")

            # Fallback: scan visible elements for exact Send code text.
            if not send_candidates and not send_clicked:
                for el in await self.page.query_selector_all("button, div[role='button'], a, span"):
                    try:
                        t = (await el.inner_text()).strip().lower()
                        if t == "send code" or ("send" in t and "code" in t and len(t) < 30):
                            send_candidates.append(("scan", el))
                    except Exception:
                        continue

            if not send_clicked:
                for source, el in send_candidates:
                    try:
                        logger.info(f"Trying Send code candidate: {source}")
                        # Normal click first.
                        await el.click(timeout=5000)
                        send_clicked = True
                        break
                    except Exception as e:
                        logger.warning(f"Normal send click failed ({source}): {e}")
                        try:
                            # JS/force click fallback for React wrappers.
                            handle = await el.element_handle() if hasattr(el, "element_handle") else el
                            if handle:
                                await self.page.evaluate("el => el.click()", handle)
                                send_clicked = True
                                break
                        except Exception as e2:
                            logger.warning(f"JS send click failed ({source}): {e2}")
                            continue

            if not send_clicked:
                result["error"] = "Could not find/click 'Send code' button"
                body = await self.page.inner_text("body")
                result["debug"].append(f"Body: {body[:500]}")
                return result

            await self._delay(self.LONG_DELAY)
            await self._screenshot("06_code_sent")

            # Verify the click produced some state change: countdown, sent text, captcha, or visible error.
            body_after_send = await self.page.inner_text("body")
            body_lower = body_after_send.lower()
            result["debug"].append(f"After send body: {body_after_send[:400]}")
            if any(x in body_lower for x in ["too many attempts", "maximum number", "invalid email", "unsupported", "try again"]):
                result["error"] = f"TikTok rejected Send code: {body_after_send[:250]}"
                return result

            result["debug"].append("Send code clicked")
            logger.info("Verification code requested, waiting for delivery...")

            # Step 8: Return — caller will wait for code and call enter_code()
            result["code_sent"] = True
            return result

        except Exception as e:
            result["error"] = f"Signup exception: {e}"
            logger.error(f"Signup error: {e}", exc_info=True)
            await self._screenshot("error")
            return result

    async def enter_code_and_finish(self, code: str, identity: dict) -> dict:
        """Enter the verification code and complete signup."""
        result = {
            "success": False,
            "error": None,
            "cookies": [],
            "debug": [],
        }

        try:
            # Step 9: Enter verification code
            logger.info(f"Entering verification code: {code}")
            code_input = None
            # Code input has placeholder "Enter 6-digit code"
            inputs = await self.page.query_selector_all('input[type="text"]')
            for inp in inputs:
                try:
                    ph = (await inp.get_attribute("placeholder") or "").lower()
                    if "code" in ph or "digit" in ph:
                        code_input = inp
                        break
                except:
                    continue

            if not code_input:
                # Try by position (usually the 2nd or 3rd text input)
                if len(inputs) >= 2:
                    code_input = inputs[-1]  # Last text input

            if code_input:
                await code_input.click()
                await self._delay(self.SHORT_DELAY)
                for char in code:
                    await self.page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                result["debug"].append(f"Code entered: {code}")
            else:
                result["error"] = "Could not find code input field"
                await self._screenshot("no_code_input")
                return result

            await self._delay(self.MED_DELAY)

            # Step 10: Check TOS checkbox if present
            logger.info("Checking TOS checkbox...")
            checkboxes = await self.page.query_selector_all('input[type="checkbox"]')
            for cb in checkboxes:
                try:
                    is_checked = await cb.is_checked()
                    if not is_checked:
                        await cb.click()
                        await self._delay(self.SHORT_DELAY)
                        result["debug"].append("TOS checkbox checked")
                except:
                    pass

            await self._screenshot("07_code_and_tos")

            # Step 11: Click Next/Submit
            logger.info("Clicking Next...")
            next_clicked = False
            for sel in [
                '[data-e2e="next-button"]',
                'button:has-text("Next")',
                'button[type="submit"]',
                'div[role="button"]:has-text("Next")',
                'button:has-text("Sign up")',
                'button:has-text("Start")',
            ]:
                try:
                    el = self.page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        # Check if button is disabled
                        is_disabled = await el.get_attribute("disabled")
                        class_name = await el.get_attribute("class") or ""
                        if is_disabled or "disabled" in class_name.lower():
                            logger.warning(f"Next button disabled: {sel}")
                            continue
                        await el.click()
                        next_clicked = True
                        break
                except:
                    continue

            if not next_clicked:
                btns = await self.page.query_selector_all("button, div[role='button']")
                for btn in btns:
                    try:
                        t = (await btn.inner_text()).strip().lower()
                        if t in ["next", "sign up", "start", "continue", "submit"]:
                            await btn.click()
                            next_clicked = True
                            break
                    except:
                        continue

            await self._delay(self.LONG_DELAY)
            await self._screenshot("08_after_next")
            result["debug"].append("Next clicked")

            # Step 12: Handle post-signup flow
            # TikTok may show: profile setup, interests, skip options, captcha
            await self._handle_post_signup(identity, result)

            # Step 13: Check if we're logged in
            current_url = self.page.url
            result["debug"].append(f"Final URL: {current_url}")

            # Extract cookies
            cookies = await self.context.cookies()
            session_cookies = [
                {"name": c["name"], "value": c["value"], "domain": c["domain"]}
                for c in cookies
                if "tiktok" in c.get("domain", "")
            ]
            result["cookies"] = session_cookies
            result["debug"].append(f"Got {len(session_cookies)} TikTok cookies")

            # Check success indicators
            body_text = await self.page.inner_text("body")
            if "tiktok.com/foryou" in current_url or "tiktok.com/trending" in current_url or "tiktok.com/home" in current_url:
                result["success"] = True
                result["debug"].append("SUCCESS: Redirected to feed")
            elif "sessionid" in str(cookies).lower() or any(c["name"] == "sessionid" for c in session_cookies):
                result["success"] = True
                result["debug"].append("SUCCESS: Session cookie present")
            elif "log in" not in body_text.lower()[:200] and "signup" not in current_url.lower():
                # If we're not on a login/signup page, likely success
                result["success"] = True
                result["debug"].append("SUCCESS: Not on signup page")
            else:
                result["error"] = "Could not confirm signup success"
                result["debug"].append(f"Unclear state. Body: {body_text[:300]}")

        except Exception as e:
            result["error"] = f"Finish exception: {e}"
            logger.error(f"Finish error: {e}", exc_info=True)
            await self._screenshot("finish_error")

        return result

    async def _handle_post_signup(self, identity: dict, result: dict):
        """Handle post-signup steps: skip profile setup, interests, etc."""
        logger.info("Handling post-signup flow...")

        for attempt in range(5):
            await self._delay(self.LONG_DELAY)
            current_url = self.page.url
            body_text = await self.page.inner_text("body")
            body_lower = body_text.lower()

            logger.info(f"Post-signup step {attempt+1}: URL={current_url}")

            # If we're on the feed, we're done
            if any(x in current_url for x in ["/foryou", "/trending", "/home", "/following"]):
                logger.info("Reached feed — post-signup complete")
                return

            # Skip buttons
            for skip_text in ["Skip", "Not now", "Maybe later", "I'll do it later", "Next", "Get started", "Done"]:
                try:
                    el = self.page.get_by_role("button", name=skip_text)
                    if await el.count() > 0 and await el.first.is_visible(timeout=1000):
                        await el.first.click()
                        await self._delay(self.MED_DELAY)
                        result["debug"].append(f"Clicked '{skip_text}' in post-signup")
                        break
                except:
                    continue

            # Try clicking any visible skip/next button
            btns = await self.page.query_selector_all("button, div[role='button'], a")
            for btn in btns:
                try:
                    t = (await btn.inner_text()).strip().lower()
                    if any(s in t for s in ["skip", "not now", "later", "done", "get started"]):
                        await btn.click()
                        await self._delay(self.MED_DELAY)
                        break
                except:
                    continue

            await self._screenshot(f"post_signup_{attempt+1}")

            # If URL changed, something happened
            if current_url != self.page.url:
                continue
            else:
                # No change — might be stuck
                break

    async def close(self):
        """Clean up browser resources."""
        try:
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
        except:
            pass


# ── Orchestrator ──────────────────────────────────────────────────────────────

class TikTokAccountCreator:
    """Orchestrates the full account creation pipeline."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.email_manager = EmailManager()
        self.bot = TikTokSignupBot(headless=headless)

    def create_account(self) -> dict:
        """Create a single TikTok account. Returns result dict."""
        return asyncio.run(self._create_account_async())

    async def _create_account_async(self) -> dict:
        identity = generate_identity()
        logger.info(f"Identity: {identity['display_name']} (@{identity['username']})")

        # Create email inbox
        logger.info("Creating email inbox...")
        inbox = self.email_manager.create_inbox()
        email = inbox["email"]
        logger.info(f"Inbox: {email}")

        # Launch browser
        await self.bot.launch()

        try:
            # Run signup flow up to code sending
            result = await self.bot.signup(identity, email)

            if result.get("error") or not result.get("code_sent"):
                logger.error(f"Signup failed: {result.get('error')}")
                return result

            # Wait for verification code
            logger.info("Waiting for TikTok verification code...")
            code = self.email_manager.wait_for_code(inbox["inbox_id"], timeout=120)

            if not code:
                result["error"] = "Did not receive verification code"
                result["success"] = False
                logger.error("No verification code received")
                return result

            logger.info(f"Got code: {code}")

            # Enter code and finish
            finish_result = await self.bot.enter_code_and_finish(code, identity)

            # Merge results
            result["success"] = finish_result.get("success", False)
            result["cookies"] = finish_result.get("cookies", [])
            result["error"] = finish_result.get("error")
            result["debug"].extend(finish_result.get("debug", []))

            return result

        finally:
            await self.bot.close()

    def create_batch(self, count: int) -> list:
        """Create multiple accounts sequentially."""
        results = []
        for i in range(count):
            logger.info(f"\n{'='*60}")
            logger.info(f"Creating account {i+1}/{count}")
            logger.info(f"{'='*60}")
            result = self.create_account()
            results.append(result)

            # Save results after each account
            self._save_results(results)

            if i < count - 1:
                delay = random.randint(30, 90)
                logger.info(f"Waiting {delay}s before next account...")
                time.sleep(delay)

        return results

    @staticmethod
    def _save_results(results: list, filename: str = "tiktok_accounts.json"):
        """Save account credentials to file."""
        with open(filename, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Saved {len(results)} accounts to {filename}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Account Creator")
    parser.add_argument("--count", type=int, default=1, help="Number of accounts")
    parser.add_argument("--no-headless", action="store_true", help="Show browser")
    args = parser.parse_args()

    creator = TikTokAccountCreator(headless=not args.no_headless)
    results = creator.create_batch(args.count)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for i, r in enumerate(results):
        status = "✅" if r.get("success") else "❌"
        print(f"  [{i+1}] {status} {r.get('email', '?')} | @{r.get('username', '?')}")
        if r.get("error"):
            print(f"      Error: {r['error']}")

    successful = [r for r in results if r.get("success")]
    print(f"\n  Success: {len(successful)}/{len(results)}")


if __name__ == "__main__":
    main()
