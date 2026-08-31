from datetime import date, timedelta

from social_scraper.investing.trajectory import (
    _google_request_delay,
    build_trajectory_query_basket,
    collect_movement_bundles,
    collect_search_trajectory,
    classify_movement_bundle,
    derive_trajectory_query,
    trajectory_is_usable,
)


class FakeSeries:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class FakeIndex:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class FakeFrame:
    def __init__(self, query, values):
        self.query = query
        self.values = values
        self.index = FakeIndex([
            date(2026, 5, 1) + timedelta(days=index)
            for index in range(len(values))
        ])

    def __len__(self):
        return len(self.values)

    def __contains__(self, key):
        return key == self.query

    def __getitem__(self, key):
        assert key == self.query
        return FakeSeries(self.values)


class FakeTrends:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def interest_over_time(self, queries, *, timeframe, geo, headers=None):
        self.calls.append({
            "queries": queries, "timeframe": timeframe, "geo": geo,
            "headers": headers,
        })
        return FakeFrame(queries[0], self.values)


def test_google_request_delay_defaults_to_trendspy_recommended_16_seconds(monkeypatch):
    monkeypatch.delenv("BOUNTY_GOOGLE_TRENDS_REQUEST_DELAY", raising=False)
    assert _google_request_delay() == 16.0

    monkeypatch.setenv("BOUNTY_GOOGLE_TRENDS_REQUEST_DELAY", "20")
    assert _google_request_delay() == 20.0

    monkeypatch.setenv("BOUNTY_GOOGLE_TRENDS_REQUEST_DELAY", "invalid")
    assert _google_request_delay() == 16.0


def test_trajectory_query_prefers_repeated_specific_phrases():
    assert derive_trajectory_query({
        "label": "Gym membership cancellation pain pushing home gym adoption",
        "anchor_terms": [
            "bought a home dumbbell set and a bench",
            "trying to cancel the membership",
            "true home gym",
        ],
    }) == "home gym"
    assert derive_trajectory_query({
        "label": "Replacing streaming services with physical media",
        "anchor_terms": [
            "replacing streaming services with physical media",
            "Cancelling Netflix to buy DVDs",
        ],
    }) == "physical media"
    assert derive_trajectory_query({
        "label": "St Michael x Aries green bag sold out early",
        "anchor_terms": ["St Michael", "Aries", "green bag", "instantly sold out"],
    }) == "st michael aries"


def test_query_basket_keeps_selected_query_and_specific_alternatives():
    basket = build_trajectory_query_basket({
        "label": "T-Mobile plan increase prompting provider-switch consideration",
        "movement_bundle": {"query": "T-Mobile price increase"},
        "anchor_terms": ["T-Mobile", "plan increase", "switched providers"],
    })

    assert [item["query"] for item in basket] == [
        "T-Mobile price increase", "T-Mobile",
    ]
    assert basket[0]["reason"] == "Primary query selected for this subject."
    assert all(item["reason"] for item in basket)


def test_discovery_query_basket_uses_google_related_terms_without_long_prose():
    basket = build_trajectory_query_basket({
        "keyword": "aaron paul",
        "keyword_basket": [
            "aaron paul", "aaron paul breaking bad", "why is aaron paul trending",
            "this phrase is much too long to be a useful comparable public query",
        ],
    })

    assert [item["query"] for item in basket] == [
        "aaron paul", "aaron paul breaking bad",
    ]
    assert all(item["source"] == "google_related_term" for item in basket)


def test_search_trajectory_returns_a_real_chart_contract():
    trends = FakeTrends([10 + (index % 7) for index in range(90)])

    result = collect_search_trajectory("home gym", trends=trends)

    assert result["status"] == "complete"
    assert result["source"] == "Google Trends"
    assert result["normalized"] is True
    assert result["timeframe"] == "today 3-m"
    assert len(result["points"]) == 90
    assert result["points"][0] == {"date": "2026-05-01", "value": 10}
    assert trends.calls == [{
        "queries": ["home gym"], "timeframe": "today 3-m", "geo": "",
        "headers": {"referer": "https://trends.google.com/"},
    }]
    assert trajectory_is_usable(result) is True


