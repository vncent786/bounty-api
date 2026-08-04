"""
Bounty Social Intelligence API — user-need-driven endpoints.

These endpoints go beyond raw search. They answer real questions:
- "What videos are trending on YouTube about [topic]?"
- "Give me full details about this YouTube video/channel"
- "What's trending on TikTok right now?"
- "Give me a 360 view of [topic] across all platforms"

Design principle: each endpoint maps to a question a real user would ask.
"""

import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from social_scraper.connectors.youtube import YouTubeConnector
from social_scraper.broker import SourceBroker
from social_scraper.connectors.tiktok_api import TikTokAPIDirectConnector
from social_scraper.connectors.tiktok_playwright import TikTokPlaywrightConnector
from social_scraper.connectors.reddit_mobile import RedditMobileConnector
from social_scraper.connectors.reddit_rss import RedditRSSConnector
from social_scraper.proxy_config import proxy_health_summary


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class YouTubeSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, description="Search query")
    count: int = Field(default=20, ge=1, le=50)
    sort: Literal["relevance", "views", "latest"] = "relevance"
    time_filter: Literal["", "1day", "week", "month", "halfyear"] = ""


class TikTokTrendingRequest(BaseModel):
    keyword: str = Field(default="", description="Optional category/niche filter")
    region: str = Field(default="US", description="Region code (US, SG, ID, etc.)")
    count: int = Field(default=12, ge=1, le=30)
    sort: Literal["hot", "latest"] = "hot"
    time_filter: Literal["", "1day", "week", "month"] = ""


