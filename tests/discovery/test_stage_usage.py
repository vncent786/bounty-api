import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from social_scraper.discovery.budgets import StageUsage
from social_scraper.discovery.storage import DiscoveryStore
from social_scraper.discovery.staged_runner import StageHandlerResult, StagedRunner


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


def _pre_cost_receipts_database(path):
    """A realistic pre-Task-0.2 database: the old stage-usage schema with rows."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations(name, applied_at) VALUES
            ('2026_08_10_phase1b_discovery_history', '2026-08-09T00:00:00+00:00'),
            ('2026_08_10_discovery_stage_usage', '2026-08-09T00:00:00+00:00');
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
        CREATE TABLE discovery_stage_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discovery_run_id TEXT NOT NULL REFERENCES discovery_runs(id),
            stage TEXT NOT NULL CHECK(stage IN (
                'observed','screening','root_probe','deep_read',
                'horizontal_extraction','lens_evaluation','optional_enrichment'
            )),
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0),
            candidates_considered INTEGER NOT NULL CHECK(candidates_considered >= 0),
            candidates_processed INTEGER NOT NULL CHECK(candidates_processed >= 0),
            records_returned INTEGER NOT NULL CHECK(records_returned >= 0),
            external_calls INTEGER NOT NULL CHECK(external_calls >= 0),
            llm_calls INTEGER NOT NULL CHECK(llm_calls >= 0),
            cache_hits INTEGER NOT NULL CHECK(cache_hits >= 0),
            status TEXT NOT NULL CHECK(status IN (
                'not_checked','complete','empty','partial','unavailable',
                'failed','skipped'
            )),
            error_category TEXT,
            input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
            output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
            tokens_estimated INTEGER NOT NULL DEFAULT 0
                CHECK(tokens_estimated IN (0,1))
        );
        INSERT INTO discovery_stage_usage
            (discovery_run_id, stage, started_at, completed_at, duration_seconds,
             candidates_considered, candidates_processed, records_returned,
             external_calls, llm_calls, cache_hits, status, error_category,
             input_tokens, output_tokens, tokens_estimated)
        VALUES
            ('legacy-run', 'root_probe', '2026-08-09T00:00:00+00:00',
             '2026-08-09T00:00:02+00:00', 2.0, 3, 3, 9, 6, 0, 0,
             'complete', NULL, NULL, NULL, 0),
            ('legacy-run', 'horizontal_extraction', '2026-08-09T00:00:02+00:00',
             '2026-08-09T00:00:05+00:00', 3.0, 1, 1, 2, 0, 1, 1,
             'partial', 'provider_timeout', 100, 40, 1);
    """)
    connection.commit()
    connection.close()


