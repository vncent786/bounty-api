"""
Job Search API — hiring signal aggregation for agents.

Scraper-first/zero-fixed-cost endpoint. Uses publicly available job feeds and
normalizes them into a compact response agents can use for hiring research,
lead generation, market maps, and company monitoring.

Current sources:
- Remote OK public API (requires attribution/linkback; source URL included)
- Hacker News Algolia API for "Who is hiring?" style posts

No fabricated salary/location data. Missing fields remain null.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/jobs", tags=["Job Search"])

REMOTEOK_URL = "https://remoteok.com/api"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
USER_AGENT = "BountyAPI/2.0 (+https://bountyapi.com; source attribution included)"


class JobResult(BaseModel):
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    currency: Optional[str] = None
    posted_at: Optional[str] = None
    url: str
    source: str
    source_terms: Optional[str] = None
    excerpt: Optional[str] = None


class JobsSearchResponse(BaseModel):
    query: str
    location: Optional[str]
    limit: int
    count: int
    generated_at: str
    results: list[JobResult]
    sources: list[dict[str, str]]
    notes: list[str]


def _match(text: str, query: str) -> bool:
    if not query:
        return True
    terms = [t.lower() for t in query.split() if t.strip()]
    lower = text.lower()
    return all(term in lower for term in terms)


def _location_match(job_location: Optional[str], wanted: Optional[str]) -> bool:
    if not wanted:
        return True
    if not job_location:
        return False
    return wanted.lower() in job_location.lower()


async def _fetch_remoteok(query: str, location: Optional[str], limit: int) -> list[JobResult]:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(REMOTEOK_URL)
        response.raise_for_status()
        data = response.json()

    results: list[JobResult] = []
    for item in data:
        if not isinstance(item, dict) or "legal" in item:
            continue

        title = str(item.get("position") or "").strip()
        company = str(item.get("company") or "").strip() or None
        loc = str(item.get("location") or "").strip() or None
        tags = [str(tag) for tag in item.get("tags", []) if tag]
        combined = " ".join([title, company or "", loc or "", " ".join(tags)])
        if not _match(combined, query) or not _location_match(loc, location):
            continue

        url = item.get("url") or item.get("apply_url") or item.get("slug")
        if isinstance(url, str) and url.startswith("remote-"):
            url = f"https://remoteok.com/remote-jobs/{url}"
        if not isinstance(url, str) or not url.startswith("http"):
            continue

        results.append(JobResult(
            title=title,
            company=company,
            location=loc,
            tags=tags,
            salary_min=item.get("salary_min") if isinstance(item.get("salary_min"), int) else None,
            salary_max=item.get("salary_max") if isinstance(item.get("salary_max"), int) else None,
            currency="USD" if item.get("salary_min") or item.get("salary_max") else None,
            posted_at=item.get("date") if isinstance(item.get("date"), str) else None,
            url=url,
            source="Remote OK",
            source_terms="Remote OK API terms require linkback/attribution. URL preserved in result.",
            excerpt=item.get("description")[:500] if isinstance(item.get("description"), str) else None,
        ))
        if len(results) >= limit:
            break
    return results


async def _fetch_hn(query: str, limit: int) -> list[JobResult]:
    params = {
        "tags": "story",
        "query": f"Who is hiring? {query}".strip(),
        "hitsPerPage": min(20, max(limit, 5)),
    }
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(HN_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    results: list[JobResult] = []
    for hit in data.get("hits", []):
        title = hit.get("title") or hit.get("story_title") or "Hacker News hiring thread"
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        text = hit.get("story_text") or hit.get("comment_text") or ""
        if query and not _match(" ".join([title, text]), query):
            continue
        results.append(JobResult(
            title=title,
            company=None,
            location=None,
            tags=["hacker-news", "hiring-thread"],
            posted_at=hit.get("created_at") if isinstance(hit.get("created_at"), str) else None,
            url=url,
            source="Hacker News Algolia",
            excerpt=text[:500] if isinstance(text, str) else None,
        ))
        if len(results) >= limit:
            break
    return results


@router.get("/search", response_model=JobsSearchResponse)
async def search_jobs(
    q: str = Query(..., min_length=1, max_length=120, description="Search query, e.g. 'AI engineer', 'sales Singapore', 'Python remote'"),
    location: Optional[str] = Query(None, max_length=80, description="Optional location substring filter"),
    limit: int = Query(10, ge=1, le=25, description="Maximum normalized results to return"),
):
    """Search job postings and hiring threads across public sources."""
    notes: list[str] = []
    results: list[JobResult] = []

    try:
        results.extend(await _fetch_remoteok(q, location, limit))
    except Exception as exc:
        notes.append(f"Remote OK unavailable: {type(exc).__name__}")

    if len(results) < limit:
        try:
            results.extend(await _fetch_hn(q, limit - len(results)))
        except Exception as exc:
            notes.append(f"Hacker News Algolia unavailable: {type(exc).__name__}")

    # Deduplicate by URL, preserve order.
    seen: set[str] = set()
    deduped: list[JobResult] = []
    for item in results:
        if item.url in seen:
            continue
        seen.add(item.url)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    return JobsSearchResponse(
        query=q,
        location=location,
        limit=limit,
        count=len(deduped),
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=deduped,
        sources=[
            {"name": "Remote OK", "url": "https://remoteok.com/api"},
            {"name": "Hacker News Algolia", "url": "https://hn.algolia.com/api"},
        ],
        notes=notes or ["No values are interpolated. Missing salary/location fields are returned as null."],
    )
