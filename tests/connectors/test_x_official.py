import asyncio
from datetime import datetime, timezone

import httpx

from apis.social_search_api import build_default_broker
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


def test_broker_registers_only_official_x_by_default(monkeypatch):
    monkeypatch.delenv("BOUNTY_ENABLE_LEGACY_X_SCWEET", raising=False)
    routes = build_default_broker().list_routes()["x"]
    assert routes == [{"connector": "x_official_api", "priority": 1}]


def test_broker_registers_scweet_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("BOUNTY_ENABLE_LEGACY_X_SCWEET", "1")
    routes = build_default_broker().list_routes()["x"]
    assert routes[0] == {"connector": "x_official_api", "priority": 1}
    assert routes[-1] == {"connector": "x_scweet", "priority": 90}
