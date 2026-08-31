import json
import sqlite3
from pathlib import Path

import pytest

from scripts.build_private_radar_snapshot import build_snapshot
from social_scraper.investing.private_radar import PrivateRadarStore


def test_snapshot_builder_rejects_failed_latest_scan_instead_of_overwriting_last_good_file(
    tmp_path: Path,
):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, created = store.create_scan_if_idle()
    assert created is True
    store.fail_scan(run_id, "preflight_reddit_unavailable")

    with pytest.raises(RuntimeError, match="successful terminal private Radar scan"):
        build_snapshot(tmp_path / "radar.db")


def _series(days):
    return {
        "query": "specific reusable liner",
        "source": "Google Trends",
        "geo": "",
        "horizon": "3m",
        "timeframe": "today 3-m",
        "normalized": True,
        "status": "complete",
        "points": [
            {"date": f"2026-01-{(index % 28) + 1:02d}", "value": 10 + index % 5}
            for index in range(days)
        ],
        "error_category": None,
    }


def _movement_bundle():
    three_month = _series(90)
    annual = {**_series(53), "horizon": "1y", "timeframe": "today 12-m"}
    five_year = {**_series(260), "horizon": "5y", "timeframe": "today 5-y"}
    option = {
        "query": "specific reusable liner",
        "source": "cited_social_anchor",
        "reason": "Exact cited phrase.",
        "series": {
            "WORLDWIDE": {
                "3m": three_month,
                "1y": annual,
                "5y": five_year,
            }
        },
        "classification": {
            "movement_type": "stable_or_unclear",
            "trend_eligible": False,
            "reason": "Fixture movement is not directional.",
            "metrics": {},
        },
    }
    return {
        "query": option["query"],
        "source": "Google Trends",
        "default_query": option["query"],
        "default_geo": "WORLDWIDE",
        "default_horizon": "3m",
        "geographies": [{"code": "WORLDWIDE", "name": "Worldwide"}],
        "horizons": [
            {"code": "3m", "name": "3 months"},
            {"code": "1y", "name": "1 year"},
            {"code": "5y", "name": "5 years"},
        ],
        "query_options": [option],
        "series": option["series"],
        "classification": option["classification"],
    }


def test_snapshot_packages_persisted_scan_without_google_or_model_calls(tmp_path: Path):
    store = PrivateRadarStore(tmp_path / "radar.db")
    run_id, _ = store.create_scan_if_idle()
    store.add_evidence(run_id, [{
        "id": "e1",
        "panel_id": "household_cleaning",
        "platform": "tiktok",
        "external_id": "post-1",
        "url": "https://example.invalid/post-1",
        "author": "qa-author",
        "text": "I switched to a specific reusable liner",
        "created_at": "2026-08-20T00:00:00Z",
        "observed_at": "2026-08-30T00:00:00Z",
        "window_key": "current",
        "query": "specific reusable liner",
        "engagement": {"likes": 20, "comments": 3},
    }])
    movement = _movement_bundle()
    decision = {
        "candidate_id": "candidate-1",
        "panel_id": "household_cleaning",
        "qualification_status": "not_qualified",
        "label": "Specific reusable liner switching",
        "behaviour_type": "switching",
        "anchor_terms": ["specific reusable liner"],
        "summary": "One person switched to a specific reusable liner.",
        "economic_mechanism": "Reusable purchases may replace disposable units.",
        "why_investigate": "Check whether the behavior replicates.",
        "contradiction": "The report may be isolated.",
        "invalidation": "Reject if no independent users appear.",
        "evidence_ids": ["e1"],
        "parity": {"level": "L1", "status": "niche_coverage", "articles": []},
        "windows": [],
        "trajectory": movement["series"]["WORLDWIDE"]["3m"],
        "movement_bundle": movement,
        "gates": {},
    }
    store.complete_scan(run_id, [decision], limitations=[], sources=[])
    calls = []

    def forbidden_movement_provider(_candidates):
        calls.append("called")
        raise AssertionError("snapshot packaging must not call Google Trends")

    payload = build_snapshot(
        tmp_path / "radar.db",
        movement_provider=forbidden_movement_provider,
    )

    assert calls == []
    assert payload["methodology_recheck"]["source_run_id"] == run_id
    assert payload["methodology_recheck"]["google_movement_refreshed"] is False
    assert payload["methodology_recheck"]["google_movement_reused_from_scan"] is True
    assert len(payload["opportunity_queue"]) == 1
    assert payload["opportunity_queue"][0]["evidence"][0]["source_url"] == (
        "https://example.invalid/post-1"
    )

    # A legacy/partial successful scan may lack persisted movement entirely.
    # Packaging must leave that hole explicit rather than calling Google.
    with sqlite3.connect(tmp_path / "radar.db") as connection:
        row = connection.execute(
            "SELECT decisions_json FROM private_radar_scans WHERE id=?",
            (run_id,),
        ).fetchone()
        decisions = json.loads(row[0])
        decisions[0].pop("trajectory", None)
        decisions[0].pop("movement_bundle", None)
        connection.execute(
            "UPDATE private_radar_scans SET decisions_json=? WHERE id=?",
            (json.dumps(decisions), run_id),
        )

    payload_without_movement = build_snapshot(
        tmp_path / "radar.db",
        movement_provider=forbidden_movement_provider,
    )

    assert calls == []
    assert len(payload_without_movement["opportunity_queue"]) == 1
    missing_opportunity = payload_without_movement["opportunity_queue"][0]
    assert missing_opportunity["trajectory"]["error_category"] == "not_persisted"
    assert missing_opportunity["movement_bundle"]["status"] == "not_persisted"
