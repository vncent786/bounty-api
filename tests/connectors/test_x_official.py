import asyncio
from datetime import datetime, timezone

import httpx

from apis.social_search_api import build_default_broker
from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.connectors.x_official import XOfficialConnector


def test_official_x_recent_search_preserves_provenance_and_metrics():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "data": [{
                    "id": "123",
                    "text": "I switched to Product B",
                    "author_id": "u1",
                    "created_at": "2026-08-26T01:00:00.000Z",
                    "lang": "en",
                    "public_metrics": {
                        "retweet_count": 2,
                        "reply_count": 3,
                        "quote_count": 4,
                        "like_count": 11,
                        "bookmark_count": 4,
                        "impression_count": 150,
                    },
                    "entities": {
                        "hashtags": [{"tag": "switching"}],
                        "mentions": [{"username": "brand"}],
                    },
                    "attachments": {"media_keys": ["m1"]},
                    "conversation_id": "120",
                    "edit_history_tweet_ids": ["123"],
                }],
                "includes": {
                    "users": [{
                        "id": "u1",
                        "username": "consumer",
                        "name": "Consumer",
                        "public_metrics": {"followers_count": 42},
                    }],
                    "media": [{
                        "media_key": "m1",
                        "type": "photo",
                        "url": "https://example.test/photo.jpg",
                    }],
                },
                "meta": {"result_count": 1},
            },
        )

    connector = XOfficialConnector(
        bearer_token="token",
        base_url="https://api.x.test",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(connector.search(
        "product switching", count=5, time_filter="week", sort="latest"
    ))

    assert result.health.status == "ok"
    assert result.health.coverage["endpoint"] == "/2/tweets/search/recent"
    assert result.health.coverage["region_filter_applied"] is False
    assert captured["path"] == "/2/tweets/search/recent"
    assert captured["params"]["query"] == "product switching"
    assert captured["params"]["max_results"] == "10"
    assert captured["params"]["sort_order"] == "recency"
    assert captured["authorization"] == "Bearer token"
    item = result.items[0]
    assert item.url == "https://x.com/consumer/status/123"
    assert item.text == "I switched to Product B"
    assert item.author_follower_count == 42
    assert item.likes == 11
    assert item.comments == 3
    assert item.reposts == 2
    assert item.bookmarks == 4
    assert item.views == 150
    assert item.hashtags == ["switching"]
    assert item.raw["source_kind"] == "official_x_api"
    assert item.raw["public_metrics"]["quote_count"] == 4
    assert result.raw_records[0]["payload"]["meta"]["result_count"] == 1


def test_official_x_fails_closed_without_credentials(monkeypatch):
    monkeypatch.delenv("BOUNTY_X_BEARER_TOKEN", raising=False)
    result = asyncio.run(XOfficialConnector().search("test"))
    assert result.items == []
    assert result.health.status == "error"
    assert result.health.error == "x_credentials_missing"


def test_official_x_requires_explicit_full_archive_enablement(monkeypatch):
    monkeypatch.delenv("BOUNTY_X_ENABLE_FULL_ARCHIVE", raising=False)
    result = asyncio.run(
        XOfficialConnector(bearer_token="token").search("test", time_filter="month")
    )
    assert result.items == []
    assert result.health.status == "skipped"
    assert result.health.error == "x_full_archive_disabled"


def test_recent_week_window_stays_inside_seven_days():
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    start_text, end_text = XOfficialConnector._window("week", now=now)
    start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))

    assert (now - start).total_seconds() < 7 * 24 * 60 * 60
    assert (end - start).total_seconds() < 7 * 24 * 60 * 60
    assert end < now


