"""
Asia Data API — Specialist data APIs for AI agents.
Singapore property, financial, and geographic data.
Designed for x402 micropayments and MCP discovery.

APIs:
- SG Stamp Duty (BSD + ABSD) — verified against IRAS
- SG Postal Code to District mapper
- SG Rental Yield Calculator
- HDB Resale Price data (live from data.gov.sg)
"""

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import math
import os

app = FastAPI(
    title="Asia Data API",
    description="Specialist Asian data APIs for AI agents. Singapore property, financial, and geographic data. Built for x402 micropayments.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Rate tables — sourced from IRAS (verified Jan 2026)
# Source: iras.gov.sg/taxes/stamp-duty/for-property
# ============================================================

BSD_RESIDENTIAL_TIERS = [
    (180_000, 0.01),       # First $180,000 at 1%
    (180_000, 0.02),       # Next $180,000 at 2%
    (640_000, 0.03),       # Next $640,000 at 3%
    (500_000, 0.04),       # Next $500,000 at 4%  (effective 15 Feb 2023)
    (1_500_000, 0.05),     # Next $1,500,000 at 5%
    (float('inf'), 0.06),  # Remaining at 6%
]

BSD_NON_RESIDENTIAL_TIERS = [
    (180_000, 0.01),
    (180_000, 0.02),
    (640_000, 0.03),
    (float('inf'), 0.04),  # Remaining at 4%
]

# ABSD rates effective on or after 27 Apr 2023
ABSD_RATES = {
    'SC':       {1: 0.00, 2: 0.20, 3: 0.30},
    'SPR':      {1: 0.05, 2: 0.30, 3: 0.35},
    'FR':       {1: 0.60},
    'entity':   {1: 0.65},
    'developer': {1: 0.40},  # 35% + 5% non-remittable
    'trustee':  {1: 0.65},
}


class BuyerProfile(str, Enum):
    SC = "SC"           # Singapore Citizen
    SPR = "SPR"         # Singapore Permanent Resident
    FR = "FR"           # Foreigner
    ENTITY = "entity"   # Entity (company, partnership, etc.)
    DEVELOPER = "developer"  # Housing Developer
    TRUSTEE = "trustee"      # Trustee


class PropertyType(str, Enum):
    RESIDENTIAL = "residential"
    NON_RESIDENTIAL = "non-residential"


# ============================================================
# Core calculation logic
# ============================================================

def calculate_marginal_duty(price: float, tiers: list) -> tuple:
    """Calculate tiered/marginal stamp duty. Returns (total_duty, breakdown)."""
    remaining = price
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
            "range": f"${lower:,.0f} – ${upper:,.0f}" if upper != float('inf') and taxable == tier_size
                     else (f"First ${upper:,.0f}" if cumulative == 0
                           else f"${lower:,.0f} – ${upper:,.0f}"),
            "rate_percent": rate * 100,
            "taxable_amount": round(taxable, 2),
            "duty": round(duty, 2),
        })
        remaining -= taxable
        cumulative += taxable

    # BSD rounded down to nearest dollar, minimum $1
    total = max(1, math.floor(total)) if total > 0 else 0
    return total, breakdown


def get_absd_rate(profile: BuyerProfile, property_count: int) -> float:
    """Get ABSD rate based on buyer profile and property count."""
    rates = ABSD_RATES.get(profile.value, {})

    if profile.value in ('FR', 'entity', 'developer', 'trustee'):
        return rates.get(1, 0.0)

    # SC and SPR: rate depends on property count
    count = max(1, property_count)
    if count in rates:
        return rates[count]
    # For 3rd and beyond, use the highest tier
    max_key = max(rates.keys())
    if count >= max_key:
        return rates[max_key]
    return 0.0


# ============================================================
# API Endpoints
# ============================================================

class StampDutyRequest(BaseModel):
    price: float = Field(..., gt=0, description="Property purchase price or market value in SGD (whichever is higher)")
    property_type: PropertyType = Field(default=PropertyType.RESIDENTIAL, description="Residential or non-residential")
    buyer_profile: BuyerProfile = Field(default=BuyerProfile.SC, description="Buyer profile for ABSD calculation")
    property_count: int = Field(default=1, ge=1, description="Number of residential properties owned including this one")


class StampDutyResult(BaseModel):
    price: float
    property_type: str
    buyer_profile: str
    property_count: int
    bsd: int
    bsd_breakdown: List[dict]
    absd: int
    absd_rate_percent: float
    absd_applicable: bool
    total_stamp_duty: int
    effective_rate_percent: float
    calculation_date: str
    source: str


