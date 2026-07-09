"""
Singapore Salary Benchmark — aggregates real salary data from MyCareersFuture
(Singapore's official government job portal, run by SkillsFuture Singapore / WSG).

This is live job listing data with employer-posted salary ranges. Not self-reported.
Not estimated. Every number comes from an actual job posting.

Data source: api.mycareersfuture.gov.sg/v2/jobs (public, no API key required)

Free endpoint — high-frequency, drives MCP installation.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import httpx
import statistics
import math

router = APIRouter(tags=["SG Salary Benchmark"])

MCF_API = "https://api.mycareersfuture.gov.sg/v2/jobs"


async def _fetch_mcf_jobs(search: str, limit: int = 100) -> list:
    """Fetch job listings from MyCareersFuture API."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            MCF_API,
            params={"search": search, "limit": limit, "page": 0},
            headers={"User-Agent": "BountyAPI/1.0", "Accept": "application/json"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("results", [])


def _extract_salaries(jobs: list) -> list:
    """Extract salary ranges from job listings, filtering out hidden/zero salaries."""
    salaries = []
    for job in jobs:
        sal = job.get("salary", {})
        min_s = sal.get("minimum")
        max_s = sal.get("maximum")
        sal_type = sal.get("type", {}).get("salaryType", "Monthly")
        hidden = job.get("metadata", {}).get("isHideSalary", False)

        # Skip if salary is hidden or not disclosed
        if hidden or not min_s or not max_s:
            continue
        # Skip if salary is unreasonably low (data quality filter — some jobs post $1)
        if min_s < 100:
            continue

        salaries.append({
            "min": float(min_s),
            "max": float(max_s),
            "mid": (float(min_s) + float(max_s)) / 2,
            "type": sal_type,
            "title": job.get("title", ""),
            "company": (job.get("postedCompany") or {}).get("name", ""),
            "experience": job.get("minimumYearsExperience", 0),
            "employment_types": [et.get("employmentType", "") for et in job.get("employmentTypes", [])],
            "position_levels": [pl.get("positionLevel", "") for pl in job.get("positionLevels", [])],
        })
    return salaries


def _compute_stats(salaries: list, freq_type: str) -> dict:
    """Compute salary statistics from a list of salary dicts."""
    if not salaries:
        return None

    mins = [s["min"] for s in salaries]
    maxs = [s["max"] for s in salaries]
    mids = [s["mid"] for s in salaries]

    n = len(salaries)

    # Compute annual equivalents
    multiplier = 12 if freq_type.lower().startswith("month") else 1
    annual_mids = [m * multiplier for m in mids]

    return {
        "salary_type": freq_type,
        "sample_size": n,
        "min_observed": round(min(mins), 0),
        "max_observed": round(max(maxs), 0),
        "median_low": round(statistics.median(mins), 0),
        "median_high": round(statistics.median(maxs), 0),
        "median_midpoint": round(statistics.median(mids), 0),
        "p25_midpoint": round(_percentile(mids, 25), 0),
        "p75_midpoint": round(_percentile(mids, 75), 0),
        "mean_midpoint": round(statistics.mean(mids), 0),
        "stdev_midpoint": round(statistics.stdev(mids), 0) if n > 1 else 0,
        "annual_median_low": round(statistics.median(mins) * multiplier, 0),
        "annual_median_high": round(statistics.median(maxs) * multiplier, 0),
        "annual_median_midpoint": round(statistics.median(mids) * multiplier, 0),
    }


def _percentile(data: list, p: float) -> float:
    """Compute percentile of a list."""
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 0:
        return 0
    k = (n - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


@router.get("/salary/search")
async def salary_search(
    role: str = Query(..., description="Job title or keyword (e.g., 'equity analyst', 'software engineer')"),
    limit: int = Query(100, ge=10, le=200, description="Max job listings to scan"),
):
    """Benchmark salary for a Singapore role using live MyCareersFuture job postings.

    Returns median, percentile ranges, and annual equivalents computed from
    real employer-posted salary ranges. Data is from Singapore's official
    government job portal (MyCareersFuture / WSG).

    Free endpoint.
    """
    jobs = await _fetch_mcf_jobs(role, limit=limit)

    if not jobs:
        return {
            "role": role,
            "sample_size": 0,
            "error": "No job listings found for this search term. Try a broader keyword.",
            "source": "MyCareersFuture (api.mycareersfuture.gov.sg)",
            "queried_at": datetime.now().strftime("%Y-%m-%d"),
        }

    salaries = _extract_salaries(jobs)

    # Group by salary frequency type (Monthly vs Annually)
    monthly = [s for s in salaries if s["type"].lower().startswith("month")]
    annual = [s for s in salaries if s["type"].lower().startswith("annu") or s["type"] == "Annually"]
    other = [s for s in salaries if s not in monthly and s not in annual]

    result = {
        "role": role,
        "total_listings_found": len(jobs),
        "listings_with_salary": len(salaries),
        "listings_hiding_salary": len(jobs) - len(salaries),
        "queried_at": datetime.now().strftime("%Y-%m-%d"),
        "source": "MyCareersFuture (api.mycareersfuture.gov.sg) — live employer-posted data",
        "source_type": "Government job portal (WSG/SkillsFuture Singapore)",
        "note": "Salaries are from active job postings, not self-reported surveys. Ranges reflect what employers are currently offering.",
    }

    if monthly:
        result["monthly"] = _compute_stats(monthly, "Monthly")
    if annual:
        result["annually"] = _compute_stats(annual, "Annually")

    # Include sample listings for transparency
    sample = sorted(salaries, key=lambda x: x["mid"])[:3]  # bottom 3
    sample += sorted(salaries, key=lambda x: x["mid"])[-3:]  # top 3
    result["sample_postings"] = [
        {
            "title": s["title"][:80],
            "company": s["company"][:60],
            "salary_range": f"${int(s['min']):,}-${int(s['max']):,} {s['type']}",
            "min_experience_years": s["experience"],
        }
        for s in sample[:6]
    ]

    # Experience breakdown
    if salaries:
        junior = [s for s in salaries if s["experience"] <= 2]
        mid_level = [s for s in salaries if 3 <= s["experience"] <= 5]
        senior = [s for s in salaries if s["experience"] >= 6]

        exp_breakdown = {}
        for label, group in [("junior_0_2yr", junior), ("mid_3_5yr", mid_level), ("senior_6yr_plus", senior)]:
            if group:
                exp_breakdown[label] = {
                    "count": len(group),
                    "median_range": f"${int(statistics.median([s['min'] for s in group])):,}-${int(statistics.median([s['max'] for s in group])):,}",
                }
        if exp_breakdown:
            result["by_experience"] = exp_breakdown

    return result
