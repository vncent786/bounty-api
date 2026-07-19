"""
Social Trend Search API — multi-platform social media intelligence.

Aggregates public social data across Reddit, YouTube, and Instagram for a query,
returns a normalized feed with per-source health reporting.

This is the productized wrapper around the `social_scraper` package. Each source
is hit in parallel via a thread pool; the response includes a health report so
callers know which sources are alive vs degraded (we never silently return []
for a failed source).

Pricing: $0.05/call via x402. Margin: zero per-call cost (all sources are free),
100% margin. When TikTok is added later via TikHub, cost becomes $0.001/req =
50x margin at the same $0.05 price.

Import pattern::

    from apis.social_trends import router
"""
from __future__ import annotations

import asyncio
import functools
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/social",
    tags=["social-trends"],
    responses={422: {"description": "Validation error"}},
)


# ============================================================
# Models
# ============================================================

class TrendSearchResponse(BaseModel):
    """Normalized multi-platform social trend response."""
    query: str = Field(..., description="The search query.")
    fetched_at: str = Field(..., description="ISO timestamp of the fetch.")
    summary: dict = Field(
        ...,
        description=(
            "Aggregate counts: sources_total, sources_ok, sources_partial, "
            "sources_failed, content_items."
        ),
    )
    sources: dict = Field(
        ...,
        description=(
            "Per-source result keyed by source name. Each value has: status, "
            "method, count, items[], error, meta. status is one of "
            "ok|partial|error. Missing data is reported explicitly, never "
            "silently empty."
        ),
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Honest caveats: which sources are login-wall limited, which "
            "failed, what data is NOT included."
        ),
    )


# ============================================================
# Helpers
# ============================================================

def _run_sync(func, *args, **kwargs):
    """Run a sync function in a thread — safe to await from async route."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


# ============================================================
# Endpoints
# ============================================================

@router.get("/", summary="Social Trend Search API info")
async def info():
    """Return basic info about the social trend search endpoint."""
    return {
        "name": "Social Trend Search",
        "description": (
            "Multi-platform social media intelligence for a query. Aggregates "
            "public data from Reddit, YouTube, and Instagram with per-source "
            "health reporting."
        ),
        "endpoints": {
            "GET /social/trend-search": "Cross-platform trend search",
            "GET /social/": "This info endpoint",
        },
        "sources": {
            "reddit": "PullPush.io (no auth, public submissions)",
            "youtube": "yt-dlp search (no auth, video metadata + views)",
            "instagram": "Tags page (no auth, top creators + hashtag volume; login-wall limited to ~10 items)",
        },
        "pricing": "$0.05 per call (x402 micropayment)",
        "limits": "Login-wall limited on Instagram. TikTok/XHS/Douyin not yet integrated — pending TikHub API key.",
    }


@router.get(
    "/trend-search",
    response_model=TrendSearchResponse,
    summary="Cross-platform social trend search",
)
async def trend_search(
    q: str = Query(
        ...,
        min_length=2,
        max_length=200,
        description="Search query (keyword, brand, trend, hashtag).",
        examples=["dopamine detox"],
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=30,
        description="Max items per source. Default 10, max 30.",
    ),
):
    """
    Search Reddit, YouTube, and Instagram in parallel for a query.

    Returns a normalized feed with explicit per-source health. **Failed sources
    are reported, never silently empty.** This is the difference between
    "no results" and "the source is down".

    **What you get per platform:**

    - **Reddit** — top submissions matching the query (title, selftext, score,
      comment count, subreddit, URL, author).
    - **YouTube** — top videos (title, channel, view count, like count, comment
      count, upload date, URL).
    - **Instagram** — top creators posting under the matching hashtag (handle,
      follower count, caption, hashtag volume). Login-wall limited to ~10
      items; no per-post engagement counts.

    **What you DON'T get (yet):**

    - TikTok, XHS, Douyin (pending paid API integration)
    - Comments / replies (top-level posts only)
    - Per-post engagement on Instagram
    - Time-series velocity (single snapshot only)

    **Use cases:**

    - Marketers: discover dominant creators, language, and hooks in a niche.
    - Investors: detect consumer behavior shifts before mainstream coverage.
    - Founders: validate demand and surface customer pain language.
    """
    # Import inside the route so the API still loads if social_scraper is removed
    try:
        from social_scraper.audit import audit
    except Exception as e:
        return TrendSearchResponse(
            query=q,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            summary={
                "sources_total": 0,
                "sources_ok": 0,
                "sources_partial": 0,
                "sources_failed": 0,
                "content_items": 0,
            },
            sources={},
            notes=[f"social_scraper package not importable: {e}"],
        )

    # Run the sync audit in a thread so the FastAPI event loop stays responsive
    result = await _run_sync(audit, q, limit=limit, include_github=False)

    # Build honest notes from the per-source statuses
    notes: list[str] = []
    for name, src in result.get("sources", {}).items():
        status = src.get("status")
        if status == "error":
            notes.append(f"{name}: source failed — {src.get('error', 'unknown cause')[:150]}")
        elif status == "partial":
            notes.append(f"{name}: partial result — {src.get('error', 'partial cause unknown')[:150]}")
        if name == "instagram" and src.get("meta", {}).get("login_wall"):
            notes.append(
                "instagram: login wall limits results to ~10 top creators; "
                "no per-post engagement counts without auth"
            )

    if not any(name in result.get("sources", {}) for name in ("tiktok", "xhs", "douyin")):
        notes.append(
            "tiktok/xhs/douyin not yet integrated — pending TikHub paid API. "
            "Most fast-moving trends cross-post to Instagram Reels / YouTube Shorts within 24-48h."
        )

    return TrendSearchResponse(
        query=result.get("query", q),
        fetched_at=result.get("fetched_at", datetime.now(timezone.utc).isoformat()),
        summary=result.get("summary", {}),
        sources=result.get("sources", {}),
        notes=notes,
    )


@router.get("/health", summary="Health check")
async def health():
    """Liveness probe for the social-trends router."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