def test_sparse_or_failed_trajectory_is_not_usable():
    sparse = collect_search_trajectory(
        "hidden door releases", trends=FakeTrends([0] * 85 + [0, 0, 100, 0, 0])
    )

    assert sparse["status"] == "insufficient_search_volume"
    assert trajectory_is_usable(sparse) is False
    assert trajectory_is_usable({"status": "failed", "points": []}) is False


class FakeBatchFrame:
    def __init__(self, queries, days):
        self.queries = list(queries)
        self.days = days
        self.index = FakeIndex([
            date(2026, 1, 1) + timedelta(days=index)
            for index in range(days)
        ])

    def __len__(self):
        return self.days

    def __contains__(self, key):
        return key in self.queries

    def __getitem__(self, key):
        return FakeSeries([
            10 + ((index + self.queries.index(key)) % 20)
            for index in range(self.days)
        ])


class FakeBatchTrends:
    def __init__(self):
        self.calls = []

    def interest_over_time(self, queries, *, timeframe, geo, headers=None):
        self.calls.append((tuple(queries), timeframe, geo, headers))
        days = {"today 3-m": 90, "today 12-m": 53, "today 5-y": 260}[timeframe]
        return FakeBatchFrame(queries, days)


def test_movement_bundles_cover_worldwide_countries_and_three_horizons():
    trends = FakeBatchTrends()
    candidates = [
        {
            "label": "Home gym adoption",
            "trajectory_query": "home gym",
            "keyword_basket": ["home gym", "garage gym"],
        },
        {"label": "Physical media switch", "trajectory_query": "physical media"},
    ]

    bundles = collect_movement_bundles(
        candidates,
        trends=trends,
        geographies=(
            {"code": "", "name": "Worldwide"},
            {"code": "US", "name": "United States"},
        ),
    )

    assert len(bundles) == 2
    assert bundles[0]["query"] == "home gym"
    assert [item["query"] for item in bundles[0]["query_options"]] == [
        "home gym", "garage gym",
    ]
    assert bundles[0]["query_options"][1]["series"]["US"]["5y"]["status"] == "complete"
    assert bundles[0]["default_geo"] == "WORLDWIDE"
    assert bundles[0]["default_horizon"] == "3m"
    assert bundles[0]["series"]["WORLDWIDE"]["3m"]["status"] == "complete"
    assert len(bundles[0]["series"]["US"]["5y"]["points"]) == 260
    assert bundles[0]["geographies"] == [
        {"code": "WORLDWIDE", "name": "Worldwide"},
        {"code": "US", "name": "United States"},
    ]
    assert {item[1] for item in trends.calls} == {
        "today 3-m", "today 12-m", "today 5-y"
    }
    assert all(call[3] == {"referer": "https://trends.google.com/"} for call in trends.calls)


def test_movement_bundle_selects_the_query_with_usable_history_as_default():
    class QueryQualityFrame(FakeBatchFrame):
        def __getitem__(self, key):
            if key == "weak phrase":
                return FakeSeries([0] * self.days)
            return FakeSeries([10 + (index % 8) for index in range(self.days)])

    class QueryQualityTrends(FakeBatchTrends):
        def interest_over_time(self, queries, *, timeframe, geo, headers=None):
            self.calls.append((tuple(queries), timeframe, geo, headers))
            days = {"today 3-m": 90, "today 12-m": 53, "today 5-y": 260}[timeframe]
            return QueryQualityFrame(queries, days)

    bundles = collect_movement_bundles(
        [{
            "movement_bundle": {"query": "weak phrase"},
            "anchor_terms": ["strong phrase"],
            "label": "Strong phrase switching behavior",
        }],
        trends=QueryQualityTrends(),
        geographies=({"code": "", "name": "Worldwide"},),
    )

    assert [item["query"] for item in bundles[0]["query_options"]] == [
        "weak phrase", "strong phrase",
    ]
    assert bundles[0]["default_query"] == "strong phrase"
    assert bundles[0]["query"] == "strong phrase"
    assert bundles[0]["series"]["WORLDWIDE"]["3m"]["status"] == "complete"


