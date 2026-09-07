"""Execution contract for the durable daily GHOST thesis monitor."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.run_ghost_thesis_monitor import (
    build_execution_requests,
)


ROOT = Path(__file__).parents[2]


def test_ghost_runner_builds_every_public_parity_lane_without_a_paywall_request():
    config = json.loads(
        (ROOT / "references" / "ghost-standing-monitor-v1.json").read_text(
            encoding="utf-8"
        )
    )

    requests = build_execution_requests(
        config,
        observed_at="2026-09-06T13:00:00Z",
    )

    assert set(requests) == {
        "official_ir",
        "regulator_filings",
        "earnings_calls",
        "official_product_context",
        "qualifying_business_news",
        "sell_side_public_mentions",
    }
    assert all(request.resume_key.startswith("standing::ghost-aw-kdp:2026-09-06:") for request in requests.values())
    assert requests["official_ir"].options["sources"]
    assert requests["regulator_filings"].options["sec_cik"] == "0001418135"
    assert requests["regulator_filings"].options["submissions_url"].startswith("https://data.sec.gov/")
    assert "8-K" in requests["regulator_filings"].options["recent_forms"]
    assert requests["earnings_calls"].options["sources"][0]["event_date"] == "2026-08-06"
    assert requests["qualifying_business_news"].options["rss_queries"]
    assert requests["sell_side_public_mentions"].options["rss_queries"]
    assert "paywall" not in json.dumps(
        {key: request.identity_payload() for key, request in requests.items()}
    ).casefold()


def test_ghost_runner_is_a_production_script_not_a_dated_tmp_collector():
    source = (ROOT / "scripts" / "run_ghost_thesis_monitor.py").read_text(
        encoding="utf-8"
    )

    assert "SourceExecutor" in source
    assert "PublicParityConnector" in source
    assert "JsonReceiptStore" in source
    assert "build_public_parity_observation" in source
    assert "coverage_latest.json" in source
    assert "coverage_history.jsonl" in source
    assert "parity-runs" in source
    assert '"NICHE_ONLY"' not in source
    assert "automatic_trade_action" in source
