import asyncio
import time

import pytest

from social_scraper.connectors.reddit_camoufox import (
    CamoufoxChallengeError,
    RedditCamoufoxConnector,
    _camoufox_launch_options,
    is_reddit_challenge_page,
    normalize_feed_posts,
    validate_hydrated_post_identity,
    validate_reddit_url,
)
from social_scraper.broker import SourceBroker


def test_normalize_feed_posts_uses_canonical_permalink_and_preserves_zero_counts():
    raw = [
        {
            "id": "t3_abc123",
            "title": "Running shoe durability discussion",
            "permalink": "/r/RunningShoeGeeks/comments/abc123/durability/",
            "score": 0,
            "comments": 0,
            "created": "2026-08-02T01:00:00.000000+0000",
            "subreddit": "RunningShoeGeeks",
        },
        {
            "id": "t3_abc123",
            "title": "duplicate",
            "permalink": "/r/RunningShoeGeeks/comments/abc123/durability/",
            "score": 99,
            "comments": 99,
            "created": "2026-08-02T01:00:00.000000+0000",
            "subreddit": "RunningShoeGeeks",
        },
    ]

    items = normalize_feed_posts(raw, keyword="running shoe", count=10)

    assert len(items) == 1
    assert items[0].post_id == "abc123"
    assert items[0].url == "https://www.reddit.com/r/RunningShoeGeeks/comments/abc123/durability/"
    assert items[0].likes == 0
    assert items[0].comments == 0
    assert items[0].created_at == "2026-08-02T01:00:00+00:00"


def test_keyword_filter_does_not_turn_punctuation_into_match_all():
    raw = [{
        "title": "Unrelated market discussion",
        "permalink": "/r/stocks/comments/abc123/market/",
        "created": "2026-08-02T01:00:00Z",
    }]

    assert normalize_feed_posts(raw, keyword="+++") == []
    assert normalize_feed_posts(raw, keyword="R") == []


def test_camoufox_connector_scans_configured_subreddits_in_worker():
    calls = []

    def fake_scan(subreddits, keyword, count, time_filter, sort):
        calls.append((subreddits, keyword, count, time_filter, sort))
        return [{
            "id": "post1",
            "title": "Battery problem on my phone",
            "permalink": "/r/Android/comments/post1/battery_problem/",
            "score": 4,
            "comments": 2,
            "created": "2026-08-02T01:00:00Z",
            "subreddit": "Android",
        }]

    connector = RedditCamoufoxConnector(subreddits=["Android"], scan_fn=fake_scan)
    result = asyncio.run(connector.search("battery problem", count=5, time_filter="week", sort="latest"))

    assert calls == [(["Android"], "battery problem", 5, "week", "latest")]
    assert result.health.status == "ok"
    assert result.items[0].post_id == "post1"

    feed_result = asyncio.run(connector.get_feed("Android", keyword="battery problem", count=2, sort="latest"))
    assert calls[-1] == (["Android"], "battery problem", 2, "", "latest")
    assert feed_result.health.status == "ok"


def test_camoufox_connector_reports_partial_without_subreddit_scope():
    connector = RedditCamoufoxConnector(subreddits=[])

    result = asyncio.run(connector.search("anything"))

    assert result.items == []
    assert result.health.status == "partial"
    assert result.health.error == "subreddit_scope_not_configured"


def test_camoufox_uses_configured_proxy_without_logging_credentials(monkeypatch):
    monkeypatch.setenv("BOUNTY_PROXY_SERVER", "http://proxy.example:9000")
    monkeypatch.setenv("BOUNTY_PROXY_USERNAME", "user")
    monkeypatch.setenv("BOUNTY_PROXY_PASSWORD", "secret")
    monkeypatch.delenv("BOUNTY_REDDIT_PROXY_SERVER", raising=False)

    assert _camoufox_launch_options() == {
        "headless": True,
        "proxy": {
            "server": "http://proxy.example:9000",
            "username": "user",
            "password": "secret",
        },
    }

    monkeypatch.setenv("BOUNTY_REDDIT_PROXY_SERVER", "http://reddit-proxy.example:9001")
    assert _camoufox_launch_options()["proxy"]["server"] == "http://reddit-proxy.example:9001"


