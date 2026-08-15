from datetime import datetime, timezone

import pytest

from social_scraper.discovery.shadow_mode import run_shadow_mode
from social_scraper.discovery.storage import DiscoveryStore

NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


def evidence(candidate_id, **updates):
    value = {
        "candidate_id": candidate_id,
        "observed_at": NOW.isoformat(),
        "root_summary": {"unique_root_count": 3, "independent_author_count": 2},
        "usable_text_root_count": 2,
        "duplicate_only_support": False,
        "source_health": {"reddit": "healthy"},
        "snapshot_windows": [
            {"snapshot_id": "d1", "status": "absent"},
            {"snapshot_id": "d2", "status": "absent"},
            {"snapshot_id": "d3", "status": "absent"},
        ],
        "platform_hits": {"reddit": 2},
        "trajectory": {},
        "engagement_roots": [],
        "creator_summary": {},
        "depth_roots": [],
        "active_discussion_roots": 0,
        "radar_match": {"matched": False, "radar_ids": []},
        "manual_request": {"requested": False, "within_budget": False},
        "stratum": {"category": "technology", "region": "US"},
    }
    value.update(updates)
    return value


def test_shadow_mode_persists_evaluations_without_actions_or_collection(tmp_path):
    store = DiscoveryStore(tmp_path / "shadow.db")
    result = run_shadow_mode(
        store,
        [
            evidence("radar", radar_match={"matched": True, "radar_ids": ["saved"]}),
            evidence("quiet"),
        ],
        evaluated_at=NOW,
    )
    assert result["mode"] == "shadow"
    assert result["collection_performed"] is False
    assert result["executed_actions"] == 0
    rows = store.list_promotion_evaluations(workspace_id="default")
    assert len(rows) == 2 and all(row["shadow"] for row in rows)
    assert rows[0]["evidence"]["candidate_id"] == "radar"
    modes = {row["candidate_id"]: row["evaluation"]["promotion_mode"] for row in rows}
    assert modes == {"radar": "automatic", "quiet": "exploration"}
    assert result["funnel"]["counts"]["evaluated"] == 2
    assert result["funnel"]["counts"]["automatic"] == 1
    assert result["funnel"]["counts"]["exploration"] == 1


def test_route_and_outcome_labels_are_action_specific(tmp_path):
    store = DiscoveryStore(tmp_path / "labels.db")
    run_shadow_mode(store, [evidence("quiet", family_id="family-1")], evaluated_at=NOW)
    evaluation = store.list_promotion_evaluations(workspace_id="default")[0]
    monitor = store.record_promotion_label(
        workspace_id="default", family_id="family-1",
        evaluation_id=evaluation["id"], action_type="monitor",
        route="exploration_allocation", details={"source": "global_explore"},
        created_at=NOW,
    )
    outcome = store.record_promotion_label(
        workspace_id="default", candidate_id="quiet",
        evaluation_id=evaluation["id"], action_type="outcome",
        route="exploration_allocation", outcome="useful",
        created_at=NOW,
    )
    assert monitor["action_type"] == "monitor"
    assert outcome["outcome"] == "useful"
    funnel = store.summarize_promotion_funnel(workspace_id="default")
    assert funnel["label_counts"] == {"monitor": 1, "outcome": 1}
    assert store.list_promotion_labels(
        workspace_id="default", family_id="family-1"
    ) == [monitor]


def test_shadow_storage_validation_and_migration_marker(tmp_path):
    store = DiscoveryStore(tmp_path / "validation.db")
    with pytest.raises(ValueError, match="candidate_id"):
        store.record_promotion_evaluation(
            workspace_id="default", candidate_id="", policy_version="1",
            evaluation={"eligible": False}, evidence={},
        )
    with pytest.raises(ValueError, match="invalid promotion label"):
        store.record_promotion_label(
            workspace_id="default", candidate_id="x", action_type="like",
        )
    with pytest.raises(ValueError, match="outcome is required"):
        store.record_promotion_label(
            workspace_id="default", candidate_id="x", action_type="outcome",
        )
    with pytest.raises(ValueError, match="only valid"):
        store.record_promotion_label(
            workspace_id="default", candidate_id="x", action_type="monitor",
            outcome="useful",
        )
    with store._connect() as connection:
        marker = connection.execute(
            "SELECT name FROM schema_migrations WHERE name = ?",
            ("2026_08_15_promotion_shadow_labels",),
        ).fetchone()
    assert marker["name"] == "2026_08_15_promotion_shadow_labels"


def test_shadow_mode_rejects_undated_future_and_stale_evidence(tmp_path):
    store = DiscoveryStore(tmp_path / "freshness.db")
    missing = evidence("missing")
    missing.pop("observed_at")
    with pytest.raises(ValueError, match="observed_at is required"):
        run_shadow_mode(store, [missing], evaluated_at=NOW)
    with pytest.raises(ValueError, match="future"):
        run_shadow_mode(
            store, [evidence("future", observed_at="2026-08-16T08:00:00+00:00")],
            evaluated_at=NOW,
        )
    with pytest.raises(ValueError, match="stale"):
        run_shadow_mode(
            store, [evidence("stale", observed_at="2026-01-01T00:00:00+00:00")],
            evaluated_at=NOW,
        )
    assert store.list_promotion_evaluations(workspace_id="default") == []
