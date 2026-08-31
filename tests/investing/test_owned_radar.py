import asyncio

from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord
import social_scraper.investing.owned_radar as owned_radar_module
from social_scraper.investing.owned_radar import (
    AdaptiveCollectionBudget,
    OwnedRadarCollector,
    SOURCE_QUERY_RECIPE_VERSION,
    _source_status,
    check_movement_bundles,
    panel_platform_query,
)
from social_scraper.investing.private_radar import DEFAULT_PANELS


def test_source_native_panel_queries_are_short_and_platform_specific():
    panel = DEFAULT_PANELS[0]

    assert panel_platform_query(panel, "reddit") == "car"
    assert panel_platform_query(panel, "tiktok") == "switching car"
    assert panel_platform_query(panel, "instagram") == "car problem"
    assert panel_platform_query(panel, "youtube") == "why I switched car"
    assert len({
        panel_platform_query(panel, platform)
        for platform in ("reddit", "tiktok", "instagram", "youtube")
    }) == 4
    assert SOURCE_QUERY_RECIPE_VERSION == "camillo-source-queries/2"


def test_source_status_does_not_attach_an_unused_fallback_error_to_a_success():
    status, error = _source_status(
        {
            "platform": "reddit",
            "status": "ok",
            "selected_connector": "reddit_mobile_owned",
        },
        [
            {
                "platform": "reddit",
                "connector": "reddit_atom_scoped",
                "status": "error",
                "error": "connector_error",
            },
            {
                "platform": "reddit",
                "connector": "reddit_mobile_owned",
                "status": "ok",
                "error": None,
            },
        ],
    )

    assert status == "complete"
    assert error is None


def test_source_status_fails_closed_when_the_selected_connector_errored():
    status, error = _source_status(
        {
            "platform": "reddit",
            "status": "ok",
            "selected_connector": "reddit_mobile_owned",
            "error": "connector_timeout",
        },
        [{
            "platform": "reddit",
            "connector": "reddit_mobile_owned",
            "status": "error",
            "error": "connector_timeout",
        }],
    )

    assert status == "partial"
    assert error == "connector_timeout"


def test_source_status_fails_closed_when_selected_connector_health_is_missing():
    status, error = _source_status(
        {
            "platform": "reddit",
            "status": "ok",
            "selected_connector": "reddit_mobile_owned",
        },
        [],
    )

    assert status == "partial"
    assert error == "selected_connector_health_missing"


class FakeX:
    def __init__(self):
        self.queries = []
        self.search_calls = []

    async def search(self, query, count=20, time_filter="", sort="", region=""):
        self.queries.append(query)
        self.search_calls.append({
            "query": query,
            "count": count,
            "time_filter": time_filter,
            "sort": sort,
            "region": region,
        })
        created_at = "2026-08-26T00:00:00Z"
        since_token = next(
            (token for token in query.split() if token.startswith("since:")),
            None,
        )
        if since_token:
            created_at = f"{since_token.split(':', 1)[1]}T12:00:00Z"
        item = SocialItem(
            platform="x",
            post_id=f"x{len(self.queries)}",
            url=f"https://x.com/u/status/{len(self.queries)}",
            author_username=f"u{len(self.queries)}",
            text="I switched to a silicone air fryer liner",
            created_at=created_at,
            views=2500,
            likes=25,
            comments=4,
            shares=2,
        )
        return ConnectorResult(
            items=[item],
            health=SourceHealth(
                platform="x", connector="x_scweet", status="ok",
                items_returned=1, items_requested=count,
                coverage={"requested_limit_reached": False},
            ),
        )


class NoDiscoveryBroker:
    async def search(self, **_kwargs):
        raise AssertionError("wide discovery must remain X-only")


class FakeBroker:
    def __init__(self):
        self.search_calls = []

    async def search(self, *, keyword, platforms, count, time_filter, sort):
        platform = platforms[0]
        self.search_calls.append({
            "keyword": keyword,
            "platform": platform,
            "count": count,
            "time_filter": time_filter,
            "sort": sort,
        })
        return {
            "items": [{
                "platform": platform,
                "post_id": f"{platform}-1",
                "url": f"https://example.com/{platform}-1",
                "author": {"username": f"{platform}-author"},
                "text": "I switched to a silicone air fryer liner",
                "created_at": "2026-08-26T00:00:00Z",
                "engagement": {"comments": 0, "likes": 1},
            }],
            "platform_results": {platform: {"status": "ok", "coverage": {}}},
            "source_health": [{"platform": platform, "status": "ok"}],
        }

    async def fetch_thread(self, *_args, **_kwargs):
        class Thread:
            records = ()
            status = "empty"
            error_category = None
        return Thread()


