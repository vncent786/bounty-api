from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import time

import pytest

from social_scraper.investing import (
    GlobalRadarSweep,
    InvestingRadarService,
    InvestingRadarStore,
)
from social_scraper.monitoring.topdown import TRENDING_NOW_COUNTRIES


def _candidate(keyword: str, **overrides):
    candidate = {
        "keyword": keyword,
        "source": "google_trends",
        "discovered_at": "2026-08-25T10:00:00Z",
        "search_volume": 500,
        "growth_pct": 120.0,
        "started_hours_ago": 2.5,
        "source_started_at": "2026-08-25T07:30:00Z",
        "categories": "Business & Finance, Technology",
        "related_terms": ["chips", "earnings"],
        "topic_ids": [3, 18],
        "discovery_run_id": "legacy-discovery-run",
        "candidate_observation_id": 91,
        "source_record_count": 1,
        "source_observations": [{"feed": "trending_now"}],
    }
    candidate.update(overrides)
    return candidate


def test_adds_schema_to_realistic_nonempty_legacy_database(tmp_path):
    db_path = tmp_path / "shared-discovery.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            PRAGMA user_version = 17;
            CREATE TABLE schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES ('2026_08_10_existing', '2026-08-10T00:00:00Z');
            CREATE TABLE discovery_runs(
                id TEXT PRIMARY KEY, geo TEXT NOT NULL, observed_at TEXT NOT NULL,
                status TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            INSERT INTO discovery_runs VALUES(
                'existing-run', 'US', '2026-08-24T00:00:00Z', 'complete',
                '{"candidate_count":388}'
            );
            CREATE TABLE unrelated_runtime_state(key TEXT PRIMARY KEY, value BLOB);
            INSERT INTO unrelated_runtime_state VALUES('connector-cookie', X'010203');
            """
        )

    InvestingRadarStore(db_path)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute(
            "SELECT id, geo, payload_json FROM discovery_runs"
        ).fetchall() == [("existing-run", "US", '{"candidate_count":388}')]
        assert connection.execute(
            "SELECT key, hex(value) FROM unrelated_runtime_state"
        ).fetchall() == [("connector-cookie", "010203")]
        radar_tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'investing_radar_%'"
            )
        }
        assert {
            "investing_radar_schema_migrations",
            "investing_radar_sweeps",
            "investing_radar_market_outcomes",
            "investing_radar_candidates",
            "investing_radar_candidate_observations",
        } <= radar_tables
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_create_sweep_if_idle_prevents_duplicate_collectors(tmp_path):
    path = tmp_path / "radar.db"
    first_store = InvestingRadarStore(path)
    second_store = InvestingRadarStore(path)

    first_id, first_created = first_store.create_sweep_if_idle(125)
    second_id, second_created = second_store.create_sweep_if_idle(125)

    assert first_created is True
    assert second_created is False
    assert second_id == first_id


def test_global_sweep_preserves_complete_empty_and_failed_markets(tmp_path):
    calls = []

    async def fetch(geo):
        calls.append(geo)
        if geo == "DE":
            raise TimeoutError("upstream timed out")
        if geo == "GB":
            return []
        return [_candidate("Nvidia")]

    store = InvestingRadarStore(tmp_path / "radar.db")
    result = asyncio.run(GlobalRadarSweep(store, fetch).run([
        ("US", "United States"),
        ("GB", "United Kingdom"),
        ("DE", "Germany"),
    ]))

    assert calls == ["US", "GB", "DE"]
    assert result["status"] == "partial"
    assert result["recorded_markets"] == 3
    assert result["complete_markets"] == 1
    assert result["empty_markets"] == 1
    assert result["failed_markets"] == 1
    assert result["candidate_observations"] == 1
    outcomes = {market["country"]: market for market in result["markets"]}
    assert outcomes["US"]["status"] == "complete"
    assert outcomes["US"]["candidate_count"] == 1
    assert outcomes["GB"]["status"] == "empty"
    assert outcomes["GB"]["error_category"] is None
    assert outcomes["DE"]["status"] == "failed"
    assert outcomes["DE"]["error_category"] == "TimeoutError"


def test_cross_country_identity_dedup_keeps_immutable_provenance_and_nulls(tmp_path):
    async def fetch(geo):
        if geo == "US":
            return [_candidate(
                "  NVIDIA   RTX  ",
                search_volume=None,
                growth_pct=220.0,
                related_terms=["Blackwell"],
                categories="Technology",
                discovery_run_id="us-source-run",
                candidate_observation_id=11,
            )]
        return [_candidate(
            "nvidia rtx",
            search_volume=900,
            growth_pct=None,
            related_terms=["GeForce"],
            categories=["Shopping", "Technology"],
            discovery_run_id="gb-source-run",
            candidate_observation_id=12,
            discovered_at="2026-08-25T10:05:00+00:00",
        )]

    db_path = tmp_path / "radar.db"
    store = InvestingRadarStore(db_path)
    sweep = asyncio.run(GlobalRadarSweep(store, fetch).run(["US", "GB"]))
    radar = store.list_radar()

    assert sweep["status"] == "complete"
    assert sweep["candidate_observations"] == 2
    assert sweep["unique_candidates"] == 1
    assert len(radar) == 1
    item = radar[0]
    assert item["normalized_keyword"] == "nvidia rtx"
    assert item["countries"] == ["GB", "US"]
    assert item["country_count"] == 2
    assert item["search_volume"] is None
    assert item["growth_pct"] is None
    assert set(item["metric_conflicts"]) >= {"search_volume", "growth_pct"}
    assert item["categories"] == ["Technology", "Shopping"]
    assert item["related_terms"] == ["Blackwell", "GeForce"]
    observations = {row["country"]: row for row in item["observations"]}
    assert observations["US"]["search_volume"] is None
    assert observations["US"]["growth_pct"] == 220.0
    assert observations["US"]["observed_at"] == "2026-08-25T10:00:00+00:00"
    assert observations["US"]["provenance"]["source_run_id"] == "us-source-run"
    assert observations["US"]["provenance"]["source_observation_id"] == 11
    assert observations["GB"]["search_volume"] == 900
    assert observations["GB"]["growth_pct"] is None
    assert store.list_radar(country="gb")[0]["countries"] == ["GB"]
    assert store.list_radar(category="Shopping")[0]["candidate_id"] == item["candidate_id"]
    assert store.list_radar(category="Sports") == []

    detail = store.get_candidate(item["candidate_id"])
    assert detail is not None
    assert len(detail["observations"]) == 2
    assert {row["source_run_id"] for row in detail["observations"]} == {
        "us-source-run", "gb-source-run",
    }

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE investing_radar_candidate_observations SET search_volume = 0"
            )


def test_reads_use_only_persisted_data_and_make_zero_fetch_calls(tmp_path):
    calls = 0

    async def fetch(_geo):
        nonlocal calls
        calls += 1
        return [_candidate("Copper demand", search_volume=None, growth_pct=None)]

    store = InvestingRadarStore(tmp_path / "radar.db")
    sweep = asyncio.run(GlobalRadarSweep(store, fetch).run(["US"]))
    assert calls == 1

    service = InvestingRadarService(store)
    latest = service.latest_sweep()
    rows = service.list_radar(limit=10, country="US", category="Business & Finance")
    fetched = service.get_candidate(rows[0]["candidate_id"])
    by_id = service.get_sweep(sweep["id"])

    assert calls == 1
    assert latest["id"] == sweep["id"]
    assert by_id["id"] == sweep["id"]
    assert rows[0]["search_volume"] is None
    assert rows[0]["growth_pct"] is None
    assert fetched["observations"][0]["raw_payload"]["search_volume"] is None


def test_failed_latest_sweep_does_not_erase_last_successful_radar(tmp_path):
    store = InvestingRadarStore(tmp_path / "radar.db")
    successful = store.create_sweep(1)
    store.record_market_success(successful, "US", [_candidate("Grid batteries")])
    store.finalize_sweep(successful)

    failed = store.create_sweep(1)
    store.record_market_failure(failed, "US", "source_unavailable")
    store.finalize_sweep(failed)

    assert store.latest_sweep()["id"] == failed
    assert store.latest_sweep()["status"] == "failed"
    assert store.list_radar()[0]["keyword"] == "Grid batteries"


def test_existing_sweep_id_can_be_executed_without_creating_a_duplicate(tmp_path):
    calls = []

    async def fetch(geo):
        calls.append(geo)
        return [_candidate("Cooling demand")]

    store = InvestingRadarStore(tmp_path / "radar.db")
    sweep_id = store.create_sweep(2)
    result = asyncio.run(
        GlobalRadarSweep(store, fetch).run(["DE", "FR"], sweep_id=sweep_id)
    )

    assert calls == ["DE", "FR"]
    assert result["id"] == sweep_id
    assert result["status"] == "complete"


def test_unfiltered_radar_round_robins_categories_without_hiding_filtered_items(tmp_path):
    store = InvestingRadarStore(tmp_path / "radar.db")
    sweep_id = store.create_sweep(1)
    store.record_market_success(
        sweep_id,
        "US",
        [
            _candidate("Match one", categories=["Sports"]),
            _candidate("Match two", categories=["Sports"]),
            _candidate("Match three", categories=["Sports"]),
            _candidate("Health topic", categories=["Health"]),
            _candidate("Finance topic", categories=["Business & Finance"]),
        ],
    )
    store.finalize_sweep(sweep_id)

    global_categories = [item["categories"][0] for item in store.list_radar(limit=3)]
    assert global_categories == ["Business & Finance", "Health", "Sports"]
    assert len(store.list_radar(limit=10, category="Sports")) == 3


def test_default_provider_runs_sync_client_off_event_loop(monkeypatch):
    from social_scraper.investing import sweep as sweep_module

    def slow_sync(_geo):
        time.sleep(0.15)
        return [_candidate("Threaded provider")]

    monkeypatch.setattr(sweep_module, "_fetch_topdown_candidates_sync", slow_sync)

    async def prove_heartbeat():
        provider = asyncio.create_task(sweep_module.fetch_topdown_candidates("US"))
        started = time.perf_counter()
        await asyncio.sleep(0.02)
        heartbeat = time.perf_counter() - started
        result = await provider
        return heartbeat, result

    heartbeat, result = asyncio.run(prove_heartbeat())
    assert heartbeat < 0.08
    assert result[0]["keyword"] == "Threaded provider"


def test_default_scope_visits_every_supported_trending_now_country(tmp_path):
    calls = []

    async def empty_fetch(geo):
        calls.append(geo)
        return []

    store = InvestingRadarStore(tmp_path / "global.db")
    result = asyncio.run(GlobalRadarSweep(store, empty_fetch).run())
    expected = [code for code, _name in TRENDING_NOW_COUNTRIES]

    assert calls == expected
    assert result["status"] == "complete"
    assert result["total_markets"] == len(expected)
    assert result["recorded_markets"] == len(expected)
    assert result["empty_markets"] == len(expected)
    assert result["failed_markets"] == 0
