"""
Singapore Rental Yield Calculator API — FastAPI router.

Computes gross/net rental yield, cap rate, price-to-rent ratio, payback
period and a full expense breakdown for a Singapore investment property.

This is a **pure-math** calculator with zero external data dependencies —
all figures are derived from the request inputs using standard real-estate
formulas. The simplified property-tax figure uses a flat rate on annual
rental income (the real Singapore property tax is tiered and progressive;
see IRAS). Designed for x402 micropayments ($0.002/call).

Import pattern::

    from apis.rental_yield import router
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

# ============================================================
# Router
# ============================================================

router = APIRouter(
    prefix="/rental-yield",
    tags=["rental-yield"],
    responses={422: {"description": "Validation error"}},
)


# ============================================================
# Constants
# ============================================================

DEFAULT_PROPERTY_TAX_RATE = 0.04  # simplified flat 4% on annual rental
SOURCE = "calculated from standard real estate formulas"


# ============================================================
# Pydantic models
# ============================================================

class RentalCalcRequest(BaseModel):
    """Request body for POST /rental-yield/calculate."""

    property_price: float = Field(
        ...,
        gt=0,
        description="Property purchase price in SGD. Must be greater than 0.",
        examples=[1_200_000.0],
    )
    monthly_rent: float = Field(
        ...,
        ge=0,
        description="Gross monthly rental income in SGD. Must be >= 0.",
        examples=[4_200.0],
    )
    annual_expenses: float = Field(
        default=0.0,
        ge=0,
        description="Other annual operating expenses (insurance, etc.) in SGD.",
        examples=[1_200.0],
    )
    property_tax_rate: float = Field(
        default=DEFAULT_PROPERTY_TAX_RATE,
        ge=0,
        le=1,
        description=(
            "Property tax applied as a fraction of gross annual rental. "
            "Singapore property tax is tiered in reality; 0.04 is a simplified "
            "flat default."
        ),
        examples=[0.04],
    )
    management_fee_monthly: float = Field(
        default=0.0,
        ge=0,
        description="Monthly property-management / letting-agent fee in SGD.",
        examples=[350.0],
    )
    maintenance_monthly: float = Field(
        default=0.0,
        ge=0,
        description="Average monthly maintenance / repair allowance in SGD.",
        examples=[150.0],
    )


class ExpensesBreakdown(BaseModel):
    """Itemised annual expense breakdown."""

    property_tax: float = Field(..., description="Annual property tax (SGD).")
    management_fee_annual: float = Field(..., description="Annual management fees (SGD).")
    maintenance_annual: float = Field(..., description="Annual maintenance allowance (SGD).")
    other_annual: float = Field(..., description="Other annual expenses supplied by the caller (SGD).")
    total_annual_expenses: float = Field(..., description="Sum of all annual expenses (SGD).")


class RentalCalcResult(BaseModel):
    """Response model for the rental-yield calculation."""

    # Inputs echoed back (rounded for display)
    property_price: float
    monthly_rent: float

    # Core rent figures
    gross_annual_rent: float
    net_annual_rent: float

    # Yield metrics
    gross_yield_percent: float
    net_yield_percent: float
    cap_rate: float

    # Cashflow
    monthly_cashflow: float
    annual_cashflow: float

    # Expense detail
    expenses_breakdown: ExpensesBreakdown

    # Investment ratios
    price_to_rent_ratio: Optional[float] = Field(
        None,
        description="property_price / gross_annual_rent. None when gross rent is 0.",
    )
    years_to_break_even: Optional[float] = Field(
        None,
        description="property_price / net_annual_rent. None when net rent <= 0.",
    )

    # Provenance
    source: str
    calculation_date: str


# ============================================================
# Core calculation logic
# ============================================================

def calculate_rental_yield(
    property_price: float,
    monthly_rent: float,
    annual_expenses: float = 0.0,
    property_tax_rate: float = DEFAULT_PROPERTY_TAX_RATE,
    management_fee_monthly: float = 0.0,
    maintenance_monthly: float = 0.0,
) -> dict:
    """
    Pure function: compute all Singapore rental-investment metrics.

    Returns a plain dict that maps 1:1 onto ``RentalCalcResult``.
    Kept separate from the endpoint so it is unit-testable in isolation.
    """
    # --- Rent ---
    gross_annual_rent = monthly_rent * 12

    # --- Expenses (all expressed on an annual basis) ---
    property_tax = gross_annual_rent * property_tax_rate
    management_fee_annual = management_fee_monthly * 12
    maintenance_annual = maintenance_monthly * 12
    other_annual = annual_expenses

    total_expenses = property_tax + management_fee_annual + maintenance_annual + other_annual

    # --- Net operating income ---
    net_annual_rent = gross_annual_rent - total_expenses

    # --- Yields ---
    gross_yield_percent = (gross_annual_rent / property_price * 100) if property_price else 0.0
    net_yield_percent = (net_annual_rent / property_price * 100) if property_price else 0.0

    # Cap rate (capitalization rate) = NOI / property_price
    cap_rate = net_yield_percent  # same formula; kept as a distinct concept/label

    # --- Cashflow (no mortgage modelled) ---
    monthly_cashflow = net_annual_rent / 12.0
    annual_cashflow = net_annual_rent

    # --- Ratios ---
    price_to_rent_ratio = (property_price / gross_annual_rent) if gross_annual_rent > 0 else None
    years_to_break_even = (property_price / net_annual_rent) if net_annual_rent > 0 else None

    return {
        "property_price": round(property_price, 2),
        "monthly_rent": round(monthly_rent, 2),
        "gross_annual_rent": round(gross_annual_rent, 2),
        "net_annual_rent": round(net_annual_rent, 2),
        "gross_yield_percent": round(gross_yield_percent, 4),
        "net_yield_percent": round(net_yield_percent, 4),
        "cap_rate": round(cap_rate, 4),
        "monthly_cashflow": round(monthly_cashflow, 2),
        "annual_cashflow": round(annual_cashflow, 2),
        "expenses_breakdown": {
            "property_tax": round(property_tax, 2),
            "management_fee_annual": round(management_fee_annual, 2),
            "maintenance_annual": round(maintenance_annual, 2),
            "other_annual": round(other_annual, 2),
            "total_annual_expenses": round(total_expenses, 2),
        },
        "price_to_rent_ratio": round(price_to_rent_ratio, 2) if price_to_rent_ratio is not None else None,
        "years_to_break_even": round(years_to_break_even, 2) if years_to_break_even is not None else None,
        "source": SOURCE,
        "calculation_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ============================================================
# Endpoints
# ============================================================

@router.get("/", summary="Rental Yield API info")
async def info():
    """Return basic info about the rental-yield calculator endpoints."""
    return {
        "name": "SG Rental Yield Calculator",
        "description": (
            "Gross/net yield, cap rate, price-to-rent ratio and payback "
            "period for Singapore rental properties."
        ),
        "endpoints": {
            "POST /rental-yield/calculate": "Full rental-investment calculation",
            "GET /rental-yield/": "This info endpoint",
        },
        "source": SOURCE,
        "pricing": "$0.002 per call (x402 micropayment)",
    }


@router.post(
    "/calculate",
    response_model=RentalCalcResult,
    summary="Calculate Singapore rental investment metrics",
)
async def calculate(req: RentalCalcRequest) -> RentalCalcResult:
    """
    Calculate rental-investment metrics for a Singapore property.

    Computes:
    - **Gross annual rent** = monthly_rent × 12
    - **Property tax (SG, simplified)** = annual_rent × property_tax_rate
    - **Net annual rent** = gross_annual_rent − all expenses − property_tax
    - **Gross yield** = gross_annual_rent / property_price × 100
    - **Net yield** = net_annual_rent / property_price × 100
    - **Cap rate** = net_annual_rent / property_price × 100
    - **Monthly cashflow** = net_annual_rent / 12 (no mortgage modelled)
    - **Price-to-rent ratio** = property_price / gross_annual_rent
    - **Years to break even** = property_price / net_annual_rent

    Note: Singapore property tax is tiered/progressive in reality; the flat
    `property_tax_rate` here (default 4%) is a deliberate simplification.
    """
    data = calculate_rental_yield(
        property_price=req.property_price,
        monthly_rent=req.monthly_rent,
        annual_expenses=req.annual_expenses,
        property_tax_rate=req.property_tax_rate,
        management_fee_monthly=req.management_fee_monthly,
        maintenance_monthly=req.maintenance_monthly,
    )
    return RentalCalcResult(**data)


@router.get("/health", summary="Health check")
async def health():
    """Liveness probe for the rental-yield router."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
