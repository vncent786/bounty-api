"""Task 3.2: deterministic relationship evidence and family planning. No LLM."""

from datetime import datetime, timezone

import pytest

from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.discovery.topic_families import (
    CORRELATIONAL_KINDS,
    build_edge,
    candidate_key,
    cluster_evidence,
    cooccurrence_evidence,
    derive_relationship,
    geographic_co_movement_evidence,
    plan_family_links,
    shared_artifact_evidence,
    temporal_co_movement_evidence,
    trend_keyword_evidence,
)

T1 = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)

X402 = candidate_key("US", "x402")
AGENTIC = candidate_key("US", "agentic payments")
PAYMAN = candidate_key("US", "payman")
PEROVSKITE = candidate_key("US", "perovskite")

RISING_A = [
    {"observed_at": "2026-08-13T04:00:00+00:00", "search_volume": 10},
    {"observed_at": "2026-08-14T04:00:00+00:00", "search_volume": 20},
]
RISING_B = [
    {"observed_at": "2026-08-13T04:00:00+00:00", "search_volume": 5},
    {"observed_at": "2026-08-14T04:00:00+00:00", "search_volume": 9},
]


def test_mutual_related_query_containment_derives_alias_high():
    evidence = trend_keyword_evidence(
        left_keyword="x402",
        left_terms=["x402", "agentic payments", "usdc payments", "ai wallets"],
        right_keyword="agentic payments",
        right_terms=["agentic payments", "x402", "ai wallets"],
    )
    assert evidence["left_in_right_related"] is True
    assert evidence["right_in_left_related"] is True
    assert evidence["shared_terms"] == ["agentic payments", "ai wallets", "x402"]

    edge = build_edge(X402, AGENTIC, evidence, observed_at=T1)
    assert edge["left_candidate_key"] == AGENTIC
    assert edge["right_candidate_key"] == X402
    assert edge["edge_type"] == "trend_keyword_overlap"
    assert edge["strength"] == evidence["term_overlap_ratio"]

    derived = derive_relationship([edge])
    assert derived == {
        "relationship": "alias",
        "confidence": "high",
        "merge_eligible": True,
        "evidence_kinds": ["trend_keyword_overlap"],
    }


def test_one_sided_trend_overlap_is_related_distinct_not_alias():
    evidence = trend_keyword_evidence(
        left_keyword="x402",
        left_terms=["x402", "crypto payments"],
        right_keyword="agentic payments",
        right_terms=["agentic payments", "crypto payments", "ai wallets"],
    )
    assert evidence["left_in_right_related"] is False
    assert evidence["right_in_left_related"] is False
    assert evidence["shared_terms"] == ["crypto payments"]

    derived = derive_relationship([build_edge(X402, AGENTIC, evidence, observed_at=T1)])
    assert derived["relationship"] == "related_distinct"
    assert derived["confidence"] == "medium"
    assert derived["merge_eligible"] is True


def test_disjoint_trend_terms_produce_no_edge():
    assert (
        trend_keyword_evidence(
            left_keyword="x402",
            left_terms=["usdc payments"],
            right_keyword="perovskite",
            right_terms=["building integrated pv"],
        )
        is None
    )


def test_root_and_reply_cooccurrence_counts_shared_items_only():
    left = [
        {"platform": "reddit", "external_id": "r1", "record_type": "reply"},
        {"platform": "reddit", "external_id": "r2", "record_type": "root"},
        {"platform": "youtube", "external_id": "y1", "record_type": "root"},
    ]
    right = [
        {"platform": "reddit", "external_id": "r2", "record_type": "root"},
        {"platform": "reddit", "external_id": "r1", "record_type": "root"},
        {"platform": "tiktok", "external_id": "t9", "record_type": "root"},
    ]
    evidence = cooccurrence_evidence(left, right)
    assert evidence["shared_item_count"] == 2
    assert evidence["shared_root_count"] == 1  # record types follow the left side
    assert evidence["shared_reply_count"] == 1
    assert evidence["sample_items"] == [
        {"platform": "reddit", "external_id": "r1", "record_type": "reply"},
        {"platform": "reddit", "external_id": "r2", "record_type": "root"},
    ]
    assert (
        cooccurrence_evidence(
            left, [{"platform": "tiktok", "external_id": "t9", "record_type": "root"}]
        )
        is None
    )


