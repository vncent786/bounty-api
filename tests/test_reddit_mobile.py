import asyncio
import json
from datetime import datetime, timezone

from social_scraper.broker import SourceBroker
from social_scraper.connectors.reddit_mobile import (
    RedditMobileConnector,
    load_or_create_device_id,
    parse_mobile_post,
)
from social_scraper.storage import ObservationStore


POST = {
    "id": "abc123",
    "name": "t3_abc123",
    "subreddit": "stocks",
    "permalink": "/r/stocks/comments/abc123/earnings_revision/",
    "title": "Earnings revision accelerates",
    "selftext": "Management raised guidance.",
    "author": "alice",
    "created_utc": 1_785_729_600,
    "score": 17,
    "num_comments": 4,
    "is_self": True,
    "link_flair_text": "Analysis",
}


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.headers = {"content-type": "application/json", **(headers or {})}

    def json(self):
        return self._payload


def test_mobile_post_parser_preserves_identity_metrics_and_timestamp():
    item = parse_mobile_post(
        POST,
        ["stocks"],
        now=datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc),
    )

    assert item.post_id == "abc123"
    assert item.url == "https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/"
    assert item.created_at == "2026-08-03T04:00:00+00:00"
    assert item.likes == 17
    assert item.comments == 4
    assert item.raw["source_kind"] == "current_oauth_listing"

    corrupt = {**POST, "score": True, "num_comments": False}
    corrupt_item = parse_mobile_post(corrupt, ["stocks"], now=datetime(2026, 8, 3, tzinfo=timezone.utc))
    assert corrupt_item.likes is None
    assert corrupt_item.comments is None


def test_mobile_connector_mints_once_and_reads_each_exact_subreddit(tmp_path):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if "access-token/loid" in url:
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400}, {
                "x-reddit-loid": "loid",
                "x-reddit-session": "session",
            })
        subreddit = url.split("/r/", 1)[1].split("/", 1)[0]
        post = {**POST, "subreddit": subreddit}
        post["permalink"] = f"/r/{subreddit}/comments/abc{subreddit}/earnings/"
        post["id"] = f"abc{subreddit}"
        post["name"] = f"t3_abc{subreddit}"
        return FakeResponse(200, {"kind": "Listing", "data": {"children": [{"data": post}]}})

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
        clock=lambda: datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc),
    )
    result = asyncio.run(connector.search_with_options(
        "earnings",
        count=5,
        time_filter="week",
        sort="latest",
        options={"subreddits": ["stocks", "investing"]},
    ))

    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    assert calls[1][1].endswith("/r/stocks/new")
    assert calls[2][1].endswith("/r/investing/new")
    assert result.health.status == "ok"
    assert result.health.coverage["successful_subreddits"] == ["stocks", "investing"]
    assert len(result.items) == 2
    assert all(item.likes == 17 for item in result.items)


def test_mobile_connector_marks_complete_listing_outage_as_error(tmp_path):
    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(403, {})

    connector = RedditMobileConnector(request_fn=request, device_path=tmp_path / "device.json")
    result = asyncio.run(connector.search_with_options(
        "earnings",
        options={"subreddits": ["stocks", "investing"]},
    ))

    assert result.items == []
    assert result.health.status == "error"
    assert result.health.error == "reddit_mobile_unavailable"
    assert result.health.coverage["failed_subreddits"] == ["stocks", "investing"]


def test_mobile_collection_records_current_metrics_at_observation_time(tmp_path):
    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(200, {"kind": "Listing", "data": {"children": [{"data": POST}]}})

    broker = SourceBroker()
    broker.register(RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
        clock=lambda: datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc),
    ))
    options = {"reddit": {"subreddits": ["stocks"]}}
    response = asyncio.run(broker.search(
        "earnings",
        platforms=["reddit"],
        count=1,
        platform_options=options,
        include_source_records=True,
    ))

    assert response["items"][0]["engagement"]["likes"] == 17
    assert response["items"][0]["engagement"]["comments"] == 4
    assert response["items"][0]["provenance"]["source_kind"] == "current_oauth_listing"

    store = ObservationStore(tmp_path / "observations.db")
    run_id = store.record_collection(response, ["reddit"], platform_options=options)
    history = store.get_observation_history("reddit", "abc123")
    saved = store.get_collection_run(run_id)["raw_response"]
    source_records = store.get_source_records(run_id)
    assert saved["items"][0]["post_id"] == "abc123"
    assert "_source_records" not in saved
    assert source_records[0]["source_id"] == "t3_abc123"
    assert source_records[0]["payload"]["score"] == 17
    assert len(source_records[0]["payload_sha256"]) == 64
    assert source_records[0]["hash_valid"] is True
    assert history[0]["connector"] == "reddit_mobile_owned"
    assert history[0]["likes"] == 17
    assert history[0]["comments"] == 4


def test_device_identity_is_stable_and_not_regenerated(tmp_path):
    path = tmp_path / "device.json"
    first = load_or_create_device_id(path)
    second = load_or_create_device_id(path)
    assert first == second


def test_rejected_token_is_refreshed_once(tmp_path):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        if len([call for call in calls if call[0] == "GET"]) == 1:
            return FakeResponse(401, {})
        return FakeResponse(200, {"kind": "Listing", "data": {"children": [{"data": POST}]}})

    connector = RedditMobileConnector(request_fn=request, device_path=tmp_path / "device.json")
    result = asyncio.run(connector.search_with_options(
        "earnings", options={"subreddits": ["stocks"]},
    ))

    assert [method for method, _ in calls] == ["POST", "GET", "POST", "GET"]
    assert result.health.status == "ok"
    assert result.items[0].post_id == "abc123"
