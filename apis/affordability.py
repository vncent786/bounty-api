"""
Singapore TDSR/MSR Affordability Calculator.

Implements MAS (Monetary Authority of Singapore) regulatory framework:
- TDSR (Total Debt Servicing Ratio): All debt ≤ 55% of gross monthly income
- MSR (Mortgage Servicing Ratio): HDB/EC mortgage ≤ 30% of gross monthly income
- Loan-to-Value (LTV) limits by loan count and tenure
- Stress-tested at MAS minimum interest rate floors

Sources:
- MAS TDSR framework (effective 28 June 2013, revised)
- HDB MSR rules (HDB loans and bank loans for HDB flats)
- LTV limits per MAS Notice 635 / HDB eligibility rules
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import math

router = APIRouter(tags=["Affordability"])

# ============================================================
# MAS regulatory constants
# ============================================================

TDSR_THRESHOLD = 0.55        # 55% of gross monthly income
MSR_THRESHOLD = 0.30         # 30% of gross monthly income (HDB/EC only)

# Stress test interest rate floors (MAS minimum)
# Source: MAS Notice 635, HDB loan rules
STRESS_RATE_HDB = 0.03       # 3% for HDB loans
STRESS_RATE_BANK_SHORT = 0.035  # 3.5% for bank loans ≤ 30 years or age ≤ 65
STRESS_RATE_BANK_LONG = 0.045   # 4.5% for bank loans > 30 years or extending past age 65

# Loan tenure limits
MAX_TENURE_HDB = 30          # HDB loans: max 30 years
MAX_TENURE_PRIVATE = 35      # Bank loans for private property: max 35 years
MAX_AGE_END = 65             # Loan must end before borrower turns 65 (bank loans)

# LTV limits
# Source: MAS residential property loan rules (effective 16 Dec 2021)
LTV_BANK = {
    1: 0.75,    # First housing loan: 75%
    2: 0.45,    # Second: 45%
    3: 0.25,    # Third and beyond: 25%
}

LTV_HDB = 0.80  # HDB concessionary loan: 80% LTV


class LoanType(str, Enum):
    HDB = "hdb"           # HDB concessionary loan
    BANK_HDB = "bank_hdb"  # Bank loan for HDB flat
    BANK_PRIVATE = "bank_private"  # Bank loan for private property


class AffordabilityRequest(BaseModel):
    monthly_income: float = Field(..., gt=0, description="Gross monthly income in SGD (all borrowers combined)")
    existing_monthly_debt: float = Field(default=0, ge=0, description="Total monthly debt obligations (car loans, personal loans, credit card min payments, other mortgages)")
    property_price: float = Field(..., gt=0, description="Property purchase price in SGD")
    loan_amount: Optional[float] = Field(default=None, gt=0, description="Requested loan amount. If omitted, calculated from LTV.")
    interest_rate: float = Field(default=0.026, gt=0, description="Actual interest rate (annual). HDB concessionary: 2.6%, bank: varies")
    loan_tenure_years: int = Field(default=30, ge=1, le=35, description="Loan tenure in years")
    borrower_age: int = Field(default=35, ge=21, le=75, description="Age of youngest borrower")
    loan_type: LoanType = Field(default=LoanType.BANK_PRIVATE, description="Type of loan")
    housing_loan_count: int = Field(default=1, ge=1, description="Number of outstanding housing loans including this one")


def _monthly_installment(principal: float, annual_rate: float, tenure_years: int) -> float:
    """Standard amortization formula: monthly payment."""
    n = tenure_years * 12
    r = annual_rate / 12
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def _max_tenure(requested_tenure: int, borrower_age: int, loan_type: LoanType) -> int:
    """Cap tenure based on loan type and borrower age."""
    if loan_type == LoanType.HDB:
        cap = MAX_TENURE_HDB
    else:
        cap = MAX_TENURE_PRIVATE
        # Bank loans: must end before age 65
        age_cap = MAX_AGE_END - borrower_age
        cap = min(cap, age_cap)
    return min(requested_tenure, cap)


def _stress_rate(loan_type: LoanType, tenure: int, borrower_age: int) -> float:
    """Get the MAS stress-test interest rate."""
    if loan_type == LoanType.HDB:
        return STRESS_RATE_HDB
    # Bank loan: check if tenure > 30 or extends past age 65
    if tenure > 30 or (borrower_age + tenure) > MAX_AGE_END:
        return STRESS_RATE_BANK_LONG
    return STRESS_RATE_BANK_SHORT


def _ltv(loan_type: LoanType, housing_loan_count: int) -> float:
    """Get LTV limit."""
    if loan_type == LoanType.HDB:
        return LTV_HDB
    count = min(housing_loan_count, 3)
    return LTV_BANK.get(count, 0.25)


@router.post("/affordability/calculate")
async def calculate_affordability(req: AffordabilityRequest):
    """
    Calculate Singapore property loan affordability under MAS TDSR/MSR framework.

    Returns:
    - Whether the loan passes TDSR and MSR checks
    - Maximum affordable loan amount and property price
    - Stress-tested monthly installment
    - LTV limit and required down payment
    """
    effective_tenure = _max_tenure(req.loan_tenure_years, req.borrower_age, req.loan_type)
    stress_rate = _stress_rate(req.loan_type, effective_tenure, req.borrower_age)
    ltv_limit = _ltv(req.loan_type, req.housing_loan_count)

    # Determine loan amount
    if req.loan_amount:
        loan_amount = min(req.loan_amount, req.property_price * ltv_limit)
    else:
        loan_amount = req.property_price * ltv_limit

    # Monthly installment at stress rate (MAS requires stress testing)
    stress_installment = _monthly_installment(loan_amount, stress_rate, effective_tenure)

    # Also calculate at actual rate for reference
    actual_installment = _monthly_installment(loan_amount, req.interest_rate, effective_tenure)

    # TDSR check: (this mortgage at stress rate + other debt) ≤ 55% of income
    total_debt_at_stress = stress_installment + req.existing_monthly_debt
    tdsr_ratio = total_debt_at_stress / req.monthly_income
    tdsr_pass = tdsr_ratio <= TDSR_THRESHOLD

    # MSR check (HDB/EC only): this mortgage ≤ 30% of income
    msr_ratio = None
    msr_pass = None
    is_msr_applicable = req.loan_type in (LoanType.HDB, LoanType.BANK_HDB)
    if is_msr_applicable:
        msr_ratio = stress_installment / req.monthly_income
        msr_pass = msr_ratio <= MSR_THRESHOLD

    # Maximum loan under TDSR
    # stress_installment ≤ (55% × income) - existing_debt
    max_payment_tdsr = max(0, TDSR_THRESHOLD * req.monthly_income - req.existing_monthly_debt)

    # Reverse-engineer max loan from max payment using stress rate
    r = stress_rate / 12
    n = effective_tenure * 12
    if r > 0:
        max_loan_tdsr = max_payment_tdsr * ((1 + r) ** n - 1) / (r * (1 + r) ** n)
    else:
        max_loan_tdsr = max_payment_tdsr * n

    # Maximum loan under MSR (if applicable)
    max_loan_msr = None
    if is_msr_applicable:
        max_payment_msr = MSR_THRESHOLD * req.monthly_income
        if r > 0:
            max_loan_msr = max_payment_msr * ((1 + r) ** n - 1) / (r * (1 + r) ** n)
        else:
            max_loan_msr = max_payment_msr * n

    # The binding constraint is the lower of TDSR and MSR
    if max_loan_msr is not None:
        max_loan = min(max_loan_tdsr, max_loan_msr)
        binding_constraint = "MSR" if max_loan_msr < max_loan_tdsr else "TDSR"
    else:
        max_loan = max_loan_tdsr
        binding_constraint = "TDSR"

    # Max affordable property price = max_loan / LTV
    max_property_price = max_loan / ltv_limit

    # Down payment required
    min_down_payment = req.property_price - loan_amount
    min_down_payment_percent = (min_down_payment / req.property_price) * 100

    # Overall verdict
    affordable = tdsr_pass and (msr_pass if msr_pass is not None else True)

    return {
        "affordable": affordable,
        "binding_constraint": binding_constraint,
        "loan_amount": round(loan_amount, 2),
        "ltv_limit_percent": round(ltv_limit * 100, 1),
        "effective_tenure_years": effective_tenure,
        "stress_interest_rate_percent": round(stress_rate * 100, 2),
        "actual_interest_rate_percent": round(req.interest_rate * 100, 2),
        "monthly_installment_at_stress": round(stress_installment, 2),
        "monthly_installment_actual": round(actual_installment, 2),
        "tdsr": {
            "threshold_percent": TDSR_THRESHOLD * 100,
            "current_ratio_percent": round(tdsr_ratio * 100, 2),
            "pass": tdsr_pass,
            "total_monthly_debt_at_stress": round(total_debt_at_stress, 2),
            "max_monthly_payment_capacity": round(max_payment_tdsr, 2),
        },
        "msr": {
            "applicable": is_msr_applicable,
            "threshold_percent": MSR_THRESHOLD * 100 if is_msr_applicable else None,
            "current_ratio_percent": round(msr_ratio * 100, 2) if msr_ratio else None,
            "pass": msr_pass,
        } if is_msr_applicable else None,
        "max_affordable": {
            "max_loan_amount": round(max_loan, 2),
            "max_property_price": round(max_property_price, 2),
        },
        "down_payment": {
            "required_amount": round(min_down_payment, 2),
            "required_percent": round(min_down_payment_percent, 1),
            "cash_component": round(max(0, min_down_payment - (req.property_price * ltv_limit * 0)), 2),
        },
        "calculation_date": datetime.now().strftime("%Y-%m-%d"),
        "source": "MAS TDSR framework (effective Jun 2013), MSR (HDB/EC), LTV limits (Dec 2021). Stress rates: HDB 3%, bank 3.5%/4.5%.",
        "notes": [
            "Stress testing uses MAS minimum interest rates, not the actual loan rate.",
            f"Loan tenure capped at {effective_tenure} years (requested {req.loan_tenure_years}).",
            f"LTV limit: {ltv_limit*100:.0f}% for {req.loan_type.value} (loan #{req.housing_loan_count}).",
            "MSR applies to HDB and Executive Condominium loans only.",
        ],
    }


@router.get("/affordability/quick")
async def quick_affordability(
    monthly_income: float = Query(..., gt=0, description="Gross monthly income in SGD"),
    property_price: float = Query(..., gt=0, description="Property price in SGD"),
    loan_type: LoanType = Query(default=LoanType.BANK_PRIVATE),
    existing_debt: float = Query(default=0, ge=0, description="Existing monthly debt obligations"),
):
    """Quick affordability check: can you afford this property?"""
    req = AffordabilityRequest(
        monthly_income=monthly_income,
        existing_monthly_debt=existing_debt,
        property_price=property_price,
        loan_type=loan_type,
    )
    result = await calculate_affordability(req)
    return {
        "affordable": result["affordable"],
        "max_affordable_price": result["max_affordable"]["max_property_price"],
        "binding_constraint": result["binding_constraint"],
        "stress_installment": result["monthly_installment_at_stress"],
        "tdsr_pass": result["tdsr"]["pass"],
        "msr_pass": result["msr"]["pass"] if result["msr"] else None,
        "source": result["source"],
    }
