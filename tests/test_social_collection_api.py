from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apis.social_search_api import create_social_router
from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.broker import SourceBroker
from social_scraper.storage import ObservationStore


class APIConnector(BaseConnector):
    platform = "reddit"
    connector_name = "api-fixture"

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        return ConnectorResult(
            items=[SocialItem(platform="reddit", post_id="post-1", url="https://reddit.com/post-1", likes=4)],
            health=SourceHealth(
                platform="reddit", connector=self.connector_name, status="ok",
                items_returned=1, items_requested=count,
            ),
        )

    def can_handle_options(self, options):
        return isinstance(options, dict) and bool(options.get("subreddits"))

    async def search_with_options(
        self, keyword, count=20, time_filter="", sort="", region="", options=None
    ):
        return await self.search(keyword, count, time_filter, sort, region)

    async def health_check(self):
        return SourceHealth(platform="reddit", connector=self.connector_name, status="ok")


def test_query_registry_collection_and_history_are_available_via_api(tmp_path):
    broker = SourceBroker()
    broker.register(APIConnector())
    store = ObservationStore(tmp_path / "api.db")
    app = FastAPI()
    app.include_router(create_social_router(broker, store, admin_token="test-admin-token"))
    client = TestClient(app)
    admin_headers = {"X-Social-Admin-Token": "test-admin-token"}

    unauthorized = client.post("/social/queries", json={
        "keyword": "blocked",
        "platforms": ["reddit"],
        "region": "US",
        "interval_minutes": 60,
        "next_run_at": "2026-08-02T01:00:00Z",
    })
    assert unauthorized.status_code == 401

    created = client.post("/social/queries", headers=admin_headers, json={
        "keyword": "running shoes",
        "platforms": ["reddit"],
        "region": "US",
        "interval_minutes": 60,
        "next_run_at": "2026-08-02T01:00:00Z",
        "reddit": {"subreddits": ["stocks"]},
    })
    assert created.status_code == 201
    query_id = created.json()["id"]

    collected = client.post(
        f"/social/queries/{query_id}/collect",
        params={"at": "2026-08-02T02:00:00Z"},
        headers=admin_headers,
    )
    assert collected.status_code == 200
    assert collected.json()["collection_run_id"]

    history = client.get(
        "/social/history/reddit/post-1", headers=admin_headers
    )
    unauthorized_history = client.get("/social/history/reddit/post-1")
    assert history.status_code == 200
    assert unauthorized_history.status_code == 401
    assert history.json()["observations"][0]["likes"] == 4

    query = client.get(f"/social/queries/{query_id}", headers=admin_headers)
    assert query.json()["next_run_at"] == "2026-08-02T03:00:00+00:00"


