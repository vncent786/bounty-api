import asyncio

from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.investing.owned_radar import OwnedRadarCollector
from social_scraper.investing.private_radar import DEFAULT_PANELS


class FakeX:
    def __init__(self):
        self.queries = []

    async def search(self, query, count=20, time_filter="", sort="", region=""):
        self.queries.append(query)
        item = SocialItem(
            platform="x",
            post_id=f"x{len(self.queries)}",
            url=f"https://x.com/u/status/{len(self.queries)}",
            author_username=f"u{len(self.queries)}",
            text="I switched to a silicone air fryer liner",
            created_at="2026-08-26T00:00:00Z",
        )
        return ConnectorResult(
            items=[item],
            health=SourceHealth(
                platform="x", connector="x_scweet", status="ok",
                items_returned=1, items_requested=count,
                coverage={"requested_limit_reached": False},
            ),
        )


class FakeBroker:
    async def search(self, *, keyword, platforms, count, time_filter, sort):
        platform = platforms[0]
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


def test_owned_discovery_preserves_platform_source_receipts():
    collector = OwnedRadarCollector(broker=FakeBroker(), x_connector=FakeX())
    result = asyncio.run(collector.collect_discovery(DEFAULT_PANELS[0]))
    assert {source["platform"] for source in result["sources"]} == {
        "x", "tiktok", "instagram", "reddit", "youtube",
    }
    assert len(result["evidence"]) == 5


def test_owned_corroboration_includes_reddit_with_source_receipts():
    collector = OwnedRadarCollector(broker=FakeBroker(), x_connector=FakeX())

    result = asyncio.run(collector.collect_corroboration(
        DEFAULT_PANELS[0], ["silicone air fryer liner"]
    ))

    assert {source["platform"] for source in result["sources"]} == {
        "tiktok", "instagram", "reddit", "youtube",
    }
    assert len(result["evidence"]) == 4