def test_official_x_paginates_and_persists_every_raw_page():
    calls = []

    def handler(request):
        params = dict(request.url.params)
        calls.append(params)
        token = params.get("next_token")
        if token is None:
            return httpx.Response(200, json={
                "data": [
                    {"id": "1", "text": "one", "public_metrics": {}},
                    {"id": "2", "text": "two", "public_metrics": {}},
                ],
                "meta": {"result_count": 2, "next_token": "page-2"},
            })
        return httpx.Response(200, json={
            "data": [{
                "id": "3",
                "text": "three",
                "public_metrics": {"repost_count": 7},
                "edit_history_post_ids": ["3"],
            }],
            "meta": {"result_count": 1},
        })

    connector = XOfficialConnector(
        bearer_token="token",
        base_url="https://api.x.test",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(connector.search("topic", count=3))

    assert [item.post_id for item in result.items] == ["1", "2", "3"]
    assert result.items[-1].reposts == 7
    assert result.items[-1].raw["edit_history_post_ids"] == ["3"]
    assert calls[1]["next_token"] == "page-2"
    assert result.health.coverage["pages_completed"] == 2
    assert result.health.coverage["window_exhausted"] is True
    assert len(result.raw_records) == 2


def test_official_x_marks_200_payload_errors_partial():
    def handler(_request):
        return httpx.Response(200, json={
            "data": [{"id": "1", "text": "one", "public_metrics": {}}],
            "errors": [{"title": "Not Found Error", "value": "u1"}],
            "meta": {"result_count": 1},
        })

    result = asyncio.run(XOfficialConnector(
        bearer_token="token",
        base_url="https://api.x.test",
        transport=httpx.MockTransport(handler),
    ).search("topic", count=1))

    assert result.health.status == "partial"
    assert result.health.error == "x_partial_response"
    assert result.health.coverage["payload_error_count"] == 1


def test_broker_registers_only_official_x_on_api_plane(monkeypatch):
    monkeypatch.setenv("BOUNTY_ENV", "production")
    monkeypatch.delenv("BOUNTY_OWNED_SOCIAL_WORKER", raising=False)
    monkeypatch.delenv("BOUNTY_X_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("BOUNTY_X_BEARER_TOKEN", "official")
    routes = build_default_broker().list_routes()["x"]
    assert routes == [{"connector": "x_official_api", "priority": 20}]


def test_broker_registers_owned_x_on_residential_worker(monkeypatch):
    monkeypatch.setenv("BOUNTY_OWNED_SOCIAL_WORKER", "1")
    monkeypatch.setenv("BOUNTY_X_AUTH_TOKEN", "owned-cookie")
    monkeypatch.delenv("BOUNTY_X_BEARER_TOKEN", raising=False)
    routes = build_default_broker().list_routes()["x"]
    assert routes == [{"connector": "x_scweet", "priority": 1}]


def test_official_x_thread_reader_reconstructs_reply_depth(monkeypatch):
    connector = XOfficialConnector(bearer_token="token")
    root = SocialItem(
        platform="x", post_id="123", url="https://x.com/a/status/123", comments=5,
    )
    replies = [
        SocialItem(
            platform="x", post_id="r1", url="https://x.com/b/status/r1",
            author_username="b", text="first reply", created_at="2026-08-26T01:00:00Z",
            likes=3, raw={
                "conversation_id": "123",
                "referenced_tweets": [{"type": "replied_to", "id": "123"}],
            },
        ),
        SocialItem(
            platform="x", post_id="r2", url="https://x.com/c/status/r2",
            author_username="c", text="nested reply", created_at="2026-08-26T01:01:00Z",
            likes=1, raw={
                "conversation_id": "123",
                "referenced_tweets": [{"type": "replied_to", "id": "r1"}],
            },
        ),
    ]

    async def fake_search(keyword, count=20, time_filter="", sort="", region=""):
        assert keyword == "conversation_id:123"
        assert sort == "hot"
        return ConnectorResult(
            items=replies,
            health=SourceHealth(
                platform="x", connector="x_official_api", status="ok",
                items_returned=2, items_requested=count,
                coverage={"window_exhausted": True},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))

    assert result.status == "partial"
    assert result.truncated is True
    assert result.platform_reported_total == 5
    assert result.attempted_route == "x_official_conversation_search"
    assert [(record.external_id, record.parent_external_id, record.depth) for record in result.records] == [
        ("r1", "123", 1),
        ("r2", "r1", 2),
    ]
    assert result.records[0].likes == 3


def test_official_x_thread_reader_respects_depth_one(monkeypatch):
    connector = XOfficialConnector(bearer_token="token")
    root = SocialItem(platform="x", post_id="123", url="https://x.com/a/status/123")
    replies = [
        SocialItem(
            platform="x", post_id="r1", url="https://x.com/b/status/r1",
            text="first", raw={"referenced_tweets": [{"type": "replied_to", "id": "123"}]},
        ),
        SocialItem(
            platform="x", post_id="r2", url="https://x.com/c/status/r2",
            text="nested", raw={"referenced_tweets": [{"type": "replied_to", "id": "r1"}]},
        ),
    ]

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=replies,
            health=SourceHealth(
                platform="x", connector="x_official_api", status="ok",
                coverage={"window_exhausted": True},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=1))
    assert [record.external_id for record in result.records] == ["r1"]


def test_official_x_skips_reply_with_missing_parent_chain(monkeypatch):
    connector = XOfficialConnector(bearer_token="token")
    root = SocialItem(platform="x", post_id="123", url="https://x.com/a/status/123")
    descendant = SocialItem(
        platform="x", post_id="desc", url="https://x.com/b/status/desc",
        text="unknown depth",
        raw={"referenced_tweets": []},
    )

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=[descendant],
            health=SourceHealth(
                platform="x", connector="x_official_api", status="ok",
                coverage={"window_exhausted": True},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))
    assert result.records == ()
    assert result.status == "partial"
    assert result.truncated is True