def test_validate_reddit_url_accepts_only_public_reddit_post_urls():
    assert validate_reddit_url("https://www.reddit.com/r/test/comments/abc/title/")
    assert validate_reddit_url("https://reddit.com/r/test/comments/abc/title/")
    with pytest.raises(ValueError):
        validate_reddit_url("https://evil.example/reddit.com/r/test/comments/abc/title/")
    with pytest.raises(ValueError):
        validate_reddit_url("https://www.reddit.com/settings/account")
    with pytest.raises(ValueError):
        validate_reddit_url("https://www.reddit.com/r/test/comments/abc/title/%2e%2e/private")
    with pytest.raises(ValueError):
        validate_reddit_url("https://www.reddit.com/r/test/comments/abc/title/extra")


def test_feed_rejects_subreddit_outside_configured_allowlist():
    connector = RedditCamoufoxConnector(subreddits=["Python"], scan_fn=lambda *args: [])

    with pytest.raises(ValueError):
        asyncio.run(connector.get_feed("WallStreetBets"))


def test_connector_times_out_slow_browser_operation():
    def slow_scan(*args):
        time.sleep(0.2)
        return []

    connector = RedditCamoufoxConnector(
        subreddits=["Python"],
        scan_fn=slow_scan,
        operation_timeout_seconds=0.01,
    )

    result = asyncio.run(connector.search("python"))

    assert result.health.status == "error"
    assert result.health.error == "camoufox_timeout"


def test_reddit_challenge_page_is_detected_instead_of_returning_empty_data():
    assert is_reddit_challenge_page("Reddit - Please wait for verification", "")
    assert is_reddit_challenge_page("Reddit", "You've been blocked by network security")
    assert not is_reddit_challenge_page("Sunday Daily Thread : r/Python", "Useful post body")


def test_challenged_collection_reports_explicit_safe_health_code():
    def challenged_scan(*args):
        raise CamoufoxChallengeError("Reddit verification challenge")

    connector = RedditCamoufoxConnector(subreddits=["Python"], scan_fn=challenged_scan)
    result = asyncio.run(connector.search("python"))

    assert result.health.status == "error"
    assert result.health.error == "camoufox_verification_challenge"


def test_scoped_feed_rejects_cross_subreddit_permalink():
    def cross_scope_scan(*args):
        return [{
            "title": "Python packaging discussion",
            "permalink": "/r/WallStreetBets/comments/abc/packaging/",
            "score": 7,
            "comments": 3,
            "created": "2026-08-02T01:00:00Z",
            "subreddit": "Python",
        }]

    connector = RedditCamoufoxConnector(subreddits=["Python"], scan_fn=cross_scope_scan)
    result = asyncio.run(connector.get_feed("Python", keyword="python packaging"))

    assert result.items == []
    assert result.health.status == "partial"


def test_hydration_requires_matching_post_identity_and_title():
    canonical = "https://www.reddit.com/r/Python/comments/abc/packaging/"
    assert validate_hydrated_post_identity(canonical, "/r/Python/comments/abc/packaging/", "Packaging")
    with pytest.raises(RuntimeError):
        validate_hydrated_post_identity(canonical, "", "")
    with pytest.raises(RuntimeError):
        validate_hydrated_post_identity(canonical, "/r/Python/comments/wrong/packaging/", "Packaging")


