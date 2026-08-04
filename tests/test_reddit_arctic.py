import asyncio
import urllib.parse

from apis.social_search_api import build_collection_broker, build_default_broker
from social_scraper.connectors.reddit_arctic import RedditArcticConnector


def test_arctic_connector_searches_configured_subreddits_and_preserves_archive_timestamp():
    requests = []

    def fake_fetch(request):
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        requests.append(params)
        subreddit = params["subreddit"][0]
        if subreddit.lower() == "python":
            return {"data": [{
                "id": "ABC123",
                "subreddit": "Python",
                "title": "Packaging discussion",
                "selftext": "A verified archived body",
                "author": "alice",
                "created_utc": 1_700_000_000,
                "retrieved_on": 1_700_000_100,
                "score": 12,
                "num_comments": 3,
                "permalink": "/r/Python/comments/abc123/packaging_discussion/",
                "link_flair_text": "Discussion",
            }]}
        return {"data": [{
            "id": "abc123",
            "subreddit": "learnpython",
            "title": "Duplicate archive record",
            "created_utc": 1_700_000_000,
            "retrieved_on": 1_700_000_200,
            "score": 10,
            "num_comments": 2,
            "permalink": "/r/learnpython/comments/abc123/duplicate/",
        }]}

    connector = RedditArcticConnector(
        subreddits=["Python", "learnpython"],
        fetch_json=fake_fetch,
        clock=lambda: 2_000_000_000,
    )
    result = asyncio.run(connector.search("packaging", count=5, time_filter="week"))

    assert result.health.status == "partial"
    assert result.health.coverage == {
        "kind": "configured_subreddits",
        "requested_subreddits": ["Python", "learnpython"],
        "successful_subreddits": ["Python", "learnpython"],
        "global_coverage": False,
        "source_kind": "archive",
    }
    assert len(result.items) == 1
    item = result.items[0]
    assert item.post_id == "abc123"
    assert item.url == "https://www.reddit.com/r/Python/comments/abc123/packaging_discussion/"
    assert item.likes == 12
    assert item.comments == 3
    assert item.raw["source_observed_at"] == "2023-11-14T22:15:00+00:00"
    assert item.raw["subreddit"] == "Python"
    assert all(params["query"] == ["packaging"] for params in requests)
    assert all(params["after"] == ["1999395200"] for params in requests)
    assert all(params["sort"] == ["desc"] for params in requests)


def test_arctic_connector_rejects_noncanonical_or_wrong_subreddit_records():
    def fake_fetch(_request):
        return {"data": [
            {
                "id": "good1",
                "subreddit": "Python",
                "title": "Good",
                "permalink": "/r/Python/comments/good1/good/",
            },
            {
                "id": "bad1",
                "subreddit": "Python",
                "title": "Bad path",
                "permalink": "https://example.com/r/Python/comments/bad1/bad/",
            },
            {
                "id": "bad2",
                "subreddit": "notpython",
                "title": "Wrong community",
                "permalink": "/r/notpython/comments/bad2/wrong/",
            },
        ]}

    result = asyncio.run(RedditArcticConnector(
        subreddits=["Python"],
        fetch_json=fake_fetch,
    ).search("good", count=5))

    assert [item.post_id for item in result.items] == ["good1"]
    assert result.items[0].likes is None
    assert result.items[0].comments is None


