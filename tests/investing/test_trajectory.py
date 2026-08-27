from datetime import date, timedelta

from social_scraper.investing.trajectory import (
    collect_search_trajectory,
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

    def interest_over_time(self, queries, *, timeframe, geo):
        self.calls.append({"queries": queries, "timeframe": timeframe, "geo": geo})
        return FakeFrame(queries[0], self.values)


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
        "queries": ["home gym"], "timeframe": "today 3-m", "geo": ""
    }]
    assert trajectory_is_usable(result) is True


def test_sparse_or_failed_trajectory_is_not_usable():
    sparse = collect_search_trajectory(
        "hidden door releases", trends=FakeTrends([0] * 85 + [0, 0, 100, 0, 0])
    )

    assert sparse["status"] == "insufficient_search_volume"
    assert trajectory_is_usable(sparse) is False
    assert trajectory_is_usable({"status": "failed", "points": []}) is False