def test_migration_adds_cost_columns_to_a_database_with_legacy_usage_rows(tmp_path):
    path = tmp_path / "legacy-usage.db"
    _pre_cost_receipts_database(path)

    store = DiscoveryStore(path)

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(discovery_stage_usage)")
        }
        assert {
            "input_records", "input_characters",
            "input_tokens_reported", "output_tokens_reported",
            "topic_family_id", "shared_evidence_reuse",
        } <= columns
        # Legacy rows survive with their original values, nulls included.
        rows = connection.execute(
            """SELECT stage, input_tokens, output_tokens, tokens_estimated,
                      input_records, input_characters, input_tokens_reported,
                      output_tokens_reported, topic_family_id, shared_evidence_reuse
               FROM discovery_stage_usage ORDER BY id"""
        ).fetchall()
        assert rows == [
            ("root_probe", None, None, 0, 0, 0, None, None, None, 0),
            ("horizontal_extraction", 100, 40, 1, 0, 0, None, None, None, 0),
        ]
        migrated = {
            row[0] for row in connection.execute("SELECT name FROM schema_migrations")
        }
        assert "2026_08_15_stage_usage_cost_receipts" in migrated
        # New columns enforce the same nonnegative/boolean contract as fresh schemas.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE discovery_stage_usage SET input_records = -1 WHERE id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE discovery_stage_usage SET shared_evidence_reuse = 2 WHERE id = 1"
            )

    listed = store.list_stage_usage("legacy-run")
    assert [row["stage"] for row in listed] == ["root_probe", "horizontal_extraction"]
    assert listed[0]["input_records"] == 0
    assert listed[0]["input_characters"] == 0
    assert listed[0]["input_tokens_reported"] is None
    assert listed[0]["output_tokens_reported"] is None
    assert listed[0]["topic_family_id"] is None
    assert listed[0]["shared_evidence_reuse"] is False
    assert listed[1]["input_tokens"] == 100
    assert listed[1]["tokens_estimated"] is True

    # A new receipt with cost fields records alongside the legacy rows untouched.
    start = datetime(2026, 8, 9, 0, 0, 6, tzinfo=timezone.utc)
    store.record_stage_usage(StageUsage(
        discovery_run_id="legacy-run",
        stage="deep_read",
        started_at=start,
        completed_at=start + timedelta(seconds=1),
        input_records=4,
        input_characters=250,
        topic_family_id="family-x402",
        shared_evidence_reuse=True,
    ))
    listed = store.list_stage_usage("legacy-run")
    assert [row["stage"] for row in listed] == [
        "root_probe", "horizontal_extraction", "deep_read"
    ]
    assert listed[1]["input_tokens"] == 100
    assert listed[2]["input_records"] == 4
    assert listed[2]["input_characters"] == 250
    assert listed[2]["topic_family_id"] == "family-x402"
    assert listed[2]["shared_evidence_reuse"] is True


def test_stage_usage_roundtrips_cost_fields_and_keeps_estimates_distinct():
    start = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
    usage = StageUsage(
        discovery_run_id="run-costs",
        stage="horizontal_extraction",
        started_at=start,
        completed_at=start + timedelta(seconds=2),
        llm_calls=1,
        status="complete",
        # Projected/estimated usage (legacy fields) and provider-reported actuals
        # coexist without one overwriting the other.
        input_tokens=500,
        output_tokens=60,
        tokens_estimated=True,
        input_records=7,
        input_characters=3210,
        input_tokens_reported=430,
        output_tokens_reported=52,
        topic_family_id="family-agentic-payments",
        shared_evidence_reuse=False,
    )

    as_dict = usage.to_dict()
    assert as_dict["input_records"] == 7
    assert as_dict["input_characters"] == 3210
    assert as_dict["input_tokens_reported"] == 430
    assert as_dict["output_tokens_reported"] == 52
    assert as_dict["topic_family_id"] == "family-agentic-payments"
    assert as_dict["shared_evidence_reuse"] is False
    assert as_dict["input_tokens"] == 500
    assert StageUsage.from_dict(as_dict) == usage

    blank = StageUsage(
        discovery_run_id="run-costs",
        stage="root_probe",
        started_at=start,
        completed_at=start,
        status="complete",
    )
    assert blank.input_records == 0
    assert blank.input_characters == 0
    assert blank.input_tokens_reported is None
    assert blank.output_tokens_reported is None
    assert blank.topic_family_id is None
    assert blank.shared_evidence_reuse is False


def test_stage_usage_validates_cost_fields_without_inventing_values():
    now = datetime.now(timezone.utc)
    common = dict(
        discovery_run_id="run-1",
        stage="horizontal_extraction",
        started_at=now,
        completed_at=now,
        status="complete",
    )
    with pytest.raises(ValueError, match="input_records"):
        StageUsage(**common, input_records=-1)
    with pytest.raises(ValueError, match="input_characters"):
        StageUsage(**common, input_characters=-5)
    with pytest.raises(TypeError, match="integer"):
        StageUsage(**common, input_records=2.5)
    with pytest.raises(ValueError, match="input_tokens_reported"):
        StageUsage(**common, input_tokens_reported=-1)
    with pytest.raises(ValueError, match="output_tokens_reported"):
        StageUsage(**common, output_tokens_reported=-2)
    with pytest.raises(TypeError, match="boolean"):
        StageUsage(**common, shared_evidence_reuse="yes")
    with pytest.raises(TypeError, match="string"):
        StageUsage(**common, topic_family_id=7)
    # An explicitly empty family label means "no family", never a fabricated one.
    assert StageUsage(**common, topic_family_id="   ").topic_family_id is None