class CrossPlatformTrendsRequest(BaseModel):
    topic: str = Field(..., min_length=1, description="Topic/keyword to analyze")
    platforms: list[str] = Field(
        default=["youtube", "tiktok", "reddit"],
        max_length=5,
        description="Platforms to include",
    )
    count_per_platform: int = Field(default=15, ge=1, le=30)
    time_filter: Literal["", "1day", "week", "month"] = "week"
    region: str = Field(default="", description="Region code for TikTok")


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_social_intel_router(
    search_broker: SourceBroker,
    paid_enabled: bool = False,
):
    """Create the social intelligence API router."""
    api = APIRouter(prefix="/social", tags=["social-intelligence"])

    yt = YouTubeConnector()

    def require_paid():
        if not paid_enabled:
            raise HTTPException(
                status_code=503,
                detail="Social intelligence API is not configured",
            )

    # -----------------------------------------------------------------------
    # YouTube Endpoints
    # -----------------------------------------------------------------------

    @api.get("/youtube/search", dependencies=[Depends(require_paid)])
    async def youtube_search(
        q: str = Query(..., min_length=1, max_length=200, description="Search query"),
        count: int = Query(default=20, ge=1, le=50),
        sort: str = Query(default="relevance", pattern=r"^(relevance|views|latest)$"),
        time_filter: str = Query(default="", pattern=r"^(|1day|week|month|halfyear)$"),
    ):
        """Search YouTube videos with engagement metrics. Fast (flat-playlist)."""
        sort_map = {"relevance": "", "views": "views", "latest": "latest"}
        result = await yt.search(
            keyword=q,
            count=count,
            sort=sort_map.get(sort, ""),
            time_filter=time_filter,
        )
        return {
            "query": q,
            "platform": "youtube",
            "count": len(result.items),
            "items": [item.to_dict() for item in result.items],
            "source_health": result.health.to_dict(),
        }

    @api.get("/youtube/video/{video_id}", dependencies=[Depends(require_paid)])
    async def youtube_video(video_id: str):
        """Deep video intelligence. Full metadata: description, likes, tags, channel subs."""
        result = await yt.get_video(video_id)
        if not result.items:
            raise HTTPException(
                status_code=404,
                detail=f"Could not retrieve video {video_id}",
            )
        item = result.items[0]
        data = item.to_dict()
        # Enrich with raw metadata
        data["intelligence"] = item.raw
        return {
            "video_id": video_id,
            "platform": "youtube",
            "video": data,
            "source_health": result.health.to_dict(),
        }

    @api.get("/youtube/channel/{handle}", dependencies=[Depends(require_paid)])
    async def youtube_channel(
        handle: str,
        count: int = Query(default=10, ge=1, le=30, description="Number of recent videos"),
    ):
        """Channel overview: subscriber count, recent top videos by views."""
        result = await yt.get_channel(handle, video_count=count)
        return {
            "handle": handle,
            "platform": "youtube",
            **result,
        }

    # -----------------------------------------------------------------------
    # TikTok Endpoints
    # -----------------------------------------------------------------------

    @api.post("/tiktok/trending", dependencies=[Depends(require_paid)])
    async def tiktok_trending(req: TikTokTrendingRequest):
        """
        Discover trending TikTok content by region and category.

        Requires proxy to be configured (BOUNTY_PROXY_SERVER env vars).
        Without proxy, TikTok will IP-block after first request.
        """
        keyword = req.keyword or "trending"
        tiktok = TikTokAPIDirectConnector()

        result = await tiktok.search(
            keyword=keyword,
            count=req.count,
            sort=req.sort,
            time_filter=req.time_filter,
            region=req.region,
        )

        # Rank by engagement velocity (views + likes as proxy for virality)
        items = result.items
        items.sort(
            key=lambda x: (x.views or 0) + (x.likes or 0) * 10,
            reverse=True,
        )

        return {
            "keyword": keyword,
            "platform": "tiktok",
            "region": req.region,
            "count": len(items),
            "items": [item.to_dict() for item in items],
            "source_health": result.health.to_dict(),
            "proxy_configured": proxy_health_summary().get("configured", False),
        }

    # -----------------------------------------------------------------------
    # Cross-Platform Trends
    # -----------------------------------------------------------------------

    @api.post("/trends", dependencies=[Depends(require_paid)])
    async def cross_platform_trends(req: CrossPlatformTrendsRequest):
        """
        360-degree view of a topic across platforms.

        Searches all requested platforms, normalizes results, and produces:
        - Top content by engagement per platform
        - Top creators/channels across platforms
        - Platform activity distribution
        - Viral outliers (unusually high engagement)

        This is the killer endpoint: one call, all platforms, analyzed.
        """
        start = time.time()
        platform_results = {}
        platform_health = {}
        all_items = []

        # Run platform searches concurrently
        tasks = {}
        for platform in req.platforms:
            if platform == "youtube":
                tasks[platform] = yt.search(
                    req.topic, count=req.count_per_platform,
                    time_filter=req.time_filter,
                )
            elif platform == "tiktok":
                tiktok = TikTokAPIDirectConnector()
                tasks[platform] = tiktok.search(
                    req.topic, count=req.count_per_platform,
                    time_filter=req.time_filter, region=req.region or "US",
                )
            elif platform == "reddit":
                # Use the existing broker for reddit
                tasks[platform] = search_broker.search(
                    keyword=req.topic,
                    platforms=["reddit"],
                    count=req.count_per_platform,
                    time_filter=req.time_filter,
                )

        for platform, task in tasks.items():
            try:
                result = await task
                if isinstance(result, dict):
                    # Broker returns dict format
                    items = [item for item in result.get("items", [])
                             if item.get("platform") == "reddit" or platform != "reddit"]
                    health = result.get("source_health", [])
                    platform_results[platform] = items
                    platform_health[platform] = health
                    for item in items:
                        all_items.append((platform, item))
                else:
                    # Connector returns ConnectorResult
                    items = [item.to_dict() for item in result.items]
                    platform_results[platform] = items
                    platform_health[platform] = result.health.to_dict()
                    for item in items:
                        all_items.append((platform, item))
            except Exception as e:
                platform_results[platform] = []
                platform_health[platform] = {"status": "error", "error": str(e)}

        # Analysis layer
        analysis = _analyze_cross_platform(all_items, platform_results)

        latency = int((time.time() - start) * 1000)
        return {
            "topic": req.topic,
            "platforms": req.platforms,
            "time_filter": req.time_filter,
            "total_items": len(all_items),
            "latency_ms": latency,
            "analysis": analysis,
            "by_platform": {
                platform: {
                    "count": len(items),
                    "items": items[:req.count_per_platform],
                }
                for platform, items in platform_results.items()
            },
            "source_health": platform_health,
        }

    return api


