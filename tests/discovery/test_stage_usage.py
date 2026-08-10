import sqlite3
from datetime import datetime, timedelta, timezone

from social_scraper.discovery.budgets import StageUsage
from social_scraper.discovery.storage import DiscoveryStore


def _legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE discovery_runs (
            id TEXT PRIMARY KEY,
            geo TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('complete','partial','error')),
            comparable INTEGER NOT NULL CHECK(comparable IN (0,1)),
            candidate_count INTEGER NOT NULL,
            error_category TEXT,
            source_health_json TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO discovery_runs
            (id, geo, observed_at, completed_at, status, comparable,
             candidate_count, source_health_json)
        VALUES ('legacy-run', 'US', '2026-08-09T00:00:00+00:00',
                '2026-08-09T00:00:01+00:00', 'complete', 1, 1, '[]');
    """)
    connection.commit()
    connection.close()


def test_schema_migration_is_additive_on_a_nonempty_legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_database(path)

    store = DiscoveryStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT candidate_count FROM discovery_runs WHERE id = 'legacy-run'"
        ).fetchone() == (1,)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(discovery_stage_usage)")
        }
        assert {
            "discovery_run_id", "stage", "started_at", "completed_at",
            "duration_seconds", "candidates_considered", "candidates_processed",
            "records_returned", "external_calls", "llm_calls", "cache_hits",
            "status", "error_category", "input_tokens", "output_tokens",
            "tokens_estimated",
        } <= columns
        assert connection.execute(
            "SELECT COUNT(*) FROM discovery_stage_usage"
        ).fetchone() == (0,)

    assert store.list_stage_usage("legacy-run") == []


def test_record_and_list_stage_usage_preserves_zeroes_nullable_tokens_and_order(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US",
        observed_at="2026-08-10T12:00:00+00:00",
        candidates=[{"keyword": "alpha"}],
    )
    start = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    first_id = store.record_stage_usage(StageUsage(
        discovery_run_id=run_id,
        stage="observed",
        started_at=start,
        completed_at=start + timedelta(milliseconds=250),
        candidates_considered=1,
        candidates_processed=1,
        records_returned=1,
        external_calls=0,
        llm_calls=0,
        cache_hits=0,
        status="complete",
    ))
    second_id = store.record_stage_usage(StageUsage(
        discovery_run_id=run_id,
        stage="horizontal_extraction",
        started_at=start + timedelta(seconds=1),
        completed_at=start + timedelta(seconds=3),
        candidates_considered=1,
        candidates_processed=1,
        records_returned=2,
        external_calls=1,
        llm_calls=1,
        cache_hits=0,
        status="partial",
        error_category="provider_timeout",
        input_tokens=100,
        output_tokens=40,
        tokens_estimated=True,
    ))

    rows = store.list_stage_usage(run_id)
    assert first_id < second_id
    assert [row["stage"] for row in rows] == ["observed", "horizontal_extraction"]
    assert rows[0]["external_calls"] == 0
    assert rows[0]["llm_calls"] == 0
    assert rows[0]["cache_hits"] == 0
    assert rows[0]["input_tokens"] is None
    assert rows[0]["output_tokens"] is None
    assert rows[0]["tokens_estimated"] is False
    assert rows[0]["duration_seconds"] == 0.25
    assert rows[1]["tokens_estimated"] is True


def test_record_stage_usage_rejects_unknown_run(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    now = datetime.now(timezone.utc)
    usage = StageUsage(
        discovery_run_id="missing",
        stage="screening",
        started_at=now,
        completed_at=now,
        status="complete",
    )

    try:
        store.record_stage_usage(usage)
    except ValueError as exc:
        assert "unknown discovery run" in str(exc)
    else:
        raise AssertionError("unknown run should be rejected")
