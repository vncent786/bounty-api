from decimal import Decimal

from social_scraper.investing.generic_dossier import (
    build_candidate_handoff,
    build_generic_dossier,
)
from social_scraper.investing.research_dossier import (
    RangeAssumption,
    evaluate_materiality_assumptions,
)


def _candidate():
    return {
        "candidate_id": "candidate-1",
        "label": "T-Mobile plan increase prompting provider-switch consideration",
        "qualification_status": "not_qualified",
        "review_status": "needs_more_evidence",
        "behaviour_type": "switching",
        "summary": "Some customers discuss switching after a plan increase.",
        "economic_mechanism": "Price increases could raise churn.",
        "why_investigate": "Check whether switching is persistent and financially relevant.",
        "contradiction": "Complaints may not convert into churn.",
        "invalidation": "Later evidence shows no switching.",
        "anchor_terms": ["T-Mobile plan increase"],
        "evidence": [{
            "id": "e1",
            "platform": "tiktok",
            "url": "https://example.com/e1",
            "text": "I am considering switching after the increase.",
        }],
    }


def test_candidate_handoff_preserves_review_only_status_and_evidence():
    candidate = _candidate()

    handoff = build_candidate_handoff(
        candidate,
        source_scan_id="scan-1",
        selection_mode="research_only",
        created_at="2026-08-29T10:00:00Z",
    )

    assert handoff["candidate_id"] == "candidate-1"
    assert handoff["qualification_status"] == "not_qualified"
    assert handoff["selection_mode"] == "research_only"
    assert handoff["evidence"][0]["id"] == "e1"
    assert handoff["candidate_hash"]


def test_complete_assumptions_produce_low_base_high_scenarios():
    assumptions = {
        "affected_population": RangeAssumption(
            "affected_population", Decimal("100"), Decimal("200"), Decimal("300"),
            "customers", "analyst_assumption", "Explicit cohort estimate",
        ),
        "behavior_change_rate": RangeAssumption(
            "behavior_change_rate", Decimal("0.05"), Decimal("0.10"), Decimal("0.20"),
            "share", "analyst_assumption", "Explicit conversion estimate",
        ),
        "incremental_revenue_per_affected": RangeAssumption(
            "incremental_revenue_per_affected", Decimal("50"), Decimal("60"), Decimal("70"),
            "USD", "analyst_assumption", "Explicit unit revenue",
        ),
        "contribution_margin": RangeAssumption(
            "contribution_margin", Decimal("0.20"), Decimal("0.30"), Decimal("0.40"),
            "share", "analyst_assumption", "Explicit margin",
        ),
        "offsetting_costs": RangeAssumption(
            "offsetting_costs", Decimal("100"), Decimal("200"), Decimal("300"),
            "USD", "analyst_assumption", "Explicit cost estimate",
        ),
    }

    result = evaluate_materiality_assumptions(assumptions)

    assert result.revenue_low == Decimal("250")
    assert result.revenue_base == Decimal("1200")
    assert result.revenue_high == Decimal("4200")
    assert result.contribution_low == Decimal("-250")
    assert result.contribution_base == Decimal("160")
    assert result.contribution_high == Decimal("1580")
    assert result.missing_assumptions == ()


def test_incomplete_assumptions_do_not_produce_scenario_values():
    result = evaluate_materiality_assumptions({})

    assert result.revenue_low is None
    assert result.contribution_base is None
    assert "affected_population" in result.missing_assumptions


def test_generic_dossier_keeps_social_counts_out_of_financial_arithmetic():
    handoff = build_candidate_handoff(
        _candidate(), source_scan_id="scan-1", selection_mode="research_only",
        created_at="2026-08-29T10:00:00Z",
    )

    payload = build_generic_dossier(
        run_id="run-1",
        handoff=handoff,
        target={
            "company_name": "T-Mobile US, Inc.",
            "ticker": "TMUS",
            "exchange_code": "US",
        },
        entities=[{"lei": "TEST", "legal_name": "T-Mobile US, Inc."}],
        instruments=[{"ticker": "TMUS", "security_type": "Common Stock"}],
        sources=[],
        reported_facts=[],
        filing_passages=[],
        transcript={"status": "unavailable", "passages": [], "findings": []},
        news_checks=[],
        assumptions={},
        created_at="2026-08-29T10:05:00Z",
        limitations=["SEC filing source unavailable"],
    )

    assert payload["status"] == "research_only"
    assert payload["direction"]["company_direction"] == "uncertain"
    assert payload["materiality"]["status"] == "not_estimable"
    assert payload["materiality"]["scenario"]["revenue_base"] is None
    assert "voice_count" not in payload["materiality"]
    assert payload["instrument_implementation"]["common_stock_eligible"] is True
    assert payload["instrument_implementation"]["options_required"] is False
