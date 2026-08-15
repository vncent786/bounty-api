"""Focused tests for deterministic root deduplication."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

from social_scraper.conversations.deduplication import (
    canonicalize_url,
    deduplicate_roots,
    normalized_content_hash,
)


def test_identity_order_deduplicates_platform_external_id_and_preserves_provenance():
    roots = [
        {
            "platform": "YouTube",
            "post_id": "video-1",
            "url": "https://www.youtube.com/watch?v=video-1&utm_source=first",
            "text": "Original text",
            "provenance": {"connector": "youtube", "query": "seed one"},
        },
        {
            "platform": "youtube",
            "external_id": "video-1",
            "url": "https://youtube.com/watch?utm_medium=social&v=video-1",
            "text": "Connector returned richer text",
            "provenance": {"connector": "youtube", "query": "seed two"},
        },
    ]
    original = deepcopy(roots)

    result = deduplicate_roots(roots)

    assert result.input_root_count == 2
    assert result.unique_root_count == 1
    assert result.duplicate_observation_count == 1
    assert result.groups[0].match_bases == ("platform_external_id", "canonical_url")
    assert result.groups[0].observations == tuple(original)
    assert roots == original  # the collection payload is never mutated
    assert result.unique_roots == [original[0]]

    manifest = result.to_dict()
    assert manifest["groups"][0]["observations"] == original
    assert [
        observation["provenance"]
        for observation in manifest["groups"][0]["observations"]
    ] == [root["provenance"] for root in original]


def test_canonical_url_is_the_second_identity_and_removes_tracking_noise():
    roots = [
        {
            "platform": "reddit",
            "url": "http://WWW.Reddit.com/r/python/comments/abc/?utm_campaign=a#reply",
            "text": "same post",
        },
        {
            "platform": "reddit",
            "url": "https://reddit.com/r/python/comments/abc",
            "text": "different connector representation",
        },
    ]

    assert canonicalize_url(roots[0]["url"]) == canonicalize_url(roots[1]["url"])
    result = deduplicate_roots(roots)
    assert result.unique_root_count == 1
    assert result.groups[0].match_bases == ("canonical_url",)


def test_exact_normalized_content_hash_is_only_a_fallback_identity():
    hash_only = [
        {"platform": "unknown", "title": "  A  TITLE ", "text": "One\nidea"},
        {"platform": "unknown", "title": "a title", "text": "one   IDEA"},
    ]
    assert normalized_content_hash(hash_only[0]) == normalized_content_hash(hash_only[1])
    assert deduplicate_roots(hash_only).unique_root_count == 1

    # Distinct stable source identities must survive for propagation analysis,
    # even when their normalized content is an exact copy.
    source_roots = [
        {**hash_only[0], "platform": "x", "external_id": "1"},
        {**hash_only[1], "platform": "x", "external_id": "2"},
    ]
    result = deduplicate_roots(source_roots)
    assert result.unique_root_count == 2


def test_no_near_duplicate_grouping_without_a_measured_threshold():
    roots = [
        {"platform": "unknown", "text": "A product fixes setup pain."},
        {"platform": "unknown", "text": "A product fixes setup pains."},
    ]

    assert normalized_content_hash(roots[0]) != normalized_content_hash(roots[1])
    assert deduplicate_roots(roots).unique_root_count == 2


def test_unidentifiable_roots_are_retained_independently_not_dropped_or_invented():
    roots = [{"platform": "x", "text": ""}, {"platform": "x", "title": "  "}]
    result = deduplicate_roots(roots)

    assert result.unique_root_count == 2
    assert all(group.identity is None for group in result.groups)


def test_root_probe_integrates_unique_counts_propagation_and_full_provenance():
    from social_scraper.discovery.handlers import build_handlers

    async def run():
        duplicate = {
            "platform": "youtube",
            "external_id": "v1",
            "url": "https://youtube.com/watch?v=v1",
            "text": "A root",
            "author": {"id": "author-1"},
            "engagement": {"views": 100},
            "provenance": {"connector": "youtube", "query": "first"},
        }
        broker = MagicMock()
        broker.search = AsyncMock(
            return_value={
                "items": [
                    duplicate,
                    {
                        **duplicate,
                        "provenance": {"connector": "youtube", "query": "second"},
                    },
                ],
                "source_health": [],
                "platform_results": {"youtube": {"status": "complete"}},
            }
        )
        handlers, collected = build_handlers(broker)
        result = await handlers["root_probe"](
            {"candidate_id": "candidate-1", "keyword": "topic", "platforms": ["youtube"]}
        )

        assert result.records_returned == 1
        payload = result.candidates[0]
        assert len(payload["_root_items"]) == 1
        assert payload["_root_summary"] == {
            "unique_root_count": 1,
            "independent_author_count": 1,
            "repost_cluster_count": 0,
            "largest_repost_cluster_size": 0,
            "propagation_reach": {
                "clustered_root_count": 0,
                "repost_root_count": 0,
                "platform_count": 0,
                "platforms": [],
                "engagement": {
                    metric: {"total": None, "observed_root_count": 0}
                    for metric in (
                        "likes",
                        "upvotes",
                        "comments",
                        "replies",
                        "views",
                        "shares",
                        "reposts",
                        "bookmarks",
                    )
                },
            },
        }
        observations = payload["_root_deduplication"]["groups"][0]["observations"]
        assert [item["provenance"]["query"] for item in observations] == ["first", "second"]
        assert [
            entry["query"]
            for entry in payload["_root_items"][0]["_collection_provenance"]
        ] == ["first", "second"]
        assert collected["candidate-1"] == payload["_root_items"]
        assert collected["candidate-1:root_summary"] == payload["_root_summary"]
        assert (
            collected["candidate-1:root_deduplication"]
            == payload["_root_deduplication"]
        )

    asyncio.run(run())
