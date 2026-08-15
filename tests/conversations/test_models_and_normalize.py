"""Contract tests for canonical conversation models and normalization."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from social_scraper.conversations import (
    NormalizationError,
    CanonicalObservation,
    conversation_identity,
    normalize_broker_item,
    payload_hash,
)
from social_scraper.conversations.models import ENGAGEMENT_FIELDS


COLLECTED_AT = "2026-08-10T12:00:00+00:00"
CANONICAL_METRICS = (
    "likes",
    "upvotes",
    "comments",
    "replies",
    "views",
    "shares",
    "reposts",
    "bookmarks",
    "creator_followers",
)
LEGACY_METRICS = ("collects",)
FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "social"
    / "conversations"
    / "broker_items.json"
)


def broker_item(**overrides):
    item = {
        "platform": "reddit",
        "post_id": "t3_abc123",
        "url": "https://reddit.com/r/products/comments/abc123/example/",
        "author": {
            "id": None,
            "username": "source-user",
            "display_name": None,
            "profile_url": "https://reddit.com/user/source-user",
            "follower_count": None,
        },
        "title": "Customers are switching",
        "text": "I cancelled Alpha and bought Beta instead.",
        "created_at": "2026-08-09T09:30:00+00:00",
        "engagement": {
            "views": None,
            "likes": 0,
            "comments": 12,
            "shares": None,
            "collects": None,
        },
        "language": "en",
        "provenance": {
            "connector": "reddit_mobile_owned",
            "source_observed_at": "2026-08-10T11:59:00+00:00",
            "query": "switched from alpha",
        },
    }
    item.update(overrides)
    return item


def test_normalization_preserves_complete_current_item():
    bundle = normalize_broker_item(broker_item(), collected_at=COLLECTED_AT)
    record = bundle.record
    observation = bundle.observation

    assert record.platform == "reddit"
    assert record.external_id == "t3_abc123"
    assert record.record_type == "post"
    assert record.root_post_external_id == "t3_abc123"
    assert record.parent_external_id is None
    assert record.depth == 0
    assert record.source_route == "reddit_mobile_owned"
    assert record.author_username == "source-user"
    assert record.title == "Customers are switching"
    assert record.published_at == "2026-08-09T09:30:00+00:00"
    assert observation.collected_at == COLLECTED_AT
    assert observation.source_observed_at == "2026-08-10T11:59:00+00:00"
    assert observation.engagement == {
        "views": None,
        "likes": 0,
        "comments": 12,
        "shares": None,
        "collects": None,
        "upvotes": None,
        "replies": None,
        "reposts": None,
        "bookmarks": None,
        "creator_followers": None,
    }
    assert observation.engagement_sources == {"likes": "likes", "comments": "comments"}
    assert set(observation.to_dict()["engagement"]) == set(ENGAGEMENT_FIELDS)
    assert observation.to_dict()["engagement_sources"] == {
        "likes": "likes",
        "comments": "comments",
    }


def test_all_current_platform_fixtures_normalize_without_inventing_metrics():
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_missing = {
        "youtube": {"shares", "collects"},
        "reddit": {"views", "shares", "collects"},
        "tiktok": set(),
        "x": {"collects"},
        "instagram": {"shares", "collects"},
    }

    assert set(fixtures) == {"youtube", "reddit", "tiktok", "x", "instagram"}
    for platform, item in fixtures.items():
        bundle = normalize_broker_item(item, collected_at=COLLECTED_AT)
        assert bundle.record.platform == platform
        assert bundle.record.external_id == str(item["post_id"])
        assert bundle.record.source_route == item["provenance"]["connector"]
        for metric in expected_missing[platform]:
            assert bundle.observation.engagement[metric] is None
        for metric in ("upvotes", "replies", "reposts", "creator_followers"):
            assert bundle.observation.engagement[metric] is None
        collects = bundle.observation.engagement["collects"]
        if collects is None:
            assert bundle.observation.engagement["bookmarks"] is None
        else:
            assert bundle.observation.engagement["bookmarks"] == collects
            assert (
                bundle.observation.engagement_sources["bookmarks"] == "collects"
            )
        assert "upvotes" not in bundle.observation.engagement_sources
        assert "creator_followers" not in bundle.observation.engagement_sources


def test_missing_values_remain_none_and_zero_remains_zero():
    item = {
        "platform": "youtube",
        "post_id": "video-1",
        "engagement": {"views": 0},
        "author": {},
    }
    bundle = normalize_broker_item(item, collected_at=COLLECTED_AT)

    assert bundle.record.text is None
    assert bundle.record.published_at is None
    assert bundle.record.author_external_id is None
    assert bundle.record.is_repost is None
    assert bundle.observation.engagement["views"] == 0
    assert bundle.observation.engagement["likes"] is None


def test_identity_ignores_route_query_engagement_and_collection_time():
    first = normalize_broker_item(broker_item(), collected_at=COLLECTED_AT)
    changed = broker_item(
        engagement={"likes": 200},
        provenance={"connector": "reddit_rss_owned", "query": "alpha churn"},
    )
    second = normalize_broker_item(
        changed,
        collected_at="2026-08-11T12:00:00+00:00",
    )

    assert first.record.identity_key == second.record.identity_key
    assert first.record.raw_payload_hash != second.record.raw_payload_hash


def test_identity_is_namespaced_by_platform():
    assert conversation_identity("reddit", "123") != conversation_identity("youtube", "123")


def test_identity_is_namespaced_by_source_object_type():
    assert conversation_identity("reddit", "same", "post") != conversation_identity(
        "reddit", "same", "comment"
    )


def test_numeric_zero_source_ids_are_preserved():
    bundle = normalize_broker_item(
        {
            "platform": "x",
            "post_id": 0,
            "record_type": "reply",
            "parent_external_id": 0,
            "root_post_external_id": 0,
            "depth": 2,
            "author": {"id": 0},
        },
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.external_id == "0"
    assert bundle.record.parent_external_id == "0"
    assert bundle.record.root_post_external_id == "0"
    assert bundle.record.author_external_id == "0"


def test_nanosecond_timestamp_precision_is_preserved():
    source_time = "2026-01-01T00:00:00.123456789Z"
    bundle = normalize_broker_item(
        broker_item(created_at=source_time),
        collected_at=source_time,
    )
    assert bundle.record.published_at == source_time
    assert bundle.observation.collected_at == source_time


def test_payload_hash_is_canonical_for_json_key_order():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_normalizer_does_not_mutate_input():
    item = broker_item()
    before = deepcopy(item)
    normalize_broker_item(item, collected_at=COLLECTED_AT)
    assert item == before


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"post_id": "1"}, "missing_platform"),
        ({"platform": "reddit"}, "missing_external_id"),
        ("not-a-dict", "item_not_object"),
    ],
)
def test_missing_identity_is_rejected_without_invention(item, reason):
    with pytest.raises(NormalizationError, match=reason):
        normalize_broker_item(item, collected_at=COLLECTED_AT)


def test_date_only_publication_is_not_promoted_to_midnight():
    bundle = normalize_broker_item(
        broker_item(created_at="2026-08-09"),
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.published_at is None
    assert bundle.record.published_date == "2026-08-09"


def test_timezone_less_source_timestamp_remains_missing():
    bundle = normalize_broker_item(
        broker_item(created_at="2026-08-09T09:30:00"),
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.published_at is None


def test_explicit_nested_reply_relationships_are_preserved():
    bundle = normalize_broker_item(
        broker_item(
            post_id="t1_reply",
            record_type="reply",
            parent_external_id="t1_parent",
            root_post_external_id="t3_root",
            depth=2,
        ),
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.record_type == "reply"
    assert bundle.record.parent_external_id == "t1_parent"
    assert bundle.record.root_post_external_id == "t3_root"
    assert bundle.record.depth == 2


def test_comment_count_does_not_create_synthetic_records():
    bundle = normalize_broker_item(
        broker_item(engagement={"comments": 25}),
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.record_type == "post"
    assert bundle.observation.engagement["comments"] == 25


def test_canonical_engagement_key_set_is_exact():
    assert set(ENGAGEMENT_FIELDS) == set(CANONICAL_METRICS) | set(LEGACY_METRICS)
    bundle = normalize_broker_item(broker_item(), collected_at=COLLECTED_AT)
    assert set(bundle.observation.engagement) == set(ENGAGEMENT_FIELDS)
    assert set(bundle.observation.to_dict()) == {
        "collected_at",
        "source_observed_at",
        "engagement",
        "engagement_sources",
    }


def test_unsupported_metrics_remain_none_without_zero_fill():
    bundle = normalize_broker_item(
        broker_item(engagement={"likes": 12}),
        collected_at=COLLECTED_AT,
    )
    engagement = bundle.observation.engagement
    assert engagement["likes"] == 12
    for metric in (
        "upvotes",
        "comments",
        "replies",
        "views",
        "shares",
        "reposts",
        "bookmarks",
        "creator_followers",
        "collects",
    ):
        assert engagement[metric] is None
    assert bundle.observation.engagement_sources == {"likes": "likes"}


@pytest.mark.parametrize("metric", CANONICAL_METRICS)
def test_explicit_zero_remains_zero(metric):
    bundle = normalize_broker_item(
        broker_item(engagement={metric: 0}),
        collected_at=COLLECTED_AT,
    )
    assert bundle.observation.engagement[metric] == 0
    assert bundle.observation.engagement_sources[metric] == metric


def test_legacy_collects_populates_bookmarks_with_provenance():
    bundle = normalize_broker_item(
        broker_item(engagement={"collects": 7}),
        collected_at=COLLECTED_AT,
    )
    engagement = bundle.observation.engagement
    assert engagement["collects"] == 7
    assert engagement["bookmarks"] == 7
    assert bundle.observation.engagement_sources["bookmarks"] == "collects"
    assert bundle.observation.engagement_sources["collects"] == "collects"


def test_explicit_bookmarks_wins_over_collects_fallback():
    bundle = normalize_broker_item(
        broker_item(engagement={"bookmarks": 3, "collects": 9}),
        collected_at=COLLECTED_AT,
    )
    assert bundle.observation.engagement["bookmarks"] == 3
    assert bundle.observation.engagement["collects"] == 9
    assert bundle.observation.engagement_sources["bookmarks"] == "bookmarks"


def test_creator_followers_falls_back_to_author_follower_count_with_provenance():
    bundle = normalize_broker_item(
        broker_item(author={"username": "source-user", "follower_count": 4321}),
        collected_at=COLLECTED_AT,
    )
    assert bundle.observation.engagement["creator_followers"] == 4321
    assert (
        bundle.observation.engagement_sources["creator_followers"]
        == "author.follower_count"
    )


def test_explicit_creator_followers_wins_over_author_fallback():
    bundle = normalize_broker_item(
        broker_item(
            author={"username": "source-user", "follower_count": 4321},
            engagement={"creator_followers": 10},
        ),
        collected_at=COLLECTED_AT,
    )
    assert bundle.observation.engagement["creator_followers"] == 10
    assert (
        bundle.observation.engagement_sources["creator_followers"]
        == "creator_followers"
    )


def test_invalid_metric_values_are_not_coerced():
    bundle = normalize_broker_item(
        broker_item(
            engagement={
                "likes": "12",
                "upvotes": 1.5,
                "reposts": True,
                "views": False,
            }
        ),
        collected_at=COLLECTED_AT,
    )
    engagement = bundle.observation.engagement
    assert engagement["likes"] is None
    assert engagement["upvotes"] is None
    assert engagement["reposts"] is None
    assert engagement["views"] is None
    for metric in ("likes", "upvotes", "reposts", "views"):
        assert metric not in bundle.observation.engagement_sources


def test_invalid_bookmarks_does_not_fall_back_to_collects():
    bundle = normalize_broker_item(
        broker_item(engagement={"bookmarks": "7", "collects": 9}),
        collected_at=COLLECTED_AT,
    )
    assert bundle.observation.engagement["bookmarks"] is None
    assert bundle.observation.engagement["collects"] == 9
    assert "bookmarks" not in bundle.observation.engagement_sources


def test_connector_engagement_field_provenance_is_recorded():
    bundle = normalize_broker_item(
        broker_item(
            engagement={"collects": 5, "bookmarks": 5, "views": 900},
            provenance={
                "connector": "tiktok_auth_web",
                "engagement_sources": {
                    "collects": "collect_count",
                    "bookmarks": "collect_count",
                },
            },
        ),
        collected_at=COLLECTED_AT,
    )
    sources = bundle.observation.engagement_sources
    assert sources["collects"] == "collect_count"
    assert sources["bookmarks"] == "collect_count"
    assert sources["views"] == "views"
    assert "upvotes" not in sources


def test_connector_provenance_for_absent_metrics_is_ignored():
    bundle = normalize_broker_item(
        broker_item(
            engagement={"likes": 4},
            provenance={
                "connector": "tiktok_auth_web",
                "engagement_sources": {"upvotes": "digg_count", "likes": 3},
            },
        ),
        collected_at=COLLECTED_AT,
    )
    sources = bundle.observation.engagement_sources
    assert sources == {"likes": "likes"}


def test_reply_records_retain_own_engagement_parent_and_depth():
    bundle = normalize_broker_item(
        broker_item(
            post_id="t1_reply",
            record_type="reply",
            parent_external_id="t1_parent",
            root_post_external_id="t3_root",
            depth=2,
            engagement={"likes": 4, "replies": 1, "comments": 0},
        ),
        collected_at=COLLECTED_AT,
    )
    assert bundle.record.record_type == "reply"
    assert bundle.record.parent_external_id == "t1_parent"
    assert bundle.record.root_post_external_id == "t3_root"
    assert bundle.record.depth == 2
    assert bundle.observation.engagement["likes"] == 4
    assert bundle.observation.engagement["replies"] == 1
    assert bundle.observation.engagement["comments"] == 0
    assert bundle.observation.engagement_sources["replies"] == "replies"


def test_observation_rejects_non_integer_engagement():
    with pytest.raises(ValueError, match="upvotes"):
        CanonicalObservation(
            collected_at=COLLECTED_AT,
            engagement={"upvotes": True},
        )


def test_observation_rejects_non_string_engagement_sources():
    with pytest.raises(ValueError, match="engagement source"):
        CanonicalObservation(
            collected_at=COLLECTED_AT,
            engagement_sources={"likes": 3},
        )
