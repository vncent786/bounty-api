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
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])

# Singleton instances — initialized on first use
_registry = None
_broker = None
_monitor = None
_discovery = None
_discovery_store = None
_lens_store = None
_workspace_store = None
_workspace_service = None
_engine = None
_research_tasks: dict[str, asyncio.Task] = {}
_investing_store = None
_social_pulse_store = None

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


class ResearchLensCreateRequest(BaseModel):
    name: str
    description: str = ""
    spec: dict[str, Any]


class ResearchLensVersionRequest(BaseModel):
    spec: dict[str, Any]
    name: Optional[str] = None
    description: Optional[str] = None


class DuplicateLensRequest(BaseModel):
    name: Optional[str] = None


class CustomFieldCreateRequest(BaseModel):
    key: str
    label: str
    description: str = ""
    data_type: str
    source_stage: str
    extraction_mode: str
    definition: dict[str, Any] = Field(default_factory=dict)


class ResearchRunCreateRequest(BaseModel):
    workspace_id: str
    name: str = Field(min_length=1, max_length=160)
    lens_preset_id: Optional[str] = None
    source_discovery_run_id: Optional[str] = None
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=5)
    budget: dict[str, Any] = Field(default_factory=dict)
    required_depth: str = "candidate"
    lens_required_depth: Optional[str] = None
    lens: Optional[dict[str, Any]] = None
    priority_metrics: list[str] = Field(default_factory=lambda: [
        "recency", "volume", "growth", "category_match", "already_processed"
    ])

    def resolved_depth(self) -> str:
        return str(
            self.lens_required_depth
            or (self.lens or {}).get("required_depth")
            or self.required_depth
        )


class SubjectCreateRequest(BaseModel):
    name: str
    description: str = ""
    geo: str = ""
    platforms: list[str] = Field(default_factory=list)
    cadence_minutes: int = 10080
    active: bool = True
    lens_id: Optional[str] = None
    lens_version: Optional[int] = None
    budget: dict[str, Any] = Field(default_factory=dict)


class SubjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    geo: Optional[str] = None
    platforms: Optional[list[str]] = None
    cadence_minutes: Optional[int] = None
    active: Optional[bool] = None
    lens_id: Optional[str] = None
    lens_version: Optional[int] = None
    budget: Optional[dict[str, Any]] = None


class ProjectCreateRequest(BaseModel):
    name: str
    description: str = ""
    default_geo: str = ""
    first_subject: Optional[SubjectCreateRequest] = None


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_geo: Optional[str] = None
    status: Optional[str] = None


class AliasCreateRequest(BaseModel):
    alias: str
    kind: str = "include"


class ActionCreateRequest(BaseModel):
    action_type: str
    subject_id: Optional[str] = None
    actor_id: Optional[str] = None
    target_type: str = "project"
    target_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    requested_budget: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class PromotionShadowRequest(BaseModel):
    workspace_id: str = "default"
    evidence: list[dict[str, Any]]
    policy: Optional[dict[str, Any]] = None


class FamilyActionRequest(BaseModel):
    workspace_id: str = "default"
    action_type: str
    route: Optional[str] = None
    outcome: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


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


def _discovery_db_path() -> Path:
    """One configurable SQLite database for Discovery data and definitions."""
    configured = os.getenv("BOUNTY_DISCOVERY_DB_PATH") or os.getenv("DISCOVERY_DB_PATH")
    return Path(configured) if configured else _DB_PATH


def _get_discovery_store():
    global _discovery_store
    if _discovery_store is None:
        from social_scraper.discovery import DiscoveryStore
        _discovery_store = DiscoveryStore(_discovery_db_path())
    return _discovery_store


def _get_investing_store():
    global _investing_store
    if _investing_store is None:
        from social_scraper.investing import InvestingRadarStore
        _investing_store = InvestingRadarStore(_discovery_db_path())
    return _investing_store


def _get_social_pulse_store():
    global _social_pulse_store
    if _social_pulse_store is None:
        from social_scraper.investing.social_pulse import SocialPulseStore
        _social_pulse_store = SocialPulseStore(_discovery_db_path())
    return _social_pulse_store


