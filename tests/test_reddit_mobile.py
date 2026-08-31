import asyncio
import json
import threading
from datetime import datetime, timezone

from social_scraper.broker import SourceBroker
from social_scraper.base import BaseConnector, SocialItem
from social_scraper.conversations.thread_reader import ThreadFetchResult
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

THREAD_PAYLOAD = [
    {"kind": "Listing", "data": {"children": [{"kind": "t3", "data": POST}]}},
    {"kind": "Listing", "data": {"children": [{
        "kind": "t1",
        "data": {
            "id": "c1", "parent_id": "t3_abc123", "author_fullname": "t2_user1",
            "author": "user1", "body": "I switched too", "score": 8,
            "created_utc": 1_785_733_200,
            "permalink": "/r/stocks/comments/abc123/earnings_revision/c1/",
            "replies": {"kind": "Listing", "data": {"children": [{
                "kind": "t1",
                "data": {
                    "id": "c2", "parent_id": "t1_c1", "author": "user2",
                    "body": "Same reason here", "score": 3,
                    "created_utc": 1_785_736_800,
                    "permalink": "/r/stocks/comments/abc123/earnings_revision/c2/",
                    "replies": "",
                },
            }]}},
        },
    }]}}
]


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


def test_mobile_connector_auto_discovery_is_reachable_through_broker(tmp_path, monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if "access-token/loid" in url:
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(200, {"kind": "Listing", "data": {"children": [{"data": POST}]}})

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
        clock=lambda: datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(connector, "_discover_subreddits", lambda _keyword: ["stocks"])
    broker = SourceBroker()
    broker.register(connector, priority=1)

    response = asyncio.run(broker.search(
        "earnings", platforms=["reddit"], count=5, time_filter="week", sort="latest"
    ))

    assert response["count"] == 1
    assert response["platform_results"]["reddit"]["selected_connector"] == "reddit_mobile_owned"
    assert response["items"][0]["post_id"] == "abc123"
    assert calls[0][0] == "POST"


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


def test_mobile_installed_client_hydrates_comment_tree_without_developer_oauth(
    tmp_path, monkeypatch,
):
    for name in (
        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            assert "access-token/loid" in url
            assert kwargs["headers"]["Authorization"].startswith("Basic ")
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        assert url.endswith("/comments/abc123")
        assert kwargs["headers"]["Authorization"] == f"Bearer {'x' * 100}"
        assert kwargs["params"]["depth"] == 2
        return FakeResponse(200, THREAD_PAYLOAD)

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    post = SocialItem(
        platform="reddit",
        post_id="abc123",
        url="https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
        comments=4,
    )

    result = asyncio.run(connector.fetch_thread(post, max_comments=20, max_depth=2))

    assert result.status == "partial"
    assert result.attempted_route == "reddit_mobile_installed_client_comments"
    assert result.error_category is None
    assert result.platform_reported_total == 4
    assert [record.external_id for record in result.records] == ["c1", "c2"]
    assert result.records[1].parent_external_id == "c1"
    assert [method for method, _url, _kwargs in calls] == ["POST", "GET"]


def test_mobile_thread_caps_request_and_parser_output_at_100(tmp_path):
    request_limits = []
    comments = []
    for index in range(101):
        comments.append({
            "kind": "t1",
            "data": {
                "id": f"c{index}", "parent_id": "t3_abc123",
                "author": f"user{index}", "body": f"comment {index}",
                "score": index, "created_utc": 1_785_733_200 + index,
                "permalink": f"/r/stocks/comments/abc123/earnings_revision/c{index}/",
                "replies": "",
            },
        })
    post = {**POST, "num_comments": 101}
    payload = [
        {"kind": "Listing", "data": {"children": [{"kind": "t3", "data": post}]}},
        {"kind": "Listing", "data": {"children": comments}},
    ]

    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        request_limits.append(kwargs["params"]["limit"])
        return FakeResponse(200, payload)

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    root = SocialItem(
        platform="reddit", post_id="abc123",
        url="https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
        comments=101,
    )

    result = asyncio.run(connector.fetch_thread(root, max_comments=500, max_depth=2))

    assert request_limits == [100]
    assert result.max_comments == 100
    assert result.returned_count == 100
    assert result.status == "partial"
    assert result.truncated is True


def test_broker_prefers_mobile_installed_client_for_reddit_thread_depth(tmp_path):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(200, THREAD_PAYLOAD)

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    broker = SourceBroker()
    broker.register(connector, priority=1)
    item = {
        "platform": "reddit", "post_id": "abc123",
        "url": "https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
        "engagement": {"comments": 4},
        "provenance": {"connector": "reddit_mobile_owned"},
    }

    result = asyncio.run(broker.fetch_thread(item, max_comments=20, max_depth=2))

    assert result.attempted_route == "reddit_mobile_installed_client_comments"
    assert result.returned_count == 2
    assert calls == [("POST", "https://www.reddit.com/auth/v2/oauth/access-token/loid"),
                     ("GET", "https://oauth.reddit.com/comments/abc123")]


def test_mobile_thread_rate_limit_is_explicit(tmp_path):
    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(429, {})

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    post = SocialItem(
        platform="reddit", post_id="abc123",
        url="https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
    )

    result = asyncio.run(connector.fetch_thread(post, max_comments=20, max_depth=2))

    assert result.status == "unavailable"
    assert result.error_category == "reddit_mobile_rate_limited"
    assert result.attempted_route == "reddit_mobile_installed_client_comments"


def test_broker_does_not_mask_selected_mobile_thread_failure_with_fallback(tmp_path):
    class FallbackConnector(BaseConnector):
        platform = "reddit"
        connector_name = "fallback_reddit"

        def __init__(self):
            self.calls = 0

        async def search(self, keyword, count=20, time_filter="", sort="", region=""):
            raise AssertionError("not used")

        async def health_check(self):
            raise AssertionError("not used")

        async def fetch_thread(self, post, max_comments, max_depth):
            self.calls += 1
            return ThreadFetchResult(
                platform="reddit", root_post_external_id=post.post_id,
                status="complete", attempted_route="masked_fallback",
                max_comments=max_comments, max_depth=max_depth,
            )

    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        return FakeResponse(429, {})

    mobile = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    fallback = FallbackConnector()
    broker = SourceBroker()
    broker.register(mobile, priority=1)
    broker.register(fallback, priority=2)
    item = {
        "platform": "reddit", "post_id": "abc123",
        "url": "https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
        "provenance": {"connector": "reddit_mobile_owned"},
    }

    result = asyncio.run(broker.fetch_thread(item, max_comments=20, max_depth=2))

    assert result.status == "unavailable"
    assert result.error_category == "reddit_mobile_rate_limited"
    assert result.attempted_route == "reddit_mobile_installed_client_comments"
    assert fallback.calls == 0


def test_mobile_thread_cancellation_waits_for_bounded_worker_cleanup(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def request(method, url, **kwargs):
        if method == "POST":
            return FakeResponse(200, {"access_token": "x" * 100, "expires_in": 86400})
        started.set()
        assert release.wait(timeout=5)
        return FakeResponse(200, THREAD_PAYLOAD)

    connector = RedditMobileConnector(
        request_fn=request,
        device_path=tmp_path / "device.json",
    )
    post = SocialItem(
        platform="reddit", post_id="abc123",
        url="https://www.reddit.com/r/stocks/comments/abc123/earnings_revision/",
    )

    async def run_probe():
        task = asyncio.create_task(
            connector.fetch_thread(post, max_comments=20, max_depth=2)
        )
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        completed_before_release = task.done()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        else:
            cancelled = False
        return completed_before_release, cancelled

    completed_before_release, cancelled = asyncio.run(run_probe())

    assert completed_before_release is False
    assert cancelled is True