def test_shared_entities_urls_and_clusters_form_merge_eligible_evidence():
    url = shared_artifact_evidence(
        "shared_url",
        ["https://x402.org/spec", "https://coinbase.com"],
        ["https://X402.org/spec ", "https://arxiv.org/abs/2405.04516"],
    )
    assert url["shared_values"] == ["https://x402.org/spec"]

    entity = shared_artifact_evidence(
        "shared_entity", ["Coinbase", "OpenAI"], ["coinbase", "Stripe"]
    )
    assert entity["shared_values"] == ["coinbase"]

    repost = cluster_evidence("repost_cluster", ["rc-7", "rc-9"], ["rc-7"])
    assert repost["shared_cluster_ids"] == ["rc-7"]
    content = cluster_evidence("content_cluster", ["cc-1"], ["cc-2"])
    assert content is None

    assert shared_artifact_evidence("shared_entity", ["coinbase"], ["stripe"]) is None
    with pytest.raises(ValueError, match="kind must be"):
        shared_artifact_evidence("shared_hashtag", ["a"], ["a"])
    with pytest.raises(ValueError, match="kind must be"):
        cluster_evidence("vibe_cluster", ["a"], ["a"])

    derived = derive_relationship([
        build_edge(X402, AGENTIC, url, observed_at=T1),
        build_edge(X402, AGENTIC, entity, observed_at=T1),
    ])
    assert derived["relationship"] == "related_distinct"
    assert derived["confidence"] == "high"  # two distinct merge-eligible kinds
    assert derived["evidence_kinds"] == ["shared_entity", "shared_url"]


def test_temporal_co_movement_uses_co_observed_points_only():
    left = [
        {"observed_at": "2026-08-13T04:00:00+00:00", "search_volume": 10},
        {"observed_at": "2026-08-14T04:00:00+00:00", "search_volume": 20},
        {"observed_at": "2026-08-15T04:00:00+00:00", "search_volume": None},
    ]
    right = [
        {"observed_at": "2026-08-13T04:00:00+00:00", "search_volume": 5},
        {"observed_at": "2026-08-14T04:00:00+00:00", "search_volume": 9},
        {"observed_at": "2026-08-16T04:00:00+00:00", "search_volume": 40},
    ]
    evidence = temporal_co_movement_evidence(left, right)
    assert evidence["pair_count"] == 2
    assert evidence["excluded_missing"] == 1
    assert evidence["correlation"] == 1.0
    assert evidence["aligned_points"] == [
        ["2026-08-13T04:00:00+00:00", 10, 5],
        ["2026-08-14T04:00:00+00:00", 20, 9],
    ]
    # Fewer than two co-observed points never yields an edge.
    assert (
        temporal_co_movement_evidence(
            [{"observed_at": "2026-08-13T04:00:00+00:00", "search_volume": 10}], right
        )
        is None
    )


def test_geographic_co_movement_shares_uppercased_geos():
    evidence = geographic_co_movement_evidence(["US", "SG"], ["us", "GB"])
    assert evidence == {
        "kind": "geographic_co_movement",
        "shared_geos": ["US"],
    }
    assert geographic_co_movement_evidence(["US"], ["GB"]) is None


def test_correlational_evidence_alone_never_merges_candidates():
    temporal = build_edge(
        X402,
        AGENTIC,
        temporal_co_movement_evidence(RISING_A, RISING_B),
        observed_at=T1,
    )
    geographic = build_edge(
        X402,
        AGENTIC,
        geographic_co_movement_evidence(["US", "SG"], ["US", "GB"]),
        observed_at=T1,
    )
    assert temporal["strength"] == 1.0

    derived = derive_relationship([temporal, geographic])
    assert derived == {
        "relationship": "uncertain",
        "confidence": "low",
        "merge_eligible": False,
        "evidence_kinds": ["geographic_co_movement", "temporal_co_movement"],
    }
    # The approved plan's hard rule: temporal (or geographic) co-movement
    # alone produces no family at all.
    assert plan_family_links([temporal, geographic]) == []
    assert CORRELATIONAL_KINDS == {"temporal_co_movement", "geographic_co_movement"}


def test_temporal_evidence_joins_a_merge_but_never_carries_one_alone():
    temporal = build_edge(
        X402,
        AGENTIC,
        temporal_co_movement_evidence(RISING_A, RISING_B),
        observed_at=T1,
    )
    url = build_edge(
        X402,
        AGENTIC,
        shared_artifact_evidence("shared_url", ["https://x402.org/spec"], ["https://X402.org/spec"]),
        observed_at=T1,
    )
    derived = derive_relationship([temporal, url])
    assert derived["merge_eligible"] is True
    assert derived["relationship"] == "related_distinct"
    # Only one merge-eligible kind: correlational company never upgrades
    # confidence.
    assert derived["confidence"] == "medium"