def test_owned_collector_builds_four_comparable_x_windows():
    x = FakeX()
    collector = OwnedRadarCollector(broker=FakeBroker(), x_connector=x)
    result = asyncio.run(collector.collect_windows(
        DEFAULT_PANELS[0], ["silicone air fryer liner"]
    ))
    assert [window["window_key"] for window in result["windows"]] == [
        "current", "prior_1", "prior_2", "prior_3",
    ]
    assert all(window["status"] == "complete" for window in result["windows"])
    assert all("since:" in query and "until:" in query for query in x.queries)
    assert len(result["evidence"]) == 4


def test_historical_windows_count_only_normalized_records_inside_each_interval():
    class StaleWindowX(FakeX):
        async def search(self, query, count=20, time_filter="", sort="", region=""):
            self.queries.append(query)
            self.search_calls.append({
                "query": query,
                "count": count,
                "time_filter": time_filter,
                "sort": sort,
                "region": region,
            })
            item = SocialItem(
                platform="x",
                post_id=f"stale-{len(self.queries)}",
                url=f"https://x.com/u/status/stale-{len(self.queries)}",
                author_username=f"u{len(self.queries)}",
                text="I switched to a silicone air fryer liner",
                created_at="2021-01-01T00:00:00Z",
            )
            return ConnectorResult(
                items=[item],
                health=SourceHealth(
                    platform="x",
                    connector="x_scweet",
                    status="ok",
                    items_returned=1,
                    items_requested=count,
                    coverage={"requested_limit_reached": False},
                ),
            )

    result = asyncio.run(OwnedRadarCollector(
        broker=FakeBroker(), x_connector=StaleWindowX()
    ).collect_windows(DEFAULT_PANELS[0], ["silicone air fryer liner"]))

    assert result["evidence"] == []
    assert all(window["result_count"] == 0 for window in result["windows"])
    assert all(window["unique_authors"] == 0 for window in result["windows"])
    assert all(window["status"] == "partial" for window in result["windows"])
    assert all(window["out_of_window_count"] == 1 for window in result["windows"])


def test_historical_window_calls_compete_for_the_shared_adaptive_budget():
    x = FakeX()
    budget = AdaptiveCollectionBudget(
        max_attempts=2,
        per_platform_limits={"x": 2},
    )

    result = asyncio.run(OwnedRadarCollector(
        broker=FakeBroker(), x_connector=x
    ).collect_windows(
        DEFAULT_PANELS[0],
        ["silicone air fryer liner"],
        budget=budget,
    ))

    assert len(x.search_calls) == 2
    assert budget.snapshot()["used_attempts"] == 2
    assert [window["status"] for window in result["windows"]] == [
        "complete", "complete", "partial", "partial",
    ]
    assert result["windows"][2]["error_category"] == "adaptive_budget_exhausted"
    assert result["sources"][3]["coverage"]["budget"]["remaining_attempts"] == 0


def test_owned_discovery_runs_all_platforms_before_shortlisting():
    broker = FakeBroker()
    collector = OwnedRadarCollector(broker=broker, x_connector=FakeX())
    result = asyncio.run(collector.collect_discovery(DEFAULT_PANELS[0]))
    assert {source["platform"] for source in result["sources"]} == {
        "x", "tiktok", "instagram", "reddit", "youtube",
    }
    assert len(result["sources"]) == 8
    assert len(result["evidence"]) == 8
    assert len(collector.x_connector.queries) == 4
    assert all("engagement" in item for item in result["evidence"])
    x_evidence = [item for item in result["evidence"] if item["platform"] == "x"]
    assert x_evidence[0]["engagement"] == {
        "views": 2500, "likes": 25, "comments": 4, "shares": 2,
        "collects": None, "upvotes": None, "replies": None,
        "reposts": None, "bookmarks": None,
    }
    assert {call["platform"] for call in broker.search_calls} == {
        "tiktok", "instagram", "reddit", "youtube",
    }
    assert all(source["stage"] == "discovery" for source in result["sources"])
    assert all(call["time_filter"] == "halfyear" for call in collector.x_connector.search_calls)
    assert all(call["time_filter"] == "halfyear" for call in broker.search_calls)
    reddit_call = next(call for call in broker.search_calls if call["platform"] == "reddit")
    assert reddit_call["keyword"] == "car"


