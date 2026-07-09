"""
Property Pitch API — generates a structured investment thesis
for any property. This is what a property agent prints and hands
to a client.

Combines:
- Stamp duty (IRAS)
- Transaction comparables (data.gov.sg for HDB)
- Rental yield analysis
- TDSR/MSR affordability
- Location intelligence (MRT, district, region)
- Risk assessment
- Plain-English verdict

The output is designed to be read by both agents and humans.
An AI assistant can format it into a one-page PDF or presentation.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import httpx
import os
import math

router = APIRouter(tags=["Property Pitch"])

# Use PORT for internal calls (Railway sets this dynamically)
INTERNAL_BASE = os.environ.get("INTERNAL_API_BASE") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


class BuyerProfile(str, Enum):
    SC = "SC"
    SPR = "SPR"
    FR = "FR"
    ENTITY = "entity"


class PropertyType(str, Enum):
    HDB = "hdb"
    PRIVATE = "private"
    CONDO = "private"
    LANDED = "private"


class PitchRequest(BaseModel):
    """Request body for POST /property/pitch."""
    property_type: str = Field(default="hdb", description="hdb, private, condo, landed")
    property_price: float = Field(..., gt=0, description="Asking price in SGD")
    town: Optional[str] = Field(default=None, description="HDB town (e.g. 'TAMPINES') or area name")
    flat_type: Optional[str] = Field(default=None, description="HDB flat type (e.g. '4 ROOM') or unit type (e.g. '3BR')")
    project_name: Optional[str] = Field(default=None, description="Condo/project name (for private property)")
    postal_code: Optional[str] = Field(default=None, description="Postal code for location intelligence")
    sqft: Optional[float] = Field(default=None, gt=0, description="Floor area in square feet")
    monthly_rent: Optional[float] = Field(default=None, gt=0, description="Expected monthly rent (optional)")
    tenure: Optional[str] = Field(default=None, description="Freehold, 99-year, 999-year, etc.")
    top_year: Optional[int] = Field(default=None, description="Year of Temporary Occupation Permit (TOP)")
    buyer_profile: BuyerProfile = Field(default=BuyerProfile.SC, description="SC, SPR, FR, entity")
    property_count: int = Field(default=1, ge=1, description="Number of properties owned including this one")
    monthly_income: Optional[float] = Field(default=None, gt=0, description="Gross monthly income")
    existing_monthly_debt: float = Field(default=0, ge=0, description="Existing monthly debt")
    buyer_notes: Optional[str] = Field(default=None, description="Any specific concerns or goals the buyer has")


async def _internal_get(path: str) -> dict:
    """Call a Bounty endpoint internally."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{INTERNAL_BASE}{path}")
        return r.json() if r.status_code == 200 else {}


async def _internal_post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{INTERNAL_BASE}{path}", json=body)
        return r.json() if r.status_code == 200 else {}


def _compute_psf(price: float, sqft: Optional[float]) -> Optional[float]:
    if sqft and sqft > 0:
        return round(price / sqft, 2)
    return None


def _assess_price_fairness(asking_price: float, median_price: Optional[float], flat_type: Optional[str], town: Optional[str]) -> dict:
    """Assess whether the asking price is fair vs transaction comps."""
    if not median_price:
        return {
            "verdict": "Insufficient comparable data to assess fairness",
            "premium_to_median": None,
            "recommendation": "Proceed with caution. Get a professional valuation.",
        }

    diff_pct = ((asking_price - median_price) / median_price) * 100

    if diff_pct > 15:
        verdict = f"Significantly OVERPRICED — {diff_pct:.0f}% above median"
        recommendation = f"Negotiate hard. Fair value is closer to ${median_price:,.0f}."
        risk = "HIGH"
    elif diff_pct > 5:
        verdict = f"Slightly above median ({diff_pct:.0f}% premium)"
        recommendation = "Moderately priced. There may be room to negotiate."
        risk = "MEDIUM"
    elif diff_pct > -5:
        verdict = f"Fairly priced (within {abs(diff_pct):.0f}% of median)"
        recommendation = "Priced at market rate. Standard negotiation applies."
        risk = "LOW"
    elif diff_pct > -15:
        verdict = f"Below median ({abs(diff_pct):.0f}% discount) — potential value"
        recommendation = "Potentially undervalued. Investigate why (condition, urgency, lease decay)."
        risk = "LOW"
    else:
        verdict = f"Significantly below median ({abs(diff_pct):.0f}% discount)"
        recommendation = "Deep discount. Verify condition and lease. Could be a genuine deal or a red flag."
        risk = "INVESTIGATE"

    return {
        "verdict": verdict,
        "median_price": median_price,
        "premium_to_median_pct": round(diff_pct, 1),
        "recommendation": recommendation,
        "risk_level": risk,
    }