@app.get("/")
async def root():
    """API info."""
    return {
        "name": "SG Stamp Duty API",
        "version": "1.0.0",
        "description": "Singapore property stamp duty calculator (BSD + ABSD). Rates verified against IRAS.",
        "endpoints": {
            "/stamp-duty": "Full stamp duty calculation (BSD + ABSD)",
            "/bsd": "Buyer's Stamp Duty only",
            "/absd": "Additional Buyer's Stamp Duty only",
            "/docs": "Interactive API documentation (Swagger UI)",
        },
        "pricing": "$0.002 per call (x402 micropayment)",
        "rate_source": "iras.gov.sg, verified Jan 2026",
    }


@app.post("/stamp-duty", response_model=StampDutyResult)
async def calculate_stamp_duty(req: StampDutyRequest):
    """
    Calculate total stamp duty for a Singapore property purchase.
    Returns BSD (Buyer's Stamp Duty) + ABSD (Additional Buyer's Stamp Duty).

    Rates verified against IRAS (iras.gov.sg):
    - BSD residential: 6-tier marginal (1%-6%), effective 15 Feb 2023
    - BSD non-residential: 4-tier marginal (1%-4%)
    - ABSD: 0%-65% depending on buyer profile, effective 27 Apr 2023
    """
    price = req.price

    # BSD calculation
    tiers = BSD_RESIDENTIAL_TIERS if req.property_type == PropertyType.RESIDENTIAL else BSD_NON_RESIDENTIAL_TIERS
    bsd, bsd_breakdown = calculate_marginal_duty(price, tiers)

    # ABSD calculation (only for residential properties)
    absd = 0
    absd_rate = 0.0
    absd_applicable = False

    if req.property_type == PropertyType.RESIDENTIAL:
        absd_rate = get_absd_rate(req.buyer_profile, req.property_count)
        if absd_rate > 0:
            absd = max(1, math.floor(price * absd_rate))
            absd_applicable = True

    total = bsd + absd
    effective_rate = (total / price * 100) if price > 0 else 0

    return StampDutyResult(
        price=price,
        property_type=req.property_type.value,
        buyer_profile=req.buyer_profile.value,
        property_count=req.property_count,
        bsd=bsd,
        bsd_breakdown=bsd_breakdown,
        absd=absd,
        absd_rate_percent=absd_rate * 100,
        absd_applicable=absd_applicable,
        total_stamp_duty=total,
        effective_rate_percent=round(effective_rate, 2),
        calculation_date=datetime.now().strftime("%Y-%m-%d"),
        source="iras.gov.sg, verified Jan 2026",
    )


@app.get("/bsd")
async def calculate_bsd_get(
    price: float = Query(..., gt=0, description="Property price in SGD"),
    property_type: PropertyType = Query(default=PropertyType.RESIDENTIAL),
):
    """Calculate BSD only (no ABSD). Simple GET endpoint for easy testing."""
    tiers = BSD_RESIDENTIAL_TIERS if property_type == PropertyType.RESIDENTIAL else BSD_NON_RESIDENTIAL_TIERS
    bsd, breakdown = calculate_marginal_duty(price, tiers)

    return {
        "price": price,
        "property_type": property_type.value,
        "bsd": bsd,
        "breakdown": breakdown,
        "source": "iras.gov.sg",
    }


@app.get("/absd")
async def calculate_absd_get(
    price: float = Query(..., gt=0, description="Property price in SGD"),
    buyer_profile: BuyerProfile = Query(default=BuyerProfile.SC),
    property_count: int = Query(default=1, ge=1),
):
    """Calculate ABSD only (no BSD). Simple GET endpoint."""
    rate = get_absd_rate(buyer_profile, property_count)
    absd = max(1, math.floor(price * rate)) if rate > 0 else 0

    return {
        "price": price,
        "buyer_profile": buyer_profile.value,
        "property_count": property_count,
        "absd_rate_percent": rate * 100,
        "absd": absd,
        "applicable": rate > 0,
        "source": "iras.gov.sg",
    }


# ============================================================
# Health check
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# ============================================================
# Mount additional API routers
# ============================================================

try:
    from apis.postal_district import router as postal_router
    app.include_router(postal_router)
except ImportError as e:
    print(f"Warning: postal_district router not loaded: {e}")

try:
    from apis.rental_yield import router as rental_router
    app.include_router(rental_router)
except ImportError as e:
    print(f"Warning: rental_yield router not loaded: {e}")

try:
    from apis.hdb_resale import router as hdb_router
    app.include_router(hdb_router)
except ImportError as e:
    print(f"Warning: hdb_resale router not loaded: {e}")


# ============================================================
# llms.txt — AI discovery (the #1 AI SEO priority)
# ============================================================

