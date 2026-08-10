"""Characterization tests for the SQLite-backed zone registry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from social_scraper.monitoring.zones import Zone


def test_zone_crud_round_trip_preserves_scope(registry, sample_zone):
    zone_id = registry.create(sample_zone)

    loaded = registry.get(zone_id)
    assert loaded is not None
    assert loaded.name == "consumer-switching"
    assert loaded.keywords == [
        "switched from alpha",
        "cancelled alpha",
        "buying beta instead",
    ]
    assert loaded.platforms == ["youtube", "reddit"]
    assert loaded.interval_hours == 168
    assert loaded.region == "US"
    assert loaded.status == "active"

    registry.update(zone_id, status="paused", description="Paused research zone")
    updated = registry.get(zone_id)
    assert updated.status == "paused"
    assert updated.description == "Paused research zone"

    registry.delete(zone_id)
    assert registry.get(zone_id) is None


def test_list_due_respects_status_and_interval(registry, sample_zone):
    never_run_id = registry.create(sample_zone)
    assert [z.id for z in registry.list_due()] == [never_run_id]

    registry.update_collected(never_run_id)
    assert registry.list_due() == []

    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=169)).isoformat()
    with registry._connect() as conn:
        conn.execute(
            "UPDATE zones SET last_collected_at = ? WHERE id = ?",
            (old_timestamp, never_run_id),
        )
    assert [z.id for z in registry.list_due()] == [never_run_id]

    registry.update(never_run_id, status="paused")
    assert registry.list_due() == []


def test_snapshots_are_returned_newest_first_and_deleted_with_zone(registry, sample_zone):
    zone_id = registry.create(sample_zone)
    registry.save_snapshot(
        zone_id,
        [{"label": "older", "post_count": 1}],
        item_count=1,
        platform_summary={"youtube": {"items": 1}},
    )
    registry.save_snapshot(
        zone_id,
        [{"label": "newer", "post_count": 2}],
        item_count=2,
        platform_summary={"reddit": {"items": 2}},
    )

    snapshots = registry.get_snapshots(zone_id)
    assert len(snapshots) == 2
    assert snapshots[0]["clusters"][0]["label"] == "newer"
    assert snapshots[1]["clusters"][0]["label"] == "older"
    assert snapshots[0]["platform_summary"] == {"reddit": {"items": 2}}

    registry.delete(zone_id)
    assert registry.get_snapshots(zone_id) == []


def test_duplicate_zone_name_is_rejected(registry, sample_zone):
    import sqlite3
    import pytest

    registry.create(sample_zone)
    with pytest.raises(sqlite3.IntegrityError):
        registry.create(
            Zone(name=sample_zone.name, keywords=["different keyword"])
        )


def test_existing_snapshot_table_is_migrated_for_source_health(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy-monitoring.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE cluster_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL,
                collected_at TEXT NOT NULL,
                clusters_json TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                platform_summary_json TEXT NOT NULL DEFAULT '{}'
            )
        """)

    from social_scraper.monitoring.zones import ZoneRegistry

    migrated = ZoneRegistry(db_path)
    with migrated._connect() as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(cluster_snapshots)").fetchall()
        }
    assert "source_health_json" in columns
