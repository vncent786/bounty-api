from types import SimpleNamespace

from social_scraper.investing.google_discovery import (
    DEFAULT_DISCOVERY_GEOGRAPHIES,
    MOVEMENT_GEOGRAPHIES,
    collect_worldwide_trend_candidates,
    panel_for_trend_categories,
)


class FakeTrends:
    def __init__(self):
        self.calls = []
        self.news_calls = []

    def trending_now(self, *, geo):
        self.calls.append(geo)
        rows = {
            "US": [
                SimpleNamespace(
                    keyword="home gym",
                    volume=200000,
                    volume_growth_pct=120,
                    started_timestamp=[1_787_000_000],
                    trend_keywords=["garage gym", "home gym equipment"],
                    topics=[7],
                    news_tokens=[[12345, "en", "US"]],
                ),
                SimpleNamespace(
                    keyword="tesla recall",
                    volume=500000,
                    volume_growth_pct=1000,
                    started_timestamp=[1_787_100_000],
                    trend_keywords=["tesla door handle"],
                    topics=[1],
                ),
            ],
            "GB": [
                SimpleNamespace(
                    keyword="home gym",
                    volume=50000,
                    volume_growth_pct=80,
                    started_timestamp=[1_787_000_000],
                    trend_keywords=["home weights"],
                    topics=[7],
                ),
                SimpleNamespace(
                    keyword="physical media",
                    volume=20000,
                    volume_growth_pct=60,
                    started_timestamp=[1_787_050_000],
                    trend_keywords=["dvd collection"],
                    topics=[4],
                ),
            ],
        }
        return rows.get(geo, [])

    def trending_now_news_by_ids(self, news_ids, max_news=3):
        self.news_calls.append((news_ids, max_news))
        return [SimpleNamespace(
            title="Home gym demand rises as membership prices increase",
            url="https://example.com/home-gym-demand",
            source="Example News",
            time=1_787_150_000,
            snippet="Consumers are comparing home equipment with gym fees.",
        )]


def test_worldwide_discovery_aggregates_country_observations_without_summing_volume():
    trends = FakeTrends()

    result = collect_worldwide_trend_candidates(
        trends=trends,
        geographies=("US", "GB"),
        limit=8,
        now_timestamp=1_787_200_000,
    )

    home = next(item for item in result["candidates"] if item["keyword"] == "home gym")
    assert result["status"] == "complete"
    assert result["geographies"] == ["US", "GB"]
    assert trends.calls == ["US", "GB"]
    assert home["country_breadth"] == 2
    assert home["countries"] == ["GB", "US"]
    assert home["observations"] == [
        {
            "geo": "GB", "search_volume": 50000, "growth_pct": 80,
            "source_rank": 1, "source_started_at": "2026-08-17T20:53:20+00:00",
            "started_hours_ago": 55.6,
        },
        {
            "geo": "US", "search_volume": 200000, "growth_pct": 120,
            "source_rank": 1, "source_started_at": "2026-08-17T20:53:20+00:00",
            "started_hours_ago": 55.6,
        },
    ]
    assert "search_volume" not in home
    assert home["keyword_basket"] == [
        "home gym", "garage gym", "home gym equipment", "home weights"
    ]
    assert home["context_articles"] == [{
        "title": "Home gym demand rises as membership prices increase",
        "url": "https://example.com/home-gym-demand",
        "source": "Example News",
        "published_at": "2026-08-19T14:33:20+00:00",
        "snippet": "Consumers are comparing home equipment with gym fees.",
    }]
    assert "_news_tokens" not in home
    assert trends.news_calls == [([[12345, "en", "US"]], 3)]
    assert home["context"] == {
        "what_it_is": (
            "Health topic. Recent coverage: Home gym demand rises as membership prices "
            "increase. Related searches: garage gym, home gym equipment, home weights."
        ),
        "why_rising": "Current headlines from Example News are focused on this term.",
        "investing_angle": (
            "Search attention alone does not establish a listed beneficiary. Check cited "
            "behavior and verified brand exposure before treating it as investable."
        ),
        "source_urls": ["https://example.com/home-gym-demand"],
    }


def test_worldwide_discovery_is_category_balanced_and_maps_consumer_panels():
    result = collect_worldwide_trend_candidates(
        trends=FakeTrends(), geographies=("US", "GB"), limit=3,
        now_timestamp=1_787_200_000,
    )

    categories = [item["categories"][0] for item in result["candidates"]]
    assert len(set(categories)) == 3
    assert panel_for_trend_categories(["Autos & Vehicles"]) == "automobiles"
    assert panel_for_trend_categories(["Health"]) == "fitness_wearables"
    assert panel_for_trend_categories(["Entertainment"]) == "streaming"
    assert panel_for_trend_categories(["Politics"]) is None


def test_worldwide_and_country_controls_have_explicit_supported_markets():
    assert DEFAULT_DISCOVERY_GEOGRAPHIES[0] == "US"
    assert MOVEMENT_GEOGRAPHIES[0] == {"code": "", "name": "Worldwide"}
    assert {item["code"] for item in MOVEMENT_GEOGRAPHIES} >= {
        "", "US", "GB", "SG", "DE", "FR"
    }