def test_cancelled_queued_operation_does_not_leak_camoufox_gate():
    def slow_scan(subreddits, keyword, count, time_filter, sort):
        time.sleep(0.08)
        return [{
            "title": "Python gate test",
            "permalink": "/r/Python/comments/gate/gate_test/",
            "score": 1,
            "comments": 0,
            "created": "2026-08-02T01:00:00Z",
            "subreddit": "Python",
        }]

    connector = RedditCamoufoxConnector(
        subreddits=["Python"],
        scan_fn=slow_scan,
        operation_timeout_seconds=1,
        queue_timeout_seconds=0.2,
    )

    async def scenario():
        first = asyncio.create_task(connector.search("python gate"))
        await asyncio.sleep(0.01)
        queued = asyncio.create_task(connector.search("python gate"))
        await asyncio.sleep(0.01)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await first
        return await connector.search("python gate")

    final = asyncio.run(scenario())
    assert final.health.status == "ok"


def test_cancelling_running_operation_keeps_gate_until_worker_exits():
    active = 0
    max_active = 0

    def slow_scan(subreddits, keyword, count, time_filter, sort):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        time.sleep(0.1)
        active -= 1
        return [{
            "title": "Python running cancellation",
            "permalink": "/r/Python/comments/run/running_cancellation/",
            "score": 1,
            "comments": 0,
            "created": "2026-08-02T01:00:00Z",
            "subreddit": "Python",
        }]

    connector = RedditCamoufoxConnector(
        subreddits=["Python"],
        scan_fn=slow_scan,
        operation_timeout_seconds=1,
        queue_timeout_seconds=0.02,
    )

    async def scenario():
        running = asyncio.create_task(connector.search("python running cancellation"))
        await asyncio.sleep(0.02)
        running.cancel()
        await asyncio.sleep(0.01)
        running.cancel()
        contender = await connector.search("python running cancellation")
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0.12)
        final = await connector.search("python running cancellation")
        return contender, final

    contender, final = asyncio.run(scenario())
    assert max_active == 1
    assert contender.health.error == "camoufox_busy"
    assert final.health.status == "ok"


def test_scheduled_camoufox_honors_exact_request_scope_and_collects_both_feeds():
    calls = []

    def fake_scan(subreddits, keyword, count, time_filter, sort):
        calls.append((subreddits, keyword, count, time_filter, sort))
        return [{
            "title": "Earnings revisions are accelerating",
            "permalink": "/r/stocks/comments/signal1/earnings_revisions/",
            "score": 12,
            "comments": 4,
            "created": "2026-08-03T01:00:00Z",
            "feed": "new",
        }]

    connector = RedditCamoufoxConnector(
        subreddits=["stocks", "investing", "Python"],
        scan_fn=fake_scan,
    )
    result = asyncio.run(connector.search_with_options(
        "earnings",
        count=10,
        time_filter="week",
        sort="",
        options={"subreddits": ["stocks", "investing"]},
    ))

    assert calls == [(["stocks", "investing"], "earnings", 10, "week", "")]
    assert result.health.status == "ok"
    assert result.health.coverage == {
        "global_coverage": False,
        "requested_subreddits": ["stocks", "investing"],
        "searched_subreddits": ["stocks", "investing"],
        "feeds": ["rising", "new"],
    }
    assert result.items[0].raw["subreddit"] == "stocks"


def test_scheduled_camoufox_skips_scope_outside_operator_allowlist():
    connector = RedditCamoufoxConnector(subreddits=["Python"], scan_fn=lambda *args: [])

    result = asyncio.run(connector.search_with_options(
        "earnings",
        options={"subreddits": ["stocks"]},
    ))

    assert result.items == []
    assert result.health.status == "skipped"
    assert result.health.error == "subreddit_scope_not_allowed"
    assert result.health.coverage["searched_subreddits"] == []


def test_collection_broker_skips_camoufox_for_unscoped_query():
    calls = []

    def fake_scan(*args):
        calls.append(args)
        return []

    broker = SourceBroker()
    broker.register(
        RedditCamoufoxConnector(subreddits=["stocks"], scan_fn=fake_scan),
        priority=5,
    )
    response = asyncio.run(broker.search("earnings", platforms=["reddit"]))

    assert calls == []
    assert response["source_health"][0]["status"] == "skipped"