def _runner_plan():
    return {"candidates": [
        {"candidate_id": "c1", "candidate": {"keyword": "x402"},
         "stages": {"deep_read": "planned", "horizontal_extraction": "planned"}},
        {"candidate_id": "c2", "candidate": {"keyword": "agentic payments"},
         "stages": {"deep_read": "planned", "horizontal_extraction": "planned"}},
    ]}


def _result_handler(results_by_candidate):
    async def handler(candidate, context=None):
        return results_by_candidate[candidate["candidate_id"]]
    return handler


def test_staged_runner_aggregates_cost_fields_and_never_estimates_tokens():
    deep_results = {
        "c1": StageHandlerResult(input_records=3, input_characters=15),
        "c2": StageHandlerResult(input_records=2, input_characters=5),
    }
    # One handler reports provider tokens, the other cannot: reported totals
    # must stay null rather than estimate, while counts stay exact.
    horizontal_results = {
        "c1": StageHandlerResult(
            llm_calls=1, input_records=3, input_characters=15,
            input_tokens_reported=430, output_tokens_reported=52,
            shared_evidence_reuse=True, topic_family_id="family-x402",
        ),
        "c2": StageHandlerResult(
            llm_calls=1, input_records=2, input_characters=5,
            input_tokens_reported=None, output_tokens_reported=None,
            topic_family_id="family-x402",
        ),
    }
    handlers = {
        "deep_read": _result_handler(deep_results),
        "horizontal_extraction": _result_handler(horizontal_results),
    }

    result = asyncio.run(StagedRunner(handlers).run("run-costs", _runner_plan()))

    deep_usage, horizontal_usage = result.usages
    assert deep_usage.stage == "deep_read"
    assert deep_usage.input_records == 5
    assert deep_usage.input_characters == 20
    assert deep_usage.input_tokens_reported is None
    assert deep_usage.output_tokens_reported is None
    assert deep_usage.shared_evidence_reuse is False

    assert horizontal_usage.stage == "horizontal_extraction"
    assert horizontal_usage.input_records == 5
    assert horizontal_usage.input_characters == 20
    assert horizontal_usage.input_tokens_reported is None
    assert horizontal_usage.output_tokens_reported is None
    assert horizontal_usage.tokens_estimated is False
    assert horizontal_usage.shared_evidence_reuse is True
    assert horizontal_usage.topic_family_id == "family-x402"


def test_staged_runner_sums_reported_tokens_when_every_handler_reports():
    handlers = {
        "deep_read": _result_handler({"c1": StageHandlerResult(), "c2": StageHandlerResult()}),
        "horizontal_extraction": _result_handler({
            "c1": StageHandlerResult(
                llm_calls=1, input_records=4, input_characters=40,
                input_tokens_reported=100, output_tokens_reported=10,
            ),
            "c2": StageHandlerResult(
                llm_calls=1, input_records=6, input_characters=60,
                input_tokens_reported=200, output_tokens_reported=20,
            ),
        }),
    }

    result = asyncio.run(StagedRunner(handlers).run("run-costs", _runner_plan()))

    usage = result.usages[-1]
    assert usage.stage == "horizontal_extraction"
    assert usage.input_tokens_reported == 300
    assert usage.output_tokens_reported == 30
    assert usage.input_records == 10
    assert usage.input_characters == 100
    # No family stamped by any handler means no family on the receipt.
    assert usage.topic_family_id is None


def test_staged_runner_leaves_family_unset_when_handlers_disagree():
    handlers = {
        "deep_read": _result_handler({"c1": StageHandlerResult(), "c2": StageHandlerResult()}),
        "horizontal_extraction": _result_handler({
            "c1": StageHandlerResult(
                llm_calls=1, topic_family_id="family-x402"),
            "c2": StageHandlerResult(
                llm_calls=1, topic_family_id="family-agentic-payments"),
        }),
    }

    result = asyncio.run(StagedRunner(handlers).run("run-costs", _runner_plan()))

    assert result.usages[-1].topic_family_id is None