def test_official_x_reply_count_compares_direct_replies_only(monkeypatch):
    connector = XOfficialConnector(bearer_token="token")
    root = SocialItem(
        platform="x", post_id="123", url="https://x.com/a/status/123", comments=3,
    )
    items = [
        SocialItem(
            platform="x", post_id="d1", url="https://x.com/b/status/d1",
            raw={"referenced_tweets": [{"type": "replied_to", "id": "123"}]},
        ),
        SocialItem(
            platform="x", post_id="n1", url="https://x.com/c/status/n1",
            raw={"referenced_tweets": [{"type": "replied_to", "id": "d1"}]},
        ),
        SocialItem(
            platform="x", post_id="n2", url="https://x.com/d/status/n2",
            raw={"referenced_tweets": [{"type": "replied_to", "id": "d1"}]},
        ),
    ]

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform="x", connector="x_official_api", status="ok",
                coverage={"window_exhausted": True},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))
    assert result.status == "partial"
    assert result.truncated is True


def test_official_x_empty_unknown_total_is_not_complete(monkeypatch):
    connector = XOfficialConnector(bearer_token="token")
    root = SocialItem(platform="x", post_id="123", url="https://x.com/a/status/123")

    async def fake_search(*_args, **_kwargs):
        return ConnectorResult(
            items=[],
            health=SourceHealth(
                platform="x", connector="x_official_api", status="ok",
                coverage={"window_exhausted": True},
            ),
        )

    monkeypatch.setattr(connector, "search", fake_search)
    result = asyncio.run(connector.fetch_thread(root, max_comments=10, max_depth=2))
    assert result.status == "partial"


def test_official_x_minimum_page_local_truncation_is_not_window_exhaustion():
    def handler(_request):
        return httpx.Response(200, json={
            "data": [
                {"id": str(index), "text": str(index), "public_metrics": {}}
                for index in range(10)
            ],
            "meta": {"result_count": 10},
        })

    result = asyncio.run(XOfficialConnector(
        bearer_token="token",
        base_url="https://api.x.test",
        transport=httpx.MockTransport(handler),
    ).search("topic", count=5))

    assert len(result.items) == 5
    assert result.health.coverage["requested_limit_reached"] is True
    assert result.health.coverage["window_exhausted"] is False
    assert result.health.coverage["local_page_truncated"] is True
