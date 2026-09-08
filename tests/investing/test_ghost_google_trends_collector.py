"""Deterministic GHOST Google Trends collector contract."""

from __future__ import annotations

from datetime import date, timedelta
import json

from scripts.collect_ghost_google_trends import (
    build_search_observation,
    persist_observation,
    should_reuse_daily_success,
)


QUERIES = ["ghost root beer energy drink", "ghost a&w root beer"]


def source_result(*, geo="US", status="complete", effective="web_default"):
    dates = [(date(2026, 8, 19) + timedelta(days=index)).isoformat() for index in range(21)]
    return {
        "status": status,
        "source": "Google Trends",
        "route": "trendspy_interest_over_time",
        "query_basket": QUERIES,
        "timeframe": "today 3-m",
        "geo": "" if geo == "WORLDWIDE" else geo,
        "requested_gprop": "web",
        "effective_gprop": effective,
        "returned_values": {
            "dates": dates,
            QUERIES[0]: list(range(1, 22)),
            QUERIES[1]: [10] * 21,
        } if status == "complete" else None,
        "isPartial_flags": [False] * 20 + [True] if status == "complete" else None,
        "rows_returned": 21 if status == "complete" else 0,
        "error_category": None if status == "complete" else "HTTP_429",
    }


def config():
    return {
        "schema_version": "bounty-standing-monitor/1",
        "monitor_id": "standing::ghost-aw-kdp",
        "candidate": "GHOST Energy x A&W Root Beer",
        "search_attention": {
            "queries": QUERIES,
            "timeframe": "today 3-m",
            "requested_gprop": "web",
            "geographies": ["US", "WORLDWIDE"],
        },
    }


def test_observation_excludes_partial_day_and_builds_same_request_windows():
    observation = build_search_observation(
        config(),
        {
            "US": source_result(geo="US"),
            "WORLDWIDE": source_result(geo="WORLDWIDE"),
        },
        observed_at="2026-09-08T16:01:00Z",
        contract_sha256="contract-hash",
        preflight=source_result(geo="US"),
    )

    assert observation["status"] == "complete"
    assert observation["observation_day_sgt"] == "2026-09-09"
    assert observation["requested_gprop"] == "web"
    assert observation["effective_gprop"] == "web_default"
    assert observation["writer"] == {
        "writer_id": "scripts/collect_ghost_google_trends.py",
        "writer_version": 1,
    }
    us = observation["geographies"]["US"]
    assert us["latest_complete_date"] == "2026-09-07"
    assert us["partial_dates_excluded"] == ["2026-09-08"]
    assert us["latest_7_dates"] == [
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
        "2026-09-05", "2026-09-06", "2026-09-07",
    ]
    assert us["prior_7_dates"] == [
        "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28",
        "2026-08-29", "2026-08-30", "2026-08-31",
    ]
    assert us["latest_7_values"][QUERIES[0]] == list(range(14, 21))
    assert us["prior_7_values"][QUERIES[0]] == list(range(7, 14))
    assert us["latest_to_prior_ratio"][QUERIES[1]] == 1.0


def test_source_failure_never_advances_last_success_pointer(tmp_path):
    success = build_search_observation(
        config(),
        {"US": source_result(), "WORLDWIDE": source_result(geo="WORLDWIDE")},
        observed_at="2026-09-08T00:10:00Z",
        contract_sha256="contract-hash",
        preflight=source_result(),
    )
    persist_observation(tmp_path, success, run_id="success")
    latest_path = tmp_path / "artifacts/dd/ghost-kdp/trends_latest.json"
    accepted = json.loads(latest_path.read_text(encoding="utf-8"))

    failure = build_search_observation(
        config(),
        {
            "US": source_result(status="SOURCE_FAILURE"),
            "WORLDWIDE": source_result(geo="WORLDWIDE", status="SOURCE_FAILURE"),
        },
        observed_at="2026-09-09T00:10:00Z",
        contract_sha256="contract-hash",
        preflight=source_result(),
    )
    persist_observation(tmp_path, failure, run_id="failure")

    assert json.loads(latest_path.read_text(encoding="utf-8")) == accepted
    attempt = json.loads(
        (tmp_path / "artifacts/dd/ghost-kdp/trends_attempt_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert attempt["status"] == "SOURCE_FAILURE"
    history = [
        json.loads(line)
        for line in (
            tmp_path / "artifacts/dd/ghost-kdp/trends_history.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in history] == ["complete", "SOURCE_FAILURE"]


def test_daily_reuse_requires_current_complete_registered_writer_and_contract():
    observation = build_search_observation(
        config(),
        {"US": source_result(), "WORLDWIDE": source_result(geo="WORLDWIDE")},
        observed_at="2026-09-08T00:10:00Z",
        contract_sha256="contract-hash",
        preflight=source_result(),
    )
    assert should_reuse_daily_success(
        observation,
        observed_at="2026-09-08T15:59:00Z",
        contract_sha256="contract-hash",
    ) is True
    assert should_reuse_daily_success(
        observation,
        observed_at="2026-09-08T16:01:00Z",
        contract_sha256="contract-hash",
    ) is False

    changed = json.loads(json.dumps(observation))
    changed["writer"]["writer_version"] = 0
    assert should_reuse_daily_success(
        changed,
        observed_at="2026-09-08T15:59:00Z",
        contract_sha256="contract-hash",
    ) is False
