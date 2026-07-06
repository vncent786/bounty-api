"""
Mortgage Calculator API — FastAPI router.

Computes the standard fixed-rate mortgage monthly payment, total interest,
total amount paid, and a compact amortization schedule (first 12 + last 12
months) for any principal / rate / term combination.

This is a **pure-math** calculator with zero external data dependencies —
all figures are derived from the request inputs using the standard
amortization formula. Currency-agnostic (works in any unit). Designed for
x402 micropayments ($0.002/call).

Import pattern::

    from apis.mortgage import router
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/mortgage",
    tags=["mortgage"],
    responses={422: {"description": "Validation error"}},
)


# ============================================================
# Constants
# ============================================================

SOURCE = "calculated from the standard fixed-rate amortization formula"
SCHEDULE_HEAD = 12  # months shown from the start of the schedule
SCHEDULE_TAIL = 12  # months shown from the end of the schedule


# ============================================================
# Pydantic models
# ============================================================

class MortgageCalcRequest(BaseModel):
    """Request body for POST /mortgage/calculate."""

    principal: float = Field(
        ...,
        gt=0,
        description=(
            "Property purchase price / loan base amount, before any down "
            "payment is subtracted. Must be greater than 0. In any currency."
        ),
        examples=[800_000.0],
    )
    annual_interest_rate: float = Field(
        ...,
        ge=0,
        description=(
            "Nominal annual interest rate as a percent (e.g. 4.5 means 4.5%). "
            "Must be >= 0; use 0 for an interest-free loan."
        ),
        examples=[4.5],
    )
    loan_term_years: int = Field(
        ...,
        gt=0,
        le=100,
        description="Loan term in whole years. Must be between 1 and 100.",
        examples=[30],
    )
    down_payment: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Optional up-front down payment to subtract from the principal. "
            "Must be >= 0 and strictly less than `principal`. Defaults to 0 "
            "(full principal is financed)."
        ),
        examples=[200_000.0],
    )


class AmortizationEntry(BaseModel):
    """A single month of the amortization schedule."""

    month: int = Field(..., description="1-indexed month number.")
    payment: float = Field(..., description="Total payment for the month (principal + interest).")
    principal: float = Field(..., description="Principal portion of the month's payment.")
    interest: float = Field(..., description="Interest portion of the month's payment.")
    balance: float = Field(..., description="Remaining loan balance after this payment.")


class AmortizationSchedule(BaseModel):
    """Compact amortization schedule: first 12 + last 12 months."""

    entries: List[AmortizationEntry] = Field(
        ..., description="Schedule rows (first 12 and last 12 months, or all if the loan is short)."
    )
    total_months: int = Field(..., description="Total number of months in the full loan term.")
    first_months_shown: int = Field(..., description="Number of leading months included.")
    last_months_shown: int = Field(..., description="Number of trailing months included.")
    truncated: bool = Field(..., description="True when middle months were omitted to keep the response small.")


class MortgageCalcResult(BaseModel):
    """Response model for the mortgage calculation."""

    # Inputs echoed back
    principal: float
    down_payment: float
    loan_amount: float
    annual_interest_rate: float
    loan_term_years: int

    # Derived rate / term
    monthly_interest_rate: float
    total_months: int

    # Core outputs
    monthly_payment: float
    total_interest: float
    total_paid: float

    # Amortization
    amortization_schedule: AmortizationSchedule

    # Provenance
    source: str
    calculation_date: str


# ============================================================
# Core calculation logic
# ============================================================

def calculate_mortgage(
    principal: float,
    annual_interest_rate: float,
    loan_term_years: int,
    down_payment: float = 0.0,
) -> dict:
    """
    Pure function: compute fixed-rate mortgage payment and amortization.

    Uses the standard amortization formula::

        M = P * [r(1+r)^n] / [(1+r)^n - 1]

    where ``r`` is the monthly interest rate and ``n`` the total number of
    months. Falls back to straight-line division when ``r == 0``.

    Returns a plain dict that maps 1:1 onto ``MortgageCalcResult``. Kept
    separate from the endpoint so it is unit-testable in isolation.
    """
    loan_amount = principal - down_payment

    monthly_rate = annual_interest_rate / 100.0 / 12.0
    n = loan_term_years * 12

    # --- Monthly payment ---
    if monthly_rate == 0:
        # Interest-free loan: simple straight-line repayment.
        monthly_payment = loan_amount / n
    else:
        factor = (1.0 + monthly_rate) ** n
        monthly_payment = loan_amount * (monthly_rate * factor) / (factor - 1.0)

    total_paid = monthly_payment * n
    total_interest = total_paid - loan_amount

    # --- Full amortization schedule (kept in memory, trimmed on output) ---
    full_schedule: List[AmortizationEntry] = []
    balance = loan_amount
    for month in range(1, n + 1):
        if monthly_rate == 0:
            interest_component = 0.0
        else:
            interest_component = balance * monthly_rate
        principal_component = monthly_payment - interest_component

        # On the final payment, clear any residual balance caused by rounding.
        if month == n:
            principal_component = balance
            monthly_payment_final = principal_component + interest_component
        else:
            monthly_payment_final = monthly_payment

        balance -= principal_component
        if balance < 0:  # guard against tiny negative tails
            balance = 0.0

        full_schedule.append(
            AmortizationEntry(
                month=month,
                payment=round(monthly_payment_final, 2),
                principal=round(principal_component, 2),
                interest=round(interest_component, 2),
                balance=round(balance, 2),
            )
        )

    # --- Trim schedule to first N + last N months to keep response small ---
    head = SCHEDULE_HEAD
    tail = SCHEDULE_TAIL
    truncated = n > (head + tail)
    if truncated:
        shown_entries = full_schedule[:head] + full_schedule[-tail:]
        first_shown = head
        last_shown = tail
    else:
        shown_entries = full_schedule
        first_shown = n
        last_shown = n

    schedule = AmortizationSchedule(
        entries=shown_entries,
        total_months=n,
        first_months_shown=first_shown,
        last_months_shown=last_shown,
        truncated=truncated,
    )

    return {
        "principal": round(principal, 2),
        "down_payment": round(down_payment, 2),
        "loan_amount": round(loan_amount, 2),
        "annual_interest_rate": annual_interest_rate,
        "loan_term_years": loan_term_years,
        "monthly_interest_rate": round(monthly_rate, 8),
        "total_months": n,
        "monthly_payment": round(monthly_payment, 2),
        "total_interest": round(total_interest, 2),
        "total_paid": round(total_paid, 2),
        "amortization_schedule": schedule,
        "source": SOURCE,
        "calculation_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ============================================================
# Endpoints
# ============================================================

@router.get("/", summary="Mortgage Calculator API info")
async def info():
    """Return basic info about the mortgage calculator endpoints."""
    return {
        "name": "Mortgage Calculator",
        "description": (
            "Standard fixed-rate mortgage monthly payment, total interest, "
            "total paid, and a compact amortization schedule."
        ),
        "endpoints": {
            "POST /mortgage/calculate": "Full mortgage calculation with amortization schedule",
            "GET /mortgage/": "This info endpoint",
        },
        "formula": "M = P * [r(1+r)^n] / [(1+r)^n - 1]  (r = monthly rate, n = total months)",
        "source": SOURCE,
        "pricing": "$0.002 per call (x402 micropayment)",
    }


@router.post(
    "/calculate",
    response_model=MortgageCalcResult,
    summary="Calculate fixed-rate mortgage payment and amortization",
)
async def calculate(req: MortgageCalcRequest) -> MortgageCalcResult:
    """
    Calculate the monthly payment, total interest, total paid, and a compact
    amortization schedule for a fixed-rate mortgage.

    Computes:
    - **Loan amount** = principal − down_payment
    - **Monthly rate** = annual_interest_rate / 100 / 12
    - **Total months** = loan_term_years × 12
    - **Monthly payment** = P × [r(1+r)^n] / [(1+r)^n − 1]
      (straight-line division when the rate is 0)
    - **Total paid** = monthly_payment × total_months
    - **Total interest** = total_paid − loan_amount
    - **Amortization schedule** = first 12 + last 12 months only
      (the full schedule is computed but trimmed to keep the response small;
      `truncated` indicates whether middle months were omitted)

    Currency-agnostic: all monetary values are in whatever unit `principal`
    is supplied in.
    """
    # --- Cross-field validation (cannot be expressed in Field() alone) ---
    if req.down_payment >= req.principal:
        raise HTTPException(
            status_code=422,
            detail=(
                "down_payment must be strictly less than principal "
                f"(got down_payment={req.down_payment}, principal={req.principal})."
            ),
        )

    data = calculate_mortgage(
        principal=req.principal,
        annual_interest_rate=req.annual_interest_rate,
        loan_term_years=req.loan_term_years,
        down_payment=req.down_payment,
    )
    return MortgageCalcResult(**data)


@router.get("/health", summary="Health check")
async def health():
    """Liveness probe for the mortgage router."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
