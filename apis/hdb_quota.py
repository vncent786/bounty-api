"""
HDB Ethnic Integration Policy (EIP) & SPR quota checker.

Singapore's EIP limits the proportion of flats in each HDB block and
neighborhood that can be owned by each ethnic group. This affects resale
because buyers from certain groups may be unable to purchase in certain blocks.

The actual real-time availability requires the HDB e-info portal
(services2.hdb.gov.sg), which is behind Cloudflare. This endpoint provides:
- EIP/SAPP rules and quota calculations
- Clear explanation of what buyers need to check
- Links to the HDB portal for real-time verification
- Impact assessment for property transactions

Free endpoint.
"""

from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime
import math

router = APIRouter(tags=["HDB EIP Quota"])

# ============================================================
# EIP Limits — Source: HDB Ethnic Integration Policy
# https://www.hdb.gov.sg/cs/infoweb/residential/buying-a-flat/resale/ethnic-integration-policy-and-spr-quota
# Verified: rates unchanged since policy implementation
# ============================================================

# Neighborhood (town) level limits
EIP_NEIGHBORHOOD_LIMITS = {
    "Malay": 0.25,           # 25% at neighborhood level
    "Chinese": 0.87,         # 87% at neighborhood level
    "Indian & Others": 0.13, # 13% at neighborhood level
}

# Block level limits
EIP_BLOCK_LIMITS = {
    "Malay": 0.22,           # 22% at block level
    "Chinese": 0.84,         # 84% at block level
    "Indian & Others": 0.10, # 10% at block level
}

# SPR quota: max 5% of flats in a block can be owned by SPR households
SPR_BLOCK_LIMIT = 0.05

# Non-Malaysian SPR: max 8% at neighborhood, 5% at block
SPR_NEIGHBORHOOD_LIMIT = 0.08


@router.get("/hdb/eip/{town}")
async def eip_check_town(
    town: str,
    buyer_ethnicity: str = Query(..., description="Malay, Chinese, Indian, or Others"),
    is_spr: bool = Query(False, description="Whether buyer is a non-Malaysian SPR"),
    is_malaysian_spr: bool = Query(False, description="Whether buyer is a Malaysian SPR"),
):
    """Check HDB Ethnic Integration Policy (EIP) limits for a Singapore town.

    The EIP ensures a balanced ethnic mix in HDB estates. Buyers from certain
    ethnic groups may be restricted from purchasing flats in blocks/neighborhoods
    where their group's quota is full.

    This endpoint provides:
    - The maximum percentage of units available to the buyer's ethnic group
    - Whether the buyer faces EIP restrictions (qualitative)
    - SPR quota information if applicable

    NOTE: Real-time availability requires checking the HDB e-info portal:
    https://services2.hdb.gov.sg/webapp/BB31EINFO/

    Free endpoint.
    """
    town = town.strip().upper()
    buyer_ethnicity = buyer_ethnicity.strip().title()

    # Normalize ethnicity
    ethnicity_map = {
        "malay": "Malay",
        "chinese": "Chinese",
        "indian": "Indian & Others",
        "others": "Indian & Others",
        "eurasian": "Indian & Others",
        "arab": "Indian & Others",
    }
    ethnic_group = ethnicity_map.get(buyer_ethnicity.lower(), buyer_ethnicity)

    if ethnic_group not in EIP_BLOCK_LIMITS:
        return {
            "error": f"Unknown ethnicity '{buyer_ethnicity}'. Use: Malay, Chinese, Indian, or Others.",
        }

    # Compute limits
    block_limit_pct = EIP_BLOCK_LIMITS[ethnic_group] * 100
    neighborhood_limit_pct = EIP_NEIGHBORHOOD_LIMITS[ethnic_group] * 100

    result = {
        "town": town,
        "buyer_ethnicity": buyer_ethnicity,
        "ethnic_group_eip": ethnic_group,
        "queried_at": datetime.now().strftime("%Y-%m-%d"),
        "eip_limits": {
            "block_level_max_pct": round(block_limit_pct, 1),
            "neighborhood_level_max_pct": round(neighborhood_limit_pct, 1),
            "explanation": f"In {town}, {ethnic_group} buyers can own up to {block_limit_pct:.0f}% of units in any single HDB block, and {neighborhood_limit_pct:.0f}% at the neighborhood level.",
        },
        "what_this_means": _explain_eip_impact(ethnic_group),
        "how_to_check_realtime": {
            "portal_url": "https://services2.hdb.gov.sg/webapp/BB31EINFO/",
            "instructions": "Enter the block/town to check current ethnic quota availability. The HDB portal shows real-time counts of available units per ethnic group per block.",
            "note": "Bounty cannot access real-time HDB quota data (HDB portal blocks API access). Agents should verify availability before showing units to clients.",
        },
        "source": "HDB Ethnic Integration Policy (hdb.gov.sg)",
        "source_url": "https://www.hdb.gov.sg/cs/infoweb/residential/buying-a-flat/resale/ethnic-integration-policy-and-spr-quota",
    }

    # SPR quota
    if is_spr and not is_malaysian_spr:
        result["spr_quota"] = {
            "block_limit_pct": SPR_BLOCK_LIMIT * 100,
            "neighborhood_limit_pct": SPR_NEIGHBORHOOD_LIMIT * 100,
            "explanation": f"Non-Malaysian SPR households are limited to {SPR_BLOCK_LIMIT*100:.0f}% of units per block. In addition to EIP, this quota may further restrict purchasing options.",
            "note": "Malaysian SPRs are exempt from the SPR quota (treated same as SC for housing purposes).",
        }
    elif is_malaysian_spr:
        result["spr_quota"] = {
            "exempt": True,
            "explanation": "Malaysian SPRs are exempt from the SPR quota. Only EIP applies.",
        }

    # Transaction impact
    result["transaction_impact"] = _transaction_impact(ethnic_group, is_spr, is_malaysian_spr)

    return result


