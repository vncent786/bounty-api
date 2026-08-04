"""
Twitter Account Creation Pipeline

Automated account creation: AgentMail (email) → Playwright (signup) → 
2Captcha (Arkose) → Email verification → Working account credentials.

Requirements:
  pip install agentmail 2captcha-python playwright httpx
  playwright install chromium

Environment:
  AGENTMAIL_API_KEY - from agentmail.to
  TWOCAPTCHA_API_KEY - from 2captcha.com
  PROXY_URL - optional residential proxy (http://user:pass@host:port)

Usage:
  python -m account_factory.twitter_creator --count 1
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

logger = logging.getLogger("account_factory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ── Name generation ──────────────────────────────────────────────────────────

FIRST_NAMES = [
    "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
    "Lucas", "Isabella", "Mason", "Mia", "Logan", "Charlotte", "Alex",
    "Amelia", "Jack", "Harper", "Ryan", "Ella", "Connor", "Luna", "Tyler",
    "Grace", "Brandon", "Chloe", "Nathan", "Lily", "Dylan", "Zoe",
    "Kevin", "Nina", "Marcus", "Ruby", "Oscar", "Iris", "Felix", "Mila",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor",
    "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White",
    "Harris", "Clark", "Lewis", "Walker", "Hall", "Young", "King",
]

def generate_name() -> dict:
    """Generate a realistic-looking full name and username."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    # Username patterns: firstname_lastname_XX, firstlastXX, flast_X
    patterns = [
        f"{first.lower()}_{last.lower()}{random.randint(100, 9999)}",
        f"{first.lower()}{last.lower()}{random.randint(10, 999)}",
        f"{first[0].lower()}{last.lower()}{random.randint(100, 9999)}",
        f"{first.lower()}{random.randint(1000, 99999)}",
        f"real_{first.lower()}_{last.lower()}{random.randint(1, 99)}",
    ]
    username = random.choice(patterns)
    return {
        "first_name": first,
        "last_name": last,
        "username": username,
        "display_name": f"{first} {last}",
        "password": _generate_password(),
    }