def test_owned_discovery_records_a_failed_x_scope_and_finishes_the_panel():
    class FirstXCallFails(FakeX):
        async def search(self, query, count=20, time_filter="", sort="", region=""):
            if not self.search_calls:
                self.search_calls.append({
                    "query": query, "count": count, "time_filter": time_filter,
                    "sort": sort, "region": region,
                })
                raise TimeoutError("temporary scope timeout")
            return await super().search(
                query, count=count, time_filter=time_filter, sort=sort, region=region
            )

    result = asyncio.run(OwnedRadarCollector(
        broker=FakeBroker(), x_connector=FirstXCallFails()
    ).collect_discovery(DEFAULT_PANELS[0]))

    x_receipts = [source for source in result["sources"] if source["platform"] == "x"]
    assert len(x_receipts) == 4
    assert x_receipts[0]["status"] == "failed"
    assert x_receipts[0]["error_category"] == "TimeoutError"
    assert all(source["status"] == "complete" for source in x_receipts[1:])
    assert len(result["evidence"]) == 7


def test_owned_corroboration_includes_reddit_with_source_receipts():
    collector = OwnedRadarCollector(broker=FakeBroker(), x_connector=FakeX())

    result = asyncio.run(collector.collect_corroboration(
        DEFAULT_PANELS[0], ["silicone air fryer liner"]
    ))

    assert {source["platform"] for source in result["sources"]} == {
        "tiktok", "instagram", "reddit", "youtube",
    }
    assert len(result["evidence"]) == 4
    assert all(source["stage"] == "corroboration" for source in result["sources"])


def test_adaptive_collection_is_bounded_and_preserves_thread_lineage():
    class AdaptiveBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.thread_calls = []

        async def search(self, *, keyword, platforms, count, time_filter, sort):
            platform = platforms[0]
            self.search_calls.append({
                "keyword": keyword, "platform": platform, "count": count,
                "time_filter": time_filter, "sort": sort,
            })
            items = [
                {
                    "platform": platform,
                    "post_id": f"{platform}-{keyword}-{index}",
                    "url": f"https://example.com/{platform}-{index}",
                    "author": {"username": f"{platform}-author-{index}"},
                    "text": f"I switched to {keyword}",
                    "created_at": "2026-08-30T00:00:00Z",
                    "engagement": {
                        "comments": (50 - index) if platform == "reddit" else (10 - index),
                        "likes": 5,
                    },
                }
                for index in range(3)
            ]
            return {
                "items": items,
                "platform_results": {platform: {"status": "ok", "coverage": {}}},
                "source_health": [{"platform": platform, "status": "ok"}],
            }

        async def fetch_thread(self, root, *, max_comments, max_depth):
            self.thread_calls.append({
                "platform": root["platform"], "root": root["post_id"],
                "max_comments": max_comments, "max_depth": max_depth,
            })
            record = ThreadRecord(
                platform=root["platform"],
                external_id=f"comment-{root['post_id']}",
                record_type="comment",
                parent_external_id=root["post_id"],
                root_post_external_id=root["post_id"],
                depth=1,
                text="I bought one too",
                author_external_id="comment-author",
                author_username="commenter",
                url=f"{root['url']}#comment",
            )
            return ThreadFetchResult(
                platform=root["platform"],
                root_post_external_id=root["post_id"],
                status="partial",
                records=(record,),
                truncated=True,
                attempted_route="fixture_comments",
                platform_reported_total=50,
                max_comments=max_comments,
                max_depth=max_depth,
                limitations=("bounded",),
            )

    broker = AdaptiveBroker()
    collector = OwnedRadarCollector(broker=broker, x_connector=FakeX())
    anchors = [{
        "anchor_id": "anchor-1",
        "normalized_anchor": "silicone air fryer liner",
        "seed_query": "household products",
    }, {
        "anchor_id": "anchor-2",
        "normalized_anchor": "enzyme laundry sheet",
        "seed_query": "household products",
    }]

    result = asyncio.run(collector.collect_adaptive(
        DEFAULT_PANELS[-1], anchors,
        max_anchors=2, max_roots_per_platform=2,
        max_comments_per_root=20, max_depth=2,
    ))

    assert len(broker.thread_calls) == 8
    reddit_depth_calls = [
        call for call in broker.thread_calls if call["platform"] == "reddit"
    ]
    other_depth_calls = [
        call for call in broker.thread_calls if call["platform"] != "reddit"
    ]
    assert sorted(call["max_comments"] for call in reddit_depth_calls) == [50, 50]
    assert all(call["max_comments"] == 20 for call in other_depth_calls)
    assert all(call["max_depth"] == 2 for call in broker.thread_calls)
    comments = [item for item in result["evidence"] if item["record_type"] == "comment"]
    assert len(comments) == 8
    assert all(item["query_lineage_id"] for item in comments)
    assert all(item["truncated"] is True for item in comments)
    depth_receipts = [source for source in result["sources"] if source["stage"] == "adaptive_depth"]
    assert len(depth_receipts) == 8
    assert all(source["platform_reported_total"] == 50 for source in depth_receipts)


