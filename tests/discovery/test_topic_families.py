"""Task 3.1: topic family persistence — strict APIs, immutability, migration."""

import sqlite3
from datetime import datetime, timezone

import pytest

from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.discovery.topic_families import RELATIONSHIPS

T1 = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 14, 5, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)


def candidate(keyword, **overrides):
    payload = {
        "keyword": keyword,
        "related_terms": [keyword, "payments"],
        "search_volume": 50_000,
        "growth_pct": 320,
        "source_started_at": "2026-08-14T03:00:00+00:00",
        "topic_ids": [7],
        "categories": ["Business & Finance"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def store(tmp_path):
    unit = DiscoveryStore(tmp_path / "families.db")
    unit.record_feed(
        geo="US",
        observed_at=T1,
        candidates=[
            candidate("x402", related_terms=["x402", "agentic payments", "usdc payments"]),
            candidate(
                "agentic payments",
                related_terms=["agentic payments", "x402", "ai wallets"],
            ),
        ],
    )
    return unit


def family_kwargs(**overrides):
    kwargs = dict(
        family_id=None,
        geo="US",
        keyword="x402",
        relationship="alias",
        confidence="high",
        evidence={"kind": "trend_keyword_overlap", "shared_terms": ["agentic payments"]},
    )
    kwargs.update(overrides)
    return kwargs


def test_relationship_enum_is_exactly_the_approved_plan_set():
    assert RELATIONSHIPS == {
        "alias",
        "broader",
        "narrower",
        "enabling_technology",
        "alternative",
        "associated_event",
        "related_distinct",
        "uncertain",
    }


def test_create_family_link_members_and_read_back(store):
    family = store.create_topic_family(
        canonical_label="x402 / agentic payments", now=T2
    )
    assert family["status"] == "active"
    assert family["memberships"] == []
    assert [f["id"] for f in store.list_topic_families(status="active")] == [family["id"]]
    assert store.list_topic_families(status="retired") == []
    assert store.get_topic_family("missing") is None
    assert store.set_topic_family_status("missing", "retired") is None

    first = store.link_topic_family_member(
        family_id=family["id"],
        geo="us",
        keyword="X402",
        relationship="alias",
        confidence="high",
        evidence={"kind": "trend_keyword_overlap", "shared_terms": ["agentic payments"]},
        now=T2,
    )
    assert first["geo"] == "US"
    assert first["normalized_keyword"] == "x402"
    second = store.link_topic_family_member(
        family_id=family["id"],
        geo="US",
        keyword="Agentic  Payments",
        relationship="related_distinct",
        confidence="medium",
        evidence={"kind": "shared_url", "shared_values": ["https://x402.org/spec"]},
        now=T2,
    )
    assert second["normalized_keyword"] == "agentic payments"

    read = store.get_topic_family(family["id"])
    assert [m["normalized_keyword"] for m in read["memberships"]] == [
        "agentic payments",
        "x402",
    ]
    assert read["memberships"][0]["evidence"]["kind"] == "shared_url"
    assert all(m["first_linked_at"] == T2.isoformat() for m in read["memberships"])


def test_linking_requires_an_existing_candidate_series(store):
    family = store.create_topic_family(canonical_label="ghost family", now=T2)
    with pytest.raises(ValueError, match="unknown candidate series"):
        store.link_topic_family_member(
            family_id=family["id"],
            geo="US",
            keyword="ghost protocol",
            relationship="alias",
            confidence="high",
            evidence={"kind": "trend_keyword_overlap", "shared_terms": ["x402"]},
        )
    assert store.get_topic_family(family["id"])["memberships"] == []


def test_link_member_rejects_values_outside_the_plan_enums(store):
    family = store.create_topic_family(canonical_label="strict", now=T2)
    with pytest.raises(ValueError, match="invalid relationship"):
        store.link_topic_family_member(
            **family_kwargs(family_id=family["id"], relationship="synonym")
        )
    with pytest.raises(ValueError, match="invalid confidence"):
        store.link_topic_family_member(
            **family_kwargs(family_id=family["id"], confidence="certain")
        )
    with pytest.raises(ValueError, match="evidence must be a non-empty mapping"):
        store.link_topic_family_member(**family_kwargs(family_id=family["id"], evidence={}))
    with pytest.raises(ValueError, match="unknown topic family"):
        store.link_topic_family_member(**family_kwargs(family_id="nope"))
    with pytest.raises(ValueError, match="canonical_label is required"):
        store.create_topic_family(canonical_label="   ")
    with pytest.raises(ValueError, match="invalid family status"):
        store.create_topic_family(canonical_label="x", status="draft")
    with pytest.raises(ValueError, match="invalid family status"):
        store.list_topic_families(status="draft")


def test_duplicate_membership_is_idempotent_and_conflicting_writes_rejected(store):
    family = store.create_topic_family(canonical_label="payments rail", now=T2)
    kwargs = family_kwargs(family_id=family["id"])
    first = store.link_topic_family_member(**kwargs, now=T2)
    again = store.link_topic_family_member(**kwargs, now=T3)
    assert again == first  # same row, original first_linked_at preserved

    with pytest.raises(ValueError, match="conflicting membership write"):
        store.link_topic_family_member(
            **family_kwargs(family_id=family["id"], relationship="related_distinct")
        )
    with pytest.raises(ValueError, match="conflicting membership write"):
        store.link_topic_family_member(
            **family_kwargs(
                family_id=family["id"],
                evidence={"kind": "trend_keyword_overlap", "shared_terms": ["ai wallets"]},
            )
        )

    stored = store.get_topic_family(family["id"])["memberships"]
    assert len(stored) == 1
    assert stored[0]["relationship"] == "alias"
    assert stored[0]["evidence"]["shared_terms"] == ["agentic payments"]
    assert stored[0]["first_linked_at"] == T2.isoformat()


def test_linking_into_a_non_active_family_is_rejected(store):
    family = store.create_topic_family(canonical_label="archived", now=T2)
    store.link_topic_family_member(**family_kwargs(family_id=family["id"]), now=T2)
    store.set_topic_family_status(family["id"], "retired", now=T3)
    with pytest.raises(ValueError, match="not active"):
        store.link_topic_family_member(
            **family_kwargs(family_id=family["id"], keyword="agentic payments")
        )
    revived = store.set_topic_family_status(family["id"], "active", now=T3)
    assert revived["status"] == "active"
    assert len(revived["memberships"]) == 1


def edge_kwargs(**overrides):
    kwargs = dict(
        left_geo="US",
        left_keyword="x402",
        right_geo="US",
        right_keyword="agentic payments",
        edge_type="trend_keyword_overlap",
        evidence={"kind": "trend_keyword_overlap", "shared_terms": ["ai wallets"]},
        strength=0.25,
        observed_at=T2,
    )
    kwargs.update(overrides)
    return kwargs


def test_record_topic_edge_validates_strictly(store):
    with pytest.raises(ValueError, match="invalid edge type"):
        store.record_topic_edge(**edge_kwargs(edge_type="same_topic"))
    with pytest.raises(ValueError, match="two different candidates"):
        store.record_topic_edge(**edge_kwargs(right_keyword="X402"))
    with pytest.raises(ValueError, match="unknown candidate series"):
        store.record_topic_edge(**edge_kwargs(right_keyword="ghost protocol"))
    with pytest.raises(ValueError, match="between -1 and 1"):
        store.record_topic_edge(**edge_kwargs(strength=1.5))
    with pytest.raises(ValueError, match="between -1 and 1"):
        store.record_topic_edge(**edge_kwargs(strength=True))
    with pytest.raises(ValueError, match="evidence must be a non-empty mapping"):
        store.record_topic_edge(**edge_kwargs(evidence={}))
    edge_id = store.record_topic_edge(**edge_kwargs())
    assert edge_id >= 1
    with pytest.raises(ValueError, match="invalid edge type"):
        store.list_topic_edges(edge_type="vibe_match")
    with pytest.raises(ValueError, match="geo and keyword must be provided together"):
        store.list_topic_edges(geo="US")


def test_edge_pair_order_is_canonical_and_exact_duplicates_are_idempotent(store):
    one = store.record_topic_edge(**edge_kwargs())
    two = store.record_topic_edge(
        **edge_kwargs(
            left_keyword="Agentic Payments",
            right_keyword="X402",
            strength=0.25,
        )
    )
    assert two == one
    edges = store.list_topic_edges()
    assert len(edges) == 1
    assert edges[0]["left_candidate_key"] == "US:agentic payments"
    assert edges[0]["right_candidate_key"] == "US:x402"
    assert edges[0]["strength"] == 0.25
    assert edges[0]["evidence"]["shared_terms"] == ["ai wallets"]


def test_conflicting_edge_evidence_appends_history_instead_of_overwriting(store):
    common = dict(
        left_geo="US",
        left_keyword="x402",
        right_geo="US",
        right_keyword="agentic payments",
        edge_type="temporal_co_movement",
        observed_at=T2,
    )
    first = store.record_topic_edge(
        **common,
        strength=0.91,
        evidence={"kind": "temporal_co_movement", "correlation": 0.91, "pair_count": 5},
    )
    second = store.record_topic_edge(
        **common,
        strength=0.34,
        evidence={"kind": "temporal_co_movement", "correlation": 0.34, "pair_count": 6},
    )
    assert second != first
    edges = store.list_topic_edges(edge_type="temporal_co_movement")
    assert [e["strength"] for e in edges] == [0.91, 0.34]
    assert [e["evidence"]["pair_count"] for e in edges] == [5, 6]
    assert len(store.list_topic_edges(geo="US", keyword="x402")) == 2
    assert len(store.list_topic_edges(geo="US", keyword="agentic payments")) == 2


def test_family_writes_never_touch_raw_candidate_histories(store):
    # A comparable run without "agentic payments" opens an explicit gap.
    store.record_feed(geo="US", observed_at=T2, candidates=[candidate("x402")])

    family = store.create_topic_family(canonical_label="x402 family", now=T2)
    store.link_topic_family_member(**family_kwargs(family_id=family["id"]), now=T2)
    store.link_topic_family_member(
        family_id=family["id"],
        geo="US",
        keyword="agentic payments",
        relationship="alias",
        confidence="high",
        evidence={"kind": "trend_keyword_overlap", "shared_terms": ["x402"]},
        now=T2,
    )
    store.record_topic_edge(**edge_kwargs())

    x402 = store.get_candidate_history("US", "x402")
    agentic = store.get_candidate_history("US", "agentic payments")
    assert len(x402["observations"]) == 2
    assert len(agentic["observations"]) == 1
    assert agentic["gaps"][0]["missed_comparable_runs"] == 1
    assert agentic["series"]["presence_status"] == "missing"

    # Feeds keep appending normally after family writes.
    store.record_feed(
        geo="US",
        observed_at=T3,
        candidates=[
            candidate("x402", search_volume=60_000),
            candidate("agentic payments", search_volume=90_000),
        ],
    )
    x402_after = store.get_candidate_history("US", "x402")
    agentic_after = store.get_candidate_history("US", "agentic payments")
    assert len(x402_after["observations"]) == 3
    assert len(agentic_after["observations"]) == 2
    assert [o["search_volume"] for o in x402_after["observations"]] == [
        50_000,
        50_000,
        60_000,
    ]
    assert agentic_after["series"]["presence_status"] == "present"
    assert agentic_after["gaps"][0]["ended_at"] == T3.isoformat()
    assert len(store.get_topic_family(family["id"])["memberships"]) == 2


@pytest.fixture
def populated_legacy_db(tmp_path):
    """A populated immediate-predecessor DB with the Task 3.1 tables absent."""
    path = tmp_path / "legacy-families.db"
    legacy = DiscoveryStore(path)
    run_one = legacy.record_feed(
        geo="US",
        observed_at=T1,
        candidates=[
            candidate("x402"),
            candidate("agentic payments"),
            candidate("ai wallets"),
        ],
    )
    legacy.record_feed(
        geo="US", observed_at=T2, candidates=[candidate("x402", growth_pct=410)]
    )
    observation_id = legacy.list_run_candidates(run_one)[0]["observation_id"]
    legacy.record_gate_check(
        observation_id,
        status="complete",
        passed=True,
        platforms=["reddit"],
        total_items=2,
        independent_voices=2,
        source_health=[{"platform": "reddit", "status": "healthy"}],
        records=[{
            "platform": "reddit",
            "post_id": "legacy-root",
            "record_type": "post",
            "text": "a real stored root",
        }],
    )
    legacy_tables = (
        "discovery_runs",
        "discovery_candidate_series",
        "discovery_candidate_observations",
        "discovery_candidate_gaps",
        "discovery_gate_checks",
        "schema_migrations",
    )
    with sqlite3.connect(path) as connection:
        # Downgrade only the Task 3.1 addition before taking the legacy
        # snapshot; the new marker is not part of the predecessor schema.
        connection.execute(
            "DELETE FROM schema_migrations WHERE name = '2026_08_15_topic_families'"
        )
        snapshot = {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in legacy_tables
        }
        # Every populated legacy row must survive re-opening on the migrated schema.
        for table in (
            "topic_families",
            "topic_family_memberships",
            "topic_relationship_edges",
        ):
            connection.execute(f"DROP TABLE {table}")
    return path, snapshot


def test_additive_topic_family_schema_migrates_populated_legacy_db(
    populated_legacy_db,
):
    path, snapshot = populated_legacy_db

    migrated = DiscoveryStore(path)

    with sqlite3.connect(path) as connection:
        for table, expected in snapshot.items():
            if table == "schema_migrations":
                rows = sorted(
                    row
                    for row in connection.execute(
                        "SELECT * FROM schema_migrations"
                    ).fetchall()
                    if row[0] != "2026_08_15_topic_families"
                )
            else:
                rows = sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
            assert rows == expected, table
        for table in (
            "topic_families",
            "topic_family_memberships",
            "topic_relationship_edges",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        marker = connection.execute(
            "SELECT name FROM schema_migrations WHERE name = '2026_08_15_topic_families'"
        ).fetchone()
    assert marker == ("2026_08_15_topic_families",)

    family = migrated.create_topic_family(canonical_label="migrated family")
    migrated.link_topic_family_member(
        family_id=family["id"],
        geo="US",
        keyword="x402",
        relationship="alias",
        confidence="high",
        evidence={"kind": "trend_keyword_overlap", "shared_terms": ["agentic payments"]},
    )
    assert len(migrated.get_topic_family(family["id"])["memberships"]) == 1
    assert len(migrated.get_candidate_history("US", "agentic payments")["gaps"]) == 1