def test_plan_family_links_unions_only_merge_eligible_edges():
    alias = build_edge(
        X402,
        AGENTIC,
        trend_keyword_evidence(
            left_keyword="x402",
            left_terms=["x402", "agentic payments"],
            right_keyword="agentic payments",
            right_terms=["agentic payments", "x402"],
        ),
        observed_at=T1,
    )
    entity = build_edge(
        AGENTIC,
        PAYMAN,
        shared_artifact_evidence("shared_entity", ["Payman"], ["payman"]),
        observed_at=T1,
    )
    temporal_only = build_edge(
        candidate_key("US", "perovskite solar"),
        PEROVSKITE,
        temporal_co_movement_evidence(RISING_A, RISING_B),
        observed_at=T1,
    )

    families = plan_family_links([alias, entity, temporal_only])
    assert len(families) == 1
    family = families[0]
    assert family["members"] == sorted([X402, AGENTIC, PAYMAN])
    assert sorted((link["left"], link["right"]) for link in family["links"]) == [
        ("US:agentic payments", "US:payman"),
        ("US:agentic payments", "US:x402"),
    ]
    alias_link = next(l for l in family["links"] if l["right"] == X402)
    assert alias_link["relationship"] == "alias"
    assert alias_link["confidence"] == "high"
    assert alias_link["evidence_kinds"] == ["trend_keyword_overlap"]

    assert plan_family_links([]) == []
    with pytest.raises(ValueError, match="unknown evidence kind"):
        plan_family_links([
            {"left_candidate_key": X402, "right_candidate_key": AGENTIC,
             "edge_type": "gut_feeling", "evidence": {}},
        ])


def test_build_edge_canonicalizes_order_and_validates_inputs():
    evidence = shared_artifact_evidence("shared_url", ["https://a.io"], ["https://A.io"])
    forward = build_edge(X402, AGENTIC, evidence, observed_at=T1)
    backward = build_edge(AGENTIC, X402, evidence, observed_at=T1)
    assert forward == backward
    assert forward["observed_at"] == T1.isoformat()
    as_string = build_edge(X402, AGENTIC, evidence, observed_at="2026-08-14T04:00:00+00:00")
    assert as_string["observed_at"] == "2026-08-14T04:00:00+00:00"

    with pytest.raises(ValueError, match="two different candidates"):
        build_edge(X402, X402, evidence, observed_at=T1)
    with pytest.raises(ValueError, match="unknown evidence kind"):
        build_edge(X402, AGENTIC, {"kind": "gut_feeling"}, observed_at=T1)
    with pytest.raises(ValueError, match="non-empty mapping"):
        build_edge(X402, AGENTIC, {}, observed_at=T1)
    with pytest.raises(ValueError, match="geo is required"):
        candidate_key("", "x402")


def test_deterministic_edges_round_trip_storage_into_one_family(tmp_path):
    store = DiscoveryStore(tmp_path / "edges.db")
    run_id = store.record_feed(
        geo="US",
        observed_at=T1,
        candidates=[
            {
                "keyword": "x402",
                "related_terms": ["x402", "agentic payments"],
                "search_volume": 50_000,
                "growth_pct": 320,
                "source_started_at": "2026-08-14T03:00:00+00:00",
                "topic_ids": [],
                "categories": [],
            },
            {
                "keyword": "agentic payments",
                "related_terms": ["agentic payments", "x402"],
                "search_volume": 20_000,
                "growth_pct": 210,
                "source_started_at": "2026-08-14T03:00:00+00:00",
                "topic_ids": [],
                "categories": [],
            },
        ],
    )
    observations = {row["keyword"]: row for row in store.list_run_candidates(run_id)}
    left, right = observations["x402"], observations["agentic payments"]

    evidence = trend_keyword_evidence(
        left_keyword="x402",
        left_terms=left["related_terms"],
        right_keyword="agentic payments",
        right_terms=right["related_terms"],
    )
    edge = build_edge(X402, AGENTIC, evidence, observed_at=T1)
    edge_id = store.record_topic_edge(
        left_geo="US",
        left_keyword="x402",
        right_geo="US",
        right_keyword="agentic payments",
        edge_type=edge["edge_type"],
        strength=edge["strength"],
        evidence=edge["evidence"],
        observed_at=edge["observed_at"],
    )

    stored = store.list_topic_edges(geo="US", keyword="x402")
    assert [row["id"] for row in stored] == [edge_id]
    assert stored[0]["evidence"]["shared_terms"] == evidence["shared_terms"]

    families = plan_family_links(stored)
    assert len(families) == 1
    plan = families[0]

    family = store.create_topic_family(canonical_label="x402 / agentic payments")
    link = plan["links"][0]
    for member in plan["members"]:
        geo, keyword = member.split(":", 1)
        store.link_topic_family_member(
            family_id=family["id"],
            geo=geo,
            keyword=keyword,
            relationship=link["relationship"],
            confidence=link["confidence"],
            evidence={
                "evidence_kinds": link["evidence_kinds"],
                "edge_ids": link["edge_ids"],
            },
        )
    read = store.get_topic_family(family["id"])
    assert [m["normalized_keyword"] for m in read["memberships"]] == [
        "agentic payments",
        "x402",
    ]
    assert {m["relationship"] for m in read["memberships"]} == {"alias"}
    assert read["memberships"][0]["evidence"]["edge_ids"] == [edge_id]
