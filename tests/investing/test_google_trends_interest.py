"""Regression and scale contract for Google Trends interest-over-time."""

from __future__ import annotations

import pytest

from social_scraper.source_connectors.google_trends_interest import (
    canonical_trendspy_gprop,
    collect_interest_plan,
    fetch_interest_over_time,
)
from social_scraper.investing.trajectory import (
    collect_movement_bundles,
    collect_search_trajectory,
)
from scripts.qualify_google_trends_interest import plan as qualification_plan, qualifies


class FakeSeries:
    def __init__(self, values):
        self.values = list(values)

    def tolist(self):
        return list(self.values)


class FakeFrame:
    def __init__(self, queries, *, rows=93, partial_last=True, omit=()):
        self.queries = [query for query in queries if query not in set(omit)]
        self.columns = [*self.queries, "isPartial"]
        self.index = FakeSeries([f"2026-06-{1 + index:02d}" for index in range(rows)])
        self.values = {
            query: [10 + ((index + query_index) % 20) for index in range(rows)]
            for query_index, query in enumerate(self.queries)
        }
        self.values["isPartial"] = [False] * (rows - 1) + [partial_last]

    def __len__(self):
        return len(self.index.values)

    def __getitem__(self, key):
        return FakeSeries(self.values[key])

    def __contains__(self, key):
        return key in self.columns


class RecordingTrends:
    def __init__(self, *, omit=()):
        self.calls = []
        self.omit = tuple(omit)

    def interest_over_time(self, queries, **kwargs):
        self.calls.append((list(queries), dict(kwargs)))
        return FakeFrame(queries, omit=self.omit)


def test_web_property_is_canonicalized_to_trendspy_default():
    assert canonical_trendspy_gprop("web") == (None, "web_default")
    assert canonical_trendspy_gprop("") == (None, "web_default")
    assert canonical_trendspy_gprop("web_default") == (None, "web_default")
    assert canonical_trendspy_gprop("froogle") == ("froogle", "froogle")


def test_exact_basket_uses_default_web_and_preserves_partial_flags():
    trends = RecordingTrends()
    queries = ["ghost root beer energy drink", "ghost a&w root beer"]

    result = fetch_interest_over_time(
        queries,
        trends=trends,
        timeframe="today 3-m",
        geo="US",
        gprop="web",
    )

    assert result["status"] == "complete"
    assert result["requested_gprop"] == "web"
    assert result["effective_gprop"] == "web_default"
    assert result["rows_returned"] == 93
    assert result["isPartial_flags"] == [False] * 92 + [True]
    assert set(result["returned_values"]) == {"dates", *queries}
    called_queries, kwargs = trends.calls[0]
    assert called_queries == queries
    assert kwargs == {
        "timeframe": "today 3-m",
        "geo": "US",
        "headers": {"referer": "https://trends.google.com/"},
    }


def test_non_web_property_is_forwarded_without_relabeling():
    trends = RecordingTrends()
    result = fetch_interest_over_time(
        ["running shoes"], trends=trends, geo="US", gprop="froogle"
    )

    assert result["status"] == "complete"
    assert result["requested_gprop"] == "froogle"
    assert result["effective_gprop"] == "froogle"
    assert trends.calls[0][1]["gprop"] == "froogle"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(400, "HTTP_400"), (429, "HTTP_429")],
)
def test_http_failures_keep_the_real_status(status_code, expected):
    class Response:
        def __init__(self, code):
            self.status_code = code

    class HttpFailureTrends:
        def interest_over_time(self, *_args, **_kwargs):
            error = RuntimeError("provider request failed")
            error.response = Response(status_code)
            raise error

    result = fetch_interest_over_time(
        ["ghost root beer energy drink"], trends=HttpFailureTrends(), gprop="web"
    )

    assert result["status"] == "SOURCE_FAILURE"
    assert result["error_category"] == expected
    assert result["returned_values"] is None