def _generate_password(length: int = 16) -> str:
    """Generate a strong password meeting Twitter requirements."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = ''.join(random.choices(chars, k=length))
    # Ensure at least one of each required type
    pwd_list = list(pwd)
    pwd_list[0] = random.choice(string.ascii_uppercase)
    pwd_list[1] = random.choice(string.ascii_lowercase)
    pwd_list[2] = random.choice(string.digits)
    pwd_list[3] = random.choice("!@#$%^&*")
    random.shuffle(pwd_list)
    return ''.join(pwd_list)


# ── Email layer (AgentMail) ──────────────────────────────────────────────────

class EmailManager:
    """Create and read AgentMail inboxes for email verification."""

    def __init__(self):
        from agentmail import AgentMail
        api_key = os.environ.get("AGENTMAIL_API_KEY")
        if not api_key:
            raise ValueError("AGENTMAIL_API_KEY not set. Get one at https://agentmail.to")
        self.client = AgentMail(api_key=api_key)

    def create_inbox(self, username: Optional[str] = None) -> dict:
        """Create a new email inbox and return inbox details."""
        # Clean up any old inboxes first (free tier limit)
        try:
            existing = self.client.inboxes.list()
            for ib in existing.inboxes:
                self.client.inboxes.delete(inbox_id=ib.inbox_id)
        except Exception:
            pass

        inbox = self.client.inboxes.create()
        return {
            "inbox_id": inbox.inbox_id,
            "email": inbox.email,
        }

    def wait_for_verification_email(self, inbox_id: str, timeout: int = 120) -> Optional[dict]:
        """
        Poll for a verification email from Twitter/X.
        Returns the message dict or None if timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                messages = self.client.inboxes.messages.list(
                    inbox_id=inbox_id,
                    limit=5,
                )
                for msg in (messages.messages if hasattr(messages, 'messages') else messages.data if hasattr(messages, 'data') else []):
                    sender = ""
                    subject = ""
                    if hasattr(msg, 'from_') and msg.from_:
                        sender = str(msg.from_)
                    if hasattr(msg, 'subject'):
                        subject = str(msg.subject or "")
                    sender_lower = sender.lower()
                    subject_lower = subject.lower()
                    if any(k in sender_lower or k in subject_lower for k in
                           ["twitter", "x.com", "verification", "confirm", "verify"]):
                        # Get full message body
                        full_msg = self.client.inboxes.messages.get(
                            inbox_id=inbox_id,
                            message_id=msg.message_id if hasattr(msg, 'message_id') else msg.id,
                        )
                        body = ""
                        if hasattr(full_msg, 'text'):
                            body = str(full_msg.text or "")
                        elif hasattr(full_msg, 'html'):
                            body = str(full_msg.html or "")
                        # Extract verification link
                        link = self._extract_verification_link(body)
                        return {
                            "message_id": msg.message_id if hasattr(msg, 'message_id') else None,
                            "from": sender,
                            "subject": subject,
                            "body": body[:2000],
                            "verification_link": link,
                        }
            except Exception as e:
                logger.warning(f"Error polling inbox: {e}")
            time.sleep(5)

        logger.error(f"Timeout waiting for verification email (inbox: {inbox_id})")
        return None

    @staticmethod
    def _extract_verification_link(body: str) -> Optional[str]:
        """Extract the verification confirmation link from email body."""
        # Twitter verification links typically contain "confirm" or "click"
        patterns = [
            r'https?://[^\s"<>]*(?:confirm|verify|click|activate)[^\s"<>]*',
            r'https?://t\.co/[^\s"<>]+',
            r'https?://x\.com/[^\s"<>]*(?:i/flow|confirm|verify)[^\s"<>]*',
            r'https?://twitter\.com/[^\s"<>]*(?:i/flow|confirm|verify)[^\s"<>]*',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(0).rstrip('.')
        return None

    def delete_inbox(self, inbox_id: str):
        """Clean up inbox after use."""
        try:
            self.client.inboxes.delete(inbox_id=inbox_id)
        except Exception:
            pass


# ── Captcha layer (2Captcha) ─────────────────────────────────────────────────

class CaptchaSolver:
    """Solve Arkose/FunCaptcha challenges via 2Captcha."""

    def __init__(self):
        api_key = os.environ.get("TWOCAPTCHA_API_KEY")
        if not api_key:
            raise ValueError("TWOCAPTCHA_API_KEY not set. Get one at https://2captcha.com")
        from twocaptcha import TwoCaptcha
        self.solver = TwoCaptcha(api_key)
        logger.info(f"2Captcha balance: ${self._balance()}")

    def _balance(self) -> float:
        try:
            return float(self.solver.balance())
        except Exception:
            return 0.0

    def solve_funcaptcha(self, public_key: str, page_url: str, surl: Optional[str] = None,
                         data_blob: Optional[str] = None,
                         proxy: Optional[dict] = None) -> Optional[str]:
        """
        Solve a FunCaptcha/Arkose Labs challenge.
        
        Args:
            public_key: The Arkose public key (data-pkey)
            page_url: URL of the page where captcha appears
            surl: Optional surl parameter
            data_blob: Optional data[blob] parameter
            proxy: Optional proxy dict {'type': 'HTTP', 'uri': 'user:pass@host:port'}
            
        Returns:
            Token string to inject into the page, or None on failure.
        """
        kwargs = {}
        if surl:
            kwargs['surl'] = surl
        if data_blob:
            kwargs['data[blob]'] = data_blob
        if proxy:
            kwargs['proxy'] = proxy

        try:
            logger.info(f"Solving FunCaptcha (key={public_key[:8]}..., url={page_url[:50]}...)")
            result = self.solver.funcaptcha(
                sitekey=public_key,
                url=page_url,
                **kwargs,
            )
            token = result.get("code") if isinstance(result, dict) else result
            logger.info(f"FunCaptcha solved: {str(token)[:30]}...")
            return token
        except Exception as e:
            logger.error(f"FunCaptcha solve failed: {e}")
            return None


# ── Playwright signup automation ─────────────────────────────────────────────

class TwitterSignupBot:
    """Automate the Twitter/X account signup flow using Playwright."""

    SIGNUP_URL = "https://x.com/i/flow/signup"

    def __init__(self, proxy_url: Optional[str] = None, headless: bool = True):
        self.proxy_url = proxy_url or os.environ.get("PROXY_URL")
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None

    async def _launch(self):
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()

        launch_kwargs = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }
        if self.proxy_url:
            launch_kwargs["proxy"] = {"server": self.proxy_url}

        self.browser = await pw.chromium.launch(**launch_kwargs)

        # Realistic viewport and user agent
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Remove webdriver detection
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        self.page = await self.context.new_page()
        return self.page

    async def _human_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        """Simulate human typing/clicking delay."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _human_type(self, selector: str, text: str):
        """Type text character by character with human-like delays."""
        await self.page.wait_for_selector(selector, state="visible", timeout=15000)
        await self.page.click(selector)
        for char in text:
            await self.page.type(selector, char, delay=random.randint(50, 150))
        await self._human_delay()

    async def _find_arkose_key(self) -> Optional[dict]:
        """
        Detect if an Arkose/FunCaptcha is present and extract its public key.
        Returns dict with public_key, surl, data_blob, or None if no captcha.
        """
        # Check for Arkose iframe
        arkose_frame = None
        try:
            arkose_frame = await self.page.wait_for_selector(
                "iframe[title*='arkose' i], iframe[src*='arkoselabs' i], "
                "iframe[src*='funcaptcha' i], div[data-pkey]",
                timeout=8000,
            )
        except Exception:
            pass

        if not arkose_frame:
            # Check for the Arkose enforcement div
            try:
                arkose_data = await self.page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-pkey]') ||
                                   document.querySelector('#arkoseFrame') ||
                                   document.querySelector('iframe[src*="arkoselabs"]');
                        if (el) {
                            return {
                                pkey: el.getAttribute('data-pkey') || null,
                                src: el.getAttribute('src') || null,
                            };
                        }
                        // Also check script tags for Arkose public key
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const text = s.textContent || '';
                            const match = text.match(/['"]?publicKey['"]?\s*[:=]\s*['"]([A-F0-9-]+)['"]/i);
                            if (match) return { pkey: match[1], src: null };
                        }
                        return null;
                    }
                """)
                if arkose_data and arkose_data.get("pkey"):
                    return {
                        "public_key": arkose_data["pkey"],
                        "url": self.page.url,
                        "surl": None,
                        "data_blob": None,
                    }
            except Exception:
                pass
            return None

        # Extract from the element
        pkey = await arkose_frame.get_attribute("data-pkey")
        src = await arkose_frame.get_attribute("src")
        if not pkey and src:
            # Extract from URL params
            match = re.search(r'[?&]pkey=([A-F0-9-]+)', src)
            if match:
                pkey = match.group(1)

        if pkey:
            return {
                "public_key": pkey,
                "url": self.page.url,
                "surl": None,
                "data_blob": None,
            }
        return None

    async def _inject_arkose_token(self, token: str):
        """Inject the solved Arkose token back into the page."""
        await self.page.evaluate(f"""
            (token) => {{
                // Set the token in the Arkose callback
                if (window.ArkoseEnforcement) {{
                    window.ArkoseEnforcement.onCompleted({{ token: token }});
                }}
                // Also try setting via the hidden input
                const input = document.querySelector('input[name="arkoseToken"]') ||
                              document.querySelector('#fc-token');
                if (input) input.value = token;
                // Dispatch event
                window.dispatchEvent(new CustomEvent('arkoseTokenSet', {{ detail: token }}));
            }}
        """, token)

        # Fallback: try clicking continue after token injection
        await self._human_delay(1, 2)
        try:
            await self.page.keyboard.press("Enter")
        except Exception:
            pass

    async def create_account(self, email: str, name_info: dict,
                              email_manager: EmailManager,
                              captcha_solver: CaptchaSolver) -> dict:
        """
        Full signup flow:
        1. Navigate to signup
        2. Enter email
        3. Click Next
        4. Solve Arkose captcha if present
        5. Enter name + password
        6. Verify email via AgentMail
        7. Return account credentials + cookies

        Returns dict with success status, credentials, and debug info.
        """
        result = {
            "success": False,
            "email": email,
            "username": name_info["username"],
            "password": name_info["password"],
            "display_name": name_info["display_name"],
            "phone_required": False,
            "error": None,
            "cookies": [],
            "debug": [],
        }

        try:
            page = await self._launch()
            await self._human_delay(1, 2)

            # Step 1: Navigate to signup page
            logger.info("Navigating to Twitter signup...")
            await page.goto(self.SIGNUP_URL, wait_until="networkidle", timeout=30000)
            await self._human_delay(2, 4)
            result["debug"].append(f"Landed on: {page.url}")

            # Step 2: Enter email
            # Twitter's signup flow uses dynamic input selectors
            # We try multiple strategies to find the email input
            logger.info(f"Entering email: {email}")

            # Wait for the email input field to appear
            email_input = None
            email_selectors = [
                'input[type="text"]',
                'input[autocomplete="username"]',
                'input[name="text"]',
                'input[data-testid="ocfEnterTextTextInput"]',
            ]
            for selector in email_selectors:
                try:
                    email_input = await page.wait_for_selector(selector, state="visible", timeout=5000)
                    if email_input:
                        break
                except Exception:
                    continue

            if not email_input:
                result["error"] = "Could not find email input field"
                result["debug"].append(f"Page content: {await page.content()[:500]}")
                return result

            # Type email with human-like delay
            await email_input.click()
            await self._human_delay(0.3, 0.8)
            for char in email:
                await page.keyboard.type(char, delay=random.randint(50, 120))

            await self._human_delay(0.5, 1)

            # Step 3: Click Next
            logger.info("Clicking Next...")
            next_selectors = [
                '[data-testid="ocfEnterTextNextButton"]',
                'button[type="button"]:has-text("Next")',
                'div[role="button"]:has-text("Next")',
            ]
            clicked = False
            for selector in next_selectors:
                try:
                    btn = await page.wait_for_selector(selector, state="visible", timeout=3000)
                    if btn:
                        await btn.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Try Enter key
                await page.keyboard.press("Enter")

            await self._human_delay(2, 4)

            # Step 4: Check for Arkose/FunCaptcha
            logger.info("Checking for Arkose captcha...")
            await page.screenshot(path="/tmp/twitter_01_after_email.png")
            arkose = await self._find_arkose_key()

            if arkose:
                logger.info(f"Arkose captcha detected (key: {arkose['public_key'][:12]}...)")
                result["debug"].append("Arkose captcha found")

                # Solve via 2Captcha
                proxy_dict = None
                if self.proxy_url:
                    proxy_dict = {"type": "HTTP", "uri": self.proxy_url}

                token = captcha_solver.solve_funcaptcha(
                    public_key=arkose["public_key"],
                    page_url=arkose["url"],
                    surl=arkose.get("surl"),
                    data_blob=arkose.get("data_blob"),
                    proxy=proxy_dict,
                )

                if not token:
                    result["error"] = "Failed to solve Arkose captcha"
                    return result

                # Inject token
                await self._inject_arkose_token(token)
                await self._human_delay(2, 4)

                # After captcha, may need to click Next again
                for selector in next_selectors:
                    try:
                        btn = await page.wait_for_selector(selector, state="visible", timeout=3000)
                        if btn:
                            await btn.click()
                            break
                    except Exception:
                        continue
                await self._human_delay(2, 4)
            else:
                result["debug"].append("No Arkose captcha on this step")

            # Step 5: Check what comes next — could be:
            # a) Phone/email verification prompt (we skip phone)
            # b) Name and password entry
            # c) Date of birth entry

            current_text = await self._get_page_text(page)
            await page.screenshot(path="/tmp/twitter_02_after_captcha.png")

            # Check if phone verification is REQUIRED (not just offered as option)
            # Only trigger on explicit phone wall, not when phone is just an option
            phone_wall_phrases = [
                "verify your phone number",
                "add your phone number",
                "enter your phone number",
                "we need to verify your phone",
                "confirm your phone",
            ]
            phone_required = any(phrase in current_text.lower() for phrase in phone_wall_phrases)

            if phone_required:
                result["phone_required"] = True
                result["error"] = "Phone verification required (IP reputation trigger)"
                result["debug"].append(f"Phone wall hit. Page text snippet: {current_text[:300]}")
                logger.warning("Phone verification required — cannot proceed without SMS service")
                return result

            # Check if this is the signup options page (phone shown as option, not requirement)
            # Look for "Continue" button next to email
            if "continue" in current_text.lower():
                logger.info("On signup options page, clicking Continue...")
                try:
                    continue_btn = await page.wait_for_selector(
                        'div[role="button"]:has-text("Continue"), '
                        '[data-testid="ocfEnterTextNextButton"]',
                        state="visible", timeout=5000,
                    )
                    if continue_btn:
                        await continue_btn.click()
                        await self._human_delay(2, 4)
                except Exception:
                    # Try pressing Enter
                    await page.keyboard.press("Enter")
                    await self._human_delay(2, 4)

            # Look for name input
            logger.info("Looking for name input...")
            name_selectors = [
                'input[name="name"]',
                'input[data-testid="ocfCustomNameTextInput"]',
                'input[autocomplete="name"]',
            ]
            name_input = None
            for selector in name_selectors:
                try:
                    name_input = await page.wait_for_selector(selector, state="visible", timeout=5000)
                    if name_input:
                        break
                except Exception:
                    continue

            if name_input:
                logger.info(f"Entering name: {name_info['display_name']}")
                await name_input.click()
                await self._human_delay(0.3, 0.5)
                for char in name_info["display_name"]:
                    await page.keyboard.type(char, delay=random.randint(40, 100))

                await self._human_delay(0.5, 1)

                # Click Next
                for selector in next_selectors:
                    try:
                        btn = await page.wait_for_selector(selector, state="visible", timeout=3000)
                        if btn:
                            await btn.click()
                            break
                    except Exception:
                        continue
                await self._human_delay(2, 4)

            # Step 6: Password entry
            logger.info("Entering password...")
            pwd_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[autocomplete="new-password"]',
            ]
            pwd_input = None
            for selector in pwd_selectors:
                try:
                    pwd_input = await page.wait_for_selector(selector, state="visible", timeout=5000)
                    if pwd_input:
                        break
                except Exception:
                    continue

            if pwd_input:
                await pwd_input.click()
                await self._human_delay(0.3, 0.5)
                for char in name_info["password"]:
                    await page.keyboard.type(char, delay=random.randint(40, 100))

                await self._human_delay(0.5, 1)

                # Click Next / Sign up
                signup_selectors = [
                    '[data-testid="ocfEnterTextNextButton"]',
                    'div[role="button"]:has-text("Sign up")',
                    'div[role="button"]:has-text("Next")',
                ]
                for selector in signup_selectors:
                    try:
                        btn = await page.wait_for_selector(selector, state="visible", timeout=3000)
                        if btn:
                            await btn.click()
                            break
                    except Exception:
                        continue
                await self._human_delay(3, 5)

            # Step 7: Username selection
            # Twitter may suggest a username or ask us to pick one
            username_selectors = [
                'input[name="username"]',
                'input[data-testid="ocfCustomUsernameTextInput"]',
                'input[autocomplete="username"]',
            ]
            for selector in username_selectors:
                try:
                    uname_input = await page.wait_for_selector(selector, state="visible", timeout=5000)
                    if uname_input:
                        logger.info(f"Entering username: {name_info['username']}")
                        await uname_input.click()
                        await self._human_delay(0.3, 0.5)
                        for char in name_info["username"]:
                            await page.keyboard.type(char, delay=random.randint(40, 100))

                        await self._human_delay(0.5, 1)
                        for sel in next_selectors:
                            try:
                                btn = await page.wait_for_selector(sel, state="visible", timeout=3000)
                                if btn:
                                    await btn.click()
                                    break
                            except Exception:
                                continue
                        break
                except Exception:
                    continue
            await self._human_delay(2, 4)

            # Step 8: Email verification prompt
            # Twitter shows "We sent you a code" or a verification email
            logger.info("Waiting for email verification...")
            verification = email_manager.wait_for_verification_email(
                inbox_id=email_manager._current_inbox_id,
                timeout=90,
            )

            if verification and verification.get("verification_link"):
                # Visit the verification link
                logger.info("Visiting verification link...")
                await page.goto(verification["verification_link"], wait_until="networkidle", timeout=20000)
                await self._human_delay(2, 3)
            elif verification:
                # May be a code-based verification
                code = self._extract_verification_code(verification.get("body", ""))
                if code:
                    logger.info(f"Entering verification code: {code}")
                    code_selectors = [
                        'input[name="verif_code"]',
                        'input[data-testid="ocfEnterTextTextInput"]',
                        'input[type="text"]',
                    ]
                    for selector in code_selectors:
                        try:
                            code_input = await page.wait_for_selector(selector, state="visible", timeout=3000)
                            if code_input:
                                await code_input.click()
                                await page.keyboard.type(code)
                                await page.keyboard.press("Enter")
                                break
                        except Exception:
                            continue
                await self._human_delay(3, 5)
            else:
                result["error"] = "No verification email received"
                result["debug"].append("Email verification timed out")
                # Account may still be created — check if we're logged in
                pass

            # Step 9: Skip optional setup steps (profile photo, interests, etc.)
            for _ in range(5):
                try:
                    skip = await page.wait_for_selector(
                        '[data-testid*="skip"], div[role="button"]:has-text("Skip")',
                        state="visible", timeout=3000,
                    )
                    if skip:
                        await skip.click()
                        await self._human_delay(1, 2)
                except Exception:
                    break

            # Step 10: Check if we're logged in
            await self._human_delay(2, 3)
            cookies = await self.context.cookies()
            auth_cookies = [c for c in cookies if c["name"] in
                           ("auth_token", "ct0", "twid")]

            if auth_cookies:
                result["success"] = True
                result["cookies"] = cookies
                result["auth_token"] = next((c["value"] for c in auth_cookies if c["name"] == "auth_token"), None)
                result["ct0"] = next((c["value"] for c in auth_cookies if c["name"] == "ct0"), None)
                logger.info("Account created successfully!")
            else:
                # Take screenshot for debugging
                screenshot_path = f"/tmp/twitter_signup_debug_{int(time.time())}.png"
                try:
                    await page.screenshot(path=screenshot_path)
                    result["debug"].append(f"Screenshot saved: {screenshot_path}")
                except Exception:
                    pass
                result["error"] = result["error"] or "Could not verify login after signup"
                current_text = await self._get_page_text(page)
                result["debug"].append(f"Final page text: {current_text[:500]}")

        except Exception as e:
            result["error"] = f"Exception during signup: {str(e)}"
            logger.exception("Signup failed")
        finally:
            await self.close()

        return result

    async def _get_page_text(self, page) -> str:
        """Extract visible text from the page for flow detection."""
        try:
            return await page.evaluate("() => document.body.innerText.slice(0, 2000)")
        except Exception:
            return ""

    @staticmethod
    def _extract_verification_code(body: str) -> Optional[str]:
        """Extract a numeric verification code from email body."""
        # Twitter verification codes are typically 6-8 digits
        patterns = [
            r'verification code[:\s]+(\d{6,8})',
            r'code is[:\s]+(\d{6,8})',
            r'>\s*(\d{6,8})\s*<',
            r'\b(\d{8})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def close(self):
        """Clean up browser resources."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass


# ── Orchestrator ─────────────────────────────────────────────────────────────

class TwitterAccountCreator:
    """
    Orchestrates the full pipeline:
    1. Create email inbox
    2. Generate account identity
    3. Run Playwright signup
    4. Solve captcha
    5. Verify email
    6. Return credentials
    """

    def __init__(self, proxy_url: Optional[str] = None, headless: bool = True):
        self.email_mgr = EmailManager()
        self.captcha = CaptchaSolver()
        self.proxy_url = proxy_url
        self.headless = headless

    def create_account(self) -> dict:
        """Create one Twitter account. Returns result dict."""
        # Step 1: Create email inbox
        logger.info("Creating email inbox...")
        inbox = self.email_mgr.create_inbox()
        self.email_mgr._current_inbox_id = inbox["inbox_id"]
        email = inbox["email"]
        logger.info(f"Inbox created: {email}")

        # Step 2: Generate identity
        name_info = generate_name()
        logger.info(f"Identity: {name_info['display_name']} (@{name_info['username']})")

        # Step 3: Run signup automation
        bot = TwitterSignupBot(proxy_url=self.proxy_url, headless=self.headless)
        result = asyncio.run(bot.create_account(
            email=email,
            name_info=name_info,
            email_manager=self.email_mgr,
            captcha_solver=self.captcha,
        ))

        # Step 4: Cleanup
        # Keep inbox alive for 24h in case re-verification needed
        # self.email_mgr.delete_inbox(inbox["inbox_id"])

        # Step 5: Save account
        if result["success"]:
            self._save_account(result)
            logger.info(f"✅ Account saved: @{name_info['username']} / {email}")
        else:
            logger.error(f"❌ Account creation failed: {result.get('error')}")
            logger.info(f"   Debug: {result.get('debug')}")

        return result

    def _save_account(self, account: dict):
        """Save account credentials to accounts.json."""
        accounts_file = Path(__file__).parent / "accounts.json"
        accounts = []
        if accounts_file.exists():
            try:
                accounts = json.loads(accounts_file.read_text())
            except Exception:
                accounts = []

        # Save without sensitive debug info
        entry = {
            "email": account["email"],
            "username": account["username"],
            "password": account["password"],
            "display_name": account["display_name"],
            "auth_token": account.get("auth_token"),
            "ct0": account.get("ct0"),
            "cookies": account.get("cookies", []),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        accounts.append(entry)
        accounts_file.write_text(json.dumps(accounts, indent=2))

    def create_batch(self, count: int, delay_min: int = 60, delay_max: int = 180):
        """Create multiple accounts with random delays between them."""
        results = []
        for i in range(count):
            logger.info(f"\n{'='*50}")
            logger.info(f"Creating account {i+1}/{count}")
            logger.info(f"{'='*50}")

            result = self.create_account()
            results.append(result)

            if i < count - 1:
                delay = random.randint(delay_min, delay_max)
                logger.info(f"Waiting {delay}s before next account...")
                time.sleep(delay)

        return results


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create Twitter/X accounts")
    parser.add_argument("--count", type=int, default=1, help="Number of accounts")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL")
    args = parser.parse_args()

    creator = TwitterAccountCreator(proxy_url=args.proxy, headless=args.headless)
    results = creator.create_batch(args.count)

    success_count = sum(1 for r in results if r["success"])
    print(f"\n{'='*50}")
    print(f"Done. {success_count}/{len(results)} accounts created.")
    for r in results:
        status = "✅" if r["success"] else "❌"
        info = f"@{r['username']}" if r["success"] else r.get("error", "unknown")
        print(f"  {status} {info}")
