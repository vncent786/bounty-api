"""
Property Ranking API.

Accepts candidate properties from ANY source (user input, web search,
Apify scrapers, listing portals) and enriches + ranks them using
Bounty's verified data layer.

Architecture note: region parameter baked in from day one.
Currently only region=SG returns data. The interface is global;
the data coverage is regional and expanding.

This is the workflow endpoint that makes Bounty a decision layer,
not just a data wrapper. An agent can gather listings from anywhere
and use Bounty to answer "which one is actually the best?"
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime
import httpx
import asyncio
import os

router = APIRouter(tags=["Property Ranking"])

API_BASE = "https://bountyapi.com"


SUPPORTED_REGIONS = ["SG"]


class Region(str, Enum):
    SG = "SG"
    HK = "HK"
    AE = "AE"
    AU = "AU"
    JP = "JP"


class CandidateProperty(BaseModel):
    name: Optional[str] = Field(default=None, description="Property/project name or address")
    property_type: str = Field(default="hdb", description="hdb, private, condo, landed")
    price: float = Field(..., gt=0, description="Asking price in local currency")
    town: Optional[str] = Field(default=None, description="Town or neighborhood (e.g. 'TAMPINES', 'Orchard')")
    flat_type: Optional[str] = Field(default=None, description="Flat type for HDB (e.g. '4 ROOM') or size for private (e.g. '3BR')")
    postal_code: Optional[str] = Field(default=None, description="Postal code for location intelligence")
    monthly_rent: Optional[float] = Field(default=None, gt=0, description="Expected monthly rent (optional, for yield analysis)")
    notes: Optional[str] = Field(default=None, description="Any user notes about this property")


class RankRequest(BaseModel):
    region: Region = Field(default=Region.SG, description="Region/country code. SG supported now. HK, AE, AU, JP coming.")
    candidates: List[CandidateProperty] = Field(..., min_length=1, max_length=50, description="Candidate properties to evaluate and rank")
    buyer_profile: str = Field(default="SC", description="Buyer profile: SC, SPR, FR, entity")
    property_count: int = Field(default=1, ge=1, description="Number of properties owned including this one")
    monthly_income: Optional[float] = Field(default=None, gt=0, description="Gross monthly income for affordability analysis")
    existing_monthly_debt: float = Field(default=0, ge=0, description="Existing monthly debt obligations")
    weights: Optional[dict] = Field(default=None, description="Custom scoring weights. Keys: value, yield, affordability, location. All should sum to 1.0.")


DEFAULT_WEIGHTS = {
    "value": 0.35,        # discount/premium vs transaction comps
    "yield": 0.25,        # rental yield
    "affordability": 0.20, # TDSR/MSR pass + headroom
    "location": 0.20,     # MRT proximity + region classification
}


async def _call_internal(path: str, method: str = "GET", json_body: dict = None, timeout: int = 15) -> dict:
    """Call another Bounty API endpoint internally (bypasses x402).

    Uses INTERNAL_API_BASE if set; otherwise localhost:$PORT. Railway exposes
    the actual runtime port via PORT, so avoid hard-coding 8000.
    """
    base = os.environ.get("INTERNAL_API_BASE") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
        if method == "GET":
            r = await client.get(url)
        else:
            r = await client.post(url, json=json_body)
        if r.status_code == 200:
            return r.json()
        return {}


async def _enrich_candidate(candidate: CandidateProperty, req: RankRequest) -> dict:
    """Enrich a single candidate with Bounty data."""
    enriched = {
        "input": candidate.model_dump(),
        "stamp_duty": None,
        "comparables": None,
        "rental_analysis": None,
        "affordability": None,
        "location": None,
        "scores": {},
    }

    tasks = []

    # 1. Stamp duty
    async def get_stamp_duty():
        try:
            sd = await _call_internal("/stamp-duty", "POST", {
                "price": candidate.price,
                "property_type": "residential",
                "buyer_profile": req.buyer_profile,
                "property_count": req.property_count,
            })
            enriched["stamp_duty"] = {
                "bsd": sd.get("bsd"),
                "absd": sd.get("absd"),
                "total": sd.get("total_stamp_duty"),
                "effective_rate": sd.get("effective_rate_percent"),
            }
        except Exception:
            pass

    # 2. HDB comparables
    async def get_comps():
        if candidate.town and candidate.property_type.lower() in ("hdb", "exec"):
            try:
                median = await _call_internal(f"/hdb/median/{candidate.town}")
                flat_types = median.get("flat_types", [])

                # Find matching comp
                comp_median = None
                if candidate.flat_type:
                    for ft in flat_types:
                        if ft.get("type", "").upper() == candidate.flat_type.upper():
                            comp_median = ft.get("median_price")
                            break
                else:
                    # Use first available
                    if flat_types:
                        comp_median = flat_types[0].get("median_price")

                enriched["comparables"] = {
                    "town": candidate.town,
                    "median_price": comp_median,
                    "premium_to_median": None,
                    "flat_types": flat_types[:5],
                }
                if comp_median and candidate.price:
                    premium = ((candidate.price - comp_median) / comp_median) * 100
                    enriched["comparables"]["premium_to_median"] = round(premium, 1)
            except Exception:
                pass

    # 3. Rental yield
    async def get_yield():
        if candidate.monthly_rent:
            try:
                y = await _call_internal("/rental-yield/calculate", "POST", {
                    "property_price": candidate.price,
                    "monthly_rent": candidate.monthly_rent,
                })
                enriched["rental_analysis"] = {
                    "gross_yield": y.get("gross_yield_percent"),
                    "net_yield": y.get("net_yield_percent"),
                    "monthly_cashflow": y.get("monthly_cashflow"),
                }
            except Exception:
                pass

    # 4. Affordability
    async def get_affordability():
        if req.monthly_income:
            try:
                loan_type = "bank_hdb" if candidate.property_type.lower() == "hdb" else "bank_private"
                aff = await _call_internal("/affordability/calculate", "POST", {
                    "monthly_income": req.monthly_income,
                    "existing_monthly_debt": req.existing_monthly_debt,
                    "property_price": candidate.price,
                    "loan_type": loan_type,
                })
                enriched["affordability"] = {
                    "affordable": aff.get("affordable"),
                    "binding_constraint": aff.get("binding_constraint"),
                    "stress_installment": aff.get("monthly_installment_at_stress"),
                    "max_property_price": aff.get("max_affordable", {}).get("max_property_price"),
                    "down_payment": aff.get("down_payment", {}).get("required_amount"),
                }
            except Exception:
                pass

    # 5. Location
    async def get_location():
        if candidate.postal_code:
            try:
                loc = await _call_internal(f"/address/{candidate.postal_code}")
                nearest_mrt = loc.get("nearest_mrt_stations", [])[:2]
                enriched["location"] = {
                    "district": loc.get("district_number"),
                    "planning_area": loc.get("planning_area"),
                    "region": loc.get("market_region"),
                    "nearest_mrt": nearest_mrt,
                    "closest_mrt_walk_min": nearest_mrt[0]["walking_time_minutes"] if nearest_mrt else None,
                }
            except Exception:
                pass

    # Run all enrichments in parallel
    await asyncio.gather(
        get_stamp_duty(),
        get_comps(),
        get_yield(),
        get_affordability(),
        get_location(),
        return_exceptions=True,
    )

    return enriched


def _score_candidate(enriched: dict, weights: dict, candidate: CandidateProperty) -> dict:
    """Score a single candidate 0-100 across dimensions."""
    scores = {}
    details = {}

    # Value score: based on premium/discount to transaction comps
    comps = enriched.get("comparables")
    if comps and comps.get("premium_to_median") is not None:
        premium = comps["premium_to_median"]
        # Below median = high score, above median = low score
        # 0% premium = 50, -20% = 100, +20% = 0
        value_score = max(0, min(100, 50 - premium * 2.5))
        scores["value"] = round(value_score, 1)
        details["premium_to_median"] = f"{premium:+.1f}%"
    else:
        scores["value"] = None
        details["value_note"] = "No comparable transaction data"

    # Yield score
    rental = enriched.get("rental_analysis")
    if rental and rental.get("gross_yield"):
        gy = rental["gross_yield"]
        # 4%+ = excellent, 3% = decent, <2% = poor
        yield_score = max(0, min(100, gy * 20))
        scores["yield"] = round(yield_score, 1)
        details["gross_yield"] = f"{gy}%"
    else:
        scores["yield"] = None
        details["yield_note"] = "No rental data provided"

    # Affordability score
    aff = enriched.get("affordability")
    if aff:
        if aff.get("affordable"):
            # Score based on headroom: how much below max affordable price?
            max_price = aff.get("max_property_price", 0)
            if max_price > 0:
                headroom_pct = ((max_price - candidate.price) / max_price) * 100
                aff_score = max(50, min(100, 50 + headroom_pct))
            else:
                aff_score = 60
            scores["affordability"] = round(aff_score, 1)
            details["affordable"] = True
            details["headroom"] = f"{headroom_pct:.0f}%" if max_price > 0 else "N/A"
        else:
            scores["affordability"] = 0
            details["affordable"] = False
            details["constraint"] = aff.get("binding_constraint")
    else:
        scores["affordability"] = None
        details["affordability_note"] = "No income data provided"

    # Location score
    loc = enriched.get("location")
    if loc:
        region = loc.get("region", "")
        walk = loc.get("closest_mrt_walk_min")
        # CCR = premium location, RCR = good, OCR = suburban
        region_base = {"CCR": 70, "RCR": 60, "OCR": 45}.get(region, 50)
        # Adjust for MRT proximity
        if walk is not None:
            mrt_bonus = max(0, 30 - walk)  # up to +30 for <1min, 0 for 30+min
            loc_score = max(0, min(100, region_base + mrt_bonus))
        else:
            loc_score = region_base
        scores["location"] = round(loc_score, 1)
        details["region"] = region
        details["nearest_mrt_walk"] = f"{walk} min" if walk else "Unknown"
    else:
        scores["location"] = None
        details["location_note"] = "No postal code provided"

    # Compute weighted total (only from dimensions with data)
    total_weight = 0
    weighted_sum = 0
    for dim, weight in weights.items():
        if scores.get(dim) is not None:
            weighted_sum += scores[dim] * weight
            total_weight += weight

    overall = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0

    return {
        "dimension_scores": scores,
        "overall_score": overall,
        "score_details": details,
    }


@router.post("/property/rank")
async def rank_properties(req: RankRequest):
    """
    Rank candidate properties by investment value.

    Accepts properties from ANY source (user, web search, Apify, listing portals)
    and enriches each with stamp duty, transaction comps, rental yield, affordability,
    and location intelligence. Returns a ranked list with transparent scores.

    Region parameter supports future expansion. Currently only SG returns data.

    Scoring weights (customizable):
    - value (35%): premium/discount vs recent transaction comps
    - yield (25%): gross rental yield
    - affordability (20%): TDSR/MSR headroom
    - location (20%): MRT proximity + market region
    """
    if req.region.value not in SUPPORTED_REGIONS:
        return {
            "error": f"Region '{req.region.value}' not yet supported.",
            "supported_regions": SUPPORTED_REGIONS,
            "roadmap": ["SG (live)", "HK (planned)", "AE (planned)", "AU (planned)"],
            "message": f"Bounty currently covers Singapore property data. {req.region.value} is on the roadmap. Follow @bountyapi for updates.",
        }

    weights = req.weights if req.weights else DEFAULT_WEIGHTS

    # Validate weights
    total_w = sum(weights.values())
    if abs(total_w - 1.0) > 0.01:
        return {
            "error": f"Scoring weights must sum to 1.0. Current sum: {total_w}",
            "provided_weights": weights,
        }

    # Enrich all candidates in parallel
    enrich_tasks = [_enrich_candidate(c, req) for c in req.candidates]
    enriched_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)

    # Score each candidate
    scored = []
    for i, (candidate, enriched) in enumerate(zip(req.candidates, enriched_results)):
        if isinstance(enriched, Exception):
            scored.append({
                "rank": None,
                "name": candidate.name or f"Property {i+1}",
                "error": str(enriched),
                "overall_score": 0,
            })
            continue

        score_data = _score_candidate(enriched, weights, candidate)

        # Calculate total upfront cost
        upfront = candidate.price * 0.25  # min down payment approx
        if enriched.get("stamp_duty", {}).get("total"):
            upfront += enriched["stamp_duty"]["total"]

        scored.append({
            "name": candidate.name or f"Property {i+1}",
            "property_type": candidate.property_type,
            "asking_price": candidate.price,
            "town": candidate.town,
            "overall_score": score_data["overall_score"],
            "dimension_scores": score_data["dimension_scores"],
            "score_details": score_data["score_details"],
            "stamp_duty_total": enriched.get("stamp_duty", {}).get("total") if enriched.get("stamp_duty") else None,
            "estimated_upfront_cost": round(upfront, 2),
            "comparables": enriched.get("comparables"),
            "rental_analysis": enriched.get("rental_analysis"),
            "affordability": enriched.get("affordability"),
            "location": enriched.get("location"),
            "risk_flags": _generate_risk_flags(enriched, candidate),
        })

    # Sort by overall score descending
    scored.sort(key=lambda x: x.get("overall_score", 0), reverse=True)

    # Assign ranks
    for rank, item in enumerate(scored, 1):
        item["rank"] = rank

    return {
        "region": req.region.value,
        "total_candidates": len(req.candidates),
        "buyer_profile": req.buyer_profile,
        "scoring_weights": weights,
        "rankings": scored,
        "best_pick": scored[0] if scored else None,
        "methodology": {
            "value": "Compares asking price to median transaction prices from government data. Below median = higher score.",
            "yield": "Gross rental yield. 4%+ = excellent for Singapore. Below 2% = poor.",
            "affordability": "MAS TDSR/MSR framework. Checks if borrower can afford the loan under stress-tested rates.",
            "location": "Market region classification (CCR/RCR/OCR) + MRT walking proximity.",
        },
        "source": "Composite: IRAS, data.gov.sg, MAS TDSR/MSR, URA planning areas, LTA MRT data",
        "analyzed_at": datetime.now().strftime("%Y-%m-%d"),
        "regions_supported": SUPPORTED_REGIONS,
        "regions_roadmap": ["HK", "AE", "AU", "JP"],
    }


def _generate_risk_flags(enriched: dict, candidate: CandidateProperty) -> List[str]:
    """Generate risk flags for a property."""
    flags = []

    comps = enriched.get("comparables")
    if comps and comps.get("premium_to_median") is not None:
        if comps["premium_to_median"] > 10:
            flags.append("PRICED_ABOVE_MEDIAN")
        elif comps["premium_to_median"] > 20:
            flags.append("SIGNIFICANTLY_OVERPRICED")

    rental = enriched.get("rental_analysis")
    if rental and rental.get("gross_yield"):
        if rental["gross_yield"] < 2.0:
            flags.append("LOW_YIELD")

    aff = enriched.get("affordability")
    if aff and aff.get("affordable") is False:
        flags.append("NOT_AFFORDABLE")

    loc = enriched.get("location")
    if loc and loc.get("closest_mrt_walk_min"):
        if loc["closest_mrt_walk_min"] > 15:
            flags.append("FAR_FROM_MRT")

    return flags
