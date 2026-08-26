import asyncio

from apis.social_search_api import build_default_broker
from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.connectors import x_graphql
from social_scraper.connectors.x_graphql import XConnector


def _tweet(tweet_id, *, parent=None, conversation="root", comments=0):
    legacy = {
        "id_str": tweet_id,
        "conversation_id_str": conversation,
        "in_reply_to_status_id_str": parent,
        "reply_count": comments,
        "favorite_count": 3,
        "retweet_count": 2,
        "quote_count": 1,
        "bookmark_count": 4,
        "user_id_str": f"u-{tweet_id}",
    }
    return {
        "tweet_id": tweet_id,
        "tweet_url": f"https://x.com/user/status/{tweet_id}",
        "text": f"text-{tweet_id}",
        "timestamp": "2026-08-26T01:00:00+00:00",
        "comments": comments,
        "likes": 3,
        "retweets": 2,
        "views": None,
        "user": {"screen_name": "user", "name": "User"},
        "raw": {
            "rest_id": tweet_id,
            "legacy": legacy,
            "views": {"count": "123"},
            "core": {"user_results": {"result": {"rest_id": f"u-{tweet_id}"}}},
        },
    }


def test_owned_x_search_uses_conservative_scweet_config_and_preserves_raw(monkeypatch, tmp_path):
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class FakeScweet:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def search(self, query, **kwargs):
            captured["query"] = query
            captured["search"] = kwargs
            return [_tweet(str(index)) for index in range(12)]

    monkeypatch.setattr(x_graphql, "ScweetConfig", FakeConfig)
    monkeypatch.setattr(x_graphql, "Scweet", FakeScweet)
    monkeypatch.setattr(x_graphql, "SCWEET_AVAILABLE", True)
    monkeypatch.setenv("BOUNTY_X_AUTH_TOKEN", "secret")
    monkeypatch.setenv("BOUNTY_X_SCWEET_DB", str(tmp_path / "state.db"))

    result = asyncio.run(XConnector().search(
        "running shoes", count=5, time_filter="week", sort="latest"
    ))

    assert len(result.items) == 5
    assert captured["config"]["daily_requests_limit"] == 100
    assert captured["config"]["requests_per_min"] == 5
    assert captured["config"]["concurrency"] == 1
    assert captured["client"]["db_path"] == str(tmp_path / "state.db")
    assert captured["search"]["display_type"] == "Latest"
    assert captured["search"]["limit"] == 5
    assert captured["search"]["since"]
    assert captured["search"]["until"]
    assert result.health.coverage["route"] == "x_web_graphql_scweet"
    assert result.items[0].views == 123
    assert result.items[0].bookmarks == 4
    assert result.items[0].raw["legacy"]["quote_count"] == 1
    assert result.raw_records[0]["payload"]["tweets"]


def test_owned_x_successful_empty_search_is_complete_not_unavailable(monkeypatch, tmp_path):
    class FakeConfig:
        def __init__(self, **_kwargs):
            pass

    class EmptyScweet:
        def __init__(self, **_kwargs):
            pass

        def search(self, _query, **_kwargs):
            return []

    monkeypatch.setattr(x_graphql, "ScweetConfig", FakeConfig)
    monkeypatch.setattr(x_graphql, "Scweet", EmptyScweet)
    monkeypatch.setattr(x_graphql, "SCWEET_AVAILABLE", True)
    monkeypatch.setenv("BOUNTY_X_AUTH_TOKEN", "secret")
    monkeypatch.setenv("BOUNTY_X_SCWEET_DB", str(tmp_path / "state.db"))

    result = asyncio.run(XConnector().search(
        '"new product phrase"', count=5, time_filter="week", sort="latest"
    ))

    assert result.items == []
    assert result.health.status == "ok"
    assert result.health.error is None
    assert result.health.coverage["provider_returned"] == 0
    assert result.health.coverage["requested_limit_reached"] is False


def test_owned_x_thread_reconstructs_replies_and_skips_unknown_parent(monkeypatch):
    connector = XConnector()
    root = SocialItem(
        platform="x",
        post_id="root",
        url="https://x.com/user/status/root",
        comments=3,
        raw={"legacy": {"conversation_id_str": "root"}},
    )
    items = [
        connector._parse_tweet(_tweet("d1", parent="root")),
        connector._parse_tweet(_tweet("n1", parent="d1")),
        connector._parse_tweet(_tweet("orphan", parent="missing")),
    ]

    async def fake_search(keyword, count=20, time_filter="", sort="", region=""):
        assert keyword == "conversation_id:root"
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform="x", connector="x_scweet", status="ok",
                coverage={"requested_limit_reached": False},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))

    assert [(record.external_id, record.parent_external_id, record.depth) for record in result.records] == [
        ("d1", "root", 1),
        ("n1", "d1", 2),
    ]
    assert result.status == "partial"
    assert result.truncated is True
    assert all(record.raw for record in result.records)


def test_owned_x_empty_unknown_thread_is_partial(monkeypatch):
    connector = XConnector()
    root = SocialItem(platform="x", post_id="root", url="https://x.com/u/status/root")

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=[],
            health=SourceHealth(
                platform="x", connector="x_scweet", status="ok",
                coverage={"requested_limit_reached": False},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))
    assert result.status == "partial"


def test_owned_x_nonempty_unknown_total_is_partial(monkeypatch):
    connector = XConnector()
    root = SocialItem(platform="x", post_id="root", url="https://x.com/u/status/root")
    direct = connector._parse_tweet(_tweet("d1", parent="root"))

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=[direct],
            health=SourceHealth(
                platform="x", connector="x_scweet", status="ok",
                coverage={"requested_limit_reached": False},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))
    assert result.returned_count == 1
    assert result.status == "partial"


def test_api_plane_does_not_register_owned_browser_connectors(monkeypatch):
    monkeypatch.setenv("BOUNTY_ENV", "production")
    monkeypatch.delenv("BOUNTY_OWNED_SOCIAL_WORKER", raising=False)
    monkeypatch.setenv("BOUNTY_X_AUTH_TOKEN", "owned-cookie")
    monkeypatch.delenv("BOUNTY_X_BEARER_TOKEN", raising=False)

    routes = build_default_broker().list_routes()

    assert "tiktok" not in routes
    assert "instagram" not in routes
    assert "x" not in routes