def test_missing_query_column_fails_closed():
    trends = RecordingTrends(omit=("ghost a&w root beer",))
    result = fetch_interest_over_time(
        ["ghost root beer energy drink", "ghost a&w root beer"],
        trends=trends,
        gprop="web",
    )

    assert result["status"] == "SOURCE_FAILURE"
    assert result["error_category"] == "MISSING_QUERY_COLUMN"
    assert result["returned_values"] is None


def test_query_basket_bounds_and_identity_are_enforced():
    trends = RecordingTrends()
    with pytest.raises(ValueError, match="between 1 and 5"):
        fetch_interest_over_time([], trends=trends)
    with pytest.raises(ValueError, match="between 1 and 5"):
        fetch_interest_over_time([str(index) for index in range(6)], trends=trends)
    with pytest.raises(ValueError, match="unique"):
        fetch_interest_over_time(["ghost", " GHOST "], trends=trends)


def test_scaled_plan_serializes_twelve_requests_without_reintroducing_web_argument():
    trends = RecordingTrends()
    plan = [
        {
            "request_key": f"request-{index}",
            "query_basket": [f"query {index}", f"comparison {index}"],
            "timeframe": "today 3-m",
            "geo": "US" if index % 2 else "",
            "gprop": "web",
        }
        for index in range(12)
    ]

    results = collect_interest_plan(plan, trends=trends, max_requests=12)

    assert list(results) == [f"request-{index}" for index in range(12)]
    assert all(row["status"] == "complete" for row in results.values())
    assert len(trends.calls) == 12
    assert all("gprop" not in kwargs for _queries, kwargs in trends.calls)
    with pytest.raises(ValueError, match="exceeds bounded maximum"):
        collect_interest_plan([*plan, {**plan[0], "request_key": "request-12"}], trends=trends, max_requests=12)


def test_existing_trajectory_paths_canonicalize_literal_web_before_provider_call():
    single = RecordingTrends()
    result = collect_search_trajectory(
        "ghost root beer energy drink",
        trends=single,
        timeframe="today 3-m",
        geo="US",
        gprop="web",
    )
    assert result["status"] == "complete"
    assert result["requested_gprop"] == "web"
    assert result["effective_gprop"] == "web_default"
    assert "gprop" not in single.calls[0][1]

    batched = RecordingTrends()
    bundles = collect_movement_bundles(
        [{
            "label": "GHOST A&W Root Beer",
            "trajectory_query": "ghost root beer energy drink",
            "keyword_basket": ["ghost a&w root beer"],
        }],
        trends=batched,
        geographies=({"code": "US", "name": "United States"},),
        horizons=({"code": "3m", "name": "3 months", "timeframe": "today 3-m"},),
        batch_size=5,
        gprop="web",
    )
    assert bundles[0]["requested_gprop"] == "web"
    assert bundles[0]["effective_gprop"] == "web_default"
    assert batched.calls
    assert all("gprop" not in kwargs for _queries, kwargs in batched.calls)


def test_live_qualification_plan_is_bounded_and_checks_exact_monitor_basket():
    rows = qualification_plan(1)
    assert len(rows) == 5
    assert all(1 <= len(row["query_basket"]) <= 5 for row in rows)
    exact = [row for row in rows if row["role"] == "exact_monitor_basket"]
    assert [row["geo"] for row in exact] == ["US", ""]
    assert all(
        row["query_basket"]
        == ["ghost root beer energy drink", "ghost a&w root beer"]
        for row in exact
    )
    assert all(row["gprop"] == "web" for row in rows)

    assert qualifies({
        "status": "complete",
        "requested_gprop": "web",
        "effective_gprop": "web_default",
        "rows_returned": 93,
        "returned_values": {"dates": [str(index) for index in range(93)]},
        "isPartial_flags": [False] * 93,
    }) is True
    assert qualifies({
        "status": "complete",
        "requested_gprop": "web",
        "effective_gprop": "web",
        "rows_returned": 93,
        "returned_values": {"dates": [str(index) for index in range(93)]},
        "isPartial_flags": [False] * 93,
    }) is False
