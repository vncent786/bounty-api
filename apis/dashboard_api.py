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
from dataclasses import asdict
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
_discovery_store = None
_engine = None

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "monitoring.db"


class DiscoveryLensCriterionRequest(BaseModel):
    criterion_id: str
    label: str
    feature_key: str
    mode: str = "score"
    weight: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    missing_policy: str = "keep_unknown"
    description: str = ""


class DiscoveryLensEvaluationRequest(BaseModel):
    geo: str = "US"
    keyword: str
    lens_id: str
    name: str
    version: str = "1"
    objective: str = ""
    criteria: list[DiscoveryLensCriterionRequest]


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


def _get_discovery_store():
    global _discovery_store
    if _discovery_store is None:
        from social_scraper.discovery import DiscoveryStore
        _discovery_store = DiscoveryStore(_DB_PATH)
    return _discovery_store


def _get_discovery():
    global _discovery
    if _discovery is None:
        from social_scraper.monitoring import TopDownDiscovery
        _discovery = TopDownDiscovery(
            broker=_get_broker(),
            discovery_store=_get_discovery_store(),
        )
    return _discovery


def _get_engine():
    global _engine
    if _engine is None:
        from social_scraper.enrichment import EnrichmentEngine
        _engine = EnrichmentEngine(llm_call_fn=_llm_call)
    return _engine


async def _llm_call(system_prompt: str, user_prompt: str) -> str:
    """Call the shared, provider-switchable Bounty LLM client."""
    from social_scraper.llm_client import call_llm

    return await call_llm(system_prompt, user_prompt, max_tokens=4000)


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


# ── Zone job tracking (background tasks with progress) ────

import time as _time

_zone_jobs: dict[int, dict] = {}  # zone_id -> job status


def _set_job(zone_id: int, **kwargs):
    """Update zone job status."""
    job = _zone_jobs.setdefault(zone_id, {
        "status": "idle", "step": "", "progress": 0,
        "started_at": None, "finished_at": None,
        "result": None, "error": None,
    })
    job.update(kwargs)


@router.post("/zones/{zone_id}/run")
async def run_zone(zone_id: int):
    """Start zone collection as a background task. Returns immediately."""
    _check_auth()
    registry = _get_registry()
    zone = registry.get(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    # If already running, return current status
    existing = _zone_jobs.get(zone_id, {})
    if existing.get("status") == "running":
        return existing

    # Start background task
    _set_job(
        zone_id,
        status="running",
        step="Collecting posts from YouTube, Reddit, TikTok, X, Instagram...",
        progress=5,
        started_at=_time.time(),
        finished_at=None,
        result=None,
        error=None,
    )
    asyncio.create_task(_run_zone_background(zone_id, zone.name))

    return _zone_jobs[zone_id]


async def _run_zone_background(zone_id: int, zone_name: str):
    """Background task: collect -> cluster -> diff -> enrich with progress updates."""
    try:
        monitor = _get_monitor()

        # Step 1: Collect + cluster + diff (the run_zone does all three)
        _set_job(zone_id, step="Collecting posts from YouTube, Reddit, TikTok, X, Instagram...", progress=10)
        report = await monitor.run_zone(zone_name)

        # Step 2: Enrich
        _set_job(zone_id, step=f"Analyzing {report.total_items} posts with AI...", progress=70)
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

        # Done
        _set_job(
            zone_id,
            status="done",
            step=f"Complete: {report.total_items} items, {report.cluster_count} clusters, {len(report.alerts)} alerts",
            progress=100,
            finished_at=_time.time(),
            result=report.to_dict(),
        )

    except Exception as e:
        logger.error(f"Zone run failed for '{zone_name}': {e}", exc_info=True)
        _set_job(
            zone_id,
            status="error",
            step=f"Error: {str(e)[:150]}",
            progress=0,
            finished_at=_time.time(),
            error=str(e),
        )


@router.get("/zones/{zone_id}/status")
async def zone_status(zone_id: int):
    """Get current/last run status for a zone."""
    _check_auth()
    return _zone_jobs.get(zone_id, {
        "status": "idle", "step": "", "progress": 0,
        "started_at": None, "finished_at": None,
        "result": None, "error": None,
    })


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
    gate_only: bool = Query(False, description="Only return candidates whose configured social check threshold passed"),
):
    """Run top-down keyword discovery.

    Pipeline:
    1. Candidate generation via Google Trends trending_now (trendspy)
    2. User filters (volume, growth, age, categories)
    3. Bounded social-source check and horizontal conversation analysis
    """
    _check_auth()
    discovery = _get_discovery()

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
        "run_id": discovery.last_run_id,
    }


