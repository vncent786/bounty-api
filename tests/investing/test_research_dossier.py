from decimal import Decimal

import pytest

from social_scraper.investing.research_dossier import (
    BridgeRangeTerm,
    BridgeTerm,
    ReportedFact,
    assess_materiality,
    evaluate_bridge,
    evaluate_scenario_bridge,
    reported_ratio,
)


def _fact(
    metric: str,
    value: str,
    *,
    issuer: str = "lei:costco",
    period_end: str = "2025-08-31",
    currency: str = "USD",
    unit: str = "currency",
    scope_type: str = "consolidated",
) -> ReportedFact:
    return ReportedFact(
        fact_id=f"fact:{metric}",
        issuer_id=issuer,
        metric=metric,
        value=Decimal(value),
        unit=unit,
        currency=currency,
        period_start="2024-09-02",
        period_end=period_end,
        scope_type=scope_type,
        scope_name=None,
        source_id="sec:cost-20250831",
        source_locator="Membership Fees",
        exact_source_text=f"{metric} {value}",
        verification_status="verified_primary",
    )


def test_reported_ratio_requires_comparable_verified_facts():
    numerator = _fact("membership_fees", "5323", scope_type="consolidated_line_item")
    denominator = _fact("total_revenue", "275235")

    result = reported_ratio(numerator, denominator)

    assert result.value == Decimal("0.01933983686667756644322124730")
    assert result.numerator_fact_id == numerator.fact_id
    assert result.denominator_fact_id == denominator.fact_id
    assert result.formula == "membership_fees / total_revenue"


@pytest.mark.parametrize(
    "override",
    [
        {"issuer": "lei:other"},
        {"period_end": "2024-09-01"},
        {"currency": "EUR"},
        {"unit": "members"},
    ],
)
def test_reported_ratio_rejects_scope_period_currency_or_unit_mismatch(override):
    numerator = _fact("membership_fees", "5323", scope_type="consolidated_line_item")
    denominator = _fact("total_revenue", "275235", **override)

    with pytest.raises(ValueError, match="comparable"):
        reported_ratio(numerator, denominator)


def test_reported_ratio_rejects_segment_to_consolidated_without_scope_bridge():
    numerator = _fact(
        "us_segment_revenue",
        "100",
        scope_type="segment",
    )
    denominator = _fact("worldwide_revenue", "1000", scope_type="consolidated")

    with pytest.raises(ValueError, match="comparable"):
        reported_ratio(numerator, denominator)


def test_materiality_is_not_estimable_without_an_affected_numerator():
    assessment = assess_materiality(
        mechanism="Executive upgrades may change fee income, spending and reward cost.",
        numerator=None,
        denominator=_fact("total_revenue", "275235"),
        missing_reason_codes=(
            "executive_fee_revenue_not_disclosed",
            "incremental_spend_not_disclosed",
        ),
    )

    assert assessment.status == "not_estimable"
    assert assessment.computed_value is None
    assert assessment.missing_reason_codes == (
        "executive_fee_revenue_not_disclosed",
        "incremental_spend_not_disclosed",
    )


def test_assumption_bridge_refuses_to_hide_missing_terms():
    terms = (
        BridgeTerm("incremental_upgrade_fee", 1, Decimal("65"), "reported"),
        BridgeTerm("incremental_merchandise_contribution", 1, None, "assumption"),
        BridgeTerm("reward_cost", -1, None, "not_disclosed"),
        BridgeTerm("benefit_cost", -1, None, "not_disclosed"),
    )

    result = evaluate_bridge(terms)

    assert result.total is None
    assert result.missing_terms == (
        "incremental_merchandise_contribution",
        "reward_cost",
        "benefit_cost",
    )
    assert result.assumption_terms == ("incremental_merchandise_contribution",)


def test_assumption_bridge_calculates_only_when_every_term_is_explicit():
    terms = (
        BridgeTerm("incremental_upgrade_fee", 1, Decimal("65"), "reported"),
        BridgeTerm("incremental_merchandise_contribution", 1, Decimal("40"), "assumption"),
        BridgeTerm("reward_cost", -1, Decimal("50"), "assumption"),
        BridgeTerm("benefit_cost", -1, Decimal("5"), "assumption"),
    )

    result = evaluate_bridge(terms)

    assert result.total == Decimal("50")
    assert result.missing_terms == ()
    assert result.assumption_terms == (
        "incremental_merchandise_contribution",
        "reward_cost",
        "benefit_cost",
    )


def test_scenario_bridge_treats_high_cost_as_the_low_case():
    result = evaluate_scenario_bridge((
        BridgeRangeTerm(
            "incremental_fee", 1,
            Decimal("65"), Decimal("65"), Decimal("65"), "reported",
        ),
        BridgeRangeTerm(
            "incremental_contribution", 1,
            Decimal("10"), Decimal("30"), Decimal("50"), "analyst_assumption",
        ),
        BridgeRangeTerm(
            "reward_cost", -1,
            Decimal("20"), Decimal("35"), Decimal("50"), "analyst_assumption",
        ),
    ))

    assert result.low_total == Decimal("25")
    assert result.base_total == Decimal("60")
    assert result.high_total == Decimal("95")
    assert result.missing_terms == ()


def test_scenario_bridge_returns_no_range_when_any_required_assumption_is_missing():
    result = evaluate_scenario_bridge((
        BridgeRangeTerm(
            "incremental_fee", 1,
            Decimal("65"), Decimal("65"), Decimal("65"), "reported",
        ),
        BridgeRangeTerm(
            "reward_cost", -1, None, None, None, "analyst_assumption",
        ),
    ))

    assert result.low_total is None
    assert result.base_total is None
    assert result.high_total is None
    assert result.missing_terms == ("reward_cost",)
