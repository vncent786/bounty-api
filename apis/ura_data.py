"""
URA Private Property Data — the #1 data moat for Singapore property.

This module provides access to URA's (Urban Redevelopment Authority) private property
data via their official developer API. This is the data that makes condo/private
property analysis work: caveat-level transactions, median rentals, developer sales,
and pipeline supply.

Authentication: 2-step
1. Use AccessKey to get a daily token via insertNewToken
2. Use daily token in the Token header for data calls

AccessKey is read from the URA_ACCESS_KEY environment variable.
Tokens are cached per-day and refreshed automatically.

Available datasets:
- PMI_Resi_Transaction: Private residential property transactions (caveat data)
- PMI_Resi_Rental: Private residential rental contracts
- PMI_Resi_Rental_Median: Median rentals by project name
- PMI_Resi_Developer_Sales: Developer units sold
- PMI_Resi_Pipeline: Future supply pipeline

Pricing strategy: $0.05/call (real exclusive data, significant compute)
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime, date
import os
import httpx
import json
import asyncio

router = APIRouter(tags=["URA Private Property"])

URA_ACCESS_KEY = os.environ.get("URA_ACCESS_KEY", "")
URA_TOKEN_URL = "https://eservice.ura.gov.sg/uraDataService/insertNewToken/v1"
URA_DATA_URL = "https://eservice.ura.gov.sg/uraDataService/invokeUraDS/v1"

# Token cache: {date_string: token_string}
_token_cache: dict = {}


async def _get_ura_token() -> str:
    """Get or refresh the daily URA API token."""
    today = date.today().isoformat()

    if _token_cache.get("date") == today and _token_cache.get("token"):
        return _token_cache["token"]

    if not URA_ACCESS_KEY:
        raise HTTPException(
            status_code=503,
            detail="URA_ACCESS_KEY not configured. This endpoint requires the URA developer API key.",
        )

    # URA's Layer 7 firewall blocks Python User-Agents. Must use a browser/curl UA.
    _ura_headers = {
        "AccessKey": URA_ACCESS_KEY,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(URA_TOKEN_URL, headers=_ura_headers)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"URA token request failed: {r.status_code}",
            )
        data = r.json()
        token = data.get("Result", "")
        if not token:
            raise HTTPException(
                status_code=502,
                detail=f"URA returned empty token: {data}",
            )

        _token_cache["date"] = today
        _token_cache["token"] = token
        return token


async def _call_ura_service(service: str, params: dict = None) -> dict:
    """Call a URA data service with token auth."""
    token = await _get_ura_token()

    url = f"{URA_DATA_URL}?service={service}"
    if params:
        for k, v in params.items():
            url += f"&{k}={v}"

    _ura_data_headers = {
        "Token": token,
        "AccessKey": URA_ACCESS_KEY,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=_ura_data_headers)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"URA data request failed: {r.status_code} - {r.text[:200]}",
            )
        return r.json()


def _is_ura_available() -> bool:
    """Check if URA API key is configured."""
    return bool(URA_ACCESS_KEY)


# ============================================================
# Endpoints
# ============================================================

@router.get("/ura/status")
async def ura_status():
    """Check if URA API is configured and get a quick health check.
    Free endpoint."""
    if not URA_ACCESS_KEY:
        return {
            "status": "not_configured",
            "message": "URA_ACCESS_KEY env var not set. Register at https://eservice.ura.gov.sg/maps/api/",
        }

    # Try to get a token to verify the key works
    try:
        token = await _get_ura_token()
        return {
            "status": "ok",
            "message": "URA API connected. Private property data is live.",
            "token_valid_for": date.today().isoformat(),
            "available_datasets": [
                "PMI_Resi_Transaction (private transactions)",
                "PMI_Resi_Rental (rental contracts)",
                "PMI_Resi_Rental_Median (median rentals by project)",
                "PMI_Resi_Developer_Sales (developer units sold)",
                "PMI_Resi_Pipeline (future supply)",
            ],
        }
    except HTTPException as e:
        return {"status": "error", "message": e.detail}


@router.get("/ura/transactions")
async def ura_transactions(
    batch: int = Query(1, ge=1, description="Batch number (results paginated)"),
):
    """Get private residential property transactions (caveat data) from URA.

    Returns transaction records including:
    - Project name, street, market segment (CCR/RCR/OCR)
    - Property type (condo, apt, landed, etc.)
    - Sale price, area (sqm/sqft), PSF
    - Transaction date, sale type (new sale, resale, sub-sale)
    - Tenure (freehold, 99yr, 999yr, etc.)

    Data is paginated in batches (~10,000 records per batch).

    Price: $0.05/call
    Source: URA Developer API (PMI_Resi_Transaction)
    """
    data = await _call_ura_service("PMI_Resi_Transaction", {"batch": batch})
    return _format_ura_response(data, "private_transactions", batch)


@router.get("/ura/rental-median")
async def ura_rental_median(
    project_name: Optional[str] = Query(None, description="Filter by project/condo name (case-insensitive partial match)"),
):
    """Get median rental data by project name from URA.

    Returns median rental rates ($psf/month) for private residential projects,
    broken down by property type (condo, apt, landed) and quarter.

    Price: $0.05/call
    Source: URA Developer API (PMI_Resi_Rental_Median)
    """
    data = await _call_ura_service("PMI_Resi_Rental_Median")
    records = _extract_records(data)

    if project_name:
        name_lower = project_name.lower()
        records = [r for r in records if name_lower in str(r).lower()]

    return {
        "total_records": len(records),
        "query": project_name or "all projects",
        "records": records[:100] if project_name else records[:50],  # cap for readability
        "source": "URA Developer API (PMI_Resi_Rental_Median)",
        "queried_at": datetime.now().strftime("%Y-%m-%d"),
        "note": "Median rental $psf/month by project, property type, and quarter. URA updates quarterly.",
    }


@router.get("/ura/developer-sales")
async def ura_developer_sales(
    ref_period: Optional[str] = Query(None, description="Reference period, e.g. '2506' for Jun 2025. Leave empty for latest."),
):
    """Get developer sales data (units sold by developers) from URA.

    Returns units launched and sold by project, including:
    - Project name, developer, location
    - Units launched, units sold, units remaining
    - Median price
    - Reference quarter

    Price: $0.05/call
    Source: URA Developer API (PMI_Resi_Developer_Sales)
    """
    params = {}
    if ref_period:
        params["refPeriod"] = ref_period
    data = await _call_ura_service("PMI_Resi_Developer_Sales", params or None)
    return _format_ura_response(data, "developer_sales")


@router.get("/ura/pipeline")
async def ura_pipeline():
    """Get future private residential supply pipeline from URA.

    Returns projects that are in the planning/construction pipeline:
    - Project name, location, developer
    - Number of units planned
    - Expected completion period
    - Project stage

    This tells buyers/investors about upcoming supply that could affect prices.

    Price: $0.05/call
    Source: URA Developer API (PMI_Resi_Pipeline)
    """
    data = await _call_ura_service("PMI_Resi_Pipeline")
    return _format_ura_response(data, "pipeline_supply")


@router.get("/ura/rental-contracts")
async def ura_rental_contracts(
    ref_period: Optional[str] = Query(None, description="Reference period, e.g. '25q1' for Q1 2025"),
):
    """Get private residential rental contract data from URA.

    Returns aggregate rental contract statistics by area and property type.

    Price: $0.05/call
    Source: URA Developer API (PMI_Resi_Rental)
    """
    params = {}
    if ref_period:
        params["refPeriod"] = ref_period
    data = await _call_ura_service("PMI_Resi_Rental", params or None)
    return _format_ura_response(data, "rental_contracts")


# ============================================================
# Helpers
# ============================================================

def _extract_records(data: dict) -> list:
    """Extract the Result records from URA response."""
    result = data.get("Result", [])
    if isinstance(result, list):
        return result
    return []


def _format_ura_response(data: dict, dataset_name: str, batch: int = None) -> dict:
    """Format URA API response into clean Bounty response."""
    records = _extract_records(data)
    return {
        "dataset": dataset_name,
        "total_records": len(records),
        "batch": batch,
        "records": records[:100],  # cap for readability
        "source": f"URA Developer API",
        "queried_at": datetime.now().strftime("%Y-%m-%d"),
    }
