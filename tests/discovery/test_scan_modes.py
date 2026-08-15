"""Explicit scan-mode policy tests (Phase 1 Task 1.1).

Every collection path must declare which capabilities it may use. The mode
policies are the contract: a violation anywhere in the pipeline is a bug,
not a tuning decision.
"""

import pytest

from social_scraper.discovery.scan_modes import (
    FEED_MODES,
    RESEARCH_RUN_MODES,
    ScanMode,
    coerce_scan_mode,
    policy_for,
    resolve_scan_mode,
)
from social_scraper.discovery.stages import (
    DEPTH_STAGES,
    stages_for_scan_mode,
)

ALL_MODE_VALUES = [
    "trends_snapshot",
    "root_sweep",
    "deep_read",
    "horizontal_synthesis",
    "optional_interpretation",
]


# ── Enum surface ──────────────────────────────────────────────


def test_scan_mode_enum_values_are_explicit_and_stable():
    assert [mode.value for mode in ScanMode] == ALL_MODE_VALUES


def test_every_mode_has_a_policy():
    for mode in ScanMode:
        assert policy_for(mode).mode is mode


def test_every_policy_is_internally_consistent():
    for mode in ScanMode:
        policy = policy_for(mode)
        # A mode that forbids LLM must advertise a zero cap, and vice versa.
        assert policy.allows_llm == (policy.max_llm_calls_per_family > 0), mode
        # A mode that forbids thread reads must advertise max_threads=0.
        if not policy.allows_thread_reads:
            assert policy.max_threads == 0, mode
        else:
            assert policy.max_threads is None or policy.max_threads > 0, mode


# ── Hard invariants from the approved plan ────────────────────


def test_trends_snapshot_forbids_broker_search_threads_and_llm():
    policy = policy_for(ScanMode.TRENDS_SNAPSHOT)
    assert policy.allows_broker_search is False
    assert policy.allows_thread_reads is False
    assert policy.max_threads == 0
    assert policy.allows_llm is False
    assert policy.max_llm_calls_per_family == 0


def test_root_sweep_allows_root_search_but_no_threads_and_no_llm():
    policy = policy_for(ScanMode.ROOT_SWEEP)
    assert policy.allows_broker_search is True
    assert policy.allows_thread_reads is False
    assert policy.max_threads == 0
    assert policy.allows_llm is False
    assert policy.max_llm_calls_per_family == 0


def test_deep_read_allows_threads_but_still_no_llm():
    policy = policy_for(ScanMode.DEEP_READ)
    assert policy.allows_broker_search is True
    assert policy.allows_thread_reads is True
    assert policy.allows_llm is False
    assert policy.max_llm_calls_per_family == 0


def test_horizontal_synthesis_bounds_llm_to_one_call_per_family():
    policy = policy_for(ScanMode.HORIZONTAL_SYNTHESIS)
    assert policy.allows_llm is True
    assert policy.max_llm_calls_per_family == 1


def test_optional_interpretation_is_llm_only_no_new_collection():
    policy = policy_for(ScanMode.OPTIONAL_INTERPRETATION)
    assert policy.allows_broker_search is False
    assert policy.allows_thread_reads is False
    assert policy.max_threads == 0
    assert policy.allows_llm is True
    assert policy.max_llm_calls_per_family == 1


def test_feed_modes_and_research_run_modes_partition_the_enum():
    assert FEED_MODES == frozenset(
        {ScanMode.TRENDS_SNAPSHOT, ScanMode.ROOT_SWEEP}
    )
    assert RESEARCH_RUN_MODES == frozenset(
        {ScanMode.DEEP_READ, ScanMode.HORIZONTAL_SYNTHESIS,
         ScanMode.OPTIONAL_INTERPRETATION}
    )
    assert not (FEED_MODES & RESEARCH_RUN_MODES)
    assert FEED_MODES | RESEARCH_RUN_MODES == frozenset(ScanMode)


# ── Coercion and legacy-flag resolution ───────────────────────


def test_coerce_scan_mode_accepts_enum_and_canonical_strings():
    assert coerce_scan_mode(ScanMode.ROOT_SWEEP) is ScanMode.ROOT_SWEEP
    assert coerce_scan_mode("root_sweep") is ScanMode.ROOT_SWEEP
    assert coerce_scan_mode(" Root-Sweep ") is ScanMode.ROOT_SWEEP


def test_coerce_scan_mode_rejects_unknown_values_with_full_list():
    with pytest.raises(ValueError) as excinfo:
        coerce_scan_mode("vibes")
    message = str(excinfo.value)
    assert "vibes" in message
    for value in ALL_MODE_VALUES:
        assert value in message


def test_resolve_scan_mode_prefers_explicit_mode_over_legacy_gate():
    assert resolve_scan_mode(
        mode="trends_snapshot", apply_gate=True
    ) is ScanMode.TRENDS_SNAPSHOT
    assert resolve_scan_mode(
        mode=ScanMode.ROOT_SWEEP, apply_gate=False
    ) is ScanMode.ROOT_SWEEP


def test_resolve_scan_mode_maps_legacy_gate_flag_without_llm_modes():
    # The legacy flag never resolves to an LLM-capable mode.
    assert resolve_scan_mode(mode=None, apply_gate=True) is ScanMode.ROOT_SWEEP
    assert resolve_scan_mode(mode=None, apply_gate=False) is ScanMode.TRENDS_SNAPSHOT
    # No flags at all: the cheap default.
    assert resolve_scan_mode(mode=None, apply_gate=None) is ScanMode.TRENDS_SNAPSHOT


# ── Stage mapping ─────────────────────────────────────────────


def test_stages_for_scan_mode_matches_collection_depth():
    assert stages_for_scan_mode(ScanMode.TRENDS_SNAPSHOT) == ()
    assert stages_for_scan_mode("root_sweep") == ("root_probe",)
    assert stages_for_scan_mode(ScanMode.DEEP_READ) == DEPTH_STAGES["deep_read"]
    assert stages_for_scan_mode(
        ScanMode.HORIZONTAL_SYNTHESIS
    ) == DEPTH_STAGES["horizontal_extraction"]
    assert stages_for_scan_mode(
        ScanMode.OPTIONAL_INTERPRETATION
    ) == DEPTH_STAGES["optional_enrichment"]


def test_stages_for_scan_mode_rejects_unknown_modes():
    with pytest.raises(ValueError):
        stages_for_scan_mode("vibes")