def _assess_yield(monthly_rent: Optional[float], price: float) -> dict:
    """Assess rental yield."""
    if not monthly_rent or monthly_rent <= 0:
        return {
            "verdict": "No rental data provided",
            "gross_yield": None,
            "recommendation": "Research market rents before committing.",
        }

    annual_rent = monthly_rent * 12
    gross_yield = (annual_rent / price) * 100

    if gross_yield >= 4.0:
        quality = "Excellent for Singapore"
        rec = "Strong cash-flow asset. Rare in SG market."
    elif gross_yield >= 3.0:
        quality = "Decent for Singapore"
        rec = "Meets typical SG yield expectations."
    elif gross_yield >= 2.0:
        quality = "Below average"
        rec = "Banking on capital appreciation, not rental income."
    else:
        quality = "Poor"
        rec = "Rental barely covers costs. This is a pure capital play."

    # Monthly cashflow estimate (assuming 75% LTV, 2% interest, 25yr)
    loan = price * 0.75
    monthly_rate = 0.02 / 12
    n = 25 * 12
    if monthly_rate > 0:
        mortgage = loan * (monthly_rate * (1 + monthly_rate)**n) / ((1 + monthly_rate)**n - 1)
    else:
        mortgage = loan / n

    net_monthly = monthly_rent - mortgage
    annual_expenses = annual_rent * 0.25  # tax, maintenance, vacancy buffer
    net_yield = ((annual_rent - annual_expenses) / price) * 100

    return {
        "verdict": f"Gross yield {gross_yield:.1f}% — {quality}",
        "gross_yield_pct": round(gross_yield, 2),
        "net_yield_pct": round(net_yield, 2),
        "estimated_mortgage": round(mortgage, 2),
        "monthly_cashflow": round(net_monthly, 2),
        "recommendation": rec,
    }


def _assess_location(location_data: dict) -> dict:
    """Assess location quality."""
    if not location_data:
        return {"verdict": "Location data unavailable"}

    region = location_data.get("region", "Unknown")
    district = location_data.get("district")
    planning_area = location_data.get("planning_area", "Unknown")
    mrt_stations = location_data.get("nearest_mrt", [])

    region_scores = {"CCR": "Prime (Core Central Region)", "RCR": "Good (Rest of Central Region)", "OCR": "Suburban (Outside Central Region)"}
    region_desc = region_scores.get(region, region)

    parts = [f"{planning_area}, District {district}, {region_desc}"]

    if mrt_stations:
        nearest = mrt_stations[0]
        walk_min = nearest.get("walking_time_minutes", "?")
        station = nearest.get("station", "Unknown")
        parts.append(f"Nearest MRT: {station} ({walk_min} min walk)")

        if isinstance(walk_min, (int, float)):
            if walk_min <= 5:
                parts.append("Excellent MRT access — walker's paradise")
            elif walk_min <= 10:
                parts.append("Good MRT access — convenient commute")
            elif walk_min <= 15:
                parts.append("Moderate MRT access")
            else:
                parts.append("Limited MRT access — consider if you drive")

    return {
        "verdict": ". ".join(parts),
        "region": region,
        "district": district,
        "planning_area": planning_area,
        "nearest_mrt": mrt_stations[:3] if mrt_stations else [],
    }


