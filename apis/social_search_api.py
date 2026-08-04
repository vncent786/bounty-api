"""Bounty Social Data API with resilient search and historical collection."""

import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from social_scraper.broker import SourceBroker
from social_scraper.collection import CollectionService
from social_scraper.connectors.douyin_playwright import DouyinPlaywrightConnector
from social_scraper.connectors.reddit import RedditConnector
from social_scraper.connectors.reddit_arctic import RedditArcticConnector
from social_scraper.connectors.reddit_camoufox import RedditCamoufoxConnector
from social_scraper.connectors.reddit_mobile import RedditMobileConnector
from social_scraper.connectors.reddit_rss import RedditRSSConnector
from social_scraper.connectors.reddit_search import RedditSearchConnector
from social_scraper.connectors.tiktok_auth import TikTokAuthConnector
from social_scraper.connectors.tiktok_playwright import TikTokPlaywrightConnector
from social_scraper.connectors.xhs_playwright import XHSPlaywrightConnector
from social_scraper.connectors.youtube import YouTubeConnector
from social_scraper.proxy_config import proxy_health_summary
from social_scraper.storage import ObservationStore


class RedditSearchOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subreddits: list[str] = Field(..., min_length=1, max_length=5)

    @field_validator("subreddits")
    @classmethod
    def validate_subreddits(cls, values):
        normalized = []
        seen = set()
        for value in values:
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", value):
                raise ValueError("Invalid subreddit name")
            if value.lower() not in seen:
                normalized.append(value)
                seen.add(value.lower())
        return normalized


class SearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1, description="Search query")
    platforms: Optional[list[str]] = Field(
        default=None,
        max_length=10,
        description="Platforms to search",
    )
    count: int = Field(default=20, ge=1, le=50, description="Items per platform")
    time_filter: Literal["", "1day", "week", "month", "halfyear"] = ""
    sort: Literal["", "latest", "hot"] = ""
    region: str = Field(default="", description="Region code, e.g. US, CN, SG")
    reddit: Optional[RedditSearchOptions] = None


class CachedSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1, max_length=10)
    count: int = Field(default=20, ge=1, le=50)
    region: str = ""
    max_age_minutes: int = Field(default=60, ge=1, le=10080)
    time_filter: Literal["", "1day", "week", "month", "halfyear"] = ""
    sort: Literal["", "latest", "hot"] = ""
    reddit: Optional[RedditSearchOptions] = None


class TikTokSearchRequest(BaseModel):
    keyword: str = Field(..., min_length=1)
    count: int = Field(default=12, ge=1, le=30)
    sort: str = ""
    time_filter: str = ""


class CollectionQueryRequest(BaseModel):
    keyword: str = Field(..., min_length=1)
    platforms: list[str] = Field(..., min_length=1)
    region: str = ""
    interval_minutes: int = Field(..., ge=1)
    next_run_at: datetime
    enabled: bool = True
    time_filter: Literal["", "1day", "week", "month", "halfyear"] = ""
    sort: Literal["", "latest", "hot"] = ""
    reddit: Optional[RedditSearchOptions] = None


def _configured_reddit_subreddits():
    return [
        value.strip()
        for value in os.getenv("BOUNTY_REDDIT_SUBREDDITS", "").split(",")
        if value.strip()
    ]


def build_default_broker(route_timeout_seconds=30.0):
    broker = SourceBroker(route_timeout_seconds=route_timeout_seconds)
    broker.register(RedditConnector(), priority=10)
    reddit_subreddits = _configured_reddit_subreddits()
    arctic = RedditArcticConnector(subreddits=reddit_subreddits)
    broker.register(arctic, priority=20)
    reddit_search_key = os.getenv("BOUNTY_BRAVE_SEARCH_API_KEY", "").strip()
    if reddit_search_key:
        broker.register(RedditSearchConnector(api_key=reddit_search_key), priority=30)
    broker.register(YouTubeConnector())
    broker.register(TikTokAuthConnector(), priority=10)
    broker.register(TikTokPlaywrightConnector(), priority=20)
    broker.register(DouyinPlaywrightConnector())
    broker.register(XHSPlaywrightConnector())
    return broker


def build_collection_broker():
    """Build the slower broker used only by scheduled/admin collection paths."""
    broker = build_default_broker(route_timeout_seconds=240.0)
    broker.register(RedditMobileConnector(), priority=1)
    broker.register(RedditRSSConnector(), priority=3)
    broker.register(
        RedditCamoufoxConnector(
            subreddits=_configured_reddit_subreddits(),
            operation_timeout_seconds=210.0,
            queue_timeout_seconds=5.0,
        ),
        priority=5,
    )
    return broker