def _public_investing_sweep(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    fields = (
        "id", "started_at", "completed_at", "status", "total_markets",
        "recorded_markets", "complete_markets", "empty_markets", "failed_markets",
        "candidate_observations", "unique_candidates", "unattempted_markets",
    )
    return {field: run.get(field) for field in fields}


def _public_radar_item(item: Mapping[str, Any]) -> dict[str, Any]:
    from social_scraper.monitoring.topdown import TRENDING_NOW_COUNTRIES

    country_names = dict(TRENDING_NOW_COUNTRIES)
    observations = list(item.get("observations") or [])
    representative = min(
        observations,
        key=lambda observation: (observation.get("market_rank") or 10**9, observation.get("country") or ""),
        default={},
    )
    country_codes = list(item.get("countries") or [])
    reasons: list[str] = []
    if len(country_codes) > 1:
        reasons.append(f"Appeared in {len(country_codes)} markets in the latest global sweep")
    elif country_codes:
        reasons.append(f"Appeared in {country_names.get(country_codes[0], country_codes[0])}")
    if representative.get("market_rank"):
        reasons.append(
            f"Ranked #{representative['market_rank']} in "
            f"{country_names.get(representative.get('country'), representative.get('country'))} current searches"
        )
    if representative.get("growth_pct") is not None:
        reasons.append(f"Source-reported search growth: {representative['growth_pct']:g}%")

    return {
        "id": item.get("candidate_id") or item.get("id"),
        "keyword": item.get("keyword") or item.get("display_keyword"),
        "lane": "breaking_now",
        "categories": list(item.get("categories") or []),
        "countries": [
            {"code": code, "name": country_names.get(code, code)} for code in country_codes
        ],
        "reasons": reasons,
        "search_volume": representative.get("search_volume"),
        "growth_pct": representative.get("growth_pct"),
        "started_hours_ago": representative.get("started_hours_ago"),
        "latest_observed_at": item.get("observed_at") or item.get("last_seen_at"),
        "source": "Google Trends Trending Now",
        "market_count": len(country_codes),
        "metric_scope_country": representative.get("country"),
    }


def _get_lens_store():
    global _lens_store
    if _lens_store is None:
        from social_scraper.lenses.storage import LensStore
        _lens_store = LensStore(_discovery_db_path())
    return _lens_store


def _get_workspace_store():
    global _workspace_store
    if _workspace_store is None:
        from social_scraper.workspaces import WorkspaceStore
        _workspace_store = WorkspaceStore(_discovery_db_path())
    return _workspace_store


def _get_workspace_service():
    global _workspace_service
    if _workspace_service is None:
        from social_scraper.workspaces import WorkspaceService
        _workspace_service = WorkspaceService(
            _get_workspace_store(), _get_lens_store(), _get_discovery_store()
        )
    return _workspace_service


def _workspace_call(method: str, *args, **kwargs):
    from social_scraper.workspaces import ConflictError, NotFoundError, ValidationError
    try:
        return getattr(_get_workspace_store(), method)(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _workspace_service_call(method: str, *args, **kwargs):
    from social_scraper.workspaces import ConflictError, NotFoundError, ValidationError
    try:
        return getattr(_get_workspace_service(), method)(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _lens_store_call(method: str, *args, **kwargs):
    """Translate domain errors consistently without coupling storage to FastAPI."""
    from social_scraper.lenses.storage import ConflictError, NotFoundError, ValidationError
    try:
        return getattr(_get_lens_store(), method)(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    """Fail closed unless a token or explicit local development mode is configured."""
    token = os.getenv("BOUNTY_DASHBOARD_TOKEN", "")
    if not token:
        environment = os.getenv("BOUNTY_ENV", os.getenv("ENVIRONMENT", "")).casefold()
        if environment in {"development", "dev", "local", "test"}:
            return
        raise HTTPException(
            status_code=503,
            detail="Dashboard authentication is not configured",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization[7:] != token:
        raise HTTPException(status_code=401, detail="Invalid token")


# Register authentication as a FastAPI dependency before routes are added. This
# preserves open development mode while resolving Authorization from requests.
router.dependencies.append(Depends(_check_auth))


def _investing_run_or_404(sweep_id: str) -> dict[str, Any]:
    run = _get_investing_store().get_sweep(sweep_id)
    if not run:
        raise HTTPException(status_code=404, detail="Radar refresh was not found")
    return run


@router.get("/investing/radar")
async def get_investing_radar(
    limit: int = Query(100, ge=1, le=500),
    country: str = Query(""),
    category: str = Query(""),
):
    from social_scraper.monitoring.topdown import (
        TREND_CATEGORY_NAMES,
        TRENDING_NOW_COUNTRIES,
    )

    store = _get_investing_store()
    raw_items = store.list_radar(
        limit=limit,
        country=country.strip().upper() or None,
        category=category.strip() or None,
    )
    items = [_public_radar_item(item) for item in raw_items]
    latest = store.latest_sweep()
    data_sweep = store.latest_data_sweep()
    data_observed_at = max(
        (item["latest_observed_at"] for item in items if item.get("latest_observed_at")),
        default=None,
    )
    if latest:
        coverage_summary = (
            f"{latest['recorded_markets']} of {latest['total_markets']} markets checked"
        )
        if latest["failed_markets"]:
            coverage_summary += f"; {latest['failed_markets']} unavailable"
        if data_sweep and data_sweep["id"] != latest["id"]:
            coverage_summary += "; displaying the most recent successful data"
        coverage_summary += f"; {len(items)} signals shown"
    else:
        coverage_summary = "No global source sweep has completed yet"

    return {
        "items": items,
        "breaking_now": items,
        "building_quietly": [],
        "last_sweep": _public_investing_sweep(latest),
        "data_sweep": _public_investing_sweep(data_sweep),
        "data_observed_at": data_observed_at,
        "coverage": {
            "summary": coverage_summary,
            "item_count": len(items),
            "country_options": [
                {"code": code, "name": name} for code, name in TRENDING_NOW_COUNTRIES
            ],
            "category_options": list(TREND_CATEGORY_NAMES),
            "source": "Google Trends Trending Now",
        },
    }


@router.get("/investing/social-pulse")
async def get_investing_social_pulse():
    """Read the latest persisted social-first discovery output."""
    return _get_social_pulse_store().public_payload()


@router.get("/investing/radar/runs/{sweep_id}")
async def get_investing_radar_run(sweep_id: str):
    return {"run": _public_investing_sweep(_investing_run_or_404(sweep_id))}


@router.get("/investing/radar/candidates/{candidate_id}")
async def get_investing_radar_candidate(candidate_id: int):
    candidate = _get_investing_store().get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Radar signal was not found")
    return _public_radar_item(candidate)


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
    registry = _get_registry()
    zones = registry.list_zones()
    return {"zones": [z.to_dict() for z in zones]}


@router.post("/zones")
async def create_zone(zone: ZoneCreate):
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
    registry = _get_registry()
    registry.delete(zone_id)
    return {"status": "deleted"}


@router.post("/zones/{zone_id}/pause")
async def pause_zone(zone_id: int):
    registry = _get_registry()
    registry.update(zone_id, status="paused")
    return {"status": "paused"}


@router.post("/zones/{zone_id}/resume")
async def resume_zone(zone_id: int):
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
    return _zone_jobs.get(zone_id, {
        "status": "idle", "step": "", "progress": 0,
        "started_at": None, "finished_at": None,
        "result": None, "error": None,
    })


@router.get("/zones/{zone_id}/report")
async def get_zone_report(zone_id: int, limit: int = Query(1)):
    """Get latest monitoring reports for a zone."""
    registry = _get_registry()
    snapshots = registry.get_snapshots(zone_id, limit=limit)
    return {"snapshots": snapshots}


@router.get("/zones/{zone_id}/diff")
async def get_zone_diff(zone_id: int):
    """Get week-over-week diff for a zone."""
    registry = _get_registry()
    snapshots = registry.get_snapshots(zone_id, limit=2)
    if len(snapshots) < 2:
        return {"diff": [], "message": "Need at least 2 snapshots for diffing"}

    monitor = _get_monitor()
    alerts = monitor.diff_snapshots(snapshots[1]["clusters"], snapshots[0]["clusters"])
    return {"alerts": [a.to_dict() for a in alerts]}


# ── Top-Down Discovery ─────────────────────────────────────

@router.get("/discover/options")
async def discovery_options():
    """Return the validated Google Trends controls used by Explore."""
    from social_scraper.monitoring.topdown import (
        TOPIC_CATEGORIES,
        TREND_CATEGORY_SCHEMA_VERSION,
        TRENDING_NOW_COUNTRIES,
        TRENDING_NOW_COUNTRIES_VERSION,
    )

    return {
        "countries": [
            {"code": code, "name": name}
            for code, name in sorted(
                TRENDING_NOW_COUNTRIES, key=lambda item: item[1].casefold()
            )
        ],
        "categories": [
            {"id": topic_id, "name": name}
            for topic_id, name in TOPIC_CATEGORIES.items()
        ],
        "defaults": {
            "country": "US",
            "category": "",
            "geo": "US",
            "categories": [],
            "mode": "trends_snapshot",
        },
        "source": "Google Trends Trending Now",
        "source_window_hours": 24,
        "category_schema_version": TREND_CATEGORY_SCHEMA_VERSION,
        "country_allowlist_version": TRENDING_NOW_COUNTRIES_VERSION,
    }


@router.get("/discover")
async def discover_keywords(
    geo: str = Query("US"),
    mode: str | None = Query(
        None,
        description=(
            "Explicit scan mode: trends_snapshot (default) or root_sweep. "
            "Deep modes run only through explicit research-runs."
        ),
    ),
    gate: bool | None = Query(
        None,
        description=(
            "Legacy social-source check flag. Maps to root_sweep; never "
            "triggers thread hydration or LLM analysis."
        ),
    ),
    min_volume: int = Query(0, ge=0, description="Minimum search volume"),
    min_growth: int = Query(0, ge=0, description="Minimum growth %"),
    max_age_hours: float = Query(0, ge=0, description="Only trends started within N hours"),
    categories: str = Query("", description="Comma-separated category names to include"),
    gate_only: bool = Query(False, description="Only return candidates whose configured social check threshold passed"),
):
    """Run top-down keyword discovery in an explicit scan mode.

    Pipeline:
    1. Candidate generation via Google Trends trending_now (trendspy)
    2. User filters (volume, growth, age, categories)
    3. Mode-governed check: trends_snapshot stops at Trends metadata
       (zero broker/LLM calls); root_sweep adds root social evidence
       with max_threads=0 and no LLM. Thread hydration and conversation
       analysis happen only in explicit research-runs.
    """
    from social_scraper.discovery.scan_modes import (
        RESEARCH_RUN_MODES,
        policy_for,
        resolve_scan_mode,
    )
    from social_scraper.monitoring.topdown import (
        TREND_CATEGORY_NAMES,
        TRENDING_NOW_COUNTRY_CODES,
        diversified_candidates,
    )

    geo = geo.strip().upper()
    if geo not in TRENDING_NOW_COUNTRY_CODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported Trending Now country code: {geo or '(blank)'}. "
                "Choose a country from /dashboard/api/discover/options."
            ),
        )

    requested_categories = [
        value.strip() for value in categories.split(",") if value.strip()
    ]
    def normalize_category(value: str) -> str:
        return " ".join(value.casefold().replace(" and ", " & ").split())

    category_lookup = {
        normalize_category(name): name for name in TREND_CATEGORY_NAMES
    }
    unknown_categories = [
        value for value in requested_categories
        if normalize_category(value) not in category_lookup
    ]
    if unknown_categories:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unsupported Trending Now category: "
                + ", ".join(unknown_categories)
            ),
        )
    cat_list = list(dict.fromkeys(
        category_lookup[normalize_category(value)]
        for value in requested_categories
    ))

    try:
        scan_mode = resolve_scan_mode(mode=mode, apply_gate=gate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if scan_mode in RESEARCH_RUN_MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"scan mode {scan_mode.value} is a research-run stage; "
                "create and execute a research run instead"
            ),
        )

    policy = policy_for(scan_mode)
    if gate_only and not policy.allows_broker_search:
        raise HTTPException(
            status_code=422,
            detail=(
                "gate_only requires a scan mode that checks conversations; "
                f"{scan_mode.value} never runs the conversation check "
                "(use root_sweep to check conversations)"
            ),
        )

    discovery = _get_discovery()

    try:
        keywords = await discovery.scan_all(
            geo=geo,
            mode=scan_mode,
            min_volume=min_volume,
            min_growth=min_growth,
            max_age_hours=max_age_hours,
            categories=cat_list or None,
            gate_only=gate_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = (
        _get_discovery_store().get_discovery_run(discovery.last_run_id)
        if discovery.last_run_id else None
    )
    if run and run["status"] == "error":
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Discovery source collection failed",
                "run_id": run["id"],
                "status": run["status"],
                "error_category": run["error_category"],
                "source_health": run["source_health"],
            },
        )
    returned_keywords = diversified_candidates(keywords, limit=50)
    return {
        "keywords": [k.to_dict() for k in returned_keywords],
        "total": len(keywords),
        "returned": len(returned_keywords),
        "selection_strategy": "category_balanced" if not cat_list else "selected_category",
        "run_id": discovery.last_run_id,
        "run": run,
    }


