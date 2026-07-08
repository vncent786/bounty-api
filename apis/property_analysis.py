"""
Singapore Property Investment Analysis API.

Composite endpoint that combines multiple data sources into a single
investment analysis response. This is the "killer endpoint" — an agent
calls ONE API and gets a complete property assessment.

Combines:
- Stamp duty calculation (IRAS rates)
- HDB transaction comparables (data.gov.sg)
- Rental yield analysis
- TDSR/MSR affordability check
- Nearest MRT / location intelligence

No agent can replicate this in-context: it requires combining 4+ data
sources with domain-specific regulatory formulas.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import math
import httpx

router = APIRouter(tags=["Property Analysis"])

API_BASE = "https://bountyapi.com"


class PropertyType(str, Enum):
    HDB = "hdb"
    PRIVATE = "private"


class BuyerProfile(str, Enum):
    SC = "SC"
    SPR = "SPR"
    FR = "FR"
    ENTITY = "entity"


class PropertyAnalysisRequest(BaseModel):
    property_type: PropertyType = Field(default=PropertyType.HDB, description="HDB or private property")
    property_price: float = Field(..., gt=0, description="Property price / asking price in SGD")
    town: Optional[str] = Field(default=None, description="HDB town (e.g. 'TAMPINES'). Required for HDB.")
    flat_type: Optional[str] = Field(default=None, description="HDB flat type (e.g. '4 ROOM')")
    postal_code: Optional[str] = Field(default=None, description="Postal code for location intelligence")
    monthly_rent: Optional[float] = Field(default=None, gt=0, description="Expected monthly rent in SGD")
    buyer_profile: BuyerProfile = Field(default=BuyerProfile.SC, description="Buyer profile for stamp duty")
    property_count: int = Field(default=1, ge=1, description="Number of properties owned including this one")
    monthly_income: Optional[float] = Field(default=None, gt=0, description="Gross monthly income for affordability check")
    existing_monthly_debt: float = Field(default=0, ge=0, description="Existing monthly debt obligations")
    loan_tenure_years: int = Field(default=30, ge=1, le=35)
    borrower_age: int = Field(default=35, ge=21, le=75)


async def _call_internal(path: str, method: str = "GET", json_body: dict = None) -> dict:
    """Call another Bounty API endpoint internally (bypasses x402 payment)."""
    async with httpx.AsyncClient(timeout=15) as client:
        url = f"http://127.0.0.1:8000{path}" if path.startswith("/") else f"http://127.0.0.1:8000/{path}"
        if method == "GET":
            r = await client.get(url)
        else:
            r = await client.post(url, json=json_body)
        if r.status_code == 200:
            return r.json()
        return {"error": f"Internal call failed: {r.status_code}", "path": path}


@router.post("/property/analyze")
async def analyze_property(req: PropertyAnalysisRequest):
    """
    Complete property investment analysis in one call.

    Returns stamp duty, transaction comparables, rental yield, affordability,
    and location intelligence — all in a single structured response.

    This is the composite endpoint that makes Bounty worth installing:
    no agent can replicate this analysis by scraping, because it requires
    combining government transaction data with MAS regulatory formulas.
    """
    results = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "property": {
            "type": req.property_type.value,
            "price": req.property_price,
            "town": req.town,
            "flat_type": req.flat_type,
            "postal_code": req.postal_code,
        },
        "stamp_duty": None,
        "transaction_comparables": None,
        "rental_analysis": None,
        "affordability": None,
        "location": None,
        "verdict": None,
    }

    # 1. Stamp duty
    try:
        sd = await _call_internal("/stamp-duty", "POST", {
            "price": req.property_price,
            "property_type": "residential",
            "buyer_profile": req.buyer_profile.value,
            "property_count": req.property_count,
        })
        results["stamp_duty"] = {
            "bsd": sd.get("bsd"),
            "absd": sd.get("absd"),
            "total_stamp_duty": sd.get("total_stamp_duty"),
            "effective_rate_percent": sd.get("effective_rate_percent"),
            "source": sd.get("source"),
        }
    except Exception as e:
        results["stamp_duty"] = {"error": str(e)}

    # 2. Transaction comparables (HDB only for now)
    if req.property_type == PropertyType.HDB and req.town:
        try:
            median = await _call_internal(f"/hdb/median/{req.town}")
            results["transaction_comparables"] = {
                "town": median.get("town"),
                "flat_types": median.get("flat_types"),
                "source": median.get("source", "data.gov.sg"),
            }

            # Also search recent transactions if flat type specified
            search_params = f"?town={req.town}&limit=5"
            if req.flat_type:
                search_params += f"&flat_type={req.flat_type}"
            recent = await _call_internal(f"/hdb/search{search_params}")
            results["transaction_comparables"]["recent_transactions"] = recent.get("transactions", [])
        except Exception as e:
            results["transaction_comparables"] = {"error": str(e)}

    # 3. Rental yield (if rent provided)
    if req.monthly_rent:
        try:
            yield_data = await _call_internal("/rental-yield/calculate", "POST", {
                "property_price": req.property_price,
                "monthly_rent": req.monthly_rent,
            })
            results["rental_analysis"] = {
                "gross_yield_percent": yield_data.get("gross_yield_percent"),
                "net_yield_percent": yield_data.get("net_yield_percent"),
                "monthly_cashflow": yield_data.get("monthly_cashflow"),
                "cap_rate": yield_data.get("cap_rate"),
                "price_to_rent_ratio": yield_data.get("price_to_rent_ratio"),
                "years_to_break_even": yield_data.get("years_to_break_even"),
            }
        except Exception as e:
            results["rental_analysis"] = {"error": str(e)}

    # 4. Affordability (if income provided)
    if req.monthly_income:
        try:
            loan_type = "hdb" if req.property_type == PropertyType.HDB else "bank_private"
            aff = await _call_internal("/affordability/calculate", "POST", {
                "monthly_income": req.monthly_income,
                "existing_monthly_debt": req.existing_monthly_debt,
                "property_price": req.property_price,
                "loan_type": loan_type,
                "loan_tenure_years": req.loan_tenure_years,
                "borrower_age": req.borrower_age,
                "housing_loan_count": req.property_count,
            })
            results["affordability"] = {
                "affordable": aff.get("affordable"),
                "binding_constraint": aff.get("binding_constraint"),
                "max_affordable_price": aff.get("max_affordable", {}).get("max_property_price"),
                "stress_installment": aff.get("monthly_installment_at_stress"),
                "tdsr_pass": aff.get("tdsr", {}).get("pass"),
                "msr_pass": aff.get("msr", {}).get("pass") if aff.get("msr") else None,
                "down_payment_required": aff.get("down_payment", {}).get("required_amount"),
                "ltv_limit_percent": aff.get("ltv_limit_percent"),
            }
        except Exception as e:
            results["affordability"] = {"error": str(e)}

    # 5. Location intelligence (if postal code provided)
    if req.postal_code:
        try:
            loc = await _call_internal(f"/address/{req.postal_code}")
            results["location"] = {
                "district_number": loc.get("district_number"),
                "planning_area": loc.get("planning_area"),
                "market_region": loc.get("market_region"),
                "region_description": loc.get("region_description"),
                "hdb_town": loc.get("hdb_town"),
                "nearest_mrt": loc.get("nearest_mrt_stations", [])[:3],
            }
        except Exception as e:
            results["location"] = {"error": str(e)}

    # 6. Compute verdict
    verdict_parts = []
    risk_flags = []

    # Price vs comparables
    if results["transaction_comparables"] and results["transaction_comparables"].get("flat_types"):
        flat_types = results["transaction_comparables"]["flat_types"]
        if req.flat_type:
            for ft in flat_types:
                if ft.get("type", "").upper() == req.flat_type.upper():
                    median_price = ft.get("median_price")
                    if median_price:
                        diff = ((req.property_price - median_price) / median_price) * 100
                        if diff > 10:
                            verdict_parts.append(f"Priced {diff:.0f}% above median for {req.flat_type} in {req.town}.")
                            risk_flags.append("ABOVE_MEDIAN")
                        elif diff < -10:
                            verdict_parts.append(f"Priced {abs(diff):.0f}% below median — potential value.")
                        else:
                            verdict_parts.append(f"Priced near median for {req.flat_type} in {req.town} ({diff:+.0f}%).")
                    break

    # Rental yield assessment
    if results["rental_analysis"]:
        gy = results["rental_analysis"].get("gross_yield_percent")
        if gy:
            if gy >= 4.0:
                verdict_parts.append(f"Gross yield {gy}% is healthy for Singapore.")
            elif gy >= 3.0:
                verdict_parts.append(f"Gross yield {gy}% is typical but not exceptional.")
            else:
                verdict_parts.append(f"Gross yield {gy}% is low — capital appreciation dependent.")
                risk_flags.append("LOW_YIELD")

    # Affordability assessment
    if results["affordability"]:
        if results["affordability"].get("affordable") is False:
            verdict_parts.append(f"NOT affordable under MAS TDSR/MSR — {results['affordability'].get('binding_constraint')} failed.")
            risk_flags.append("NOT_AFFORDABLE")
        else:
            verdict_parts.append("Passes MAS affordability checks.")

    # Total cash outlay
    total_upfront = req.property_price * 0.25  # min down payment approx
    if results["stamp_duty"] and results["stamp_duty"].get("total_stamp_duty"):
        total_upfront = (req.property_price * 0.25) + results["stamp_duty"]["total_stamp_duty"]

    results["verdict"] = {
        "summary": " ".join(verdict_parts) if verdict_parts else "Insufficient data for verdict.",
        "risk_flags": risk_flags,
        "estimated_total_upfront_cost": round(total_upfront, 2),
        "upfront_breakdown": {
            "down_payment_25pct": round(req.property_price * 0.25, 2),
            "stamp_duty": results["stamp_duty"].get("total_stamp_duty") if results["stamp_duty"] else None,
        },
    }

    results["source"] = "Composite: IRAS (stamp duty), data.gov.sg (HDB transactions), MAS (TDSR/MSR), URA (planning areas), LTA (MRT data)"
    return results
