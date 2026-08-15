"""Focused contract tests for deterministic engagement baselines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from social_scraper.analysis.engagement import (
    AGE_BUCKETS,
    CREATOR_SIZE_BUCKETS,
    EngagementBaselineConfig,
    age_bucket_for,
    calculate_engagement_percentiles,
    creator_size_bucket_for,
    is_supported_outlier,
    prepare_baseline_observation,
)
from social_scraper.discovery.storage import DiscoveryStore


AS_OF = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
CONFIG = EngagementBaselineConfig(
    trailing_period=timedelta(days=30),
    min_supported_sample_size=4,
)


def root(
    post_id: str,
    *,
    platform: str = "youtube",
    observed_at: datetime = AS_OF,
    age: timedelta = timedelta(hours=12),
    followers: int | None = 5_000,
    likes: int | None = None,
    upvotes: int | None = None,
    comments: int | None = None,
    replies: int | None = None,
    reposts: int | None = None,
    shares: int | None = None,
    views: int | None = None,
) -> dict:
    return {
        "platform": platform,
        "post_id": post_id,
        "record_type": "post",
        "depth": 0,
        "published_at": (observed_at - age).isoformat(),
        "observed_at": observed_at.isoformat(),
        "author": {"follower_count": followers},
        "engagement": {
            "likes": likes,
            "upvotes": upvotes,
            "comments": comments,
            "replies": replies,
            "reposts": reposts,
            "shares": shares,
            "views": views,
        },
    }


def test_bucket_policies_are_explicit_and_half_open():
    assert [(bucket.name, bucket.minimum_hours, bucket.maximum_hours) for bucket in AGE_BUCKETS] == [
        ("under_6h", 0, 6),
        ("6h_to_24h", 6, 24),
        ("1d_to_3d", 24, 72),
        ("3d_to_7d", 72, 168),
        ("7d_to_30d", 168, 720),
        ("30d_plus", 720, None),
    ]
    assert [
        (bucket.name, bucket.minimum_followers, bucket.maximum_followers)
        for bucket in CREATOR_SIZE_BUCKETS
    ] == [
        ("under_1k", 0, 1_000),
        ("1k_to_10k", 1_000, 10_000),
        ("10k_to_100k", 10_000, 100_000),
        ("100k_to_1m", 100_000, 1_000_000),
        ("1m_plus", 1_000_000, None),
    ]

    assert age_bucket_for(AS_OF - timedelta(hours=6), AS_OF) == "6h_to_24h"
    assert age_bucket_for(AS_OF - timedelta(hours=24), AS_OF) == "1d_to_3d"
    assert age_bucket_for(AS_OF + timedelta(seconds=1), AS_OF) is None
    assert age_bucket_for("2026-08-15T01:00:00", AS_OF) is None
    assert creator_size_bucket_for(999) == "under_1k"
    assert creator_size_bucket_for(1_000) == "1k_to_10k"
    assert creator_size_bucket_for(None) is None


def test_percentiles_use_only_trailing_platform_age_and_creator_comparables():
    target = root("target", likes=30, comments=3, views=0)
    comparable = [
        root("a", likes=10, comments=1, views=0),
        root("b", likes=20, comments=2, views=10),
        root("c", likes=30, comments=3, views=20),
        root("d", likes=40, comments=4, views=30),
        root("wrong-platform", platform="tiktok", likes=10_000, comments=100, views=100),
        root("wrong-age", age=timedelta(days=2), likes=10_000, comments=100, views=100),
        root("wrong-creator", followers=50_000, likes=10_000, comments=100, views=100),
        root(
            "stale",
            observed_at=AS_OF - timedelta(days=31),
            likes=10_000,
            comments=100,
            views=100,
        ),
    ]

    result = calculate_engagement_percentiles(
        target, comparable, as_of=AS_OF, config=CONFIG
    )

    assert result["like_percentile"] == 75.0
    assert result["comment_percentile"] == 75.0
    assert result["repost_percentile"] is None
    assert result["view_percentile"] == 25.0
    assert result["creator_adjusted_percentile"] == 50.0
    assert result["baseline_sample_size"] == 4
    assert result["baseline_status"] == "supported"
    assert result["metric_sample_sizes"] == {
        "like": 4,
        "comment": 4,
        "repost": 0,
        "view": 4,
        "creator_adjusted": 4,
    }
    assert result["platform"] == "youtube"
    assert result["content_age_bucket"] == "6h_to_24h"
    assert result["creator_size_bucket"] == "1k_to_10k"
    assert result["baseline_observed_from"] == "2026-07-16T12:00:00+00:00"
    assert result["baseline_observed_through"] == "2026-08-15T12:00:00+00:00"
    assert result["trailing_period_seconds"] == 30 * 24 * 60 * 60


def test_missing_never_becomes_zero_and_raw_source_counts_are_retained():
    target = root(
        "target",
        likes=None,
        upvotes=8,
        comments=None,
        replies=0,
        shares=None,
        views=None,
        followers=None,
    )
    observed = [
        root("a", likes=None, upvotes=4, replies=None, followers=50),
        root("b", likes=None, upvotes=8, replies=0, followers=5_000),
        root("c", likes=None, upvotes=None, replies=2, followers=50_000),
        root("d", likes=None, upvotes=16, replies=None, followers=None),
    ]

    result = calculate_engagement_percentiles(
        target, observed, as_of=AS_OF, config=CONFIG
    )

    assert result["raw_counts"]["likes"] is None
    assert result["raw_counts"]["upvotes"] == 8
    assert result["raw_counts"]["comments"] is None
    assert result["raw_counts"]["replies"] == 0
    assert result["raw_counts"]["shares"] is None
    assert result["raw_counts"]["views"] is None
    assert result["metric_sources"] == {
        "like": "upvotes",
        "comment": "replies",
        "repost": None,
        "view": None,
    }
    # Unknown target creator size intentionally does not filter by creator bucket.
    assert result["creator_size_bucket"] is None
    assert result["like_percentile"] == pytest.approx(2 / 3 * 100)
    assert result["comment_percentile"] == 50.0
    assert result["repost_percentile"] is None
    assert result["view_percentile"] is None
    assert result["creator_adjusted_percentile"] is None
    assert result["baseline_sample_size"] == 2
    assert result["baseline_status"] == "weak"


def test_metric_availability_controls_samples_and_weak_cannot_route():
    target = root("target", likes=100, comments=9)
    observed = [
        root("a", likes=10, comments=None),
        root("b", likes=20, comments=1),
        root("c", likes=30, comments=None),
        root("d", likes=40, comments=9),
    ]

    result = calculate_engagement_percentiles(
        target, observed, as_of=AS_OF, config=CONFIG
    )

    assert result["like_percentile"] == 100.0
    assert result["comment_percentile"] == 100.0
    assert result["metric_sample_sizes"]["like"] == 4
    assert result["metric_sample_sizes"]["comment"] == 2
    assert result["baseline_sample_size"] == 2
    assert result["baseline_status"] == "weak"
    assert not is_supported_outlier(result, threshold=95)


def test_supported_outlier_requires_supported_sample_for_selected_feature():
    target = root("target", likes=30, comments=3)
    observed = [
        root("a", likes=10, comments=1),
        root("b", likes=20, comments=2),
        root("c", likes=30, comments=3),
        root("d", likes=40, comments=4),
        # Same dimensional pool for raw metrics, but exact metric availability
        # differs, so it is excluded from the creator-adjusted sample.
        root("e", likes=5, comments=None),
    ]

    result = calculate_engagement_percentiles(
        target, observed, as_of=AS_OF, config=CONFIG
    )

    assert result["baseline_status"] == "supported"
    assert result["metric_sample_sizes"]["like"] == 5
    assert result["metric_sample_sizes"]["comment"] == 4
    assert result["metric_sample_sizes"]["creator_adjusted"] == 4
    assert is_supported_outlier(result, threshold=60, metric="like")
    assert is_supported_outlier(result, threshold=50, metric="creator_adjusted")

    sparse_adjustment = dict(result)
    sparse_adjustment["metric_sample_sizes"] = dict(result["metric_sample_sizes"])
    sparse_adjustment["metric_sample_sizes"]["creator_adjusted"] = 3
    assert not is_supported_outlier(
        sparse_adjustment, threshold=50, metric="creator_adjusted"
    )


def test_unavailable_dimensions_or_no_observed_comparables_stay_unavailable():
    missing_age = root("target", likes=10)
    missing_age["published_at"] = None

    result = calculate_engagement_percentiles(
        missing_age,
        [root("comparison", likes=0)],
        as_of=AS_OF,
        config=CONFIG,
    )

    assert result["content_age_bucket"] is None
    assert result["like_percentile"] is None
    assert result["baseline_sample_size"] == 0
    assert result["baseline_status"] == "unavailable"
    assert not is_supported_outlier(result, threshold=0)


def test_creator_adjustment_requires_exact_metric_availability_not_zero_fill():
    target = root("target", likes=20, comments=2)
    observed = [
        root("same-a", likes=10, comments=1),
        root("same-b", likes=20, comments=2),
        root("missing-comment", likes=1_000, comments=None),
        root("extra-view", likes=1, comments=1, views=1),
    ]
    weak_config = EngagementBaselineConfig(
        trailing_period=timedelta(days=30), min_supported_sample_size=2
    )

    result = calculate_engagement_percentiles(
        target, observed, as_of=AS_OF, config=weak_config
    )

    assert result["metric_sample_sizes"]["like"] == 4
    assert result["metric_sample_sizes"]["comment"] == 3
    assert result["metric_sample_sizes"]["creator_adjusted"] == 2
    assert result["creator_adjusted_percentile"] == 100.0


def test_prepare_observation_preserves_nulls_zero_and_dimensions():
    item = root(
        "video-1",
        likes=0,
        comments=None,
        reposts=2,
        views=100,
        followers=999,
    )

    prepared = prepare_baseline_observation(item, config=CONFIG)

    assert prepared == {
        "platform": "youtube",
        "root_external_id": "video-1",
        "observed_at": "2026-08-15T12:00:00+00:00",
        "published_at": "2026-08-15T00:00:00+00:00",
        "content_age_seconds": 43_200.0,
        "content_age_bucket": "6h_to_24h",
        "creator_size_bucket": "under_1k",
        "raw_counts": {
            "likes": 0,
            "upvotes": None,
            "comments": None,
            "replies": None,
            "reposts": 2,
            "shares": None,
            "views": 100,
            "bookmarks": None,
            "collects": None,
            "creator_followers": 999,
        },
    }

    reply = dict(item, record_type="reply", depth=1)
    with pytest.raises(ValueError, match="must be roots"):
        prepare_baseline_observation(reply)


def test_config_and_outlier_inputs_are_validated():
    with pytest.raises(ValueError, match="positive"):
        EngagementBaselineConfig(trailing_period=timedelta(0))
    with pytest.raises(ValueError, match="at least one"):
        EngagementBaselineConfig(min_supported_sample_size=0)
    with pytest.raises(ValueError, match="between 0 and 100"):
        is_supported_outlier({"baseline_status": "supported"}, threshold=101)


@pytest.fixture
def nonempty_legacy_discovery_db(tmp_path):
    """A populated immediate-predecessor DB with the new additive table absent."""
    path = tmp_path / "legacy-discovery.db"
    legacy = DiscoveryStore(path)
    run_id = legacy.record_feed(
        geo="US",
        observed_at=AS_OF - timedelta(days=1),
        candidates=[{
            "keyword": "creator economy tools",
            "search_volume": 12_300,
            "growth_pct": 240,
            "source_started_at": "2026-08-13T00:00:00+00:00",
            "related_terms": ["video analytics", "social listening"],
            "topic_ids": [42],
            "categories": ["Business & Finance"],
        }],
    )
    observation_id = legacy.list_run_candidates(run_id)[0]["observation_id"]
    gate_id = legacy.record_gate_check(
        observation_id,
        status="complete",
        passed=True,
        platforms=["youtube", "reddit"],
        total_items=3,
        independent_voices=2,
        source_health=[{"platform": "youtube", "status": "healthy"}],
        records=[{
            "platform": "youtube",
            "post_id": "legacy-video",
            "record_type": "post",
            "title": "A real stored root",
            "engagement": {"likes": 17, "comments": 3, "views": 800},
        }],
    )
    # Downgrade only the Task 2.3 addition.  All realistic non-empty legacy
    # discovery rows remain and must survive the next initialization.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE engagement_baseline_observations")
        connection.execute(
            "DELETE FROM schema_migrations "
            "WHERE name = '2026_08_15_engagement_baseline_observations'"
        )
    return path, run_id, observation_id, gate_id


def test_additive_schema_migrates_nonempty_legacy_db_without_data_loss(
    nonempty_legacy_discovery_db,
):
    path, run_id, observation_id, gate_id = nonempty_legacy_discovery_db

    migrated = DiscoveryStore(path)

    candidates = migrated.list_run_candidates(run_id)
    assert candidates[0]["keyword"] == "creator economy tools"
    checks = migrated.list_gate_checks(observation_id)
    assert checks[0]["id"] == gate_id
    assert checks[0]["records"][0]["post_id"] == "legacy-video"
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'engagement_baseline_observations'"
        ).fetchone()
        migration = connection.execute(
            "SELECT name FROM schema_migrations "
            "WHERE name = '2026_08_15_engagement_baseline_observations'"
        ).fetchone()
    assert table == ("engagement_baseline_observations",)
    assert migration == ("2026_08_15_engagement_baseline_observations",)


def test_storage_retains_raw_counts_and_lists_an_inclusive_trailing_window(tmp_path):
    store = DiscoveryStore(tmp_path / "engagement.db")
    current = root("current", likes=0, comments=None, reposts=2, views=100)
    boundary = root(
        "boundary",
        observed_at=AS_OF - timedelta(days=30),
        likes=1,
        comments=0,
        reposts=None,
        views=None,
    )
    stale = root(
        "stale",
        observed_at=AS_OF - timedelta(days=30, seconds=1),
        likes=999,
    )
    other_platform = root("other", platform="tiktok", likes=999)

    current_id = store.record_engagement_baseline_observation(current, config=CONFIG)
    assert store.record_engagement_baseline_observation(current, config=CONFIG) == current_id
    store.record_engagement_baseline_observation(boundary, config=CONFIG)
    store.record_engagement_baseline_observation(stale, config=CONFIG)
    store.record_engagement_baseline_observation(other_platform, config=CONFIG)

    rows = store.list_engagement_baseline_observations(
        platform="YouTube",
        observed_through=AS_OF,
        trailing_period=timedelta(days=30),
    )

    assert [row["root_external_id"] for row in rows] == ["boundary", "current"]
    assert rows[0]["content_age_bucket"] == "6h_to_24h"
    assert rows[1]["engagement"]["likes"] == 0
    assert rows[1]["engagement"]["comments"] is None
    assert rows[1]["engagement"]["reposts"] == 2
    assert rows[1]["engagement"]["views"] == 100
    assert rows[1]["raw_counts"] == rows[1]["engagement"]

    conflicting = root("current", likes=1, comments=None, reposts=2, views=100)
    with pytest.raises(ValueError, match="conflicting engagement baseline"):
        store.record_engagement_baseline_observation(conflicting, config=CONFIG)
    with pytest.raises(ValueError, match="positive timedelta"):
        store.list_engagement_baseline_observations(
            platform="youtube",
            observed_through=AS_OF,
            trailing_period=timedelta(0),
        )


def test_persisted_observations_feed_the_deterministic_calculator(tmp_path):
    store = DiscoveryStore(tmp_path / "engagement.db")
    for index, likes in enumerate((10, 20, 30, 40), start=1):
        store.record_engagement_baseline_observation(
            root(str(index), likes=likes), config=CONFIG
        )
    persisted = store.list_engagement_baseline_observations(
        platform="youtube",
        observed_through=AS_OF,
        trailing_period=CONFIG.trailing_period,
    )

    result = calculate_engagement_percentiles(
        root("target", likes=30),
        persisted,
        as_of=AS_OF,
        config=CONFIG,
    )

    assert result["like_percentile"] == 75.0
    assert result["baseline_sample_size"] == 4
    assert result["baseline_status"] == "supported"