@router.get("/discover/trend-detail")
async def get_trend_detail(
    keyword: str = Query(..., description="Trend keyword to enrich"),
    geo: str = Query("US", description="Region code"),
):
    """Enrich a single trend with interest-over-time timeline and related queries.

    Returns weekly sparkline data and rising/top related search queries from
    Google Trends, so users can triage trends without running a full research plan.
    """
    result = {
        "keyword": keyword,
        "timeline": [],
        "related_rising": [],
        "related_top": [],
        "error": None,
    }

    try:
        from trendspy import Trends
        tr = Trends(request_delay=2.0)

        # Interest over time (7-day window for recent trend shape)
        try:
            df = tr.interest_over_time([keyword], timeframe="now 7-d", geo=geo)
            if len(df) > 0:
                vals = df[keyword].tolist()
                idx = df.index.tolist()
                result["timeline"] = [
                    {"date": str(d), "value": int(v)}
                    for d, v in zip(idx, vals)
                ]
        except Exception as e:
            logger.warning(f"interest_over_time failed for '{keyword}': {e}")
            result["error"] = f"timeline: {type(e).__name__}"

        # Related queries (rising + top)
        try:
            rq = tr.related_queries(keyword, geo=geo)
            if isinstance(rq, dict):
                for key, dest in [("rising", "related_rising"), ("top", "related_top")]:
                    df = rq.get(key)
                    if df is not None and len(df) > 0:
                        records = df.head(8).to_dict("records")
                        # Serialize numpy types
                        cleaned = []
                        for r in records:
                            cleaned.append({
                                str(k): (int(v) if hasattr(v, 'item') else str(v) if not isinstance(v, (int, float, str)) else v)
                                for k, v in r.items()
                            })
                        result[dest] = cleaned
        except Exception as e:
            logger.warning(f"related_queries failed for '{keyword}': {e}")
            if not result["error"]:
                result["error"] = f"related: {type(e).__name__}"

    except ImportError:
        result["error"] = "trendspy not installed"
    except Exception as e:
        result["error"] = str(e)

    return result


