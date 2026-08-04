import asyncio

from social_scraper.connectors.tiktok_playwright import _navigate_search_page


class FakePage:
    def __init__(self):
        self.calls = []

    async def goto(self, url, **kwargs):
        self.calls.append((url, kwargs))


def test_navigation_waits_for_dom_not_network_idle():
    page = FakePage()

    asyncio.run(_navigate_search_page(page, "https://www.tiktok.com/search?q=test"))

    assert page.calls == [
        (
            "https://www.tiktok.com/search?q=test",
            {"wait_until": "domcontentloaded", "timeout": 60000},
        )
    ]
