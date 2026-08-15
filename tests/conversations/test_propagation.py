"""Focused tests for separating propagation from independent corroboration."""

from social_scraper.conversations.propagation import summarize_propagation


def _root(
    external_id,
    author_id,
    *,
    platform="x",
    text=None,
    is_repost=None,
    repost_of_external_id=None,
    created_at=None,
    engagement=None,
):
    return {
        "platform": platform,
        "external_id": external_id,
        "url": f"https://example.test/{platform}/{external_id}",
        "text": text or f"root {external_id}",
        "author": {"id": author_id},
        "is_repost": is_repost,
        "repost_of_external_id": repost_of_external_id,
        "created_at": created_at,
        "engagement": engagement or {},
    }


def test_native_repost_chain_is_one_cluster_and_repost_authors_are_not_independent():
    roots = [
        _root("1", "origin", engagement={"views": 100, "likes": 4}),
        _root(
            "2",
            "amplifier-a",
            is_repost=True,
            repost_of_external_id="1",
            engagement={"views": 25},
        ),
        _root(
            "3",
            "amplifier-b",
            is_repost=True,
            repost_of_external_id="2",
            engagement={"likes": 2},
        ),
        _root("other", "independent", platform="reddit", engagement={"views": 50}),
    ]

    summary = summarize_propagation(roots)

    assert summary.unique_root_count == 4
    assert summary.independent_author_count == 2
    assert summary.repost_cluster_count == 1
    assert summary.largest_repost_cluster_size == 3
    assert summary.propagation_reach == {
        "clustered_root_count": 3,
        "repost_root_count": 2,
        "platform_count": 1,
        "platforms": ["x"],
        "engagement": {
            "likes": {"total": 6, "observed_root_count": 2},
            "upvotes": {"total": None, "observed_root_count": 0},
            "comments": {"total": None, "observed_root_count": 0},
            "replies": {"total": None, "observed_root_count": 0},
            "views": {"total": 125, "observed_root_count": 2},
            "shares": {"total": None, "observed_root_count": 0},
            "reposts": {"total": None, "observed_root_count": 0},
            "bookmarks": {"total": None, "observed_root_count": 0},
        },
    }
    assert summary.clusters[0].relationship_bases == ("native_repost",)
    assert summary.clusters[0].member_count == 3


def test_exact_content_copies_with_distinct_source_ids_are_propagation_not_deduped_voices():
    roots = [
        _root(
            "later",
            "copy-author",
            text="  Same launch NEWS ",
            created_at="2026-08-15T11:00:00Z",
        ),
        _root(
            "earlier",
            "origin-author",
            text="same launch news",
            created_at="2026-08-15T10:00:00Z",
        ),
        _root("independent", "third-author", text="My own analysis"),
    ]

    summary = summarize_propagation(roots)

    assert summary.unique_root_count == 3
    assert summary.independent_author_count == 2
    assert summary.repost_cluster_count == 1
    assert summary.largest_repost_cluster_size == 2
    cluster = summary.clusters[0]
    assert cluster.relationship_bases == ("exact_content",)
    assert cluster.original_external_id == "earlier"
    assert cluster.original_observed is True


def test_near_duplicate_text_is_not_a_repost_cluster():
    roots = [
        _root("1", "a", text="The setup is painful."),
        _root("2", "b", text="The setup was painful."),
    ]

    summary = summarize_propagation(roots)

    assert summary.independent_author_count == 2
    assert summary.repost_cluster_count == 0
    assert summary.largest_repost_cluster_size == 0


def test_reported_repost_count_is_reach_not_extra_independent_corroboration():
    summary = summarize_propagation(
        [_root("viral", "one-origin", engagement={"views": 5000, "reposts": 12})]
    )

    assert summary.unique_root_count == 1
    assert summary.independent_author_count == 1
    assert summary.repost_cluster_count == 1
    # Only one root was observed; the source count is retained as reach and is
    # never converted into twelve invented root/author records.
    assert summary.largest_repost_cluster_size == 1
    assert summary.propagation_reach["repost_root_count"] == 0
    assert summary.propagation_reach["engagement"]["reposts"] == {
        "total": 12,
        "observed_root_count": 1,
    }
    assert summary.clusters[0].relationship_bases == ("reported_repost_count",)


def test_orphan_native_repost_remains_propagation_with_unobserved_original():
    summary = summarize_propagation(
        [
            _root(
                "copy",
                "amplifier",
                is_repost=True,
                repost_of_external_id="missing-original",
                engagement={"shares": 7},
            )
        ]
    )

    assert summary.unique_root_count == 1
    assert summary.independent_author_count == 0
    assert summary.repost_cluster_count == 1
    assert summary.largest_repost_cluster_size == 1
    assert summary.propagation_reach["repost_root_count"] == 1
    assert summary.clusters[0].original_observed is False
    assert summary.clusters[0].original_external_id == "missing-original"


def test_author_identity_is_platform_scoped_and_unknown_authors_are_not_invented():
    roots = [
        _root("x-1", "same-name", platform="x"),
        _root("r-1", "same-name", platform="reddit"),
        {
            "platform": "youtube",
            "external_id": "y-1",
            "text": "anonymous evidence",
            "author": {},
        },
    ]

    summary = summarize_propagation(roots)
    assert summary.unique_root_count == 3
    assert summary.independent_author_count == 2
    assert summary.to_dict().keys() == {
        "unique_root_count",
        "independent_author_count",
        "repost_cluster_count",
        "largest_repost_cluster_size",
        "propagation_reach",
    }
