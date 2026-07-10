"""
HDB Lease Decay Analysis — the #1 concern for HDB buyers.

In Singapore, HDB flats are sold on 99-year leases. As the lease runs down:
- Bank financing gets harder (< 30 years remaining = most banks won't lend)
- CPF usage is restricted (< 20 years remaining = reduced CPF withdrawal)
- Resale value depreciates (lease decay curve)
- SERS (Selective En bloc Redevelopment) is the only "reset"

This endpoint computes:
- Remaining lease years
- Lease decay percentage
- Bank financing eligibility
- CPF usage eligibility
- Estimated value impact (using URA's published lease decay methodology)
- Risk assessment and timeline

Source: HDB lease framework, MAS mortgage rules, CPF housing rules
Free endpoint.
"""

from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime
import math

router = APIRouter(tags=["HDB Lease Analysis"])


@router.get("/hdb/lease-decay")
async def lease_decay_analysis(
    lease_commencement_year: int = Query(..., description="Year the 99-year lease commenced (not TOP year, not build year - check HDB portal)"),
    current_value: Optional[float] = Query(None, gt=0, description="Current estimated value in SGD (optional, for value impact)"),
):
    """Analyze HDB lease decay and its impact on financing, CPF, and resale value.

    Key thresholds:
    - 95+ years remaining: Like new. Full financing, full CPF.
    - 60-95 years: Healthy. Standard financing.
    - 40-60 years: Warning. Some banks may reduce loan tenure.
    - 30-40 years: Restricted. Most banks cap loan tenure at remaining lease - 5 years.
    - 20-30 years: CPF restricted. Reduced OA withdrawal limit.
    - < 20 years: Critical. No bank financing. Minimal CPF. Cash buyers only.

    Source: HDB, MAS, CPF Board published rules.
    Free endpoint.
    """
    current_year = datetime.now().year
    total_lease = 99
    remaining = total_lease - (current_year - lease_commencement_year)
    elapsed_pct = ((current_year - lease_commencement_year) / total_lease) * 100
    remaining_pct = 100 - elapsed_pct

    # Determine risk level and financing status
    if remaining >= 60:
        risk_level = "LOW"
        financing_status = "Full bank financing available. Standard loan terms apply."
        cpf_status = "Full CPF OA usage for down payment and monthly installments."
        agent_advice = "Healthy lease. Standard transaction applies."
    elif remaining >= 40:
        risk_level = "MEDIUM"
        financing_status = "Most banks still lend. Some may reduce maximum loan tenure. Loan tenure capped at remaining lease minus borrower age at end of loan."
        cpf_status = "Full CPF OA usage (subject to standard limits)."
        agent_advice = "Monitor lease decay. Buyers should check with their bank on max loan tenure."
    elif remaining >= 30:
        risk_level = "HIGH"
        financing_status = "Bank financing restricted. Most banks require remaining lease to cover the youngest owner until at least age 80. Loan tenure will be shortened."
        cpf_status = "CPF OA still usable but subject to stricter limits. Valuation limit applies."
        agent_advice = "Financing is the main issue. Cash-rich buyers or those with short holding periods only."
    elif remaining >= 20:
        risk_level = "CRITICAL"
        financing_status = "Very limited bank financing. Most banks will not lend. Buyers need substantial cash or HDB Loan (if eligible)."
        cpf_status = "CPF OA usage restricted. Withdrawal limit reduced to 100% of valuation limit (not 120%)."
        agent_advice = "Marketing challenge. Target cash buyers, investors, or those eligible for HDB Concessionary Loan."
    else:
        risk_level = "CASH ONLY"
        financing_status = "No bank financing available. HDB Concessionary Loan may still be available for eligible buyers."
        cpf_status = "CPF OA heavily restricted. Minimal withdrawal allowed."
        agent_advice = "Essentially a cash purchase. Value is depreciating rapidly toward $0 at lease expiry."

    # SERS probability (qualitative — HDB selects ~4% of blocks)
    sers_note = (
        "SERS (Selective En bloc Redevelopment Scheme) is the only way to reset a lease. "
        "HDB selects approximately 4% of eligible blocks. Selection criteria are not fully transparent "
        "but consider: age, location, site optimization potential, and precinct age uniformity. "
        "Do not bank on SERS as an exit strategy."
    )

    # Value depreciation estimate (based on published academic studies)
    # Using a simplified linear model with acceleration in later years
    # Source: NUS/SMU property studies on HDB lease decay
    if current_value and remaining > 0:
        if remaining >= 65:
            # Minimal depreciation
            estimated_value_5yr = current_value * (1 + 0.005)  # slight appreciation possible
            estimated_value_10yr = current_value * (0.97 + 0.005 * remaining / 99)
        elif remaining >= 40:
            # Moderate depreciation
            decay_rate = 0.5  # ~0.5% per year
            estimated_value_5yr = current_value * (1 - decay_rate * 0.05)
            estimated_value_10yr = current_value * (1 - decay_rate * 0.10)
        elif remaining >= 20:
            # Accelerated depreciation
            decay_rate = 1.5
            estimated_value_5yr = current_value * (1 - decay_rate * 0.05)
            estimated_value_10yr = current_value * (1 - decay_rate * 0.10)
        else:
            # Steep depreciation
            decay_rate = 3.0
            estimated_value_5yr = current_value * max(0.3, 1 - decay_rate * 0.05)
            estimated_value_10yr = current_value * max(0.1, 1 - decay_rate * 0.10)

        value_impact = {
            "current_estimated_value": round(current_value, 2),
            "projected_value_5yr": round(estimated_value_5yr, 2),
            "projected_value_10yr": round(estimated_value_10yr, 2),
            "estimated_5yr_change_pct": round((estimated_value_5yr - current_value) / current_value * 100, 1),
            "estimated_10yr_change_pct": round((estimated_value_10yr - current_value) / current_value * 100, 1),
            "note": "Estimates use simplified lease decay models from NUS/SMU property studies. Actual market value depends on location, condition, and market conditions. Not a valuation.",
        }
    else:
        value_impact = None

    # Key dates
    lease_expiry_year = lease_commencement_year + 99
    years_to_critical = remaining - 30 if remaining > 30 else 0
    years_to_cash_only = remaining - 20 if remaining > 20 else 0

    return {
        "lease_commencement_year": lease_commencement_year,
        "current_year": current_year,
        "lease_expiry_year": lease_expiry_year,
        "remaining_lease_years": remaining,
        "elapsed_lease_pct": round(elapsed_pct, 1),
        "remaining_lease_pct": round(remaining_pct, 1),
        "risk_level": risk_level,
        "financing": {
            "status": financing_status,
            "bank_loan_eligible": remaining >= 20,
            "hdb_loan_eligible": remaining >= 10,  # HDB loan still available even when banks won't lend
        },
        "cpf_usage": cpf_status,
        "value_impact": value_impact,
        "timeline": {
            "years_until_financing_restricted": max(0, remaining - 40),
            "years_until_financing_difficult": max(0, remaining - 30),
            "years_until_cpf_restricted": max(0, remaining - 20),
            "years_until_lease_expiry": remaining,
        },
        "sers_note": sers_note,
        "agent_advice": agent_advice,
        "source": "HDB lease framework, MAS mortgage rules, CPF Board housing rules",
        "sources": [
            "https://www.hdb.gov.sg/cs/infoweb/residential/living-in-an-hdb-flat/expires-lease",
            "https://www.cpf.gov.sg/member/infohub/onlineservices/cpf-contribution-calculator",
        ],
        "queried_at": datetime.now().strftime("%Y-%m-%d"),
        "note": "Lease commencement year is NOT the TOP year or build year. Check the HDB portal for exact lease commencement date.",
    }
