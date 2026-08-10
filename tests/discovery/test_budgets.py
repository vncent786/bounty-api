from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from social_scraper.discovery.budgets import ScanBudget, StageUsage


def test_scan_budget_defaults_are_immutable_and_serializable():
    budget = ScanBudget()

    assert budget.to_dict() == {
        "root_probe_candidates": 20,
        "deep_read_candidates": 5,
        "horizontal_llm_candidates": 5,
        "threads_per_platform": 2,
        "comments_per_thread": 20,
        "max_thread_depth": 2,
        "optional_enrichments": 0,
    }
    assert ScanBudget.from_dict(budget.to_dict()) == budget
    with pytest.raises(FrozenInstanceError):
        budget.root_probe_candidates = 30


@pytest.mark.parametrize("field", [
    "root_probe_candidates",
    "deep_read_candidates",
    "horizontal_llm_candidates",
    "threads_per_platform",
    "comments_per_thread",
    "max_thread_depth",
    "optional_enrichments",
])
def test_scan_budget_rejects_negative_and_non_integer_limits(field):
    with pytest.raises(ValueError):
        ScanBudget(**{field: -1})
    with pytest.raises(TypeError):
        ScanBudget(**{field: 1.5})


def test_scan_budget_bounds_thread_depth_as_an_operational_guard():
    assert ScanBudget(max_thread_depth=0).max_thread_depth == 0
    assert ScanBudget(max_thread_depth=10).max_thread_depth == 10
    with pytest.raises(ValueError, match="0..10"):
        ScanBudget(max_thread_depth=11)


def test_stage_usage_normalizes_outcome_and_computes_duration():
    start = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    usage = StageUsage(
        discovery_run_id="run-1",
        stage=" ROOT_PROBE ",
        started_at=start,
        completed_at=start + timedelta(seconds=1.25),
        candidates_considered=3,
        candidates_processed=2,
        records_returned=4,
        external_calls=2,
        llm_calls=0,
        cache_hits=1,
        status=" COMPLETE ",
    )

    assert usage.stage == "root_probe"
    assert usage.status == "complete"
    assert usage.duration_seconds == 1.25
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.tokens_estimated is False
    assert StageUsage.from_dict(usage.to_dict()) == usage


def test_stage_usage_rejects_unknown_values_bad_times_and_negative_counters():
    now = datetime.now(timezone.utc)
    common = dict(
        discovery_run_id="run-1",
        stage="observed",
        started_at=now,
        completed_at=now,
        status="complete",
    )
    with pytest.raises(ValueError, match="unknown stage"):
        StageUsage(**{**common, "stage": "made_up"})
    with pytest.raises(ValueError, match="unknown status"):
        StageUsage(**{**common, "status": "mysterious"})
    with pytest.raises(ValueError, match="completed_at"):
        StageUsage(**{**common, "completed_at": now - timedelta(seconds=1)})
    with pytest.raises(ValueError):
        StageUsage(**common, external_calls=-1)


def test_stage_usage_requires_explicit_nonnegative_token_metadata():
    now = datetime.now(timezone.utc)
    common = dict(
        discovery_run_id="run-1",
        stage="horizontal_extraction",
        started_at=now,
        completed_at=now,
        status="complete",
    )
    usage = StageUsage(**common, input_tokens=12, output_tokens=7, tokens_estimated=True)
    assert usage.to_dict()["tokens_estimated"] is True
    with pytest.raises(ValueError):
        StageUsage(**common, input_tokens=-1)