def _assess_affordability(aff_data: dict, price: float) -> dict:
    """Assess affordability."""
    if not aff_data:
        return {"verdict": "No income data provided — affordability not assessed"}

    affordable = aff_data.get("affordable")
    constraint = aff_data.get("binding_constraint")
    max_price = aff_data.get("max_property_price")
    stress_installment = aff_data.get("stress_installment")
    down_payment = aff_data.get("down_payment")

    if affordable:
        if max_price and max_price > price:
            headroom = ((max_price - price) / max_price) * 100
            verdict = f"AFFORDABLE — {headroom:.0f}% headroom below your max"
        else:
            verdict = "AFFORDABLE — within limits"
        rec = "You can comfortably service this loan under MAS stress-test rates."
        risk = "LOW"
    else:
        verdict = f"NOT AFFORDABLE — fails {constraint or 'MAS requirements'}"
        if max_price:
            rec = f"Your max affordable price is ${max_price:,.0f}. This property exceeds that by ${price - max_price:,.0f}."
        else:
            rec = "Consider a lower price, longer tenure, or higher down payment."
        risk = "CRITICAL"

    return {
        "verdict": verdict,
        "affordable": affordable,
        "binding_constraint": constraint,
        "max_affordable_price": max_price,
        "stress_tested_installment": stress_installment,
        "down_payment_required": down_payment,
        "recommendation": rec,
        "risk_level": risk,
    }


def _assess_tenure(tenure: Optional[str], top_year: Optional[int]) -> dict:
    """Assess tenure risk."""
    if not tenure:
        return {"verdict": "Tenure not specified"}

    current_year = 2026
    tenure_lower = tenure.lower()

    if "freehold" in tenure_lower or "999" in tenure_lower:
        return {
            "verdict": f"{tenure} — no meaningful lease decay",
            "lease_remaining": "Permanent / very long",
            "risk_level": "LOW",
            "recommendation": "Freehold/999-year retains value long-term. Premium asset quality.",
        }

    if "99" in tenure_lower and top_year:
        lease_remaining = 99 - (current_year - top_year)
        if lease_remaining < 30:
            return {
                "verdict": f"{tenure} — only {lease_remaining} years remaining",
                "lease_remaining": lease_remaining,
                "risk_level": "HIGH",
                "recommendation": "CRITICAL: Banks may not finance. CPF usage restricted. Resale will be difficult.",
            }
        elif lease_remaining < 60:
            return {
                "verdict": f"{tenure} — {lease_remaining} years remaining",
                "lease_remaining": lease_remaining,
                "risk_level": "MEDIUM",
                "recommendation": "Lease decay is accelerating. Factor in value depreciation over your holding period.",
            }
        else:
            return {
                "verdict": f"{tenure} — {lease_remaining} years remaining (healthy)",
                "lease_remaining": lease_remaining,
                "risk_level": "LOW",
                "recommendation": "Adequate lease remaining for financing and resale.",
            }

    return {"verdict": tenure, "risk_level": "UNKNOWN"}