def default_store():
    configured = os.getenv("BOUNTY_SOCIAL_DB")
    path = Path(configured) if configured else Path(__file__).resolve().parents[1] / "data" / "social_observations.db"
    return ObservationStore(path)


def _platform_options(reddit, time_filter="", sort=""):
    options = {}
    if reddit is not None:
        options["reddit"] = reddit.model_dump()
    search_options = {}
    if time_filter:
        search_options["time_filter"] = time_filter
    if sort:
        search_options["sort"] = sort
    if search_options:
        options["_search"] = search_options
    return options


def create_social_router(
    active_broker: SourceBroker,
    store: ObservationStore,
    admin_token: Optional[str] = None,
    paid_search_enabled: bool = False,
    reddit_depth_connector=None,
    collection_broker=None,
):
    api = APIRouter(prefix="/social", tags=["social"])
    collection_service = CollectionService(collection_broker or active_broker, store)

    def require_admin(x_social_admin_token: Optional[str] = Header(default=None)):
        if not admin_token:
            raise HTTPException(status_code=503, detail="Social collection administration is not configured")
        if not x_social_admin_token or not secrets.compare_digest(x_social_admin_token, admin_token):
            raise HTTPException(status_code=401, detail="Invalid social collection administration token")

    def require_paid_search():
        if not paid_search_enabled:
            raise HTTPException(status_code=503, detail="Paid social search is not configured")

    def validate_reddit_request(platforms, reddit, sort):
        if reddit is not None and platforms is not None and "reddit" not in platforms:
            raise HTTPException(status_code=422, detail="Reddit options require the reddit platform")
        if reddit is not None and sort == "hot":
            raise HTTPException(status_code=422, detail="Scoped Reddit hot sorting is not supported")

    @api.post("/search", dependencies=[Depends(require_paid_search)])
    async def social_search(req: SearchRequest):
        requested = req.platforms if req.platforms is not None else active_broker.list_platforms()
        unsupported = sorted(set(requested) - set(active_broker.list_platforms()))
        if unsupported:
            raise HTTPException(status_code=422, detail=f"Unsupported platforms: {', '.join(unsupported)}")
        validate_reddit_request(req.platforms, req.reddit, req.sort)
        return await active_broker.search(
            keyword=req.keyword,
            platforms=req.platforms,
            count=req.count,
            time_filter=req.time_filter,
            sort=req.sort,
            region=req.region,
            platform_options=_platform_options(req.reddit, req.time_filter, req.sort),
        )

    @api.post("/search/cached", dependencies=[Depends(require_paid_search)])
    async def cached_social_search(req: CachedSearchRequest):
        validate_reddit_request(req.platforms, req.reddit, req.sort)
        unsupported = sorted(set(req.platforms) - set(active_broker.list_platforms()))
        if unsupported:
            raise HTTPException(status_code=422, detail=f"Unsupported platforms: {', '.join(unsupported)}")
        snapshot = store.get_latest_collection(
            req.keyword,
            req.platforms,
            req.region,
            max_age_minutes=req.max_age_minutes,
            platform_options=_platform_options(req.reddit, req.time_filter, req.sort),
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="No fresh cached social snapshot is available")
        response = dict(snapshot["raw_response"])
        items = []
        platform_counts = {}
        for item in response.get("items", []):
            platform = str(item.get("platform", ""))
            if platform_counts.get(platform, 0) >= req.count:
                continue
            items.append(item)
            platform_counts[platform] = platform_counts.get(platform, 0) + 1
        response.update({
            "items": items,
            "count": len(items),
            "cached": True,
            "cache": {
                "collection_run_id": snapshot["id"],
                "collected_at": snapshot["collected_at"],
                "age_seconds": snapshot["age_seconds"],
            },
        })
        return response

    @api.post("/tiktok/search", dependencies=[Depends(require_paid_search)])
    async def tiktok_search(req: TikTokSearchRequest):
        result = await active_broker.search(
            keyword=req.keyword,
            platforms=["tiktok"],
            count=req.count,
            time_filter=req.time_filter,
            sort=req.sort,
        )
        return {
            "query": req.keyword,
            "platform": "tiktok",
            "count": result["count"],
            "items": result["items"],
            "source_health": result["source_health"],
            "platform_result": result["platform_results"].get("tiktok"),
        }

    @api.get("/reddit/feed", dependencies=[Depends(require_paid_search)])
    async def reddit_feed(
        subreddit: str = Query(..., min_length=2, max_length=21, pattern=r"^[A-Za-z0-9_]+$"),
        keyword: str = Query(default="", max_length=200),
        count: int = Query(default=20, ge=1, le=50),
        time_filter: str = Query(default="", pattern=r"^(|1day|week|month)$"),
        sort: str = Query(default="", pattern=r"^(|rising|new|latest|hot)$"),
    ):
        if reddit_depth_connector is None:
            raise HTTPException(status_code=503, detail="Reddit depth connector is not configured")
        try:
            result = await reddit_depth_connector.get_feed(
                subreddit,
                keyword=keyword,
                count=count,
                time_filter=time_filter,
                sort=sort,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        items = []
        for item in result.items:
            serialized = item.to_dict()
            serialized["subreddit"] = item.raw.get("subreddit")
            serialized["flair"] = item.raw.get("flair")
            serialized["feed"] = item.raw.get("feed")
            serialized["provenance"] = {
                "connector": result.health.connector,
                "fetched_at": result.health.fetched_at,
                "query": keyword,
            }
            items.append(serialized)
        health = result.health.to_dict()
        if health.get("error"):
            health["error"] = "connector_error"
        return {"subreddit": subreddit, "count": len(items), "items": items, "source_health": health}

    @api.get("/reddit/post", dependencies=[Depends(require_paid_search)])
    async def reddit_post(
        url: str = Query(..., max_length=500),
        comment_limit: int = Query(default=20, ge=0, le=100),
    ):
        if reddit_depth_connector is None:
            raise HTTPException(status_code=503, detail="Reddit depth connector is not configured")
        try:
            return await reddit_depth_connector.get_post(url, comment_limit=comment_limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Reddit hydration failed") from exc

    @api.get("/sources/health")
    async def sources_health():
        return {
            "platforms": active_broker.list_platforms(),
            "proxy": proxy_health_summary(),
            "health": await active_broker.health_check_all(),
        }

    @api.get("/platforms")
    async def list_platforms():
        return {
            "platforms": active_broker.list_platforms(),
            "routes": active_broker.list_routes(),
        }

    @api.post(
        "/queries",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    async def create_query(req: CollectionQueryRequest):
        if "reddit" in req.platforms and req.reddit is None:
            raise HTTPException(
                status_code=422,
                detail="Scheduled Reddit collection requires an explicit subreddit scope",
            )
        validate_reddit_request(req.platforms, req.reddit, req.sort)
        unsupported = sorted(set(req.platforms) - set(active_broker.list_platforms()))
        if unsupported:
            raise HTTPException(status_code=422, detail=f"Unsupported platforms: {', '.join(unsupported)}")
        query_id = store.upsert_query(
            keyword=req.keyword,
            platforms=req.platforms,
            region=req.region,
            interval_minutes=req.interval_minutes,
            next_run_at=req.next_run_at,
            enabled=req.enabled,
            platform_options=_platform_options(req.reddit, req.time_filter, req.sort),
        )
        return store.get_query(query_id)

    @api.get("/queries/{query_id}", dependencies=[Depends(require_admin)])
    async def get_query(query_id: int):
        query_record = store.get_query(query_id)
        if query_record is None:
            raise HTTPException(status_code=404, detail="Collection query not found")
        return query_record

    @api.get("/queries", dependencies=[Depends(require_admin)])
    async def due_queries(at: Optional[datetime] = Query(default=None)):
        return {"queries": store.list_due_queries(now=at)}

    @api.post("/queries/{query_id}/collect", dependencies=[Depends(require_admin)])
    async def collect_query(query_id: int, at: Optional[datetime] = Query(default=None)):
        try:
            return await collection_service.collect_query(query_id, now=at)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/collect-due", dependencies=[Depends(require_admin)])
    async def collect_due(at: Optional[datetime] = Query(default=None)):
        results = await collection_service.collect_due(now=at)
        return {"collections": results, "count": len(results)}

    @api.get(
        "/history/{platform}/{post_id}",
        dependencies=[Depends(require_admin)],
    )
    async def observation_history(platform: str, post_id: str):
        return {
            "platform": platform,
            "post_id": post_id,
            "observations": store.get_observation_history(platform, post_id),
        }

    return api


reddit_depth = RedditCamoufoxConnector()
broker = build_default_broker()
scheduled_broker = build_collection_broker()
observation_store = default_store()
router = create_social_router(
    broker,
    observation_store,
    admin_token=os.getenv("BOUNTY_SOCIAL_ADMIN_TOKEN"),
    paid_search_enabled=os.getenv("BOUNTY_X402_ACTIVE") == "1",
    reddit_depth_connector=reddit_depth,
    collection_broker=scheduled_broker,
)