def _explain_eip_impact(ethnic_group: str) -> dict:
    """Explain what EIP means for the buyer."""
    impact = {
        "Malay": {
            "typical_impact": "Low in most estates. Malay quota is rarely full outside of predominantly Malay areas.",
            "high_risk_areas": "Geylang, Bedok, Tampines (historically higher Malay concentration)",
            "agent_advice": "Usually not a concern. Check block-level if buying in predominantly Malay estates.",
        },
        "Chinese": {
            "typical_impact": "Very low. Chinese quota is 84-87% and rarely reached.",
            "high_risk_areas": "Almost never an issue",
            "agent_advice": "EIP almost never blocks Chinese buyers. Focus on other factors.",
        },
        "Indian & Others": {
            "typical_impact": "Highest risk. Indian/Others quota is only 10-13% per block.",
            "high_risk_areas": "Little India area, Serangoon, neighborhoods with high Indian/Others concentration",
            "agent_advice": "Always check block-level availability before showing units. This is the group most affected by EIP.",
        },
    }
    return impact.get(ethnic_group, {"typical_impact": "Unknown", "agent_advice": "Check HDB portal."})


def _transaction_impact(ethnic_group: str, is_spr: bool, is_malaysian_spr: bool) -> dict:
    """Assess transaction impact."""
    risk = "LOW"
    notes = []

    if ethnic_group == "Indian & Others":
        risk = "MEDIUM"
        notes.append("Indian/Others group has the tightest quota (10% per block). Always verify before marketing.")

    if is_spr and not is_malaysian_spr:
        risk = "HIGH" if risk == "MEDIUM" else "MEDIUM"
        notes.append("SPR quota (5% per block) further restricts options. Combined with EIP, some blocks may be fully unavailable.")

    if not notes:
        notes.append("No significant EIP/SPR restrictions expected. Verify on HDB portal for certainty.")

    return {
        "risk_level": risk,
        "notes": notes,
        "recommendation": "Check HDB e-info portal before proceeding with any transaction." if risk != "LOW" else "Low risk. Standard due diligence applies.",
    }