def test_arctic_archive_timestamp_flows_through_broker_and_storage(tmp_path):
    from social_scraper.broker import SourceBroker
    from social_scraper.storage import ObservationStore

    def fake_fetch(_request):
        return {"data": [{
            "id": "archive1",
            "subreddit": "Python",
            "title": "Archived",
            "created_utc": 1_700_000_000,
            "retrieved_on": 1_700_000_100,
            "score": 9,
            "num_comments": 2,
            "permalink": "/r/Python/comments/archive1/archived/",
        }]}

    broker = SourceBroker()
    broker.register(RedditArcticConnector(subreddits=["Python"], fetch_json=fake_fetch))
    response = asyncio.run(broker.search("archived", platforms=["reddit"], count=1))

    provenance = response["items"][0]["provenance"]
    assert provenance["source_kind"] == "archive"
    assert provenance["source_observed_at"] == "2023-11-14T22:15:00+00:00"
    assert response["platform_results"]["reddit"]["coverage"]["global_coverage"] is False
    assert response["platform_results"]["reddit"]["coverage"]["successful_subreddits"] == ["Python"]
    assert response["platform_results"]["reddit"]["data_quality"] == {
        "items": 1,
        "created_at_present": 1,
        "source_observed_at_present": 1,
        "newest_created_at": "2023-11-14T22:13:20+00:00",
        "newest_source_observed_at": "2023-11-14T22:15:00+00:00",
    }

    store = ObservationStore(tmp_path / "arctic.db")
    store.record_collection(response, ["reddit"])
    history = store.get_observation_history("reddit", "archive1")
    assert history[0]["observed_at"] == "2023-11-14T22:15:00+00:00"
    assert history[0]["likes"] == 9


def test_arctic_connector_fails_explicitly_without_subreddit_scope():
    result = asyncio.run(RedditArcticConnector(subreddits=[]).search("python", count=5))

    assert result.items == []
    assert result.health.status == "error"
    assert result.health.error == "missing_subreddit_scope"
    assert result.health.coverage["global_coverage"] is False


def test_arctic_rejects_corrupt_archive_types_and_does_not_fabricate_identity():
    def fake_fetch(_request):
        return {"data": [{
            "id": "types1",
            "subreddit": "Python",
            "title": "Types",
            "author": "[deleted]",
            "created_utc": "1e999",
            "retrieved_on": False,
            "score": True,
            "num_comments": False,
            "post_hint": "image",
            "link_flair_text": "Help",
            "permalink": "/r/Python/comments/types1/types/",
        }]}

    item = asyncio.run(RedditArcticConnector(
        subreddits=["Python"], fetch_json=fake_fetch,
    ).search("types", count=1)).items[0]

    assert item.created_at is None
    assert item.likes is None
    assert item.comments is None
    assert item.author_username == ""
    assert item.author_profile_url == ""
    assert item.media_type == "image"
    assert item.hashtags == []
    assert item.raw["flair"] == "Help"


def test_arctic_rejects_unsupported_sort_instead_of_simulating_hot_results():
    result = asyncio.run(RedditArcticConnector(
        subreddits=["Python"], fetch_json=lambda _request: {"data": []},
    ).search("python", count=5, sort="hot"))

    assert result.health.status == "error"
    assert result.health.error == "unsupported_sort"


def test_request_scope_overrides_default_arctic_scope_and_skips_unscoped_routes():
    from social_scraper.base import BaseConnector, ConnectorResult, SourceHealth
    from social_scraper.broker import SourceBroker

    class UnscopedConnector(BaseConnector):
        platform = "reddit"
        connector_name = "unscoped"

        def __init__(self):
            self.calls = 0

        async def search(self, keyword, count=20, time_filter="", sort="", region=""):
            self.calls += 1
            return ConnectorResult([], SourceHealth("reddit", self.connector_name, "partial"))

        async def health_check(self):
            return SourceHealth("reddit", self.connector_name, "ok")

    captured = []

    def fake_fetch(request):
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        captured.append(params["subreddit"][0])
        return {"data": [{
            "id": "scope1",
            "subreddit": "Marketing",
            "title": "Scoped",
            "retrieved_on": 1_700_000_100,
            "permalink": "/r/Marketing/comments/scope1/scoped/",
        }]}

    primary = UnscopedConnector()
    arctic = RedditArcticConnector(subreddits=["Python"], fetch_json=fake_fetch)
    broker = SourceBroker()
    broker.register(primary, priority=10)
    broker.register(arctic, priority=20)

    response = asyncio.run(broker.search(
        "campaign",
        platforms=["reddit"],
        count=1,
        platform_options={"reddit": {"subreddits": ["Marketing"]}},
    ))

    assert primary.calls == 0
    assert captured == ["Marketing"]
    assert response["items"][0]["provenance"]["subreddit"] == "Marketing"
    assert response["platform_results"]["reddit"]["coverage"]["successful_subreddits"] == ["Marketing"]
    assert response["source_health"][0]["status"] == "skipped"