@router.get("/discovery/candidates/{geo}/{keyword:path}/history")
async def get_discovery_candidate_history(geo: str, keyword: str):
    """Return persisted observations and explicit gaps for one candidate."""
    _check_auth()
    history = _get_discovery_store().get_candidate_history(geo, keyword)
    if history["series"] is None:
        raise HTTPException(status_code=404, detail="Discovery candidate not found")
    return history


@router.get("/discovery/runs/{run_id}/usage")
async def get_discovery_run_usage(run_id: str):
    """Return persisted cost receipts and additive totals for one Discovery run."""
    _check_auth()
    store = _get_discovery_store()
    if not store.discovery_run_exists(run_id):
        raise HTTPException(status_code=404, detail="Discovery run not found")
    rows = store.list_stage_usage(run_id)

    def _token_total(name: str):
        relevant = [
            row for row in rows if row["llm_calls"] > 0 or row[name] is not None
        ]
        if not relevant or any(row[name] is None for row in relevant):
            return None
        return sum(row[name] for row in relevant)

    totals = {
        "source_calls": sum(row["external_calls"] for row in rows),
        "llm_calls": sum(row["llm_calls"] for row in rows),
        "cache_hits": sum(row["cache_hits"] for row in rows),
        "candidates_considered": sum(row["candidates_considered"] for row in rows),
        "candidates_processed": sum(row["candidates_processed"] for row in rows),
        "records_returned": sum(row["records_returned"] for row in rows),
        "duration_seconds": round(sum(row["duration_seconds"] for row in rows), 9),
        "input_tokens": _token_total("input_tokens"),
        "output_tokens": _token_total("output_tokens"),
        "tokens_estimated": any(row["tokens_estimated"] for row in rows),
    }
    return {"run_id": run_id, "totals": totals, "rows": rows}


@router.get("/discovery/lenses/presets")
async def discovery_lens_presets():
    """Return neutral and use-case views without assigning universal scores."""
    _check_auth()
    from social_scraper.lenses import list_lens_presets
    return {
        "default_preset_id": "horizontal-explorer",
        "presets": list_lens_presets(),
    }


@router.post("/discovery/lenses/evaluate")
async def evaluate_discovery_candidate_lens(body: DiscoveryLensEvaluationRequest):
    """Evaluate one persisted candidate under a versioned, user-defined lens."""
    _check_auth()
    from social_scraper.discovery.ranking import features_from_analysis
    from social_scraper.lenses import LensCriterion, ResearchLensSpec, evaluate_lens

    store = _get_discovery_store()
    context = store.get_latest_candidate_context(body.geo, body.keyword)
    if context is None:
        raise HTTPException(status_code=404, detail="Discovery candidate not found")
    criteria = tuple(LensCriterion(
        criterion_id=item.criterion_id,
        label=item.label,
        feature_key=item.feature_key,
        mode=item.mode,
        weight=item.weight,
        minimum=item.minimum,
        maximum=item.maximum,
        missing_policy=item.missing_policy,
        description=item.description,
    ) for item in body.criteria)
    spec = ResearchLensSpec(
        lens_id=body.lens_id,
        name=body.name,
        version=body.version,
        objective=body.objective,
        criteria=criteria,
    )
    analysis = ((context.get("gate_check") or {}).get("analysis") or {})
    features = features_from_analysis(analysis)
    candidate_id = f"{body.geo.upper()}:{context['series']['normalized_keyword']}"
    try:
        evaluation = evaluate_lens(
            {"candidate_id": candidate_id, "features": features}, spec
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = asdict(evaluation)
    evaluation_id = store.record_lens_evaluation(
        context["observation"]["observation_id"],
        lens_id=spec.lens_id,
        lens_version=spec.version,
        spec=asdict(spec),
        features=features,
        result=result,
    )
    return {
        "evaluation_id": evaluation_id,
        "candidate_id": candidate_id,
        "features": features,
        "evaluation": result,
        "evidence_status": analysis.get("status", "not_checked"),
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