def test_paid_search_fails_closed_when_payment_is_not_configured(tmp_path):
    broker = SourceBroker()
    broker.register(APIConnector())
    app = FastAPI()
    app.include_router(
        create_social_router(
            broker,
            ObservationStore(tmp_path / "closed.db"),
            paid_search_enabled=False,
        )
    )

    response = TestClient(app).post(
        "/social/search",
        json={"keyword": "running shoes", "platforms": ["reddit"]},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Paid social search is not configured"


def test_cached_search_serves_latest_snapshot_without_live_connector_call(tmp_path):
    class CountingConnector(APIConnector):
        def __init__(self):
            self.calls = 0

        async def search(self, *args, **kwargs):
            self.calls += 1
            return await super().search(*args, **kwargs)

    connector = CountingConnector()
    broker = SourceBroker()
    broker.register(connector)
    store = ObservationStore(tmp_path / "cache.db")
    store.record_collection(
        {
            "query": "python",
            "platforms": ["reddit"],
            "count": 1,
            "items": [{"platform": "reddit", "post_id": "cached", "url": "https://reddit.com/cached"}],
            "source_health": [],
        },
        ["reddit"],
        "US",
        collected_at=datetime.now(timezone.utc),
    )
    app = FastAPI()
    app.include_router(create_social_router(broker, store, paid_search_enabled=True))

    response = TestClient(app).post("/social/search/cached", json={
        "keyword": "python",
        "platforms": ["reddit"],
        "region": "US",
        "max_age_minutes": 60,
    })

    assert response.status_code == 200
    assert response.json()["cached"] is True
    assert response.json()["items"][0]["post_id"] == "cached"
    assert response.json()["cache"]["age_seconds"] >= 0
    assert connector.calls == 0


def test_cached_search_returns_404_when_no_fresh_snapshot_exists(tmp_path):
    broker = SourceBroker()
    broker.register(APIConnector())
    app = FastAPI()
    app.include_router(create_social_router(
        broker,
        ObservationStore(tmp_path / "empty-cache.db"),
        paid_search_enabled=True,
    ))

    response = TestClient(app).post("/social/search/cached", json={
        "keyword": "missing",
        "platforms": ["reddit"],
        "region": "US",
        "max_age_minutes": 60,
    })

    assert response.status_code == 404
    assert response.json()["detail"] == "No fresh cached social snapshot is available"


def test_request_level_reddit_scope_is_validated_and_reaches_scoped_connector(tmp_path):
    from social_scraper.connectors.reddit_arctic import RedditArcticConnector

    def fake_fetch(_request):
        return {"data": [{
            "id": "marketing1",
            "subreddit": "Marketing",
            "title": "Campaign signal",
            "retrieved_on": 1_700_000_100,
            "permalink": "/r/Marketing/comments/marketing1/campaign_signal/",
        }]}

    broker = SourceBroker()
    broker.register(RedditArcticConnector(subreddits=[], fetch_json=fake_fetch))
    app = FastAPI()
    app.include_router(create_social_router(
        broker, ObservationStore(tmp_path / "scope-api.db"), paid_search_enabled=True,
    ))
    client = TestClient(app)

    response = client.post("/social/search", json={
        "keyword": "campaign",
        "platforms": ["reddit"],
        "count": 1,
        "reddit": {"subreddits": ["Marketing"]},
    })
    invalid = client.post("/social/search", json={
        "keyword": "campaign",
        "platforms": ["reddit"],
        "reddit": {"subreddits": ["bad/name"]},
    })
    unsupported_hot = client.post("/social/search", json={
        "keyword": "campaign",
        "platforms": ["reddit"],
        "sort": "hot",
        "reddit": {"subreddits": ["Marketing"]},
    })
    irrelevant_scope = client.post("/social/search", json={
        "keyword": "campaign",
        "platforms": ["youtube"],
        "reddit": {"subreddits": ["Marketing"]},
    })
    unknown_platform = client.post("/social/search", json={
        "keyword": "campaign",
        "platforms": ["madeup"],
    })

    assert response.status_code == 200
    assert response.json()["items"][0]["provenance"]["subreddit"] == "Marketing"
    assert response.json()["platform_options"] == {
        "reddit": {"subreddits": ["Marketing"]}
    }
    assert invalid.status_code == 422
    assert unsupported_hot.status_code == 422
    assert irrelevant_scope.status_code == 422
    assert unknown_platform.status_code == 422


def test_query_registry_persists_request_level_reddit_scope(tmp_path):
    broker = SourceBroker()
    broker.register(APIConnector())
    store = ObservationStore(tmp_path / "query-scope-api.db")
    app = FastAPI()
    app.include_router(create_social_router(broker, store, admin_token="admin"))

    unscoped = TestClient(app).post(
        "/social/queries",
        headers={"X-Social-Admin-Token": "admin"},
        json={
            "keyword": "pricing",
            "platforms": ["reddit"],
            "region": "US",
            "interval_minutes": 60,
            "next_run_at": "2026-08-02T01:00:00Z",
        },
    )
    response = TestClient(app).post(
        "/social/queries",
        headers={"X-Social-Admin-Token": "admin"},
        json={
            "keyword": "pricing",
            "platforms": ["reddit"],
            "region": "US",
            "interval_minutes": 60,
            "next_run_at": "2026-08-02T01:00:00Z",
            "reddit": {"subreddits": ["Marketing", "marketing"]},
        },
    )

    assert unscoped.status_code == 422
    assert response.status_code == 201
    assert response.json()["platform_options"] == {
        "reddit": {"subreddits": ["marketing"]}
    }


def test_reddit_depth_endpoints_return_feed_and_hydrated_post(tmp_path):
    from social_scraper.connectors.reddit_camoufox import RedditCamoufoxConnector

    def fake_scan(subreddits, keyword, count, time_filter, sort):
        return [{
            "id": "t3_abc",
            "title": "Python packaging discussion",
            "permalink": "/r/Python/comments/abc/packaging/",
            "score": 7,
            "comments": 3,
            "created": "2026-08-02T01:00:00Z",
            "subreddit": subreddits[0],
        }]

    def fake_post(url, comment_limit):
        return {"url": url, "title": "Python packaging discussion", "body": "Body", "comments": [{"text": "Comment"}]}

    depth = RedditCamoufoxConnector(subreddits=["Python"], scan_fn=fake_scan, post_fn=fake_post)
    broker = SourceBroker()
    broker.register(APIConnector())
    app = FastAPI()
    app.include_router(create_social_router(
        broker,
        ObservationStore(tmp_path / "reddit.db"),
        paid_search_enabled=True,
        reddit_depth_connector=depth,
    ))
    client = TestClient(app)

    feed = client.get("/social/reddit/feed", params={"subreddit": "Python", "keyword": "python packaging", "sort": "latest"})
    post = client.get("/social/reddit/post", params={"url": "https://www.reddit.com/r/Python/comments/abc/packaging/"})

    assert feed.status_code == 200
    assert feed.json()["items"][0]["post_id"] == "abc"
    assert feed.json()["items"][0]["subreddit"] == "Python"
    assert post.status_code == 200
    assert post.json()["comments"][0]["text"] == "Comment"