def test_adaptive_collection_uses_one_hard_budget_for_searches_and_thread_reads():
    broker = FakeBroker()
    x = FakeX()
    budget = AdaptiveCollectionBudget(
        max_attempts=3,
        per_platform_limits={
            "x": 3,
            "tiktok": 3,
            "instagram": 3,
            "reddit": 3,
            "youtube": 3,
        },
    )
    anchors = [{
        "anchor_id": "anchor-1",
        "normalized_anchor": "silicone air fryer liner",
        "seed_query": "household products",
    }, {
        "anchor_id": "anchor-2",
        "normalized_anchor": "enzyme laundry sheet",
        "seed_query": "household products",
    }]

    result = asyncio.run(OwnedRadarCollector(
        broker=broker,
        x_connector=x,
    ).collect_adaptive(
        DEFAULT_PANELS[-1],
        anchors,
        max_anchors=2,
        max_roots_per_platform=2,
        max_comments_per_root=20,
        max_depth=2,
        budget=budget,
    ))

    assert len(x.search_calls) + len(broker.search_calls) == 3
    assert budget.snapshot()["used_attempts"] == 3
    assert budget.snapshot()["remaining_attempts"] == 0
    assert any(
        source.get("error_category") == "adaptive_budget_exhausted"
        for source in result["sources"]
    )
    assert all(
        source.get("error_category") == "adaptive_budget_exhausted"
        for source in result["sources"]
        if source.get("stage") == "adaptive_depth"
    )


def test_broker_route_exception_becomes_an_explicit_failed_source_receipt():
    class BrokenBroker:
        async def search(self, **_kwargs):
            raise TimeoutError("route timeout")

    collector = OwnedRadarCollector(broker=BrokenBroker(), x_connector=FakeX())
    result = asyncio.run(collector._broker_search(
        DEFAULT_PANELS[0], "reddit", "home gym", count=3
    ))

    assert result["evidence"] == []
    assert result["source"]["status"] == "failed"
    assert result["source"]["error_category"] == "TimeoutError"


def test_broker_search_retries_one_transient_connector_timeout():
    class FlakyBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def search(self, *, keyword, platforms, count, time_filter, sort):
            self.attempts += 1
            if self.attempts == 1:
                platform = platforms[0]
                return {
                    "items": [],
                    "platform_results": {
                        platform: {
                            "platform": platform,
                            "status": "partial",
                            "selected_connector": f"{platform}_owned",
                            "coverage": {},
                        }
                    },
                    "source_health": [{
                        "platform": platform,
                        "connector": f"{platform}_owned",
                        "status": "error",
                        "error": "connector_timeout",
                    }],
                }
            return await super().search(
                keyword=keyword,
                platforms=platforms,
                count=count,
                time_filter=time_filter,
                sort=sort,
            )

    broker = FlakyBroker()
    result = asyncio.run(OwnedRadarCollector(
        broker=broker, x_connector=FakeX()
    )._broker_search(
        DEFAULT_PANELS[0], "tiktok", "hotel", count=3, hydrate=False
    ))

    assert broker.attempts == 2
    assert result["source"]["status"] == "complete"
    assert result["source"]["error_category"] is None
    assert result["source"]["coverage"]["attempt_count"] == 2
    assert result["source"]["coverage"]["recovered_errors"] == ["connector_timeout"]
    assert len(result["evidence"]) == 1


