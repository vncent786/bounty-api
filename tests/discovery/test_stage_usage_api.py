import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from apis import dashboard_api
from social_scraper.discovery.budgets import StageUsage
from social_scraper.discovery.storage import DiscoveryStore


def test_run_usage_api_returns_rows_and_separate_totals(tmp_path, monkeypatch):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US",
        observed_at="2026-08-10T12:00:00+00:00",
        candidates=[{"keyword": "alpha"}, {"keyword": "beta"}],
    )
    start = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    store.record_stage_usage(StageUsage(
        discovery_run_id=run_id,
        stage="root_probe",
        started_at=start,
        completed_at=start + timedelta(seconds=1),
        candidates_considered=2,
        candidates_processed=1,
        records_returned=3,
        external_calls=2,
        llm_calls=0,
        cache_hits=0,
        status="complete",
    ))
    store.record_stage_usage(StageUsage(
        discovery_run_id=run_id,
        stage="horizontal_extraction",
        started_at=start + timedelta(seconds=1),
        completed_at=start + timedelta(seconds=2.5),
        candidates_considered=1,
        candidates_processed=1,
        records_returned=1,
        external_calls=0,
        llm_calls=1,
        cache_hits=1,
        status="complete",
        input_tokens=80,
        output_tokens=20,
        tokens_estimated=True,
    ))
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)

    response = asyncio.run(dashboard_api.get_discovery_run_usage(run_id))

    assert response["run_id"] == run_id
    assert len(response["rows"]) == 2
    assert response["totals"] == {
        "source_calls": 2,
        "llm_calls": 1,
        "cache_hits": 1,
        "candidates_considered": 3,
        "candidates_processed": 2,
        "records_returned": 4,
        "duration_seconds": 2.5,
        "input_tokens": 80,
        "output_tokens": 20,
        "tokens_estimated": True,
    }
    assert response["rows"][0]["error_category"] is None


def test_run_usage_api_returns_zeros_for_run_without_receipts(tmp_path, monkeypatch):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US", observed_at="2026-08-10T12:00:00+00:00", candidates=[]
    )
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)

    response = asyncio.run(dashboard_api.get_discovery_run_usage(run_id))

    assert response["rows"] == []
    assert response["totals"]["source_calls"] == 0
    assert response["totals"]["input_tokens"] is None
    assert response["totals"]["output_tokens"] is None
    assert response["totals"]["tokens_estimated"] is False


def test_run_usage_api_returns_404_for_unknown_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_api, "_discovery_store", DiscoveryStore(tmp_path / "discovery.db")
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(dashboard_api.get_discovery_run_usage("missing"))

    assert exc.value.status_code == 404


def test_run_usage_api_does_not_present_partial_llm_token_totals_as_complete(
    tmp_path, monkeypatch
):
    store = DiscoveryStore(tmp_path / "discovery.db")
    run_id = store.record_feed(
        geo="US", observed_at="2026-08-10T12:00:00+00:00", candidates=[]
    )
    now = datetime.now(timezone.utc)
    for stage, input_tokens in (("horizontal_extraction", 50), ("lens_evaluation", None)):
        store.record_stage_usage(StageUsage(
            discovery_run_id=run_id,
            stage=stage,
            started_at=now,
            completed_at=now,
            llm_calls=1,
            status="complete",
            input_tokens=input_tokens,
            output_tokens=input_tokens,
            tokens_estimated=input_tokens is not None,
        ))
    monkeypatch.setattr(dashboard_api, "_discovery_store", store)

    response = asyncio.run(dashboard_api.get_discovery_run_usage(run_id))

    assert response["totals"]["input_tokens"] is None
    assert response["totals"]["output_tokens"] is None
    assert response["totals"]["tokens_estimated"] is True