@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """llms.txt — structured description for LLM discovery at inference time."""
    return """# Asia Data API

> Specialist Asian data APIs for AI agents. Singapore property, financial, and geographic data. Pay-per-call via x402 micropayments (USDC on Base). No API keys, no subscriptions.

```json
{
  "name": "Asia Data API",
  "url": "https://api.asiadata.market",
  "category": "Data API Marketplace",
  "focus": "Singapore / Southeast Asia",
  "payment_protocol": "x402",
  "discovery_protocol": "MCP",
  "settlement_currency": "USDC",
  "settlement_chain": "Base",
  "live_apis": 4
}
```

## What it is

- A marketplace of specialist Asian data APIs where buyers are AI agents
- Discovery via MCP (single endpoint exposes all APIs as tools)
- Payment via x402 (USDC on Base, sub-second settlement)
- First and only x402 marketplace focused on Asian data

## Available APIs

### SG Stamp Duty Calculator
- Endpoints: POST /stamp-duty, GET /bsd, GET /absd
- Price: $0.002/call
- Coverage: BSD (6-tier marginal, 1%-6%), ABSD (0%-65% by buyer profile)
- Source: iras.gov.sg, verified Jan 2026

### SG Postal Code to District
- Endpoints: GET /postal/{code}, GET /postal/districts
- Price: $0.001/call
- Coverage: All 28 Singapore postal districts with area names

### SG Rental Yield Calculator
- Endpoints: POST /rental-yield/calculate
- Price: $0.002/call
- Coverage: Gross yield, net yield, cap rate, price-to-rent ratio, cashflow

### HDB Resale Price Data
- Endpoints: GET /hdb/towns, GET /hdb/median/{town}, GET /hdb/search
- Price: $0.003/call
- Coverage: 234K+ HDB resale transactions, all 26 towns, 2017-present
- Source: data.gov.sg (live)

## How AI agents connect

Add to Claude Desktop or any MCP-compatible client:
```json
{
  "mcpServers": {
    "asia-data": {
      "url": "https://api.asiadata.market/mcp"
    }
  }
}
```

## Full documentation

See: https://api.asiadata.market/llms-full.txt
"""


@app.get("/llms-full.txt", response_class=PlainTextResponse)
async def llms_full_txt():
    """Full crawlable documentation for LLM training and inference."""
    return """# Asia Data API — Full Documentation

## SG Stamp Duty Calculator

### POST /stamp-duty
Request:
{
  "price": 1500000,
  "property_type": "residential",
  "buyer_profile": "SC",
  "property_count": 1
}

Response:
{
  "price": 1500000,
  "bsd": 44600,
  "bsd_breakdown": [
    {"range": "$0 - $180,000", "rate_percent": 1, "taxable_amount": 180000, "duty": 1800},
    {"range": "$180,000 - $360,000", "rate_percent": 2, "taxable_amount": 180000, "duty": 3600},
    {"range": "$360,000 - $1,000,000", "rate_percent": 3, "taxable_amount": 640000, "duty": 19200},
    {"range": "$1,000,000 - $1,500,000", "rate_percent": 4, "taxable_amount": 500000, "duty": 20000}
  ],
  "absd": 0,
  "total_stamp_duty": 44600,
  "effective_rate_percent": 2.97
}

### Buyer profiles: SC (Singapore Citizen), SPR (Permanent Resident), FR (Foreigner), entity, developer, trustee
### ABSD rates: SC 1st=0%, 2nd=20%, 3rd=30%. SPR 1st=5%, 2nd=30%, 3rd=35%. FR=60%. Entity=65%.

## SG Postal Code to District

### GET /postal/{postal_code}
Response:
{
  "postal_code": "238801",
  "sector": "23",
  "district_number": 5,
  "district_name": "D5",
  "general_area": "RCR",
  "areas": ["Buona Vista", "Dover", "Pasir Panjang", "West Coast"]
}

## SG Rental Yield Calculator

### POST /rental-yield/calculate
Request:
{
  "property_price": 1200000,
  "monthly_rent": 4200
}

Response:
{
  "gross_annual_rent": 50400,
  "net_annual_rent": 41328,
  "gross_yield_percent": 4.2,
  "net_yield_percent": 3.44,
  "monthly_cashflow": 3444.0,
  "cap_rate": 3.44,
  "price_to_rent_ratio": 23.81,
  "years_to_break_even": 29.04
}

## HDB Resale Data

### GET /hdb/median/{town}
Response:
{
  "town": "ANG MO KIO",
  "flat_types": [
    {"type": "3 ROOM", "median_price": 350000, "count": 5034, "min_price": 200000, "max_price": 600000},
    {"type": "4 ROOM", "median_price": 520000, "count": 6892, "min_price": 350000, "max_price": 800000}
  ]
}
"""