def test_broker_search_does_not_retry_a_non_timeout_connector_error():
    class ConnectorErrorBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def search(self, *, keyword, platforms, count, time_filter, sort):
            self.attempts += 1
            platform = platforms[0]
            return {
                "items": [],
                "platform_results": {
                    platform: {
                        "platform": platform,
                        "status": "partial",
                        "selected_connector": f"{platform}_owned",
                        "coverage": {},
                    }
                },
                "source_health": [{
                    "platform": platform,
                    "connector": f"{platform}_owned",
                    "status": "error",
                    "error": "connector_error",
                }],
            }

    broker = ConnectorErrorBroker()
    result = asyncio.run(OwnedRadarCollector(
        broker=broker, x_connector=FakeX()
    )._broker_search(
        DEFAULT_PANELS[0], "instagram", "hotel", count=3, hydrate=False
    ))

    assert broker.attempts == 1
    assert result["source"]["status"] == "partial"
    assert result["source"]["error_category"] == "connector_error"


def test_broker_search_retries_one_tiktok_transient_empty_and_keeps_route_receipts():
    class EmptyOnceBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def search(self, *, keyword, platforms, count, time_filter, sort):
            self.attempts += 1
            if self.attempts == 1:
                return {
                    "items": [],
                    "platform_results": {
                        "tiktok": {
                            "platform": "tiktok",
                            "status": "partial",
                            "selected_connector": None,
                            "attempted_connectors": ["authenticated", "playwright"],
                            "coverage": {},
                        }
                    },
                    "source_health": [
                        {
                            "platform": "tiktok",
                            "connector": "authenticated",
                            "status": "partial",
                            "items_returned": 0,
                            "error": None,
                        },
                        {
                            "platform": "tiktok",
                            "connector": "playwright",
                            "status": "partial",
                            "items_returned": 0,
                            "error": None,
                        },
                    ],
                }
            return await super().search(
                keyword=keyword,
                platforms=platforms,
                count=count,
                time_filter=time_filter,
                sort=sort,
            )

    broker = EmptyOnceBroker()
    result = asyncio.run(OwnedRadarCollector(
        broker=broker, x_connector=FakeX()
    )._broker_search(
        DEFAULT_PANELS[0],
        "tiktok",
        "switching skincare",
        count=3,
        time_filter="month",
        sort="latest",
        hydrate=False,
        retry_empty=True,
    ))

    assert broker.attempts == 2
    assert result["source"]["status"] == "complete"
    coverage = result["source"]["coverage"]
    assert coverage["attempt_count"] == 2
    assert coverage["recovered_errors"] == ["tiktok_transient_empty"]
    assert coverage["attempted_connectors"] == [
        "authenticated", "playwright",
    ]
    assert [item["connector"] for item in coverage["route_health"][:2]] == [
        "authenticated", "playwright",
    ]


def test_owned_trend_discovery_feeds_google_candidates_into_social_collection():
    def trends():
        return {
            "status": "complete",
            "source": "Google Trends Trending Now",
            "observed_at": "2026-08-28T00:00:00+00:00",
            "geographies": ["US", "GB"],
            "failures": [],
            "candidates": [{
                "keyword": "home gym",
                "normalized_keyword": "home gym",
                "categories": ["Health"],
                "countries": ["GB", "US"],
                "country_breadth": 2,
                "observations": [],
                "keyword_basket": ["home gym", "garage gym"],
                "panel_id": "fitness_wearables",
                "source": "Google Trends Trending Now",
            }],
        }

    collector = OwnedRadarCollector(
        broker=FakeBroker(), x_connector=FakeX(), trend_discovery_fn=trends
    )

    result = asyncio.run(collector.collect_trend_discovery())

    assert result["trend_candidates"][0]["keyword"] == "home gym"
    assert len(result["evidence"]) == 5
    assert result["sources"][0]["platform"] == "google_trends"
    assert result["sources"][0]["stage"] == "trend_discovery"
    assert {source["platform"] for source in result["sources"][1:]} == {
        "x", "tiktok", "instagram", "reddit", "youtube",
    }
    assert all(source["stage"] == "trend_candidate" for source in result["sources"][1:])


