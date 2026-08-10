"""Acceptance tests for canonical corpus persistence beside legacy observations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from social_scraper.conversations import ConversationStore, normalize_broker_item
from social_scraper.storage import ObservationStore


T0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def source_health():
    return [
        {
            "platform": "reddit",
            "connector": "reddit_mobile_owned",
            "status": "partial",
            "items_requested": 10,
            "items_returned": 1,
            "latency_ms": 120,
            "fetched_at": "2026-08-10T11:59:00+00:00",
            "error": None,
            "coverage": {"comments_complete": False},
        },
        {
            "platform": "reddit",
            "connector": "reddit_rss_owned",
            "status": "error",
            "items_requested": 10,
            "items_returned": 0,
            "latency_ms": None,
            "fetched_at": None,
            "error": "reddit_rss_unavailable",
            "coverage": {},
        },
    ]


def post_item(*, likes=0, text="I switched from Alpha to Beta"):
    return {
        "platform": "reddit",
        "post_id": "t3_signal",
        "url": "https://reddit.com/r/products/comments/signal/example/",
        "author": {
            "username": "real-user",
            "display_name": None,
            "profile_url": None,
            "follower_count": None,
        },
        "text": text,
        "created_at": "2026-08-09T08:00:00+00:00",
        "engagement": {
            "views": None,
            "likes": likes,
            "comments": 4,
            "shares": None,
            "collects": None,
        },
        "provenance": {
            "connector": "reddit_mobile_owned",
            "source_observed_at": "2026-08-10T11:59:00+00:00",
        },
    }


def response(item=None):
    return {
        "query": "switched from alpha",
        "platforms": ["reddit"],
        "region": "US",
        "items": [item or post_item()],
        "count": 1,
        "source_health": source_health(),
        "platform_results": {
            "reddit": {
                "status": "partial",
                "selected_connector": "reddit_mobile_owned",
                "attempted_connectors": [
                    "reddit_mobile_owned",
                    "reddit_rss_owned",
                ],
            }
        },
    }


def test_record_collection_automatically_writes_canonical_record_and_sources(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    run_id = legacy.record_collection(response(), ["reddit"], "US", T0)
    corpus = ConversationStore(path)

    records = corpus.get_run_records(run_id)
    assert len(records) == 1
    assert records[0]["external_id"] == "t3_signal"
    assert records[0]["record_type"] == "post"
    assert records[0]["engagement"]["likes"] == 0
    assert records[0]["engagement"]["views"] is None

    sources = corpus.get_run_sources(run_id)
    assert [(row["source_route"], row["status"]) for row in sources] == [
        ("reddit_mobile_owned", "partial"),
        ("reddit_rss_owned", "error"),
    ]
    assert sources[0]["selected"] is True
    assert sources[1]["selected"] is False
    assert sources[1]["latency_ms"] is None
    assert sources[1]["fetched_at"] is None


def test_same_payload_in_two_runs_reuses_version_and_keeps_two_observations(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    first_run = legacy.record_collection(response(), ["reddit"], "US", T0)
    second_run = legacy.record_collection(response(), ["reddit"], "US", T1)
    corpus = ConversationStore(path)

    first = corpus.get_run_records(first_run)[0]
    second = corpus.get_run_records(second_run)[0]
    versions = corpus.get_record_versions("reddit", "t3_signal")

    assert first["id"] == second["id"]
    assert first["identity_key"] == second["identity_key"]
    assert len(versions) == 2
    assert [row["collected_at"] for row in versions] == [
        T0.isoformat(),
        T1.isoformat(),
    ]


def test_changed_payload_creates_immutable_version_with_stable_identity(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    legacy.record_collection(response(post_item(likes=1)), ["reddit"], "US", T0)
    legacy.record_collection(response(post_item(likes=8)), ["reddit"], "US", T1)
    corpus = ConversationStore(path)

    versions = corpus.get_record_versions("reddit", "t3_signal")
    assert len(versions) == 2
    assert versions[0]["id"] != versions[1]["id"]
    assert versions[0]["identity_key"] == versions[1]["identity_key"]
    assert [row["engagement"]["likes"] for row in versions] == [1, 8]
    assert versions[0]["raw_payload_hash"] != versions[1]["raw_payload_hash"]


def test_invalid_identity_remains_in_legacy_storage_with_diagnostic(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    invalid = post_item()
    invalid.pop("post_id")
    run_id = legacy.record_collection(response(invalid), ["reddit"], "US", T0)
    corpus = ConversationStore(path)

    assert legacy.get_observation_history("reddit", "")
    assert corpus.get_run_records(run_id) == []
    diagnostics = corpus.get_normalization_diagnostics(run_id)
    assert diagnostics[0]["reason"] == "missing_external_id"
    assert diagnostics[0]["raw_item"]["platform"] == "reddit"


def test_post_and_comment_with_same_native_id_do_not_collide(tmp_path):
    path = tmp_path / "namespaced.db"
    legacy = ObservationStore(path)
    root = post_item()
    root["post_id"] = "same-id"
    run_id = legacy.record_collection(response(root), ["reddit"], "US", T0)
    corpus = ConversationStore(path)
    comment = normalize_broker_item(
        {
            "platform": "reddit",
            "post_id": "same-id",
            "record_type": "comment",
            "parent_external_id": "same-id",
            "root_post_external_id": "same-id",
            "depth": 1,
            "text": "Separate source object despite the same native ID",
        },
        collected_at=T0.isoformat(),
    )
    corpus.record_bundles(run_id, [comment])

    records = corpus.get_run_records(run_id)
    assert {(record["object_type"], record["external_id"]) for record in records} == {
        ("post", "same-id"),
        ("comment", "same-id"),
    }
    assert len({record["identity_key"] for record in records}) == 2
    assert [
        record["object_type"]
        for record in corpus.get_record_versions("reddit", "same-id")
    ] == ["post"]
    assert [
        record["object_type"]
        for record in corpus.get_record_versions(
            "reddit",
            "same-id",
            object_type="comment",
        )
    ] == ["comment"]
    thread = corpus.get_thread("reddit", "same-id")
    assert thread["roots"][0]["object_type"] == "post"
    assert thread["roots"][0]["children"][0]["object_type"] == "comment"


def test_nested_reply_bundle_round_trips_and_preserves_zone_membership(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    run_id = legacy.record_collection(response(), ["reddit"], "US", T0)
    corpus = ConversationStore(path)

    comment = normalize_broker_item(
        {
            "platform": "reddit",
            "post_id": "t1_comment",
            "record_type": "comment",
            "parent_external_id": "t3_signal",
            "root_post_external_id": "t3_signal",
            "depth": 1,
            "text": "Quality dropped for me too",
            "engagement": {"likes": 2},
            "provenance": {"connector": "reddit_camoufox_owned"},
        },
        collected_at=T0.isoformat(),
    )
    reply = normalize_broker_item(
        {
            "platform": "reddit",
            "post_id": "t1_reply",
            "record_type": "reply",
            "parent_external_id": "t1_comment",
            "root_post_external_id": "t3_signal",
            "depth": 2,
            "text": "Mine changed after the reformulation",
            "engagement": {"likes": 0},
            "provenance": {"connector": "reddit_camoufox_owned"},
        },
        collected_at=T0.isoformat(),
    )

    ids = corpus.record_bundles(
        run_id,
        [comment, reply],
        zone_id=7,
        keyword="switched from alpha",
    )
    thread = corpus.get_thread("reddit", "t3_signal")

    assert len(ids) == 2
    assert thread["record_count"] == 3
    root = thread["roots"][0]
    assert root["external_id"] == "t3_signal"
    assert root["children"][0]["external_id"] == "t1_comment"
    assert root["children"][0]["children"][0]["external_id"] == "t1_reply"
    assert thread["orphans"] == []

    with corpus._connect() as connection:
        memberships = connection.execute(
            "SELECT zone_id, keyword FROM zone_record_membership ORDER BY id"
        ).fetchall()
    assert [(row["zone_id"], row["keyword"]) for row in memberships] == [
        (7, "switched from alpha"),
        (7, "switched from alpha"),
    ]


def test_existing_observation_api_shape_is_unchanged(tmp_path):
    path = tmp_path / "social.db"
    legacy = ObservationStore(path)
    run_id = legacy.record_collection(response(), ["reddit"], "US", T0)

    run = legacy.get_collection_run(run_id)
    history = legacy.get_observation_history("reddit", "t3_signal")

    assert set(run) == {
        "id",
        "query",
        "platforms",
        "platform_options",
        "region",
        "collected_at",
        "raw_response",
    }
    assert set(history[0]) == {
        "collection_run_id",
        "observed_at",
        "connector",
        "views",
        "likes",
        "comments",
        "shares",
    }
    assert run["raw_response"]["items"][0]["post_id"] == "t3_signal"
    assert "identity_key" not in run["raw_response"]["items"][0]


def test_schema_initialization_is_idempotent_and_records_migration(tmp_path):
    path = tmp_path / "social.db"
    ObservationStore(path)
    ConversationStore(path)
    ConversationStore(path)

    with sqlite3.connect(path) as connection:
        migrations = connection.execute(
            "SELECT name FROM schema_migrations"
        ).fetchall()
    assert migrations == [("2026_08_10_phase1_canonical_conversations",)]


def test_canonical_failure_rolls_back_legacy_collection_atomically(tmp_path, monkeypatch):
    path = tmp_path / "atomic.db"
    legacy = ObservationStore(path)

    def fail_after_schema_setup(*args, **kwargs):
        raise RuntimeError("forced canonical failure")

    monkeypatch.setattr(
        ConversationStore,
        "_persist_run_sources",
        fail_after_schema_setup,
    )
    with pytest.raises(RuntimeError, match="forced canonical failure"):
        legacy.record_collection(response(), ["reddit"], "US", T0)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM conversation_observations").fetchone()[0] == 0
