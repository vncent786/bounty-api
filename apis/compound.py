"""
Compound Interest & Investment Growth Calculator API — FastAPI router.

Projects the future value of an initial principal (plus optional recurring
contributions) under either **simple** or **compound** interest. For compound
interest, compounding can occur monthly (the default), annually, quarterly,
daily or continuously.

This is a **pure-math** calculator with zero external data dependencies —
every figure is derived from the request inputs using standard finance
formulas. Designed for x402 micropayments ($0.002/call).

Import pattern::

    from apis.compound import router
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/invest",
    tags=["invest"],
    responses={422: {"description": "Validation error"}},
)


# ============================================================
# Constants
# ============================================================

MONTHS_PER_YEAR = 12
SOURCE = "calculated from standard compound/simple interest formulas"


# ============================================================
# Enums
# ============================================================

class InterestType(str, Enum):
    """Whether interest accrues linearly (simple) or on prior interest (compound)."""

    simple = "simple"
    compound = "compound"


class ContributionFrequency(str, Enum):
    """Cadence at which `contribution_monthly` is added to the balance."""

    monthly = "monthly"
    annual = "annual"


class CompoundingFrequency(str, Enum):
    """Compounding cadence for compound interest (ignored for simple interest)."""

    monthly = "monthly"
    annually = "annually"
    quarterly = "quarterly"
    daily = "daily"
    continuous = "continuous"


# ============================================================
# Pydantic models
# ============================================================

class InvestCalcRequest(BaseModel):
    """Request body for POST /invest/calculate."""

    principal: float = Field(
        ...,
        ge=0,
        description=(
            "Initial amount invested (any currency). 0 is allowed for "
            "contributions-only growth."
        ),
        examples=[10_000.0],
    )
    annual_rate: float = Field(
        ...,
        ge=0,
        description="Annual interest / growth rate as a percent (e.g. 7 means 7%).",
        examples=[7.0],
    )
    years: int = Field(
        ...,
        ge=1,
        description="Investment horizon in whole years.",
        examples=[20],
    )
    contribution_monthly: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Recurring contribution amount per period. Its cadence is set by "
            "`contribution_frequency`: when monthly (default) this amount is "
            "added every month; when annual it is added once per year."
        ),
        examples=[500.0],
    )
    contribution_frequency: ContributionFrequency = Field(
        default=ContributionFrequency.monthly,
        description="How often `contribution_monthly` is added: monthly or annual.",
    )
    interest_type: InterestType = Field(
        default=InterestType.compound,
        description=(
            "simple = interest accrues linearly on deposited capital only; "
            "compound = interest is earned on previously-accrued interest."
        ),
    )
    compounding_frequency: CompoundingFrequency = Field(
        default=CompoundingFrequency.monthly,
        description=(
            "Compounding cadence for compound interest. Ignored when "
            "`interest_type` is simple. Monthly is the default."
        ),
    )


class YearRow(BaseModel):
    """One row of the yearly growth table."""

    year: int = Field(..., description="Year number (1-based).")
    balance: float = Field(..., description="Balance at the end of the year.")
    contributions_so_far: float = Field(
        ...,
        description="Total money deposited up to and including this year (principal + recurring contributions).",
    )
    interest_earned: float = Field(
        ...,
        description="Cumulative interest earned as of the end of this year (= balance − contributions_so_far).",
    )


class InvestCalcResult(BaseModel):
    """Response model for the investment-growth calculation."""

    # Inputs echoed back
    principal: float
    annual_rate: float
    years: int
    contribution_monthly: float
    contribution_frequency: ContributionFrequency
    interest_type: InterestType
    compounding_frequency: CompoundingFrequency

    # Core results
    final_balance: float
    total_contributed: float = Field(
        ..., description="All money deposited: principal + every recurring contribution."
    )
    total_interest_earned: float = Field(
        ..., description="final_balance − total_contributed."
    )
    multiplier: Optional[float] = Field(
        None,
        description="final_balance / principal. None when principal is 0.",
    )

    # Table
    yearly_growth: List[YearRow]

    # Provenance
    source: str
    calculation_date: str


# ============================================================
# Core calculation logic
# ============================================================

def _monthly_growth_factor(annual_rate_percent: float, freq: CompoundingFrequency) -> float:
    """
    Return the per-month balance multiplier for compound interest given a
    nominal annual rate (in percent) and a compounding frequency.

    Each cadence is converted to an equivalent monthly factor so a single
    monthly loop reproduces the exact result of compounding at the native
    cadence::

        discrete  (n periods/yr):  (1 + r/n) ** (n / 12)
        continuous              :  e ** (r / 12)
    """
    r = annual_rate_percent / 100.0
    if freq == CompoundingFrequency.continuous:
        return math.exp(r / MONTHS_PER_YEAR)
    periods_per_year = {
        CompoundingFrequency.monthly: 12,
        CompoundingFrequency.quarterly: 4,
        CompoundingFrequency.annually: 1,
        CompoundingFrequency.daily: 365,
    }[freq]
    return (1.0 + r / periods_per_year) ** (periods_per_year / MONTHS_PER_YEAR)


def calculate_investment_growth(
    principal: float,
    annual_rate: float,
    years: int,
    contribution_monthly: float = 0.0,
    contribution_frequency: ContributionFrequency = ContributionFrequency.monthly,
    interest_type: InterestType = InterestType.compound,
    compounding_frequency: CompoundingFrequency = CompoundingFrequency.monthly,
) -> dict:
    """
    Pure function: project investment growth under simple or compound interest.

    The simulation runs month by month for ``years * 12`` months. Contributions
    are applied at the **end** of each period (ordinary-annuity convention), so
    a contribution does not earn interest in the period it is deposited.

    Returns a plain dict that maps 1:1 onto ``InvestCalcResult``. Kept separate
    from the endpoint so it is unit-testable in isolation.
    """
    total_months = years * MONTHS_PER_YEAR
    r_monthly = (annual_rate / 100.0) / MONTHS_PER_YEAR  # simple-interest monthly rate

    balance = principal
    total_deposited = principal  # all money put in (principal + contributions)

    yearly: List[dict] = []

    is_compound = interest_type == InterestType.compound
    growth_factor = (
        _monthly_growth_factor(annual_rate, compounding_frequency)
        if is_compound
        else None
    )

    for month in range(1, total_months + 1):
        # --- accrue one month of growth ---
        if is_compound:
            balance = balance * growth_factor
        else:
            # simple: interest accrues only on capital already deposited,
            # never on previously-earned interest (linear growth).
            balance += total_deposited * r_monthly

        # --- contribution at end of period ---
        if contribution_monthly > 0:
            if contribution_frequency == ContributionFrequency.monthly:
                balance += contribution_monthly
                total_deposited += contribution_monthly
            elif contribution_frequency == ContributionFrequency.annual and month % MONTHS_PER_YEAR == 0:
                balance += contribution_monthly
                total_deposited += contribution_monthly

        # --- end-of-year snapshot ---
        if month % MONTHS_PER_YEAR == 0:
            yearly.append(
                {
                    "year": month // MONTHS_PER_YEAR,
                    "balance": round(balance, 2),
                    "contributions_so_far": round(total_deposited, 2),
                    "interest_earned": round(balance - total_deposited, 2),
                }
            )

    final_balance = balance
    total_contributed = total_deposited
    multiplier = (final_balance / principal) if principal > 0 else None

    return {
        "principal": round(principal, 2),
        "annual_rate": annual_rate,
        "years": years,
        "contribution_monthly": round(contribution_monthly, 2),
        "contribution_frequency": contribution_frequency,
        "interest_type": interest_type,
        "compounding_frequency": compounding_frequency,
        "final_balance": round(final_balance, 2),
        "total_contributed": round(total_contributed, 2),
        "total_interest_earned": round(final_balance - total_contributed, 2),
        "multiplier": round(multiplier, 4) if multiplier is not None else None,
        "yearly_growth": yearly,
        "source": SOURCE,
        "calculation_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ============================================================
# Endpoints
# ============================================================

@router.get("/", summary="Investment growth calculator info")
async def info():
    """Return basic info about the investment-growth calculator endpoints."""
    return {
        "name": "Compound Interest & Investment Growth Calculator",
        "description": (
            "Projects the future value of a principal (plus optional recurring "
            "contributions) under simple or compound interest, with a full "
            "year-by-year growth table."
        ),
        "endpoints": {
            "POST /invest/calculate": "Full investment-growth calculation",
            "GET /invest/": "This info endpoint",
        },
        "interest_types": [e.value for e in InterestType],
        "compounding_frequencies": [e.value for e in CompoundingFrequency],
        "contribution_frequencies": [e.value for e in ContributionFrequency],
        "source": SOURCE,
        "pricing": "$0.002 per call (x402 micropayment)",
    }


@router.post(
    "/calculate",
    response_model=InvestCalcResult,
    summary="Calculate compound/simple investment growth",
)
async def calculate(req: InvestCalcRequest) -> InvestCalcResult:
    """
    Project the growth of an initial principal plus optional recurring
    contributions.

    - **compound** (default): interest is reinvested; the compounding cadence is
      set by `compounding_frequency` (monthly by default).
    - **simple**: interest accrues linearly on deposited capital only.

    Contributions are applied at the end of each period (ordinary-annuity
    convention). Returns the final balance, total contributed, total interest
    earned, the growth multiplier (final/principal) and a year-by-year growth
    table.

    **Compound, monthly compounding** example:
    `principal=10000, annual_rate=7, years=1` → final ≈ `10722.90`
    (10000 × (1 + 0.07/12)^12).
    """
    data = calculate_investment_growth(
        principal=req.principal,
        annual_rate=req.annual_rate,
        years=req.years,
        contribution_monthly=req.contribution_monthly,
        contribution_frequency=req.contribution_frequency,
        interest_type=req.interest_type,
        compounding_frequency=req.compounding_frequency,
    )
    return InvestCalcResult(**data)


@router.get("/health", summary="Health check")
async def health():
    """Liveness probe for the investment-growth router."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