# ---------------------------------------------------------------------------
# Analysis Functions
# ---------------------------------------------------------------------------

def _analyze_cross_platform(all_items: list, platform_results: dict) -> dict:
    """Produce cross-platform analysis from collected items."""

    # Platform distribution
    platform_counts = {}
    for platform, _ in all_items:
        platform_counts[platform] = platform_counts.get(0, 0) + 1

    # Fix: count properly
    platform_counts = {}
    for platform in platform_results:
        platform_counts[platform] = len(platform_results.get(platform, []))

    # Top content by engagement (normalized across platforms)
    scored_items = []
    for platform, item in all_items:
        engagement = item.get("engagement", {})
        views = engagement.get("views") or 0
        likes = engagement.get("likes") or 0
        comments = engagement.get("comments") or 0
        shares = engagement.get("shares") or 0

        # Composite engagement score
        score = views + (likes * 50) + (comments * 20) + (shares * 30)
        scored_items.append({
            "platform": platform,
            "score": score,
            "url": item.get("url", ""),
            "author": item.get("author", {}).get("username", ""),
            "text": item.get("text", "")[:200],
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "created_at": item.get("created_at"),
        })

    scored_items.sort(key=lambda x: x["score"], reverse=True)

    # Top creators across platforms
    creator_stats = {}
    for platform, item in all_items:
        author = item.get("author", {})
        username = author.get("username", "")
        if not username:
            continue
        key = f"{platform}:{username}"
        engagement = item.get("engagement", {})
        if key not in creator_stats:
            creator_stats[key] = {
                "platform": platform,
                "username": username,
                "profile_url": author.get("profile_url", ""),
                "follower_count": author.get("follower_count"),
                "content_count": 0,
                "total_views": 0,
                "total_likes": 0,
            }
        creator_stats[key]["content_count"] += 1
        creator_stats[key]["total_views"] += engagement.get("views") or 0
        creator_stats[key]["total_likes"] += engagement.get("likes") or 0

    top_creators = sorted(
        creator_stats.values(),
        key=lambda x: x["total_views"] + x["total_likes"] * 50,
        reverse=True,
    )[:10]

    # Viral outliers (engagement > 2x median)
    if scored_items:
        scores = [s["score"] for s in scored_items if s["score"] > 0]
        if scores:
            median_score = sorted(scores)[len(scores) // 2]
            outliers = [s for s in scored_items if s["score"] > median_score * 2][:5]
        else:
            outliers = []
    else:
        outliers = []

    # Hashtag/topic aggregation
    hashtag_freq = {}
    for _, item in all_items:
        for tag in item.get("hashtags", []):
            tag_lower = tag.lower() if isinstance(tag, str) else str(tag).lower()
            hashtag_freq[tag_lower] = hashtag_freq.get(tag_lower, 0) + 1

    trending_hashtags = sorted(
        [{"tag": k, "count": v} for k, v in hashtag_freq.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:15]

    return {
        "platform_distribution": platform_counts,
        "top_content": scored_items[:10],
        "top_creators": top_creators,
        "viral_outliers": outliers,
        "trending_hashtags": trending_hashtags,
        "summary": {
            "total_content_analyzed": len(all_items),
            "platforms_with_results": len([p for p in platform_results if platform_results[p]]),
            "highest_engagement": scored_items[0]["score"] if scored_items else 0,
        },
    }