def _series(values):
    return {
        "status": "complete",
        "points": [
            {"date": str(index), "value": value}
            for index, value in enumerate(values)
        ],
    }


def test_movement_classifier_separates_event_spikes_from_rising_five_year_peaks():
    event = {
        "default_geo": "WORLDWIDE",
        "series": {
            "WORLDWIDE": {
                "3m": _series([1] * 40 + [100] + [1] * 49),
                "1y": _series([5] * 53),
                "5y": _series([5] * 260),
            }
        },
    }
    rising_values = []
    for year, peak in enumerate((20, 30, 40, 50, 60)):
        rising_values.extend([10 + year * 4] * 51 + [peak])
    rising = {
        "default_geo": "WORLDWIDE",
        "series": {
            "WORLDWIDE": {
                "3m": _series([20 + index // 15 for index in range(90)]),
                "1y": _series([20 + index // 10 for index in range(53)]),
                "5y": _series(rising_values),
            }
        },
    }

    event_result = classify_movement_bundle(event)
    rising_result = classify_movement_bundle(rising)

    assert event_result["movement_type"] == "event_spike"
    assert event_result["trend_eligible"] is False
    assert rising_result["movement_type"] == "rising_peaks"
    assert rising_result["trend_eligible"] is True
    assert rising_result["metrics"]["five_year_peaks"] == [20, 30, 40, 50, 60]


def test_movement_bundle_deduplicates_equal_queries_without_losing_candidates():
    class RejectDuplicateQueries(FakeBatchTrends):
        def interest_over_time(self, queries, *, timeframe, geo, headers=None):
            assert len(queries) == len(set(queries))
            return super().interest_over_time(
                queries, timeframe=timeframe, geo=geo, headers=headers
            )

    trends = RejectDuplicateQueries()
    bundles = collect_movement_bundles(
        [
            {"label": "Home gym adoption", "trajectory_query": "home gym"},
            {"label": "Home gym equipment", "trajectory_query": "home gym"},
        ],
        trends=trends,
        geographies=({"code": "", "name": "Worldwide"},),
    )

    assert len(bundles) == 2
    assert bundles[0]["series"] == bundles[1]["series"]
    assert bundles[0]["series"]["WORLDWIDE"]["3m"]["status"] == "complete"
    assert len(trends.calls) == 3
    assert all(call[0] == ("home gym",) for call in trends.calls)


def test_movement_bundle_keeps_other_queries_when_one_series_cannot_be_parsed():
    class OneBrokenSeries(FakeBatchTrends):
        def interest_over_time(self, queries, *, timeframe, geo, headers=None):
            self.calls.append((tuple(queries), timeframe, geo, headers))
            frame = FakeBatchFrame(queries, 90 if timeframe == "today 3-m" else 53)
            original_getitem = frame.__class__.__getitem__

            class PartialFrame(FakeBatchFrame):
                def __getitem__(self, key):
                    if key == "broken query":
                        raise ValueError("unparseable series")
                    return original_getitem(self, key)

            days = {"today 3-m": 90, "today 12-m": 53, "today 5-y": 260}[timeframe]
            return PartialFrame(queries, days)

    bundles = collect_movement_bundles(
        [
            {"trajectory_query": "home gym"},
            {"trajectory_query": "broken query"},
        ],
        trends=OneBrokenSeries(),
        geographies=({"code": "", "name": "Worldwide"},),
    )

    assert bundles[0]["series"]["WORLDWIDE"]["3m"]["status"] == "complete"
    assert bundles[1]["series"]["WORLDWIDE"]["3m"]["status"] == "failed"
    assert bundles[1]["series"]["WORLDWIDE"]["3m"]["error_category"] == "ValueError"
