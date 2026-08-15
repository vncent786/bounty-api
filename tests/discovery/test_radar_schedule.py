"""Durable radar schedule persistence and lease semantics (Task 1.3a).

Covers: additive migration over a genuinely pre-radar populated discovery
DB built with raw sqlite3, scan-mode rejection at the storage boundary,
upsert idempotency, atomic claim exclusion across two store instances,
expired-lease reclaim, stale-token protection, previous-success
preservation after failed attempts, and the read-only active-subject
listing that keeps 1.3b workspace-controlled.
"""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.workspaces.storage import WorkspaceStore


T0 = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)

# Migration rows a pre-radar build of the store would have applied.
LEGACY_MIGRATIONS = (
    "2026_08_10_phase1b_discovery_history",
    "2026_08_10_discovery_stage_usage",
    "2026_08_10_phase2_staged_research",
    "2026_08_10_shared_evidence_cache",
    "2026_08_11_research_findings",
    "2026_08_15_stage_usage_cost_receipts",
)

# Discovery tables exactly as the pre-radar schema defined them: no
# radar_schedules, no radar_schedule_runs.
LEGACY_SCHEMA = """
    CREATE TABLE schema_migrations (
        name TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
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
    CREATE TABLE discovery_candidate_series (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        geo TEXT NOT NULL,
        normalized_keyword TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        consecutive_observations INTEGER NOT NULL DEFAULT 1,
        total_observations INTEGER NOT NULL DEFAULT 0,
        presence_status TEXT NOT NULL DEFAULT 'present'
            CHECK(presence_status IN ('present','missing','unknown')),
        UNIQUE(geo, normalized_keyword)
    );
    CREATE TABLE discovery_candidate_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discovery_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
        candidate_series_id INTEGER NOT NULL REFERENCES discovery_candidate_series(id),
        observed_at TEXT NOT NULL,
        keyword TEXT NOT NULL,
        search_volume INTEGER,
        growth_pct REAL,
        source_started_at TEXT,
        related_terms_json TEXT NOT NULL,
        topic_ids_json TEXT NOT NULL,
        categories_json TEXT NOT NULL,
        raw_payload_hash TEXT NOT NULL,
        raw_payload_json TEXT NOT NULL,
        UNIQUE(discovery_run_id, candidate_series_id)
    );
    CREATE TABLE discovery_candidate_gaps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_series_id INTEGER NOT NULL REFERENCES discovery_candidate_series(id),
        started_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
        started_at TEXT NOT NULL,
        missed_comparable_runs INTEGER NOT NULL DEFAULT 1,
        ended_run_id TEXT REFERENCES discovery_runs(id),
        ended_at TEXT
    );
    CREATE UNIQUE INDEX uq_discovery_open_gap
        ON discovery_candidate_gaps(candidate_series_id)
        WHERE ended_at IS NULL;
    CREATE INDEX idx_discovery_observations_series_time
        ON discovery_candidate_observations(candidate_series_id, observed_at, id);
    CREATE INDEX idx_discovery_runs_geo_time
        ON discovery_runs(geo, observed_at, id);
"""


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _legacy_payload(keyword: str, volume: int, growth: float) -> dict:
    record = candidate(keyword, volume=volume, growth=growth)
    return {
        **record,
        "source_records": [record],
        "source_record_count": 1,
        "metric_conflicts": [],
    }


