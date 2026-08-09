"""
Dashboard API — internal JSON endpoints for the SaaS monitoring product.

These are NOT x402 endpoints. They're behind a simple bearer token for
subscription users. The dashboard frontend calls these via fetch().

All endpoints prefixed with /dashboard/api/
"""

import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])

# Singleton instances — initialized on first use
_registry = None
_broker = None
_monitor = None
_discovery = None
_engine = None

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "monitoring.db"


def _get_registry():
    global _registry
    if _registry is None:
        from social_scraper.monitoring import ZoneRegistry
        _registry = ZoneRegistry(_DB_PATH)
    return _registry


def _get_broker():
    global _broker
    if _broker is None:
        from apis.social_search_api import build_default_broker
        _broker = build_default_broker(route_timeout_seconds=180.0)
    return _broker


def _get_monitor():
    global _monitor
    if _monitor is None:
        from social_scraper.monitoring import TrendMonitor
        _monitor = TrendMonitor(_get_registry(), _get_broker(), llm_cluster_fn=None)
    return _monitor


def _get_engine():
    global _engine
    if _engine is None:
        from social_scraper.enrichment import EnrichmentEngine
        _engine = EnrichmentEngine(llm_call_fn=_llm_call)
    return _engine


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call z.ai GLM for enrichment."""
    import httpx
    api_key = os.getenv("ZAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("ZAI_API_KEY not set")

    base_url = "https://api.z.ai/api/paas/v4"
    model = "glm-4-flash"

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 4000,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _check_auth(authorization: Optional[str] = Header(None)):
    """Simple bearer token auth."""
    token = os.getenv("BOUNTY_DASHBOARD_TOKEN", "")
    if not token:
        return  # No token configured = open access (dev mode)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization[7:] != token:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Zone CRUD ──────────────────────────────────────────────

class ZoneCreate(BaseModel):
    name: str
    keywords: list[str]
    platforms: list[str] = ["youtube", "reddit", "tiktok", "x", "instagram"]
    interval_hours: int = 168
    region: str = ""
    description: str = ""


@router.get("/zones")
async def list_zones():
    _check_auth()
    registry = _get_registry()
    zones = registry.list_zones()
    return {"zones": [z.to_dict() for z in zones]}


@router.post("/zones")
async def create_zone(zone: ZoneCreate):
    _check_auth()
    registry = _get_registry()
    from social_scraper.monitoring import Zone
    existing = registry.get_by_name(zone.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Zone '{zone.name}' already exists")
    new_zone = Zone(
        name=zone.name,
        keywords=zone.keywords,
        platforms=zone.platforms,
        interval_hours=zone.interval_hours,
        region=zone.region,
        description=zone.description,
    )
    zone_id = registry.create(new_zone)
    return {"id": zone_id, "status": "created", "zone": new_zone.to_dict()}


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: int):
    _check_auth()
    registry = _get_registry()
    registry.delete(zone_id)
    return {"status": "deleted"}


@router.post("/zones/{zone_id}/pause")
async def pause_zone(zone_id: int):
    _check_auth()
    registry = _get_registry()
    registry.update(zone_id, status="paused")
    return {"status": "paused"}


@router.post("/zones/{zone_id}/resume")
async def resume_zone(zone_id: int):
    _check_auth()
    registry = _get_registry()
    registry.update(zone_id, status="active")
    return {"status": "active"}


# ── Monitoring ─────────────────────────────────────────────

@router.post("/zones/{zone_id}/run")
async def run_zone(zone_id: int):
    """Manually trigger zone collection + clustering + enrichment."""
    _check_auth()
    registry = _get_registry()
    zone = registry.get(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    monitor = _get_monitor()
    report = await monitor.run_zone(zone.name)

    # Enrich the top clusters' sample posts
    engine = _get_engine()
    if engine and report.top_clusters:
        all_sample_posts = []
        for cluster in report.top_clusters:
            for post in cluster.get("sample_posts", []):
                all_sample_posts.append(post)
        if all_sample_posts:
            try:
                enriched = await engine.enrich_posts(all_sample_posts)
                report.enrichment = enriched.to_dict()
            except Exception as e:
                logger.warning(f"Enrichment failed: {e}")
                report.enrichment = {"error": str(e)}

    return report.to_dict()


@router.get("/zones/{zone_id}/report")
async def get_zone_report(zone_id: int, limit: int = Query(1)):
    """Get latest monitoring reports for a zone."""
    _check_auth()
    registry = _get_registry()
    snapshots = registry.get_snapshots(zone_id, limit=limit)
    return {"snapshots": snapshots}


@router.get("/zones/{zone_id}/diff")
async def get_zone_diff(zone_id: int):
    """Get week-over-week diff for a zone."""
    _check_auth()
    registry = _get_registry()
    snapshots = registry.get_snapshots(zone_id, limit=2)
    if len(snapshots) < 2:
        return {"diff": [], "message": "Need at least 2 snapshots for diffing"}

    monitor = _get_monitor()
    alerts = monitor.diff_snapshots(snapshots[1]["clusters"], snapshots[0]["clusters"])
    return {"alerts": [a.to_dict() for a in alerts]}


# ── Top-Down Discovery ─────────────────────────────────────

@router.get("/discover")
async def discover_keywords(
    geo: str = Query("US"),
    gate: bool = Query(True, description="Apply conversation gate"),
    min_volume: int = Query(0, description="Minimum search volume"),
    min_growth: int = Query(0, description="Minimum growth %"),
    max_age_hours: float = Query(0, description="Only trends started within N hours"),
    categories: str = Query("", description="Comma-separated category names to include"),
    gate_only: bool = Query(False, description="Only return gate-verified keywords"),
):
    """Run top-down keyword discovery.

    Pipeline:
    1. Candidate generation via Google Trends trending_now (trendspy)
    2. User filters (volume, growth, age, categories)
    3. Conversation gate: checks social platforms for real discussion
    """
    _check_auth()
    from social_scraper.monitoring import TopDownDiscovery
    discovery = TopDownDiscovery(broker=_get_broker())

    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None

    keywords = await discovery.scan_all(
        geo=geo,
        apply_gate=gate,
        min_volume=min_volume,
        min_growth=min_growth,
        max_age_hours=max_age_hours,
        categories=cat_list,
        gate_only=gate_only,
    )
    return {
        "keywords": [k.to_dict() for k in keywords[:50]],
        "total": len(keywords),
    }


# ── Alerts ─────────────────────────────────────────────────

@router.get("/alerts")
async def get_recent_alerts():
    """Get recent alerts across all zones."""
    _check_auth()
    registry = _get_registry()
    zones = registry.list_zones()
    monitor = _get_monitor()
    all_alerts = []

    for zone in zones:
        snapshots = registry.get_snapshots(zone.id, limit=2)
        if len(snapshots) >= 2:
            alerts = monitor.diff_snapshots(snapshots[1]["clusters"], snapshots[0]["clusters"])
            for alert in alerts:
                alert.zone_name = zone.name
                all_alerts.append(alert.to_dict())

    # Sort by detected_at (most recent first)
    all_alerts.sort(key=lambda a: a.get("detected_at", ""), reverse=True)
    return {"alerts": all_alerts[:50]}


# ── Stats ──────────────────────────────────────────────────

@router.get("/stats")
async def get_stats():
    """Dashboard overview stats."""
    _check_auth()
    registry = _get_registry()
    zones = registry.list_zones()
    active = [z for z in zones if z.status == "active"]
    due = registry.list_due()

    total_items = 0
    total_clusters = 0
    for zone in zones:
        snapshots = registry.get_snapshots(zone.id, limit=1)
        if snapshots:
            total_items += snapshots[0].get("item_count", 0)
            total_clusters += len(snapshots[0].get("clusters", []))

    return {
        "total_zones": len(zones),
        "active_zones": len(active),
        "zones_due": len(due),
        "total_items_collected": total_items,
        "total_clusters": total_clusters,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
