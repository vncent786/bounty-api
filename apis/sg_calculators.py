"""
Singapore Financial Calculators — Income Tax, GST, CPF, Agent Commission.

All rates sourced from IRAS and CPF Board publications.
Every response carries its source for provenance.

Free endpoints — drive MCP installs and agent adoption.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import math

router = APIRouter(tags=["SG Financial Calculators"])

# ============================================================
# Income Tax — Resident progressive rates (YA 2024 onwards)
# Source: IRAS "Individual Income Tax Rates"
# https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/tax-residency-and-tax-rates
# Verified: rates unchanged from YA 2024
# ============================================================

INCOME_TAX_TIERS_RESIDENT = [
    # (upper_bound, rate)
    (20_000, 0.00),      # First $20,000: $0
    (10_000, 0.02),      # Next $10,000 ($20,001-$30,000): 2%
    (10_000, 0.035),     # Next $10,000 ($30,001-$40,000): 3.5%
    (40_000, 0.07),      # Next $40,000 ($40,001-$80,000): 7%
    (40_000, 0.115),     # Next $40,000 ($80,001-$120,000): 11.5%
    (40_000, 0.15),      # Next $40,000 ($120,001-$160,000): 15%
    (40_000, 0.18),      # Next $40,000 ($160,001-$200,000): 18%
    (40_000, 0.19),      # Next $40,000 ($200,001-$240,000): 19%
    (40_000, 0.195),     # Next $40,000 ($240,001-$280,000): 19.5%
    (40_000, 0.20),      # Next $40,000 ($280,001-$320,000): 20%
    (float('inf'), 0.22), # Above $320,000: 22%
]

# Non-resident rates (flat, employment income only)
# Source: IRAS — non-residents taxed at 15% or progressive resident rate, whichever is higher
NON_RESIDENT_EMPLOYMENT_RATE = 0.15


def _compute_marginal_tax(taxable_income: float, tiers: list) -> tuple:
    """Compute marginal/progressive tax. Returns (total_tax, breakdown)."""
    remaining = taxable_income
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
            "rate_percent": round(rate * 100, 2),
            "taxable_amount": round(taxable, 2),
            "tax": round(duty, 2),
        })
        remaining -= taxable
        cumulative += taxable

    return round(total, 2), breakdown


class IncomeTaxRequest(BaseModel):
    annual_income: float = Field(..., gt=0, description="Gross annual income in SGD")
    deductions: float = Field(default=0, ge=0, description="Total deductions (CPF, expenses, donations, etc.)")
    reliefs: float = Field(default=0, ge=0, description="Total personal reliefs (earned income, spouse, child, etc.)")
    is_resident: bool = Field(default=True, description="Tax residency status (true = resident, false = non-resident)")
    employment_income_only: bool = Field(default=True, description="Whether income is solely from employment (affects non-resident rate)")


@router.get("/tax/income")
async def income_tax_get(
    annual_income: float = Query(..., gt=0, description="Gross annual income in SGD"),
    deductions: float = Query(0, ge=0, description="Total deductions"),
    reliefs: float = Query(0, ge=0, description="Total personal reliefs"),
    is_resident: bool = Query(True, description="Tax residency"),
):
    """Singapore individual income tax calculator. Resident progressive rates (YA 2024+)."""
    return _compute_income_tax(annual_income, deductions, reliefs, is_resident)


@router.post("/tax/income")
async def income_tax_post(req: IncomeTaxRequest):
    """Singapore individual income tax calculator (POST body)."""
    return _compute_income_tax(req.annual_income, req.deductions, req.reliefs, req.is_resident)


def _compute_income_tax(annual_income: float, deductions: float, reliefs: float, is_resident: bool) -> dict:
    taxable_income = max(0, annual_income - deductions - reliefs)

    if not is_resident:
        # Non-residents: 15% flat on employment income, or progressive, whichever is higher
        flat_tax = taxable_income * NON_RESIDENT_EMPLOYMENT_RATE
        progressive_tax, _ = _compute_marginal_tax(taxable_income, INCOME_TAX_TIERS_RESIDENT)
        total_tax = max(flat_tax, progressive_tax)
        return {
            "annual_income": round(annual_income, 2),
            "deductions": round(deductions, 2),
            "reliefs": round(reliefs, 2),
            "taxable_income": round(taxable_income, 2),
            "is_resident": False,
            "tax_payable": round(total_tax, 2),
            "effective_rate_percent": round((total_tax / annual_income * 100) if annual_income > 0 else 0, 2),
            "method": f"Non-resident: 15% flat or progressive, whichever higher ({ '15% flat applied' if flat_tax >= progressive_tax else 'progressive applied' })",
            "source": "IRAS non-resident employment income rate (15%)",
            "calculation_date": datetime.now().strftime("%Y-%m-%d"),
        }

    total_tax, breakdown = _compute_marginal_tax(taxable_income, INCOME_TAX_TIERS_RESIDENT)

    return {
        "annual_income": round(annual_income, 2),
        "deductions": round(deductions, 2),
        "reliefs": round(reliefs, 2),
        "taxable_income": round(taxable_income, 2),
        "is_resident": True,
        "tax_payable": round(total_tax, 2),
        "effective_rate_percent": round((total_tax / annual_income * 100) if annual_income > 0 else 0, 2),
        "marginal_rate_percent": next(
            (round(b["rate_percent"], 1) for b in reversed(breakdown) if b["tax"] > 0),
            0
        ),
        "breakdown": breakdown,
        "source": "IRAS individual income tax rates, YA 2024 onwards (verified)",
        "source_url": "https://www.iras.gov.sg/taxes/individual-income-tax/basics-of-individual-income-tax/tax-residency-and-tax-rates",
        "calculation_date": datetime.now().strftime("%Y-%m-%d"),
        "note": "Excludes tax rebates (e.g., GSTV, Parenthood rebate). Actual payable may be lower.",
    }


# ============================================================
# GST Calculator
# Source: IRAS "Current GST Rates"
# GST increased from 8% to 9% on 1 Jan 2024
# ============================================================

GST_RATE = 0.09  # 9% from 1 Jan 2024


@router.get("/gst")
async def gst_calculator(
    amount: float = Query(..., description="Amount in SGD (inclusive or exclusive of GST)"),
    mode: str = Query("add", description="'add' (add GST to price) or 'remove' (extract GST from GST-inclusive price)"),
):
    """Singapore GST calculator. Current rate: 9% (effective 1 Jan 2024)."""
    if mode == "remove":
        # Amount includes GST; extract the GST component
        net = amount / (1 + GST_RATE)
        gst = amount - net
        return {
            "gross_amount": round(amount, 2),
            "net_amount": round(net, 2),
            "gst_amount": round(gst, 2),
            "gst_rate_percent": 9.0,
            "mode": "remove (GST-inclusive input)",
            "source": "IRAS GST rate: 9% from 1 Jan 2024",
        }
    else:
        gst = amount * GST_RATE
        return {
            "net_amount": round(amount, 2),
            "gst_amount": round(gst, 2),
            "gross_amount": round(amount + gst, 2),
            "gst_rate_percent": 9.0,
            "mode": "add (GST-exclusive input)",
            "source": "IRAS GST rate: 9% from 1 Jan 2024",
        }


# ============================================================
# Property Agent Commission Calculator
# Source: CEA (Council for Estate Agencies) guidelines + market norms
# Note: Commission rates are negotiable, not legally fixed.
# These are prevailing market rates.
# ============================================================

@router.get("/commission")
async def commission_calculator(
    transaction_type: str = Query(..., description="sale, rental, or sublet"),
    property_type: str = Query("hdb", description="hdb, private, or landed"),
    price: float = Query(..., gt=0, description="Sale price or monthly rent (SGD)"),
    is_seller_landlord: bool = Query(True, description="true = seller/landlord side, false = buyer/tenant side"),
):
    """Singapore property agent commission estimator. Rates are market norms, not legally fixed.

    Sale commissions:
    - HDB: seller pays 2% (incl. GST), buyer typically $0
    - Private: seller pays 2%, buyer pays 1% (both incl. GST)
    - Landed: seller pays 2-4%, negotiable

    Rental commissions:
    - <24 months: tenant pays 0.5 month, landlord pays 0.5-1 month
    - >=24 months: tenant pays 1 month, landlord pays 1 month
    """
    transaction_type = transaction_type.lower()
    property_type = property_type.lower()

    if transaction_type == "sale":
        if property_type == "hdb":
            if is_seller_landlord:
                rate = 0.02
                commission = price * rate
                party = "Seller"
            else:
                rate = 0.0
                commission = 0
                party = "Buyer (typically no commission for HDB)"
        elif property_type == "landed":
            rate = 0.03  # mid-range of 2-4%
            commission = price * rate
            party = "Seller" if is_seller_landlord else "Buyer"
        else:  # private condo
            rate = 0.02 if is_seller_landlord else 0.01
            commission = price * rate
            party = "Seller" if is_seller_landlord else "Buyer"

        return {
            "transaction_type": "sale",
            "property_type": property_type,
            "price": round(price, 2),
            "party": party,
            "commission_rate_percent": round(rate * 100, 2),
            "commission_amount": round(commission, 2),
            "includes_gst": True,
            "source": "CEA guidelines + prevailing market rates (negotiable, not legally fixed)",
            "source_url": "https://www.cea.gov.sg",
            "note": "Commission rates are negotiable. HDB buyer typically pays no commission. Private property buyer typically pays 1%.",
        }

    elif transaction_type in ("rental", "sublet"):
        # Monthly rent provided; commission based on monthly rent
        monthly_rent = price
        # Standard: tenant pays 0.5 month for <2yr lease, landlord pays 0.5-1 month
        # Simplified: both sides pay 0.5 month (common for standard 1-2yr lease)
        tenant_months = 0.5
        landlord_months = 0.5

        if is_seller_landlord:
            commission = monthly_rent * landlord_months
            party = f"Landlord ({landlord_months} month's rent)"
        else:
            commission = monthly_rent * tenant_months
            party = f"Tenant ({tenant_months} month's rent)"

        return {
            "transaction_type": transaction_type,
            "monthly_rent": round(monthly_rent, 2),
            "party": party,
            "commission_amount": round(commission, 2),
            "includes_gst": True,
            "source": "CEA guidelines + prevailing market rates (negotiable, not legally fixed)",
            "note": "Standard for 1-2 year leases. For 2+ year leases, commission may increase to 1 month. Rates are negotiable.",
        }

    else:
        return {"error": "transaction_type must be 'sale', 'rental', or 'sublet'"}


# ============================================================
# CPF Housing Calculator
# Source: CPF Board housing schemes
# Ordinary Account (OA) can be used for housing.
# SA and MA cannot be used for housing.
# ============================================================

@router.get("/cpf/housing")
async def cpf_housing_eligibility(
    monthly_income: float = Query(..., gt=0, description="Gross monthly income in SGD"),
    age: int = Query(..., ge=16, le=65, description="Current age (affects CPF contribution rate)"),
    existing_oa_balance: float = Query(0, ge=0, description="Existing CPF OA balance"),
):
    """Estimate CPF Ordinary Account accumulation for housing use.

    CPF contribution rates (total = employer + employee) by age, for wages <= $6,000/month:
    - Age <=35: 37% (17% employer + 20% employee). OA allocation: 23%
    - Age 36-45: 37%. OA allocation: 21%
    - Age 46-50: 37%. OA allocation: 19%
    - Age 51-55: 37%. OA allocation: 15%
    - Age 55-60: 26% (13+13). OA allocation: 12%
    - Age 61-65: 16.5% (9+7.5). OA allocation: 10.5%

    OA monthly allocation goes toward housing.
    """
    # Determine OA allocation rate by age
    if age <= 35:
        oa_rate = 0.23
        total_rate = 0.37
        age_band = "35 and below"
    elif age <= 45:
        oa_rate = 0.21
        total_rate = 0.37
        age_band = "36-45"
    elif age <= 50:
        oa_rate = 0.19
        total_rate = 0.37
        age_band = "46-50"
    elif age <= 55:
        oa_rate = 0.15
        total_rate = 0.37
        age_band = "51-55"
    elif age <= 60:
        oa_rate = 0.12
        total_rate = 0.26
        age_band = "56-60"
    else:
        oa_rate = 0.105
        total_rate = 0.165
        age_band = "61-65"

    # CPF Ordinary Wage ceiling: $6,000/month (2024). Increases to $6,300 in Sep 2024, $6,800 in 2025, $7,400 in 2026
    # Using $6,000 as base (conservative for 2024 calculation)
    cpf_ordinary_wage_ceiling = 6800  # Sep 2024 rate
    cpf_capped_income = min(monthly_income, cpf_ordinary_wage_ceiling)

    monthly_oa_contribution = cpf_capped_income * oa_rate
    annual_oa_contribution = monthly_oa_contribution * 12

    # Project OA balance over time (simple linear, no interest)
    # CPF OA interest rate: 2.5% (current, reviewed quarterly)
    cpf_oa_rate = 0.025
    projected_3yr = existing_oa_balance * (1 + cpf_oa_rate)**3 + annual_oa_contribution * (((1 + cpf_oa_rate)**3 - 1) / cpf_oa_rate)
    projected_5yr = existing_oa_balance * (1 + cpf_oa_rate)**5 + annual_oa_contribution * (((1 + cpf_oa_rate)**5 - 1) / cpf_oa_rate)

    # Housing usage limits
    # For HDB: can use all OA for down payment + monthly installments (subject to HDB rules)
    # For private: can use OA for down payment (up to 120% of valuation) + monthly installments
    available_now = existing_oa_balance
    available_3yr = projected_3yr
    available_5yr = projected_5yr

    return {
        "monthly_income": round(monthly_income, 2),
        "age": age,
        "age_band": age_band,
        "cpf_contribution_rate_total_percent": round(total_rate * 100, 1),
        "oa_allocation_rate_percent": round(oa_rate * 100, 1),
        "monthly_oa_contribution": round(monthly_oa_contribution, 2),
        "annual_oa_contribution": round(annual_oa_contribution, 2),
        "existing_oa_balance": round(existing_oa_balance, 2),
        "cpf_ordinary_wage_ceiling": cpf_ordinary_wage_ceiling,
        "cpf_oa_interest_rate_percent": 2.5,
        "projected_oa_3yr": round(projected_3yr, 2),
        "projected_oa_5yr": round(projected_5yr, 2),
        "usable_for_housing_now": round(available_now, 2),
        "usable_for_housing_3yr": round(available_3yr, 2),
        "usable_for_housing_5yr": round(available_5yr, 2),
        "source": "CPF Board contribution rates (Jan 2024), OA interest rate (2.5%), Ordinary Wage ceiling ($6,800 from Sep 2024)",
        "source_url": "https://www.cpf.gov.sg/member/infohub/onlineservices/cpf-contribution-calculator",
        "note": "Projections assume constant income and CPF rates. OA rate may change with age bands. CPF OA interest compounded at 2.5% annually.",
    }