def _generate_verdict(price_assessment, yield_assessment, location_assessment,
                      affordability_assessment, tenure_assessment, buyer_notes: Optional[str]) -> dict:
    """Generate the one-paragraph plain-English verdict."""

    parts = []
    risk_flags = []
    strengths = []

    # Price
    if price_assessment.get("risk_level") == "HIGH":
        risk_flags.append("OVERPRICED")
    elif price_assessment.get("premium_to_median_pct", 0) and price_assessment["premium_to_median_pct"] < -5:
        strengths.append("priced below comparable transactions")

    # Yield
    gy = yield_assessment.get("gross_yield_pct")
    if gy:
        if gy >= 3.5:
            strengths.append(f"strong rental yield of {gy}%")
        elif gy < 2.0:
            risk_flags.append("WEAK_RENTAL_YIELD")

    # Affordability
    if affordability_assessment.get("risk_level") == "CRITICAL":
        risk_flags.append("NOT_AFFORDABLE")
    elif affordability_assessment.get("affordable"):
        strengths.append("passes MAS affordability requirements")

    # Location
    region = location_assessment.get("region")
    if region == "CCR":
        strengths.append("prime central location")
    mrt = location_assessment.get("nearest_mrt", [])
    if mrt and isinstance(mrt[0].get("walking_time_minutes"), (int, float)):
        if mrt[0]["walking_time_minutes"] <= 5:
            strengths.append("excellent MRT access")

    # Tenure
    if tenure_assessment.get("risk_level") == "HIGH":
        risk_flags.append("LEASE_RISK")
    elif "freehold" in tenure_assessment.get("verdict", "").lower():
        strengths.append("freehold tenure")

    # Build verdict paragraph
    if risk_flags:
        if "NOT_AFFORDABLE" in risk_flags:
            verdict = "DO NOT PROCEED without restructuring finances. "
        elif "LEASE_RISK" in risk_flags:
            verdict = "HIGH RISK. Lease decay is a serious concern. "
        else:
            verdict = "PROCEED WITH CAUTION. "

        verdict += "Key concerns: " + ", ".join(r.replace("_", " ").lower() for r in risk_flags) + "."
        if strengths:
            verdict += " Despite concerns, this property offers: " + ", ".join(strengths) + "."
        recommendation = "NEGOTIATE or WALK AWAY"
    elif strengths:
        verdict = "THIS PROPERTY HAS MERIT. "
        verdict += "Key strengths: " + ", ".join(strengths) + "."
        recommendation = "WORTH PURSUING — conduct physical inspection and due diligence"
    else:
        verdict = "NEUTRAL ASSESSMENT. This property is fairly priced with no significant red flags or standout advantages."
        recommendation = "Standard due diligence applies"

    # Add custom note if buyer has specific goals
    if buyer_notes:
        verdict += f" Note: Buyer's specific concern — {buyer_notes}. Recommend addressing this with the seller's agent."

    return {
        "summary": verdict,
        "recommendation": recommendation,
        "risk_flags": risk_flags,
        "strengths": strengths,
        "confidence": "HIGH" if len(strengths) + len(risk_flags) >= 3 else "MEDIUM",
    }


def _estimate_upfront_costs(price: float, stamp_duty_total: Optional[float], buyer_profile: str) -> dict:
    """Estimate total upfront costs."""
    # Down payment: 20% for HDB loan, 25% for bank loan
    # Use 25% as conservative estimate
    down_payment_pct = 0.25
    down_payment = price * down_payment_pct

    # Minimum cash component: 5% for bank loans
    cash_min = price * 0.05
    cpf_eligible = down_payment - cash_min

    # BSD is payable in cash or CPF
    bsd = stamp_duty_total or 0

    # Legal fees (estimate)
    legal = 2500 if price < 1000000 else 3500

    # Valuation fee
    valuation = 500

    total = down_payment + bsd + legal + valuation
    total_cash = cash_min + bsd + legal + valuation  # worst case: BSD in cash
    total_cpf = cpf_eligible  # CPF can cover most of down payment + BSD

    return {
        "down_payment_25pct": round(down_payment, 2),
        "minimum_cash_component": round(cash_min, 2),
        "cpf_eligible_for_downpayment": round(cpf_eligible, 2),
        "stamp_duty": round(bsd, 2),
        "legal_fees_est": legal,
        "valuation_fee_est": valuation,
        "total_upfront": round(total, 2),
        "total_cash_worst_case": round(total_cash, 2),
        "note": "Cash component can be reduced by using CPF OA for stamp duty and down payment (subject to CPF rules)",
    }