def _public_research_run(run: Mapping[str, Any]) -> dict[str, Any]:
    """Remove worker-lease internals from the dashboard contract."""
    public = dict(run)
    public.pop("lease_token", None)
    public.pop("lease_until", None)
    return public


# Bounds for persisted research-brief candidates: keywords stay short search
# topics, every other nested string stays a bounded label/note, and platform
# collections stay a small, deduplicated list.
_RESEARCH_TOPIC_MAX_LENGTH = 120
_RESEARCH_CANDIDATE_STRING_MAX = 500
_RESEARCH_PLATFORMS_MAX = 8
_RESEARCH_PLATFORM_NAME_MAX = 32


def _reject_oversized_candidate_strings(value: object, path: str = "candidate") -> None:
    if isinstance(value, str):
        if len(value) > _RESEARCH_CANDIDATE_STRING_MAX:
            raise ValueError(
                f"{path} must be at most {_RESEARCH_CANDIDATE_STRING_MAX} characters"
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_oversized_candidate_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_oversized_candidate_strings(item, f"{path}[{index}]")


def _allowed_research_platforms() -> frozenset[str]:
    """Platforms research briefs may request; shared with the executor."""
    from social_scraper.discovery.handlers import ALLOWED_PLATFORMS

    return ALLOWED_PLATFORMS


def _normalize_research_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Bound one research topic without rewriting its untouched fields."""
    normalized = dict(candidate)
    if "platforms" in normalized:
        raw_platforms = normalized["platforms"]
        if not isinstance(raw_platforms, list):
            raise ValueError("candidate platforms must be a list")
        allowed = _allowed_research_platforms()
        platforms: list[str] = []
        for entry in raw_platforms:
            if not isinstance(entry, str):
                raise ValueError("candidate platforms must be strings")
            platform = entry.strip().lower()
            if not platform:
                raise ValueError("candidate platforms must be non-empty")
            if len(platform) > _RESEARCH_PLATFORM_NAME_MAX:
                raise ValueError(
                    "candidate platform names must be at most "
                    f"{_RESEARCH_PLATFORM_NAME_MAX} characters"
                )
            if platform not in allowed:
                raise ValueError(
                    f"unknown platform {platform!r}; allowed platforms: "
                    + ", ".join(sorted(allowed))
                )
            if platform not in platforms:
                platforms.append(platform)
        if len(platforms) > _RESEARCH_PLATFORMS_MAX:
            raise ValueError(
                f"candidates may list at most {_RESEARCH_PLATFORMS_MAX} platforms"
            )
        if platforms:
            normalized["platforms"] = platforms
        else:
            # An empty collection falls back to the handler defaults.
            normalized.pop("platforms")
    _reject_oversized_candidate_strings(normalized)
    return normalized


@router.post("/discovery/research-runs", status_code=201)
async def create_discovery_research_run(body: ResearchRunCreateRequest):
    """Persist a deterministic plan; live collection intentionally happens elsewhere."""
    from social_scraper.discovery import ScanBudget
    from social_scraper.discovery.scheduler import DiscoveryScheduler
    from social_scraper.lenses import get_lens_preset
    from social_scraper.lenses.storage import LensStoreError
    try:
        name = str(body.name or "").strip()
        if not name:
            raise ValueError("research brief name is required")
        normalized_candidates: list[dict[str, Any]] = []
        seen_topics: set[str] = set()
        for candidate in body.candidates:
            keyword = str(candidate.get("keyword") or "").strip()
            if not keyword:
                raise ValueError("every research topic must have a keyword")
            if len(keyword) > _RESEARCH_TOPIC_MAX_LENGTH:
                raise ValueError(
                    "research topic keywords must be at most "
                    f"{_RESEARCH_TOPIC_MAX_LENGTH} characters"
                )
            topic_key = keyword.casefold()
            if topic_key in seen_topics:
                raise ValueError("research topics must be unique")
            seen_topics.add(topic_key)
            normalized_candidates.append(
                _normalize_research_candidate({**candidate, "keyword": keyword})
            )

        candidate_count = len(normalized_candidates)
        budget_payload = ScanBudget().to_dict()
        budget_payload.update({
            "root_probe_candidates": candidate_count,
            "deep_read_candidates": candidate_count,
            "horizontal_llm_candidates": candidate_count,
        })
        budget_payload.update(body.budget)
        limits = {
            "root_probe_candidates": 5,
            "deep_read_candidates": 5,
            "horizontal_llm_candidates": 5,
            "threads_per_platform": 10,
            "comments_per_thread": 100,
            "max_thread_depth": 5,
            "optional_enrichments": 5,
        }
        for field, limit in limits.items():
            value = budget_payload.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
            if value < 0 or value > limit:
                raise ValueError(f"{field} must be within 0..{limit}")
        requested = ScanBudget.from_dict(budget_payload)
        resolved_depth = body.resolved_depth()
        lens_reference = None
        lens_preset = None
        if body.lens_preset_id:
            try:
                lens_preset = get_lens_preset(body.lens_preset_id).to_dict()
            except KeyError as exc:
                raise ValueError(
                    f"unknown lens preset: {body.lens_preset_id}"
                ) from exc
        if body.lens is not None:
            lens_id = str(body.lens.get("id") or "").strip()
            try:
                lens_version = int(body.lens.get("version"))
            except (TypeError, ValueError) as exc:
                raise ValueError("lens version is required") from exc
            if not lens_id:
                raise ValueError("lens id is required")
            version = _get_lens_store().get_lens_version(
                body.workspace_id, lens_id, lens_version
            )
            resolved_depth = version["compiled_requirements"]["required_depth"]
            lens_reference = {
                "id": lens_id,
                "version": lens_version,
                "required_depth": resolved_depth,
            }
        required_budget_fields = ["root_probe_candidates"]
        if resolved_depth in {"deep_read", "horizontal_analysis", "custom_extraction"}:
            required_budget_fields.append("deep_read_candidates")
        if resolved_depth in {"horizontal_analysis", "custom_extraction"}:
            required_budget_fields.append("horizontal_llm_candidates")
        for field in required_budget_fields:
            if budget_payload[field] < candidate_count:
                raise ValueError(f"{field} must cover every research topic")
        plan = DiscoveryScheduler().plan(
            normalized_candidates, requested, resolved_depth,
            workspace_id=body.workspace_id, metric_order=body.priority_metrics,
        )
        plan["lens"] = lens_reference
        plan["lens_preset"] = lens_preset
        plan["name"] = name
        run = _get_discovery_store().create_research_run(
            workspace_id=body.workspace_id,
            source_discovery_run_id=body.source_discovery_run_id,
            requested_budget=requested.to_dict(),
            effective_budget=plan["effective_budget"],
            plan=plan,
        )
        return _public_research_run(run)
    except (TypeError, ValueError, LensStoreError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/discovery/research-runs")
async def list_discovery_research_runs(workspace_id: Optional[str] = None):
    runs = _get_discovery_store().list_research_runs(workspace_id)
    return {"runs": [_public_research_run(run) for run in runs]}


@router.get("/discovery/research-runs/{run_id}")
async def get_discovery_research_run(run_id: str):
    run = _get_discovery_store().get_research_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return _public_research_run(run)


@router.get("/discovery/research-runs/{run_id}/candidates")
async def get_discovery_research_run_candidates(run_id: str):
    store = _get_discovery_store()
    if store.get_research_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return {"run_id": run_id, "candidates": store.list_research_run_candidates(run_id)}


@router.get("/discovery/research-runs/{run_id}/candidates/{candidate_id}/history")
async def get_discovery_research_candidate_stage_history(run_id: str, candidate_id: str):
    store = _get_discovery_store()
    candidates = store.list_research_run_candidates(run_id)
    if not any(row["candidate_id"] == candidate_id for row in candidates):
        raise HTTPException(status_code=404, detail="Research run candidate not found")
    return {"run_id": run_id, "candidate_id": candidate_id,
            "history": store.list_stage_transitions(run_id, candidate_id)}


@router.post("/discovery/research-runs/{run_id}/candidates/{candidate_id}/promote")
async def promote_discovery_research_candidate(run_id: str, candidate_id: str):
    try:
        return _get_discovery_store().promote_research_candidate(run_id, candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _schedule_research_run(run_id: str, claim_token: str) -> None:
    """Keep a strong reference to one leased process-local task until it ends."""
    task = asyncio.create_task(_execute_research_run_background(run_id, claim_token))
    _research_tasks[run_id] = task

    def _forget(_task: asyncio.Task) -> None:
        _research_tasks.pop(run_id, None)

    task.add_done_callback(_forget)


@router.post("/discovery/research-runs/{run_id}/execute", status_code=202)
async def execute_research_run(run_id: str):
    """Start a persisted research run and return immediately for status polling."""
    store = _get_discovery_store()
    run = store.get_research_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    if run["status"] not in {"planned", "running"}:
        raise HTTPException(
            status_code=409,
            detail=f"Research run status is '{run['status']}', expected 'planned' or 'running'",
        )
    if run_id in _research_tasks:
        return {
            "run_id": run_id,
            "status": "running",
            "resumed": False,
            "claimed_elsewhere": False,
            "status_url": f"/dashboard/api/discovery/research-runs/{run_id}",
        }

    claim_token = store.claim_research_run(run_id)
    if claim_token is not None:
        _schedule_research_run(run_id, claim_token)
    return {
        "run_id": run_id,
        "status": "running",
        "resumed": run["status"] == "running" and claim_token is not None,
        "claimed_elsewhere": claim_token is None,
        "status_url": f"/dashboard/api/discovery/research-runs/{run_id}",
    }


async def _renew_research_run_lease(run_id: str, claim_token: str) -> None:
    """Keep a live worker lease short and fail loudly if ownership is lost."""
    store = _get_discovery_store()
    while True:
        await asyncio.sleep(30)
        if not store.renew_research_run_claim(run_id, claim_token):
            raise RuntimeError(f"research run {run_id} lost its worker lease")


async def _execute_research_run_background(
    run_id: str, claim_token: str,
) -> dict[str, Any]:
    """Collect conversations, analyze them, and persist findings for one run."""
    store = _get_discovery_store()
    run = store.get_research_run(run_id)
    if run is None:
        logger.error("Research run %s disappeared before execution", run_id)
        return {"run_id": run_id, "status": "error", "findings_count": 0}

    heartbeat: asyncio.Task | None = None
    execution: asyncio.Task | None = None
    latest_progress: dict[str, Any] | None = None
    try:
        if not store.renew_research_run_claim(run_id, claim_token):
            logger.warning("Research run %s started after its worker lease expired", run_id)
            return {"run_id": run_id, "status": "error", "findings_count": 0}
        heartbeat = asyncio.create_task(
            _renew_research_run_lease(run_id, claim_token)
        )
        from social_scraper.discovery.handlers import build_handlers
        from social_scraper.discovery.staged_runner import StagedRunner
        from social_scraper.discovery.triage import (
            ConversationAnalysis,
            all_sources_failed,
            prepare_conversation_prompt,
            source_failure_limitations,
        )

        plan = run["plan"]
        handlers, collected = build_handlers(
            _get_broker(), plan, llm_call_fn=_llm_call,
        )

        def persist_progress(snapshot: Mapping[str, Any]) -> None:
            nonlocal latest_progress
            progress = dict(snapshot)
            store.update_research_run(
                run_id, status="running", result={"progress": progress},
                claim_token=claim_token,
            )
            latest_progress = progress

        execution = asyncio.create_task(
            StagedRunner(
                handlers, progress_recorder=persist_progress,
            ).run(run_id, plan)
        )
        done, _ = await asyncio.wait(
            {execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED,
        )
        if heartbeat in done:
            error = (
                None if heartbeat.cancelled() else heartbeat.exception()
            )
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            raise error or RuntimeError("research lease heartbeat stopped unexpectedly")
        result = await execution

        if not store.renew_research_run_claim(run_id, claim_token):
            raise ValueError("research worker lost its claim before persistence")

        findings_saved = []
        for candidate in plan.get("candidates", []):
            cid = candidate.get("candidate_id", "")
            topic = str(candidate.get("candidate", {}).get("keyword") or cid)
            analysis = collected.get(cid + ":findings")
            if analysis is None:
                posts = (
                    collected.get(cid + ":deep")
                    or collected.get(cid)
                    or []
                )
                # Root-probe and deep-read health together decide how total
                # the collection gap is; deep-read failures must not vanish.
                source_health = list(collected.get(cid + ":health") or [])
                source_health.extend(collected.get(cid + ":deep_health") or [])
                prepared = prepare_conversation_prompt(topic, posts)
                public_evidence = [
                    {key: value for key, value in item.items() if key != "voice_id"}
                    for item in prepared.evidence
                ]
                source_unavailable = not posts and all_sources_failed(source_health)
                analysis = ConversationAnalysis(
                    topic=topic,
                    status=(
                        "sources_unavailable" if source_unavailable
                        else "insufficient_evidence"
                    ),
                    evidence=public_evidence,
                    coverage={
                        "raw_records": len(posts),
                        "deduplicated_records": len(public_evidence),
                        "independent_voices": len({
                            item.get("voice_id") for item in prepared.evidence
                            if item.get("voice_id")
                        }),
                        "thread_count": len({
                            item.get("root_id") or item.get("id")
                            for item in prepared.evidence
                        }),
                        "platform_count": len({
                            item.get("platform") for item in prepared.evidence
                            if item.get("platform")
                        }),
                        "source_status": source_health,
                    },
                    limitations=[
                        "This topic did not reach a completed interpretation; source records and collection gaps remain available.",
                        *source_failure_limitations(source_health),
                    ],
                ).to_dict()
            finding = store.save_findings(
                run_id, cid, topic,
                analysis.get("status", "unknown"), analysis,
                claim_token=claim_token,
            )
            findings_saved.append(finding)

        stage_statuses = [
            stage_result.status
            for stage_results in result.handler_results.values()
            for stage_result in stage_results.values()
        ]
        final_status = "partial" if any(
            status == "failed" for status in stage_statuses
        ) else "complete"
        if latest_progress is None or latest_progress.get("phase") != "finalizing":
            raise RuntimeError("research stages finished without finalization progress")
        final_progress = {
            **latest_progress,
            "phase": "complete",
            "candidate_id": None,
            "complete": True,
            "percent": 100.0 if latest_progress.get("total_units") else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        receipt = {
            "stages_executed": list(result.handler_results.keys()),
            "findings_count": len(findings_saved),
            "usage": [usage.to_dict() for usage in result.usages],
            "progress": final_progress,
        }
        store.update_research_run(
            run_id, status=final_status, result=receipt, claim_token=claim_token,
        )
        return {"run_id": run_id, "status": final_status, **receipt}
    except asyncio.CancelledError:
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        store.release_research_run_claim(run_id, claim_token)
        raise
    except Exception as exc:
        logger.exception("Research run %s failed", run_id)
        error_receipt = {
            "findings_count": len(store.list_findings(run_id)),
            **({"progress": latest_progress} if latest_progress is not None else {}),
        }
        try:
            store.update_research_run(
                run_id, status="error", error_category=str(exc)[:200],
                result=error_receipt, claim_token=claim_token,
            )
        except ValueError:
            logger.warning("Research run %s failed after its worker claim expired", run_id)
        return {"run_id": run_id, "status": "error", **error_receipt}
    finally:
        tasks = [task for task in (execution, heartbeat) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/discovery/research-runs/{run_id}/findings")
async def get_research_run_findings(run_id: str):
    """Return persisted findings for a completed research run."""
    store = _get_discovery_store()
    if store.get_research_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    findings = store.list_findings(run_id)
    return {"run_id": run_id, "findings": findings}


@router.get("/discovery/candidates/{geo}/{keyword:path}/history")
async def get_discovery_candidate_history(geo: str, keyword: str):
    """Return persisted observations and explicit gaps for one candidate."""
    history = _get_discovery_store().get_candidate_history(geo, keyword)
    if history["series"] is None:
        raise HTTPException(status_code=404, detail="Discovery candidate not found")
    return history


@router.get("/discovery/runs/{run_id}/usage")
async def get_discovery_run_usage(run_id: str):
    """Return persisted cost receipts and additive totals for one Discovery run."""
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
        # Provider-reported actuals stay separate from projected/estimated
        # token fields above; neither is derived from the other.
        "input_records": sum(row["input_records"] for row in rows),
        "input_characters": sum(row["input_characters"] for row in rows),
        "input_tokens_reported": _token_total("input_tokens_reported"),
        "output_tokens_reported": _token_total("output_tokens_reported"),
        "shared_evidence_reuse": any(row["shared_evidence_reuse"] for row in rows),
    }
    return {"run_id": run_id, "totals": totals, "rows": rows}


@router.post("/promotion/shadow/evaluate")
async def evaluate_promotion_shadow(body: PromotionShadowRequest):
    """Evaluate persisted evidence in shadow mode; execute no downstream action."""
    from social_scraper.discovery.promotion import PromotionPolicy
    from social_scraper.discovery.shadow_mode import run_shadow_mode
    try:
        policy = PromotionPolicy(body.policy) if body.policy is not None else None
        return run_shadow_mode(
            _get_discovery_store(), body.evidence,
            workspace_id=body.workspace_id, policy=policy,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/promotion/funnel")
async def promotion_funnel(workspace_id: str = "default"):
    return _get_discovery_store().summarize_promotion_funnel(
        workspace_id=workspace_id
    )


@router.get("/explore/families")
async def global_explore_families(
    workspace_id: str = "default", perspective_id: Optional[str] = None,
):
    from social_scraper.discovery.explore_service import build_persisted_explore
    perspective = None
    if perspective_id:
        lens = _lens_store_call("get_lens", workspace_id, perspective_id)
        perspective = lens["latest_version"]["spec"]
    return build_persisted_explore(
        _get_discovery_store(), workspace_id=workspace_id, perspective=perspective
    )


@router.get("/explore/families/{family_id}/labels")
async def family_labels(family_id: str, workspace_id: str = "default"):
    return _get_discovery_store().list_promotion_labels(
        workspace_id=workspace_id, family_id=family_id
    )


@router.post("/explore/families/{family_id}/actions", status_code=201)
async def create_family_action(family_id: str, body: FamilyActionRequest):
    store = _get_discovery_store()
    family = store.get_topic_family(family_id)
    if family is None:
        raise HTTPException(status_code=404, detail="topic family not found")
    try:
        label = store.record_promotion_label(
            workspace_id=body.workspace_id, family_id=family_id,
            action_type=body.action_type, route=body.route,
            outcome=body.outcome, details=body.details,
        )
        return {"label": label, "family": family}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/discovery/lenses/presets")
async def discovery_lens_presets():
    """Return neutral and use-case views without assigning universal scores."""
    from social_scraper.lenses import list_lens_presets
    return {
        "default_preset_id": "horizontal-explorer",
        "presets": list_lens_presets(),
    }


# Workspace project and durable-action APIs are path-scoped by workspace at every level.
@router.get("/workspaces/{workspace_id}/projects")
async def list_workspace_projects(workspace_id: str, include_archived: bool = False):
    return {"projects": _workspace_call(
        "list_projects", workspace_id, include_archived=include_archived
    )}


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
async def create_workspace_project(workspace_id: str, body: ProjectCreateRequest):
    first = body.first_subject.model_dump() if body.first_subject is not None else None
    return _workspace_service_call(
        "create_project", workspace_id, name=body.name, description=body.description,
        default_geo=body.default_geo, first_subject=first,
    )


@router.get("/workspaces/{workspace_id}/projects/{project_id}")
async def get_workspace_project(workspace_id: str, project_id: str):
    return _workspace_call("get_project", workspace_id, project_id)


@router.patch("/workspaces/{workspace_id}/projects/{project_id}")
@router.put("/workspaces/{workspace_id}/projects/{project_id}")
async def update_workspace_project(
    workspace_id: str, project_id: str, body: ProjectUpdateRequest
):
    return _workspace_call(
        "update_project", workspace_id, project_id, **body.model_dump(exclude_unset=True)
    )


@router.delete("/workspaces/{workspace_id}/projects/{project_id}")
async def archive_workspace_project(workspace_id: str, project_id: str):
    return _workspace_call("archive_project", workspace_id, project_id)


@router.get("/workspaces/{workspace_id}/projects/{project_id}/subjects")
async def list_project_subjects(
    workspace_id: str, project_id: str, include_inactive: bool = True
):
    return {"subjects": _workspace_call(
        "list_subjects", workspace_id, project_id, include_inactive=include_inactive
    )}


@router.post("/workspaces/{workspace_id}/projects/{project_id}/subjects", status_code=201)
async def create_project_subject(
    workspace_id: str, project_id: str, body: SubjectCreateRequest
):
    return _workspace_service_call(
        "create_subject", workspace_id, project_id, **body.model_dump()
    )


@router.get("/workspaces/{workspace_id}/projects/{project_id}/subjects/{subject_id}")
async def get_project_subject(workspace_id: str, project_id: str, subject_id: str):
    return _workspace_call("get_subject", workspace_id, project_id, subject_id)


@router.patch("/workspaces/{workspace_id}/projects/{project_id}/subjects/{subject_id}")
@router.put("/workspaces/{workspace_id}/projects/{project_id}/subjects/{subject_id}")
async def update_project_subject(
    workspace_id: str, project_id: str, subject_id: str, body: SubjectUpdateRequest
):
    return _workspace_service_call(
        "update_subject", workspace_id, project_id, subject_id,
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/workspaces/{workspace_id}/projects/{project_id}/subjects/{subject_id}")
async def archive_project_subject(workspace_id: str, project_id: str, subject_id: str):
    return _workspace_call("archive_subject", workspace_id, project_id, subject_id)


_ALIAS_PATH = "/workspaces/{workspace_id}/projects/{project_id}/subjects/{subject_id}/aliases"


@router.get(_ALIAS_PATH)
async def list_subject_aliases(workspace_id: str, project_id: str, subject_id: str):
    return {"aliases": _workspace_call(
        "list_aliases", workspace_id, project_id, subject_id
    )}


@router.post(_ALIAS_PATH, status_code=201)
async def create_subject_alias(
    workspace_id: str, project_id: str, subject_id: str, body: AliasCreateRequest
):
    return _workspace_call(
        "create_alias", workspace_id, project_id, subject_id, body.alias, body.kind
    )


@router.get(_ALIAS_PATH + "/{alias_id}")
async def get_subject_alias(
    workspace_id: str, project_id: str, subject_id: str, alias_id: str
):
    return _workspace_call(
        "get_alias", workspace_id, project_id, subject_id, alias_id
    )


@router.delete(_ALIAS_PATH + "/{alias_id}", status_code=204)
async def delete_subject_alias(
    workspace_id: str, project_id: str, subject_id: str, alias_id: str
):
    _workspace_call("delete_alias", workspace_id, project_id, subject_id, alias_id)
    return None


_ACTION_PATH = "/workspaces/{workspace_id}/projects/{project_id}/actions"


@router.get(_ACTION_PATH)
async def list_project_actions(
    workspace_id: str, project_id: str, status: Optional[str] = None,
    subject_id: Optional[str] = None,
):
    return {"actions": _workspace_call(
        "list_actions", workspace_id, project_id, status=status, subject_id=subject_id
    )}


@router.post(_ACTION_PATH, status_code=201)
async def create_project_action(
    workspace_id: str, project_id: str, body: ActionCreateRequest
):
    values = body.model_dump()
    action_type = values.pop("action_type")
    action, created = _workspace_service_call(
        "create_action", workspace_id, project_id, action_type, **values
    )
    return {"action": action, "created": created}


@router.get(_ACTION_PATH + "/{action_id}")
async def get_project_action(workspace_id: str, project_id: str, action_id: str):
    return _workspace_call("get_action", workspace_id, project_id, action_id)


@router.post(_ACTION_PATH + "/{action_id}/cancel")
@router.delete(_ACTION_PATH + "/{action_id}")
async def cancel_project_action(workspace_id: str, project_id: str, action_id: str):
    return _workspace_call("cancel_action", workspace_id, project_id, action_id)


# Definition CRUD is deliberately configuration-only: no broker, LLM, or usage receipt.
@router.get("/workspaces/{workspace_id}/lenses")
async def list_workspace_lenses(workspace_id: str, include_archived: bool = False):
    return _lens_store_call("list_lenses", workspace_id, include_archived=include_archived)


@router.post("/workspaces/{workspace_id}/lenses", status_code=201)
async def create_workspace_lens(workspace_id: str, body: ResearchLensCreateRequest):
    return _lens_store_call("create_lens", workspace_id, body.name, body.description, body.spec)


@router.get("/workspaces/{workspace_id}/lenses/{lens_id}")
async def get_workspace_lens(workspace_id: str, lens_id: str, include_archived: bool = False):
    return _lens_store_call(
        "get_lens", workspace_id, lens_id, include_archived=include_archived
    )


@router.get("/workspaces/{workspace_id}/lenses/{lens_id}/versions")
async def list_workspace_lens_versions(workspace_id: str, lens_id: str):
    return _lens_store_call("list_lens_versions", workspace_id, lens_id)


@router.post("/workspaces/{workspace_id}/lenses/{lens_id}/versions", status_code=201)
@router.put("/workspaces/{workspace_id}/lenses/{lens_id}", status_code=201)
@router.patch("/workspaces/{workspace_id}/lenses/{lens_id}", status_code=201)
async def create_workspace_lens_version(
    workspace_id: str, lens_id: str, body: ResearchLensVersionRequest
):
    return _lens_store_call(
        "create_lens_version", workspace_id, lens_id, body.spec,
        name=body.name, description=body.description,
    )


@router.get("/workspaces/{workspace_id}/lenses/{lens_id}/versions/{version}")
async def get_workspace_lens_version(workspace_id: str, lens_id: str, version: int):
    return _lens_store_call("get_lens_version", workspace_id, lens_id, version)


@router.post("/workspaces/{workspace_id}/lenses/{lens_id}/duplicate", status_code=201)
async def duplicate_workspace_lens(
    workspace_id: str, lens_id: str, body: Optional[DuplicateLensRequest] = None
):
    return _lens_store_call(
        "duplicate_lens", workspace_id, lens_id,
        name=body.name if body is not None else None,
    )


@router.post("/workspaces/{workspace_id}/lenses/{lens_id}/archive")
@router.delete("/workspaces/{workspace_id}/lenses/{lens_id}")
async def archive_workspace_lens(workspace_id: str, lens_id: str):
    return _lens_store_call("archive_lens", workspace_id, lens_id)


@router.get("/workspaces/{workspace_id}/fields")
async def list_workspace_fields(workspace_id: str, include_archived: bool = False):
    return _lens_store_call(
        "list_custom_fields", workspace_id, include_archived=include_archived
    )


@router.post("/workspaces/{workspace_id}/fields", status_code=201)
async def create_workspace_field(workspace_id: str, body: CustomFieldCreateRequest):
    return _lens_store_call(
        "create_custom_field", workspace_id, key=body.key, label=body.label,
        description=body.description, data_type=body.data_type,
        source_stage=body.source_stage, extraction_mode=body.extraction_mode,
        definition=body.definition,
    )


@router.get("/workspaces/{workspace_id}/fields/{field_id}")
async def get_workspace_field(
    workspace_id: str, field_id: str, include_archived: bool = False
):
    return _lens_store_call(
        "get_custom_field", workspace_id, field_id, include_archived=include_archived
    )


@router.post("/workspaces/{workspace_id}/fields/{field_id}/archive")
@router.delete("/workspaces/{workspace_id}/fields/{field_id}")
async def archive_workspace_field(workspace_id: str, field_id: str):
    return _lens_store_call("archive_custom_field", workspace_id, field_id)


@router.post("/discovery/lenses/evaluate")
async def evaluate_discovery_candidate_lens(body: DiscoveryLensEvaluationRequest):
    """Evaluate one persisted candidate under a versioned, user-defined lens."""
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
