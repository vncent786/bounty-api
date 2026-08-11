"""Deterministic browser smoke check for the build-free Bounty dashboard.

Usage: python tests/browser_dashboard_qa.py [base_url] [artifact_dir]
The target server controls data; this script never mocks or fabricates API responses.
"""

from pathlib import Path
import sys

from playwright.sync_api import sync_playwright


BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
ARTIFACTS = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/browser-qa")
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def check_page(page, mobile=False):
    console_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle")
    page.get_by_role("heading", name="Projects", exact=True).wait_for()
    assert page.locator("nav[aria-label='Product']").count() == 1
    assert page.locator("img[alt='Bounty']:visible").count() >= 1
    assert page.get_by_text("Searches never run on page load.").count() == 1

    if mobile:
        menu = page.get_by_role("button", name="Menu")
        assert menu.is_visible()
        menu.click()
        page.get_by_role("button", name="Explore", exact=True).click()
    else:
        page.get_by_role("button", name="Explore", exact=True).click()
    page.get_by_role("heading", name="Explore conversations").wait_for()
    assert page.get_by_text("No current-session results").is_visible()
    assert page.get_by_text("Not run.").is_visible()

    if mobile:
        page.get_by_role("button", name="Menu").click()
    page.get_by_role("button", name="Lenses", exact=True).click()
    page.get_by_role("heading", name="Lenses", exact=True).wait_for()
    page.get_by_role("button", name="New lens").click()
    assert page.get_by_role("dialog").is_visible()
    page.get_by_role("button", name="Cancel").click()

    page.screenshot(path=str(ARTIFACTS / ("dashboard-mobile.png" if mobile else "dashboard-desktop.png")), full_page=True)
    assert not console_errors, f"browser console errors: {console_errors}"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
    requests = []
    desktop.on("request", lambda request: requests.append(request.url))
    check_page(desktop.new_page())
    assert not any("/dashboard/api/discover" in url for url in requests), "Explore ran on page load"
    desktop.close()

    mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    requests = []
    mobile.on("request", lambda request: requests.append(request.url))
    check_page(mobile.new_page(), mobile=True)
    assert not any("/dashboard/api/discover" in url for url in requests), "Explore ran on mobile page load"
    mobile.close()
    browser.close()

print(f"Browser QA passed; screenshots: {ARTIFACTS.resolve()}")