@router.post("/property/pitch")
async def property_pitch(req: PitchRequest):
    """
    Generate a complete property investment pitch.

    Returns a structured investment thesis that a property agent can
    present to a client: price fairness, stamp duty, affordability,
    rental yield, location quality, tenure risk, and a plain-English verdict.

    Works with HDB data now. Private property data requires URA API access.
    """

    pitch = {
        "generated_at": datetime.now().strftime("%Y-%m-%d"),
        "property": {
            "type": req.property_type,
            "price": req.property_price,
            "town": req.town,
            "flat_type": req.flat_type,
            "project_name": req.project_name,
            "postal_code": req.postal_code,
            "sqft": req.sqft,
            "tenure": req.tenure,
            "top_year": req.top_year,
        },
        "buyer": {
            "profile": req.buyer_profile.value,
            "property_count": req.property_count,
            "monthly_income": req.monthly_income,
        },
        "price_per_sqft": None,
        "price_assessment": None,
        "stamp_duty": None,
        "affordability": None,
        "rental_yield": None,
        "location": None,
        "tenure_assessment": None,
        "upfront_costs": None,
        "verdict": None,
    }

    # PSF
    psf = _compute_psf(req.property_price, req.sqft)
    pitch["price_per_sqft"] = psf

    # 1. Stamp duty
    sd = await _internal_post("/stamp-duty", {
        "price": req.property_price,
        "property_type": "residential",
        "buyer_profile": req.buyer_profile.value,
        "property_count": req.property_count,
    })
    if sd and not sd.get("error"):
        pitch["stamp_duty"] = {
            "bsd": sd.get("bsd"),
            "absd": sd.get("absd"),
            "total": sd.get("total_stamp_duty"),
            "effective_rate_pct": sd.get("effective_rate_percent"),
            "source": "IRAS rates, verified",
        }

    # 2. Transaction comparables + price fairness
    median_price = None
    if req.property_type == "hdb" and req.town:
        median = await _internal_get(f"/hdb/median/{req.town}")
        if median and not median.get("error"):
            flat_types = median.get("flat_types", [])
            for ft in flat_types:
                if req.flat_type and ft.get("type", "").upper() == req.flat_type.upper():
                    median_price = ft.get("median_price")
                    break
            if not median_price and flat_types:
                median_price = flat_types[0].get("median_price")

            pitch["transaction_comparables"] = {
                "town": median.get("town"),
                "source": median.get("source", "data.gov.sg"),
                "flat_types": flat_types[:5],
            }
    elif req.property_type.lower() in ("private", "condo", "landed"):
        pitch["transaction_comparables"] = {
            "note": "Private property transaction data requires URA API access. Currently unavailable.",
            "source": "URA (pending API registration)",
        }

    pitch["price_assessment"] = _assess_price_fairness(req.property_price, median_price, req.flat_type, req.town)

    # 3. Rental yield
    pitch["rental_yield"] = _assess_yield(req.monthly_rent, req.property_price)

    # 4. Affordability
    if req.monthly_income:
        loan_type = "bank_hdb" if req.property_type == "hdb" else "bank_private"
        aff = await _internal_post("/affordability/calculate", {
            "monthly_income": req.monthly_income,
            "existing_monthly_debt": req.existing_monthly_debt,
            "property_price": req.property_price,
            "loan_type": loan_type,
        })
        pitch["affordability"] = _assess_affordability(aff, req.property_price)
    else:
        pitch["affordability"] = {"verdict": "No income data provided — affordability not assessed"}

    # 5. Location intelligence
    if req.postal_code:
        loc = await _internal_get(f"/address/{req.postal_code}")
        nearest_mrt = loc.get("nearest_mrt_stations", [])[:3]
        pitch["location"] = _assess_location({
            "district": loc.get("district_number"),
            "planning_area": loc.get("planning_area"),
            "region": loc.get("market_region"),
            "nearest_mrt": nearest_mrt,
        })
    else:
        pitch["location"] = {"verdict": "No postal code provided — location intelligence unavailable"}

    # 6. Tenure assessment
    pitch["tenure_assessment"] = _assess_tenure(req.tenure, req.top_year)

    # 7. Upfront costs
    sd_total = pitch.get("stamp_duty", {}).get("total") if pitch.get("stamp_duty") else None
    pitch["upfront_costs"] = _estimate_upfront_costs(req.property_price, sd_total, req.buyer_profile.value)

    # 8. Generate verdict
    pitch["verdict"] = _generate_verdict(
        pitch["price_assessment"],
        pitch["rental_yield"],
        pitch["location"],
        pitch["affordability"],
        pitch["tenure_assessment"],
        req.buyer_notes,
    )

    return pitch
