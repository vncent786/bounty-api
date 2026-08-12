import asyncio
import urllib.parse

from apis.social_search_api import build_default_broker
from social_scraper.connectors.reddit_search import RedditSearchConnector


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def headers(self):
        return type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

    def read(self):
        import json
        return json.dumps(self.payload).encode("utf-8")


def test_search_connector_canonicalizes_and_deduplicates_reddit_post_urls():
    captured = {}

    def fake_fetch(request):
        captured["url"] = request.full_url
        captured["token"] = request.get_header("X-subscription-token")
        return {
            "web": {
                "results": [
                    {
                        "title": "First result",
                        "url": "https://www.reddit.com/r/Python/comments/ABC123/a_title/?utm_source=x",
                        "description": "A useful discussion.",
                    },
                    {
                        "title": "Duplicate mirror",
                        "url": "https://old.reddit.com/r/Python/comments/abc123/a_title/",
                        "description": "Duplicate.",
                    },
                    {
                        "title": "Not a post",
                        "url": "https://www.reddit.com/r/Python/",
                        "description": "Subreddit home.",
                    },
                    {
                        "title": "Foreign result",
                        "url": "https://example.com/r/Python/comments/nope/title/",
                        "description": "Wrong host.",
                    },
                ]
            }
        }

    connector = RedditSearchConnector(api_key="test-key", fetch_json=fake_fetch)
    result = asyncio.run(connector.search("python packaging", count=10, time_filter="week", region="US"))

    assert result.health.status == "partial"
    assert result.health.items_returned == 1
    assert result.items[0].post_id == "abc123"
    assert result.items[0].url == "https://www.reddit.com/r/Python/comments/abc123/a_title/"
    assert result.items[0].raw["subreddit"] == "Python"
    assert result.items[0].text == "A useful discussion."
    assert "freshness=pw" in captured["url"]
    assert "country=US" in captured["url"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(captured["url"]).query)["q"][0]
    assert query == "site:reddit.com python packaging"
    assert captured["token"] == "test-key"


def test_search_connector_fails_explicitly_without_credentials():
    connector = RedditSearchConnector(api_key="")

    result = asyncio.run(connector.search("python", count=5))

    assert result.items == []
    assert result.health.status == "error"
    assert result.health.error == "missing_api_key"


def test_reddit_discovery_routes_apply_a_six_month_window(monkeypatch):
    import re
    from social_scraper.connectors.reddit import RedditConnector

    captured = {}

    def brave_fetch(request):
        captured["brave"] = request.full_url
        return {"web": {"results": []}}

    brave = RedditSearchConnector(api_key="test-key", fetch_json=brave_fetch)
    asyncio.run(brave.search("python", count=5, time_filter="halfyear"))
    assert re.search(r"freshness=\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}", captured["brave"])

    pullpush = RedditConnector()

    def pullpush_fetch(request):
        captured["pullpush"] = request.full_url
        return {"data": []}

    monkeypatch.setattr(pullpush, "_fetch_json", pullpush_fetch)
    monkeypatch.setattr("social_scraper.connectors.reddit.time.time", lambda: 2_000_000_000)
    asyncio.run(pullpush.search("python", count=5, time_filter="halfyear"))
    assert "after=1984448000" in captured["pullpush"]


def test_default_broker_registers_search_fallback_only_when_configured(monkeypatch):
    monkeypatch.delenv("BOUNTY_REDDIT_SUBREDDITS", raising=False)
    monkeypatch.delenv("BOUNTY_BRAVE_SEARCH_API_KEY", raising=False)
    without_key = build_default_broker().list_routes()["reddit"]
    assert without_key == [
        {"connector": "reddit_mobile_owned", "priority": 1},
        {"connector": "reddit_atom_scoped", "priority": 3},
        {"connector": "pullpush_free", "priority": 10},
        {"connector": "arctic_shift_scoped", "priority": 20},
    ]

    monkeypatch.setenv("BOUNTY_BRAVE_SEARCH_API_KEY", "configured-secret")
    with_key = build_default_broker().list_routes()["reddit"]
    assert with_key == [
        {"connector": "reddit_mobile_owned", "priority": 1},
        {"connector": "reddit_atom_scoped", "priority": 3},
        {"connector": "pullpush_free", "priority": 10},
        {"connector": "arctic_shift_scoped", "priority": 20},
        {"connector": "brave_reddit_discovery", "priority": 30},
    ]


def test_search_fallback_is_used_when_pullpush_returns_no_usable_items(monkeypatch):
    from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
    from social_scraper.broker import SourceBroker
    from social_scraper.connectors.reddit import RedditConnector

    async def empty_pullpush(self, keyword, count=20, time_filter="", sort="", region=""):
        return ConnectorResult(
            items=[],
            health=SourceHealth(
                platform="reddit",
                connector="pullpush_free",
                status="partial",
                items_requested=count,
            ),
        )

    async def search_result(self, keyword, count=20, time_filter="", sort="", region=""):
        return ConnectorResult(
            items=[SocialItem(
                platform="reddit",
                post_id="fallback1",
                url="https://www.reddit.com/r/test/comments/fallback1/title/",
            )],
            health=SourceHealth(
                platform="reddit",
                connector="brave_reddit_discovery",
                status="ok",
                items_returned=1,
                items_requested=count,
            ),
        )

    monkeypatch.setattr(RedditConnector, "search", empty_pullpush)
    monkeypatch.setattr(RedditSearchConnector, "search", search_result)
    broker = SourceBroker()
    broker.register(RedditConnector(), priority=10)
    broker.register(RedditSearchConnector(api_key="test-key"), priority=20)

    response = asyncio.run(broker.search("python", platforms=["reddit"]))

    assert response["count"] == 1
    assert response["items"][0]["post_id"] == "fallback1"
    assert response["items"][0]["provenance"]["connector"] == "brave_reddit_discovery"
    assert response["platform_results"]["reddit"]["attempted_connectors"] == [
        "pullpush_free",
        "brave_reddit_discovery",
    ]