def test_google_candidate_social_collection_retains_other_sources_when_x_drops():
    class BrokenX:
        async def search(self, *_args, **_kwargs):
            raise RuntimeError("temporary X failure")

    def trends():
        return {
            "status": "complete",
            "source": "Google Trends Trending Now",
            "observed_at": "2026-08-28T00:00:00+00:00",
            "geographies": ["US"],
            "failures": [],
            "candidates": [{
                "keyword": "home gym",
                "normalized_keyword": "home gym",
                "categories": ["Health"],
                "countries": ["US"],
                "country_breadth": 1,
                "observations": [],
                "keyword_basket": ["home gym"],
                "panel_id": "fitness_wearables",
                "source": "Google Trends Trending Now",
            }],
        }

    result = asyncio.run(OwnedRadarCollector(
        broker=FakeBroker(), x_connector=BrokenX(), trend_discovery_fn=trends
    ).collect_trend_discovery())

    x_receipt = next(
        source for source in result["sources"]
        if source.get("platform") == "x" and source.get("stage") == "trend_candidate"
    )
    assert x_receipt["status"] == "failed"
    assert x_receipt["error_category"] == "RuntimeError"
    assert {item["platform"] for item in result["evidence"]} == {
        "tiktok", "instagram", "reddit", "youtube",
    }


def test_owned_preflight_requires_every_release_source():
    async def trajectory(query):
        return {
            "query": query,
            "source": "Google Trends",
            "status": "complete",
            "points": [
                {"date": f"2026-06-{(index % 28) + 1:02d}", "value": 20}
                for index in range(30)
            ],
        }

    collector = OwnedRadarCollector(
        broker=FakeBroker(),
        x_connector=FakeX(),
        trajectory_check_fn=trajectory,
    )

    result = asyncio.run(collector.preflight())

    assert result["ok"] is True
    assert {source["platform"] for source in result["sources"]} == {
        "x", "tiktok", "instagram", "reddit", "youtube", "google_trends",
    }
    assert all(source["status"] == "complete" for source in result["sources"])
    tiktok_call = next(
        call for call in collector.broker.search_calls
        if call["platform"] == "tiktok"
    )
    assert tiktok_call["keyword"] == "switching skincare"
    assert tiktok_call["time_filter"] == "halfyear"
    assert tiktok_call["sort"] == "latest"


def test_owned_preflight_blocks_the_sweep_when_tiktok_cannot_return_records():
    class TikTokFails(FakeBroker):
        async def search(self, *, keyword, platforms, count, time_filter, sort):
            if platforms == ["tiktok"]:
                raise TimeoutError("TikTok worker unavailable")
            return await super().search(
                keyword=keyword,
                platforms=platforms,
                count=count,
                time_filter=time_filter,
                sort=sort,
            )

    async def trajectory(query):
        return {
            "query": query,
            "source": "Google Trends",
            "status": "complete",
            "points": [
                {"date": f"2026-06-{(index % 28) + 1:02d}", "value": 20}
                for index in range(30)
            ],
        }

    result = asyncio.run(OwnedRadarCollector(
        broker=TikTokFails(),
        x_connector=FakeX(),
        trajectory_check_fn=trajectory,
    ).preflight())

    assert result["ok"] is False
    assert result["error_category"] == "preflight_tiktok_unavailable"
    receipt = next(item for item in result["sources"] if item["platform"] == "tiktok")
    assert receipt["status"] == "failed"
    assert receipt["error_category"] == "TimeoutError"


def test_owned_preflight_records_a_source_exception_without_starting_a_scan():
    class BrokenX:
        async def search(self, *_args, **_kwargs):
            raise RuntimeError("session expired")

    async def trajectory(query):
        return {
            "query": query,
            "source": "Google Trends",
            "status": "complete",
            "points": [
                {"date": f"2026-06-{(index % 28) + 1:02d}", "value": 20}
                for index in range(30)
            ],
        }

    collector = OwnedRadarCollector(
        broker=FakeBroker(),
        x_connector=BrokenX(),
        trajectory_check_fn=trajectory,
    )

    result = asyncio.run(collector.preflight())

    assert result["ok"] is False
    assert result["error_category"] == "preflight_x_unavailable"
    x_receipt = next(item for item in result["sources"] if item["platform"] == "x")
    assert x_receipt == {
        "platform": "x",
        "stage": "preflight",
        "status": "failed",
        "count": 0,
        "error_category": "RuntimeError",
    }


def test_initial_google_movement_collection_is_worldwide_only(monkeypatch):
    captured = {}

    def fake_collect(candidates, **kwargs):
        captured["candidates"] = list(candidates)
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        owned_radar_module,
        "collect_movement_bundles",
        fake_collect,
    )

    result = asyncio.run(check_movement_bundles([{"label": "Specific behavior"}]))

    assert result == []
    assert len(captured["geographies"]) == 1
    assert captured["geographies"][0]["code"] == ""