def _insert_legacy_observation(connection, run_id, series_id, observed_at,
                               keyword, volume, growth):
    payload = _legacy_payload(keyword, volume, growth)
    raw = _canonical_json(payload)
    connection.execute(
        """INSERT INTO discovery_candidate_observations
           (discovery_run_id, candidate_series_id, observed_at, keyword,
            search_volume, growth_pct, source_started_at,
            related_terms_json, topic_ids_json, categories_json,
            raw_payload_hash, raw_payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, series_id, observed_at, keyword, volume, float(growth),
            "2026-08-15T03:00:00+00:00",
            _canonical_json([keyword, "jet engine"]),
            _canonical_json([3]),
            _canonical_json(["Business & Finance"]),
            hashlib.sha256(raw.encode("utf-8")).hexdigest(), raw,
        ),
    )


def build_pre_radar_db(path) -> tuple[str, str]:
    """Create a non-empty pre-radar discovery DB with raw sqlite3 only.

    Mirrors what a pre-radar build persisted through record_feed: two
    comparable US runs, a candidate seen in both, a candidate that went
    missing in the second run with an open gap, and the migration rows
    that predate radar scheduling. No DiscoveryStore is involved.
    """
    run_one_at = T0.isoformat()
    run_two_at = (T0 + timedelta(hours=1)).isoformat()
    run_one, run_two = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.executemany(
            "INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
            [(name, run_one_at) for name in LEGACY_MIGRATIONS],
        )
        connection.execute(
            """INSERT INTO discovery_runs
               (id, geo, observed_at, completed_at, status, comparable,
                candidate_count, error_category, source_health_json)
               VALUES (?, 'US', ?, ?, 'complete', 1, 2, NULL, ?)""",
            (run_one, run_one_at, run_one_at, _canonical_json(
                [{"source": "trends", "status": "ok", "records": 2}])),
        )
        connection.execute(
            """INSERT INTO discovery_runs
               (id, geo, observed_at, completed_at, status, comparable,
                candidate_count, error_category, source_health_json)
               VALUES (?, 'US', ?, ?, 'complete', 1, 1, NULL, '[]')""",
            (run_two, run_two_at, run_two_at),
        )
        turbine = connection.execute(
            """INSERT INTO discovery_candidate_series
               (geo, normalized_keyword, first_seen_at, last_seen_at,
                consecutive_observations, total_observations, presence_status)
               VALUES ('US', 'turbine blade', ?, ?, 2, 2, 'present')""",
            (run_one_at, run_two_at),
        ).lastrowid
        jet_fuel = connection.execute(
            """INSERT INTO discovery_candidate_series
               (geo, normalized_keyword, first_seen_at, last_seen_at,
                consecutive_observations, total_observations, presence_status)
               VALUES ('US', 'jet fuel', ?, ?, 0, 1, 'missing')""",
            (run_one_at, run_one_at),
        ).lastrowid
        _insert_legacy_observation(
            connection, run_one, turbine, run_one_at, "turbine blade", 20_000, 700.0)
        _insert_legacy_observation(
            connection, run_one, jet_fuel, run_one_at, "jet fuel", 8_000, 120.0)
        _insert_legacy_observation(
            connection, run_two, turbine, run_two_at, "turbine blade", 21_000, 640.0)
        connection.execute(
            """INSERT INTO discovery_candidate_gaps
               (candidate_series_id, started_run_id, started_at,
                missed_comparable_runs, ended_run_id, ended_at)
               VALUES (?, ?, ?, 1, NULL, NULL)""",
            (jet_fuel, run_two, run_two_at),
        )
    return run_one, run_two


def candidate(keyword="turbine blade", volume=20_000, growth=700):
    return {
        "keyword": keyword,
        "related_terms": [keyword, "jet engine"],
        "search_volume": volume,
        "growth_pct": growth,
        "source_started_at": "2026-08-15T03:00:00+00:00",
        "topic_ids": [3],
        "categories": ["Business & Finance"],
    }


def schedule_kwargs(**overrides):
    base = {
        "scan_mode": "trends_snapshot",
        "scope_type": "geography",
        "geo": "US",
        "interval_minutes": 1440,
        "next_run_at": T0,
    }
    base.update(overrides)
    return base


# --- Migration -----------------------------------------------------------


def test_migration_preserves_populated_legacy_discovery_db(tmp_path):
    path = tmp_path / "discovery.db"
    run_one, run_two = build_pre_radar_db(path)

    # Genuinely pre-radar before the store ever touches the file.
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "radar_schedules" not in tables
        assert "radar_schedule_runs" not in tables
        migrations = {row[0] for row in connection.execute(
            "SELECT name FROM schema_migrations")}
        assert "2026_08_15_radar_schedules" not in migrations
        assert set(LEGACY_MIGRATIONS) <= migrations
        # Legacy relationships are referentially valid.
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    migrated = DiscoveryStore(path)  # ensure_schema migrates in place

    assert migrated.discovery_run_exists(run_one)
    first = migrated.get_discovery_run(run_one)
    assert first["status"] == "complete"
    assert first["comparable"] is True
    assert first["candidate_count"] == 2
    assert first["source_health"] == [{"source": "trends", "status": "ok", "records": 2}]
    assert len(migrated.list_run_candidates(run_one)) == 2

    turbine = migrated.get_candidate_history("US", "turbine blade")
    assert turbine["series"]["total_observations"] == 2
    assert turbine["series"]["consecutive_observations"] == 2
    assert turbine["series"]["presence_status"] == "present"
    assert [obs["keyword"] for obs in turbine["observations"]] == [
        "turbine blade", "turbine blade",
    ]
    assert turbine["observations"][0]["search_volume"] == 20_000

    jet = migrated.get_candidate_history("US", "jet fuel")
    assert jet["series"]["total_observations"] == 1
    assert jet["series"]["presence_status"] == "missing"
    assert len(jet["gaps"]) == 1
    assert jet["gaps"][0]["started_at"] == (T0 + timedelta(hours=1)).isoformat()
    assert jet["gaps"][0]["missed_comparable_runs"] == 1
    assert jet["gaps"][0]["ended_at"] is None

    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        assert {
            "radar_schedules", "radar_schedule_runs",
            "idx_radar_schedules_due", "idx_radar_schedule_runs_schedule",
        } <= names
        for table, expected in (
            ("discovery_runs", 2),
            ("discovery_candidate_series", 2),
            ("discovery_candidate_observations", 3),
            ("discovery_candidate_gaps", 1),
        ):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == expected, table
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE name='2026_08_15_radar_schedules'"
        ).fetchone()
        kept = {row[0] for row in connection.execute(
            "SELECT name FROM schema_migrations")}
    assert applied is not None
    assert set(LEGACY_MIGRATIONS) <= kept

    assert migrated.list_radar_schedules() == []

    # The migrated store keeps writing legacy tables with correct semantics.
    third = migrated.record_feed(
        geo="US", observed_at=T0 + timedelta(hours=2),
        candidates=[candidate("turbine blade"), candidate("jet fuel")],
    )
    assert migrated.get_discovery_run(third)["candidate_count"] == 2
    closed = migrated.get_candidate_history("US", "jet fuel")
    assert closed["gaps"][0]["ended_at"] == (T0 + timedelta(hours=2)).isoformat()
    assert closed["gaps"][0]["missed_comparable_runs"] == 1
    assert closed["series"]["total_observations"] == 2
    assert closed["series"]["presence_status"] == "present"


def test_new_store_on_fresh_db_creates_radar_tables(tmp_path):
    store = DiscoveryStore(tmp_path / "fresh.db")
    assert store.list_radar_schedules() == []
    with sqlite3.connect(tmp_path / "fresh.db") as connection:
        columns = {
            row[1]: row[2].upper()
            for row in connection.execute("PRAGMA table_info(radar_schedules)")
        }
    # The success pointer references a radar_schedule_runs row id.
    assert columns["last_successful_comparable_run_id"] == "INTEGER"


# --- Mode and scope validation ------------------------------------------


@pytest.mark.parametrize("mode", ["deep_read", "horizontal_synthesis", "optional_interpretation"])
def test_research_modes_rejected_at_storage_boundary(tmp_path, mode):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="cannot be scheduled"):
        store.upsert_radar_schedule(**schedule_kwargs(scan_mode=mode))


def test_unknown_mode_string_rejected(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="unknown scan mode"):
        store.upsert_radar_schedule(**schedule_kwargs(scan_mode="vibes"))


def test_scope_type_validated(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="scope_type"):
        store.upsert_radar_schedule(**schedule_kwargs(scope_type="neighbourhood"))


def test_subject_scope_requires_subject_id_and_geography_forbids_it(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="subject_id"):
        store.upsert_radar_schedule(**schedule_kwargs(scope_type="subject"))
    with pytest.raises(ValueError, match="subject_id"):
        store.upsert_radar_schedule(
            **schedule_kwargs(scope_type="geography", subject_id="sub-1")
        )
    made = store.upsert_radar_schedule(
        **schedule_kwargs(scope_type="subject", subject_id="sub-1")
    )
    assert made["subject_id"] == "sub-1"
    assert made["scope_key"] == "sub-1"


def test_interval_must_be_positive(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="interval_minutes"):
        store.upsert_radar_schedule(**schedule_kwargs(interval_minutes=0))


# --- Upsert idempotency ---------------------------------------------------


def test_upsert_is_idempotent_on_mode_and_scope(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    first = store.upsert_radar_schedule(**schedule_kwargs())
    second = store.upsert_radar_schedule(
        **schedule_kwargs(interval_minutes=10080, next_run_at=T0 + timedelta(days=1),
                          enabled=False)
    )
    rows = store.list_radar_schedules()
    assert len(rows) == 1
    assert second["id"] == first["id"]
    assert second["interval_minutes"] == 10080
    assert second["enabled"] is False
    assert second["created_at"] == first["created_at"]
    # A conflicting upsert is a config refresh: it must not reset the live
    # schedule's due time even though a different next_run_at was supplied.
    assert second["next_run_at"] == first["next_run_at"] == T0.isoformat()


def test_upsert_preserves_attempt_history_and_success_pointer(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-1", now=T0 + timedelta(minutes=2),
    )
    after = store.upsert_radar_schedule(**schedule_kwargs(interval_minutes=60))
    assert after["last_successful_comparable_run_id"] == receipt["run"]["id"]
    assert after["last_status"] == "complete"
    assert after["last_attempt_at"] == T0.isoformat()


def test_same_geo_and_mode_deduplicates_but_distinct_scopes_do_not(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    store.upsert_radar_schedule(**schedule_kwargs(geo="sg"))
    store.upsert_radar_schedule(**schedule_kwargs(geo="SG"))  # case-normalized
    assert len(store.list_radar_schedules()) == 1
    store.upsert_radar_schedule(
        **schedule_kwargs(scan_mode="root_sweep", geo="SG")
    )
    store.upsert_radar_schedule(
        **schedule_kwargs(scope_type="subject", subject_id="sub-9")
    )
    assert len(store.list_radar_schedules()) == 3


# --- Claim exclusion, expiry, stale tokens -------------------------------


def test_two_store_instances_cannot_claim_the_same_row(tmp_path):
    path = tmp_path / "discovery.db"
    store_a = DiscoveryStore(path)
    store_b = DiscoveryStore(path)
    store_a.upsert_radar_schedule(**schedule_kwargs())

    claims_a = store_a.claim_due_schedules(now=T0, lease_minutes=30)
    claims_b = store_b.claim_due_schedules(now=T0, lease_minutes=30)
    assert len(claims_a) == 1
    assert claims_b == []
    # Still excluded while the lease is live.
    assert store_a.claim_due_schedules(now=T0 + timedelta(minutes=29)) == []
    assert store_b.claim_due_schedules(now=T0 + timedelta(minutes=29)) == []

    receipt = store_a.complete_schedule_attempt(
        claims_a[0]["schedule_id"], claims_a[0]["claim_token"],
        status="complete", comparable=True, discovery_run_id="run-1",
        now=T0 + timedelta(minutes=2),
    )
    assert receipt["schedule"]["lease_token"] is None
    assert receipt["schedule"]["last_successful_comparable_run_id"] == receipt["run"]["id"]
    assert receipt["schedule"]["next_run_at"] == (
        T0 + timedelta(minutes=2) + timedelta(minutes=1440)
    ).isoformat()


def test_expired_lease_is_reclaimable_and_old_token_is_stale(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    first = store.claim_due_schedules(now=T0, lease_minutes=10)[0]

    later = T0 + timedelta(minutes=11)
    reclaimed = store.claim_due_schedules(now=later, lease_minutes=10)
    assert len(reclaimed) == 1
    assert reclaimed[0]["claim_token"] != first["claim_token"]
    assert reclaimed[0]["lease_until"] == (later + timedelta(minutes=10)).isoformat()

    with pytest.raises(RuntimeError, match="claim"):
        store.complete_schedule_attempt(
            first["schedule_id"], first["claim_token"],
            status="complete", comparable=True, discovery_run_id="run-stale",
            now=later + timedelta(minutes=1),
        )
    assert store.list_radar_schedule_runs(made["id"]) == []
    assert store.get_radar_schedule(made["id"])["lease_token"] == reclaimed[0]["claim_token"]

    store.complete_schedule_attempt(
        reclaimed[0]["schedule_id"], reclaimed[0]["claim_token"],
        status="error", comparable=False, error_category="source_timeout",
        now=later + timedelta(minutes=1),
    )
    runs = store.list_radar_schedule_runs(made["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "error"
    assert runs[0]["started_at"] == later.isoformat()


@pytest.mark.parametrize(
    ("status", "comparable", "error_category"),
    [("complete", True, None), ("error", False, "source_timeout")],
)
def test_expired_radar_lease_cannot_finalize_attempt(
    tmp_path, status, comparable, error_category,
):
    store = DiscoveryStore(tmp_path / f"expired-{status}.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=1)[0]

    with pytest.raises(RuntimeError, match="claim"):
        store.complete_schedule_attempt(
            made["id"], claim["claim_token"], status=status,
            comparable=comparable, error_category=error_category,
            now=T0 + timedelta(minutes=1),
        )

    assert store.list_radar_schedule_runs(made["id"]) == []
    persisted = store.get_radar_schedule(made["id"])
    assert persisted["lease_token"] == claim["claim_token"]
    assert persisted["lease_until"] == (T0 + timedelta(minutes=1)).isoformat()


def test_wrong_token_fails_without_side_effects(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]

    with pytest.raises(ValueError, match="lease_token"):
        store.complete_schedule_attempt(
            made["id"], "", status="complete",
            comparable=True, discovery_run_id="run-x", now=T0 + timedelta(minutes=1),
        )
    with pytest.raises(RuntimeError, match="claim"):
        store.complete_schedule_attempt(
            made["id"], "not-the-token", status="complete",
            comparable=True, discovery_run_id="run-x", now=T0 + timedelta(minutes=1),
        )
    schedule = store.get_radar_schedule(made["id"])
    assert schedule["next_run_at"] == T0.isoformat()
    assert schedule["lease_token"] == claim["claim_token"]
    assert store.list_radar_schedule_runs(made["id"]) == []

    store.complete_schedule_attempt(
        made["id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-x", now=T0 + timedelta(minutes=1),
    )
    assert len(store.list_radar_schedule_runs(made["id"])) == 1


def test_claim_excludes_disabled_and_not_due_rows(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    store.upsert_radar_schedule(**schedule_kwargs())
    store.upsert_radar_schedule(
        **schedule_kwargs(scope_type="subject", subject_id="sub-2", enabled=False)
    )
    store.upsert_radar_schedule(
        **schedule_kwargs(scan_mode="root_sweep", geo="DE",
                          next_run_at=T0 + timedelta(hours=1))
    )
    claims = store.claim_due_schedules(now=T0, lease_minutes=5)
    assert len(claims) == 1
    assert claims[0]["scan_mode"] == "trends_snapshot"
    assert claims[0]["scope_type"] == "geography"
    assert claims[0]["geo"] == "US"


def test_claim_respects_limit(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    store.upsert_radar_schedule(**schedule_kwargs())
    store.upsert_radar_schedule(**schedule_kwargs(scan_mode="root_sweep", geo="DE"))
    claims = store.claim_due_schedules(now=T0, lease_minutes=5, limit=1)
    assert len(claims) == 1


# --- Completion semantics -------------------------------------------------


def test_failure_preserves_previous_successful_comparable_run(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())

    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    good = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-good", now=T0 + timedelta(minutes=2),
    )
    good_attempt_id = good["run"]["id"]
    assert store.get_radar_schedule(made["id"])[
        "last_successful_comparable_run_id"
    ] == good_attempt_id

    due = T0 + timedelta(minutes=2) + timedelta(minutes=1440)
    claim = store.claim_due_schedules(now=due, lease_minutes=30)[0]
    store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="error",
        comparable=False, error_category="source_timeout",
        now=due + timedelta(minutes=3),
    )
    after_error = store.get_radar_schedule(made["id"])
    assert after_error["last_successful_comparable_run_id"] == good_attempt_id
    assert after_error["last_status"] == "error"
    assert after_error["last_error_category"] == "source_timeout"
    assert after_error["next_run_at"] == (
        due + timedelta(minutes=3) + timedelta(minutes=1440)
    ).isoformat()

    due = datetime.fromisoformat(after_error["next_run_at"])
    claim = store.claim_due_schedules(now=due, lease_minutes=30)[0]
    store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="partial",
        comparable=True,  # must be normalized down, not promoted
        discovery_run_id="run-partial", now=due + timedelta(minutes=1),
    )
    runs = store.list_radar_schedule_runs(made["id"])
    assert [run["status"] for run in runs] == ["complete", "error", "partial"]
    assert runs[2]["comparable"] is False
    assert store.get_radar_schedule(made["id"])[
        "last_successful_comparable_run_id"
    ] == good_attempt_id


def test_subject_root_sweep_completes_comparable_without_discovery_run(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(
        **schedule_kwargs(scan_mode="root_sweep", scope_type="subject",
                          subject_id="sub-7", interval_minutes=10080)
    )
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, now=T0 + timedelta(minutes=1),
    )
    assert receipt["run"]["discovery_run_id"] is None
    assert receipt["run"]["comparable"] is True
    # The pointer is the radar attempt row id; discovery_run_id stays
    # optional provenance and may legitimately be absent.
    assert receipt["schedule"]["last_successful_comparable_run_id"] == receipt["run"]["id"]
    stored = store.list_radar_schedule_runs(made["id"])
    assert len(stored) == 1
    assert stored[0]["discovery_run_id"] is None


def test_invalid_attempt_status_rejected(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    with pytest.raises(ValueError, match="status"):
        store.complete_schedule_attempt(
            claim["schedule_id"], claim["claim_token"],
            status="complete-ish", comparable=False, now=T0,
        )
    assert store.list_radar_schedule_runs(made["id"]) == []


def test_run_row_records_timestamps_comparability_and_health(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    health = [{"source": "trends", "status": "ok", "records": 20}]
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-42", source_health=health,
        now=T0 + timedelta(minutes=4),
    )
    run = receipt["run"]
    assert run["started_at"] == T0.isoformat()
    assert run["completed_at"] == (T0 + timedelta(minutes=4)).isoformat()
    assert run["comparable"] is True
    assert run["discovery_run_id"] == "run-42"
    assert run["source_health"] == health
    schedule = receipt["schedule"]
    assert schedule["last_source_health"] == health
    assert schedule["last_successful_comparable_run_id"] == run["id"]
    assert schedule["last_attempt_at"] == T0.isoformat()


def test_missing_source_health_is_not_invented(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    made = store.upsert_radar_schedule(**schedule_kwargs())
    claim = store.claim_due_schedules(now=T0, lease_minutes=30)[0]
    store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="error",
        comparable=False, error_category="source_timeout",
        now=T0 + timedelta(minutes=1),
    )
    run = store.list_radar_schedule_runs(made["id"])[0]
    assert run["source_health"] is None
    assert store.get_radar_schedule(made["id"])["last_source_health"] is None


# --- Deterministic timestamps and normalization ---------------------------


def test_aware_non_utc_datetimes_normalized_to_utc_iso(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    local = datetime(2026, 8, 15, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    made = store.upsert_radar_schedule(**schedule_kwargs(next_run_at=local))
    assert made["next_run_at"] == T0.isoformat()

    claim = store.claim_due_schedules(now="2026-08-15T00:00:00Z", lease_minutes=30)[0]
    assert claim["claimed_at"] == T0.isoformat()
    receipt = store.complete_schedule_attempt(
        claim["schedule_id"], claim["claim_token"], status="complete",
        comparable=True, discovery_run_id="run-tz",
        now="2026-08-15T00:05:00+00:00",
    )
    assert receipt["run"]["completed_at"] == "2026-08-15T00:05:00+00:00"
    assert receipt["schedule"]["next_run_at"] == "2026-08-16T00:05:00+00:00"


def test_unparseable_timestamp_rejected(tmp_path):
    store = DiscoveryStore(tmp_path / "discovery.db")
    with pytest.raises(ValueError, match="ISO-8601"):
        store.upsert_radar_schedule(**schedule_kwargs(next_run_at="tomorrow"))


# --- Workspace active-subject listing (read-only) -------------------------


def test_list_active_subjects_filters_and_decodes(tmp_path):
    store = WorkspaceStore(tmp_path / "workflow.db")
    project = store.create_project("one", "Radar", default_geo="SG")
    live = store.create_subject(
        "one", project["id"], "Turbines", platforms=["Reddit", "TikTok"],
        cadence_minutes=10080, geo="SG",
        budget={"max_llm_calls": 3},
    )
    store.create_alias("one", project["id"], live["id"], "turbine", "include")
    store.create_alias("one", project["id"], live["id"], "spam", "exclude")
    store.create_subject("one", project["id"], "Paused", active=False)
    archived = store.create_project("one", "Old", default_geo="US")
    store.archive_project("one", archived["id"])
    store.create_subject("one", archived["id"], "Zombie")
    other = store.create_project("two", "Other", default_geo="US")
    store.create_subject("two", other["id"], "Machines")

    subjects = store.list_active_subjects()

    assert [subject["name"] for subject in subjects] == ["Turbines", "Machines"]
    turbines, machines = subjects
    assert turbines["workspace_id"] == "one"
    assert turbines["project_id"] == project["id"]
    assert turbines["project_default_geo"] == "SG"
    assert turbines["platforms"] == ["reddit", "tiktok"]
    assert turbines["budget"] == {"max_llm_calls": 3}
    assert turbines["active"] is True
    assert [
        (alias["alias"], alias["kind"]) for alias in turbines["aliases"]
    ] == [("spam", "exclude"), ("turbine", "include")]
    assert machines["workspace_id"] == "two"
    assert machines["project_default_geo"] == "US"


def test_list_active_subjects_on_empty_store(tmp_path):
    store = WorkspaceStore(tmp_path / "workflow.db")
    assert store.list_active_subjects() == []
