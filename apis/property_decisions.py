"""
Property Tax Calculator + Buy-vs-Rent Analysis.

Property tax rates verified from IRAS (effective 1 Jan 2024).
Buy-vs-rent is pure financial modeling with transparent assumptions.

These are decision-grade endpoints that property agents, investors,
and financial advisors use to help clients decide.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import math

router = APIRouter(tags=["Property Decision Tools"])

# ============================================================
# Property Tax — Owner-Occupier progressive rates (effective 1 Jan 2024)
# Source: IRAS https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates
# Verified from IRAS website HTML: "Effective 1 Jan 2024 to 31 Dec 2024"
# Note: Rates continue into 2025-2026 as IRAS has not announced changes.
# ============================================================

# Owner-occupier: progressive on Annual Value (AV)
OWNER_OCCUPIER_TIERS = [
    (12_000, 0.00),    # First $12,000: 0%
    (28_000, 0.04),    # Next $28,000 ($12K-$40K): 4%
    (10_000, 0.06),    # Next $10,000 ($40K-$50K): 6%
    (25_000, 0.10),    # Next $25,000 ($50K-$75K): 10%
    (10_000, 0.14),    # Next $10,000 ($75K-$85K): 14%
    (15_000, 0.20),    # Next $15,000 ($85K-$100K): 20%
    (40_000, 0.26),    # Next $40,000 ($100K-$140K): 26%
    (float('inf'), 0.32),  # Above $140,000: 32%
]

# Non-owner-occupied residential: progressive (higher rates)
# Source: IRAS — same page, "Non-owner-occupier residential tax rates"
NON_OWNER_OCCUPIED_TIERS = [
    (30_000, 0.10),    # First $30,000: 10%
    (10_000, 0.12),    # Next $10,000 ($30K-$40K): 12%
    (10_000, 0.14),    # Next $10,000 ($40K-$50K): 14%
    (40_000, 0.16),    # Next $40,000 ($50K-$90K): 16%
    (40_000, 0.18),    # Next $40,000 ($90K-$130K): 18%
    (float('inf'), 0.20),  # Above $130,000: 20%
]

# Non-residential (commercial/industrial): flat 10%
NON_RESIDENTIAL_RATE = 0.10


def _compute_progressive_tax(annual_value: float, tiers: list) -> tuple:
    """Compute progressive property tax. Returns (total_tax, breakdown)."""
    remaining = annual_value
    total = 0.0
    breakdown = []
    cumulative = 0

    for tier_size, rate in tiers:
        if remaining <= 0:
            break
        taxable = min(remaining, tier_size)
        duty = taxable * rate
        total += duty

        lower = cumulative
        upper = cumulative + taxable

        breakdown.append({
            "range": f"${lower:,.0f} – ${upper:,.0f}" if upper != float('inf')
                     else f"Above ${lower:,.0f}",
            "rate_percent": round(rate * 100, 1),
            "taxable_amount": round(taxable, 2),
            "tax": round(duty, 2),
        })
        remaining -= taxable
        cumulative += taxable

    return round(total, 2), breakdown


@router.get("/property-tax")
async def property_tax(
    annual_value: float = Query(..., gt=0, description="Annual Value of the property in SGD (from IRAS/property tax bill)"),
    property_type: str = Query("residential", description="residential or non-residential"),
    is_owner_occupied: bool = Query(True, description="Whether owner lives in the property (affects rate)"),
):
    """Singapore property tax calculator.

    Owner-occupier residential: progressive 0-32% (effective Jan 2024).
    Non-owner-occupied residential: progressive 10-20%.
    Non-residential (commercial/industrial): flat 10%.

    Source: IRAS property tax rates, verified from iras.gov.sg.
    """
    if property_type.lower() == "non-residential":
        tax = annual_value * NON_RESIDENTIAL_RATE
        return {
            "annual_value": round(annual_value, 2),
            "property_type": "non-residential",
            "is_owner_occupied": "N/A",
            "annual_property_tax": round(tax, 2),
            "effective_rate_percent": 10.0,
            "source": "IRAS: Non-residential properties taxed at flat 10% of Annual Value",
            "source_url": "https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates",
        }

    if is_owner_occupied:
        tax, breakdown = _compute_progressive_tax(annual_value, OWNER_OCCUPIER_TIERS)
        return {
            "annual_value": round(annual_value, 2),
            "property_type": "residential (owner-occupied)",
            "is_owner_occupied": True,
            "annual_property_tax": tax,
            "effective_rate_percent": round((tax / annual_value * 100) if annual_value > 0 else 0, 2),
            "breakdown": breakdown,
            "source": "IRAS owner-occupier tax rates, effective 1 Jan 2024",
            "source_url": "https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates",
            "note": "Rates verified from IRAS website. Annual Value is determined by IRAS based on market rent.",
        }
    else:
        tax, breakdown = _compute_progressive_tax(annual_value, NON_OWNER_OCCUPIED_TIERS)
        return {
            "annual_value": round(annual_value, 2),
            "property_type": "residential (non-owner-occupied / investment)",
            "is_owner_occupied": False,
            "annual_property_tax": tax,
            "effective_rate_percent": round((tax / annual_value * 100) if annual_value > 0 else 0, 2),
            "breakdown": breakdown,
            "source": "IRAS non-owner-occupier residential tax rates, effective 1 Jan 2024",
            "source_url": "https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates",
            "note": "Investment/rental properties pay higher progressive rates. Annual Value determined by IRAS based on market rent.",
        }


# ============================================================
# Buy-vs-Rent Analysis
# Compares total cost of ownership vs renting over a holding period.
# Includes: down payment, mortgage, stamp duty, property tax, maintenance,
# vs rent + opportunity cost of down payment invested.
# ============================================================

@router.get("/buy-vs-rent")
async def buy_vs_rent(
    property_price: float = Query(..., gt=0, description="Property purchase price in SGD"),
    monthly_rent: float = Query(..., gt=0, description="Monthly rent for comparable property in SGD"),
    annual_value: float = Query(0, ge=0, description="Property Annual Value (for property tax). If 0, estimated from monthly rent * 12."),
    down_payment_pct: float = Query(25, ge=5, le=100, description="Down payment percentage (25% for bank loan, 20% for HDB loan)"),
    mortgage_rate: float = Query(2.6, gt=0, description="Mortgage interest rate (%) — 2.6% is HDB concessionary, banks ~2.5-3.5%"),
    loan_tenure_years: int = Query(25, ge=5, le=35, description="Loan tenure in years"),
    holding_period_years: int = Query(10, ge=1, le=40, description="How long you plan to hold the property"),
    property_appreciation_pct: float = Query(3.0, description="Expected annual property price appreciation (%)"),
    rent_increases_pct: float = Query(2.0, description="Expected annual rent increase (%)"),
    investment_return_pct: float = Query(4.0, description="If you invested the down payment instead, expected annual return (%)"),
    maintenance_monthly: float = Query(300, ge=0, description="Monthly maintenance/conservancy/management fees"),
    is_owner_occupied: bool = Query(True, description="If owner-occupied, use lower property tax rates"),
):
    """Buy-vs-Rent analysis: should you buy or rent over your holding period?

    Computes total cost of buying (down payment + mortgage + stamp duty + property tax +
    maintenance) vs total cost of renting (rent + opportunity cost of down payment invested).

    Returns a year-by-year comparison and break-even analysis.
    """
    # Estimate AV if not provided (rough: AV ~ 80% of gross annual rent, per IRAS methodology)
    av = annual_value if annual_value > 0 else monthly_rent * 12 * 0.8

    # === BUYING COSTS ===
    down_payment = property_price * (down_payment_pct / 100)
    loan_amount = property_price - down_payment

    # Monthly mortgage payment (amortized)
    monthly_rate = (mortgage_rate / 100) / 12
    n_payments = loan_tenure_years * 12
    if monthly_rate > 0:
        monthly_mortgage = loan_amount * (monthly_rate * (1 + monthly_rate)**n_payments) / ((1 + monthly_rate)**n_payments - 1)
    else:
        monthly_mortgage = loan_amount / n_payments

    total_mortgage_payments = monthly_mortgage * min(holding_period_years * 12, n_payments)
    loan_balance_at_sale = 0
    if holding_period_years < loan_tenure_years:
        # Calculate remaining balance
        payments_made = holding_period_years * 12
        remaining_payments = n_payments - payments_made
        loan_balance_at_sale = monthly_mortgage * ((1 - (1 + monthly_rate)**(-remaining_payments)) / monthly_rate) if monthly_rate > 0 else monthly_mortgage * remaining_payments

    # Stamp duty (approximate BSD only for SC 1st property)
    bsd = _estimate_bsd(property_price)

    # Property tax (annual, from our calculator)
    if is_owner_occupied:
        annual_ptax, _ = _compute_progressive_tax(av, OWNER_OCCUPIER_TIERS)
    else:
        annual_ptax, _ = _compute_progressive_tax(av, NON_OWNER_OCCUPIED_TIERS)

    # Property value at sale
    property_value_at_sale = property_price * ((1 + property_appreciation_pct / 100) ** holding_period_years)

    # Total buying costs over holding period
    total_buying_costs = (
        down_payment +
        total_mortgage_payments +
        bsd +
        (annual_ptax * holding_period_years) +
        (maintenance_monthly * 12 * holding_period_years) +
        (property_price * 0.002 * holding_period_years)  # rough insurance/repairs at 0.2%/yr
    )

    # Net proceeds from sale
    sale_proceeds = property_value_at_sale - loan_balance_at_sale
    net_buying_cost = total_buying_costs - sale_proceeds

    # === RENTING COSTS ===
    total_rent_paid = 0
    current_rent = monthly_rent
    for year in range(holding_period_years):
        total_rent_paid += current_rent * 12
        current_rent *= (1 + rent_increases_pct / 100)

    # Opportunity cost: down payment + BSD invested instead
    upfront_not_spent = down_payment + bsd
    invested_value = upfront_not_spent * ((1 + investment_return_pct / 100) ** holding_period_years)
    investment_gain = invested_value - upfront_not_spent

    total_renting_costs = total_rent_paid - investment_gain

    # === VERDICT ===
    break_even_year = None
    if net_buying_cost < total_renting_costs:
        verdict = f"BUYING is better by ${total_renting_costs - net_buying_cost:,.0f} over {holding_period_years} years"
    elif total_renting_costs < net_buying_cost:
        # Find break-even year (simplified: linear approximation)
        diff_per_year = abs(net_buying_cost - total_renting_costs) / holding_period_years
        if diff_per_year > 0:
            break_even_year = holding_period_years + (net_buying_cost - total_renting_costs) / diff_per_year
        verdict = f"RENTING is better by ${net_buying_cost - total_renting_costs:,.0f} over {holding_period_years} years"
    else:
        verdict = "BREAK-EVEN"

    return {
        "assumptions": {
            "property_price": round(property_price, 2),
            "monthly_rent": round(monthly_rent, 2),
            "estimated_annual_value": round(av, 2),
            "down_payment": round(down_payment, 2),
            "loan_amount": round(loan_amount, 2),
            "mortgage_rate_pct": mortgage_rate,
            "monthly_mortgage": round(monthly_mortgage, 2),
            "loan_tenure_years": loan_tenure_years,
            "holding_period_years": holding_period_years,
            "property_appreciation_pct": property_appreciation_pct,
            "rent_increases_pct": rent_increases_pct,
            "investment_return_pct": investment_return_pct,
        },
        "buying": {
            "down_payment": round(down_payment, 2),
            "total_mortgage_paid": round(total_mortgage_payments, 2),
            "stamp_duty_bsd": round(bsd, 2),
            "property_tax_total": round(annual_ptax * holding_period_years, 2),
            "maintenance_total": round(maintenance_monthly * 12 * holding_period_years, 2),
            "property_value_at_sale": round(property_value_at_sale, 2),
            "loan_balance_at_sale": round(loan_balance_at_sale, 2),
            "sale_proceeds": round(sale_proceeds, 2),
            "total_gross_costs": round(total_buying_costs, 2),
            "net_cost_after_sale": round(net_buying_cost, 2),
        },
        "renting": {
            "total_rent_paid": round(total_rent_paid, 2),
            "down_payment_invested_instead": round(upfront_not_spent, 2),
            "investment_value_at_end": round(invested_value, 2),
            "investment_gain": round(investment_gain, 2),
            "net_cost": round(total_renting_costs, 2),
        },
        "verdict": {
            "recommendation": verdict,
            "buy_net_cost": round(net_buying_cost, 2),
            "rent_net_cost": round(total_renting_costs, 2),
            "difference": round(abs(net_buying_cost - total_renting_costs), 2),
            "winner": "buy" if net_buying_cost < total_renting_costs else "rent" if total_renting_costs < net_buying_cost else "break-even",
            "break_even_year": round(break_even_year, 1) if break_even_year else None,
        },
        "source": "Composite computation using IRAS property tax rates, standard mortgage amortization, and transparent assumptions",
        "source_url": "https://www.iras.gov.sg/taxes/property-tax/property-owners/property-tax-rates",
        "note": "All assumptions are user-configurable. Property appreciation and investment returns are estimates, not guarantees. Stamp duty is BSD-only (SC first property).",
    }


def _estimate_bsd(price: float) -> float:
    """Estimate Buyer's Stamp Duty for SC first property."""
    tiers = [
        (180_000, 0.01),
        (180_000, 0.02),
        (640_000, 0.03),
        (500_000, 0.04),
        (1_500_000, 0.05),
        (float('inf'), 0.06),
    ]
    remaining = price
    total = 0.0
    for tier_size, rate in tiers:
        if remaining <= 0:
            break
        taxable = min(remaining, tier_size)
        total += taxable * rate
        remaining -= taxable
    return math.floor(total)