def test_scoped_outage_is_error_not_partial_when_other_routes_are_ineligible():
    from social_scraper.base import BaseConnector, ConnectorResult, SourceHealth
    from social_scraper.broker import SourceBroker

    class UnscopedConnector(BaseConnector):
        platform = "reddit"
        connector_name = "unscoped"

        async def search(self, keyword, count=20, time_filter="", sort="", region=""):
            return ConnectorResult([], SourceHealth("reddit", self.connector_name, "partial"))

        async def health_check(self):
            return SourceHealth("reddit", self.connector_name, "ok")

    def failed_fetch(_request):
        raise RuntimeError("upstream failed")

    broker = SourceBroker()
    broker.register(UnscopedConnector(), priority=10)
    broker.register(RedditArcticConnector(subreddits=[], fetch_json=failed_fetch), priority=20)
    response = asyncio.run(broker.search(
        "earnings",
        platforms=["reddit"],
        platform_options={"reddit": {"subreddits": ["stocks"]}},
    ))

    assert response["count"] == 0
    assert response["platform_results"]["reddit"]["status"] == "error"
    assert [item["status"] for item in response["source_health"]] == ["skipped", "error"]


def test_default_broker_orders_arctic_before_brave_when_scope_is_configured(monkeypatch):
    monkeypatch.setenv("BOUNTY_REDDIT_SUBREDDITS", "Python,learnpython")
    monkeypatch.setenv("BOUNTY_BRAVE_SEARCH_API_KEY", "configured-secret")

    routes = build_default_broker().list_routes()["reddit"]

    assert routes == [
        {"connector": "pullpush_free", "priority": 10},
        {"connector": "arctic_shift_scoped", "priority": 20},
        {"connector": "brave_reddit_discovery", "priority": 30},
    ]


def test_default_broker_keeps_dynamic_arctic_route_but_rejects_invalid_default_scope(monkeypatch):
    monkeypatch.setenv("BOUNTY_REDDIT_SUBREDDITS", "***,x")
    monkeypatch.delenv("BOUNTY_BRAVE_SEARCH_API_KEY", raising=False)

    broker = build_default_broker()
    assert broker.list_routes()["reddit"] == [
        {"connector": "pullpush_free", "priority": 10},
        {"connector": "arctic_shift_scoped", "priority": 20},
    ]
    connector = broker._routes["reddit"][1].connector
    arctic = asyncio.run(connector.health_check()).to_dict()
    assert arctic["status"] == "skipped"


def test_collection_broker_adds_camoufox_without_changing_live_route_order(monkeypatch):
    monkeypatch.setenv("BOUNTY_REDDIT_SUBREDDITS", "stocks,investing")
    monkeypatch.delenv("BOUNTY_BRAVE_SEARCH_API_KEY", raising=False)

    live_routes = build_default_broker().list_routes()["reddit"]
    collection_routes = build_collection_broker().list_routes()["reddit"]

    assert live_routes[0] == {"connector": "pullpush_free", "priority": 10}
    assert collection_routes[:3] == [
        {"connector": "reddit_mobile_owned", "priority": 1},
        {"connector": "reddit_atom_scoped", "priority": 3},
        {"connector": "camoufox_depth", "priority": 5},
    ]
    assert {route["connector"] for route in collection_routes} == {
        "reddit_mobile_owned",
        "reddit_atom_scoped",
        "camoufox_depth",
        "pullpush_free",
        "arctic_shift_scoped",
    }
