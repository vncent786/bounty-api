"""Owned-source collection adapter for the private investment Radar."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx

from apis.news_search import GOOGLE_NEWS_RSS, parse_google_news_rss
from apis.social_search_api import build_default_broker
from social_scraper.connectors.x_graphql import XConnector
from social_scraper.investing.adaptive_investigation import make_query_lineage_id
from social_scraper.investing.google_discovery import (
    MOVEMENT_GEOGRAPHIES,
    collect_worldwide_trend_candidates,
)
from social_scraper.investing.private_radar import (
    DEFAULT_PANELS,
    NON_X_DISCOVERY_PLATFORMS,
    Panel,
    PrivateRadarScanner,
    PrivateRadarStore,
    stable_evidence_id,
)
from social_scraper.investing.trajectory import (
    collect_movement_bundles,
    collect_search_trajectory,
    trajectory_is_usable,
)


FINANCIAL_SOURCES = (
    "reuters", "bloomberg", "cnbc", "financial times", "wall street journal",
    "barron's", "marketwatch", "seeking alpha", "investor's business daily",
    "morningstar", "the motley fool", "yahoo finance", "benzinga",
)

# Reddit's owned mobile route reads current subreddit listings and then applies an
# exact keyword filter. Long natural-language panel descriptions therefore match
# nothing. Keep one source-native discovery term per versioned consumer panel.
REDDIT_PANEL_QUERIES = {
    "automobiles": "car",
    "airlines": "flight",
    "hotels_travel": "hotel",
    "restaurants_qsr": "restaurant",
    "food_beverage": "food",
    "beauty_skincare": "skincare",
    "fashion_apparel": "fashion",
    "luxury": "luxury",
    "retail": "retail",
    "consumer_technology": "gadget",
    "streaming": "streaming",
    "telecom": "broadband",
    "fintech_payments": "payment",
    "fitness_wearables": "fitness",
    "pets": "pet",
    "household_cleaning": "cleaning",
}


SOURCE_QUERY_RECIPE_VERSION = "camillo-source-queries/2"
DISCOVERY_LOOKBACK_DAYS = 90
# Connectors do not expose a 90-day option consistently. Request the next wider
# source-native window, then enforce the exact 90-day boundary locally.
DISCOVERY_TIME_FILTER = "halfyear"
SOURCE_PREFLIGHT_QUERIES = {
    # Verified production-shape query with current TikTok results. The first
    # consumer panel's "switching car" can legitimately return only stale rows,
    # which tests topic yield rather than connector health.
    "tiktok": "switching skincare",
}
DEFAULT_ADAPTIVE_PLATFORM_LIMITS = {
    "x": 64,
    "tiktok": 48,
    "instagram": 48,
    "reddit": 48,
    "youtube": 48,
}


class AdaptiveCollectionBudget:
    """One hard per-run budget shared by fan-out, retries, and thread reads."""

    def __init__(
        self,
        *,
        max_attempts: int = 240,
        per_platform_limits: Mapping[str, int] | None = None,
    ):
        self.max_attempts = max(0, int(max_attempts))
        self.per_platform_limits = {
            str(platform): max(0, int(limit))
            for platform, limit in (
                per_platform_limits or DEFAULT_ADAPTIVE_PLATFORM_LIMITS
            ).items()
        }
        self.used_attempts = 0
        self.used_by_platform = {
            platform: 0 for platform in self.per_platform_limits
        }
        self.used_by_operation: dict[str, int] = {}
        self.denied_attempts = 0

    def reserve(self, *, platform: str, operation: str) -> bool:
        platform_name = str(platform or "unknown")
        platform_limit = self.per_platform_limits.get(platform_name, self.max_attempts)
        if (
            self.used_attempts >= self.max_attempts
            or self.used_by_platform.get(platform_name, 0) >= platform_limit
        ):
            self.denied_attempts += 1
            return False
        self.used_attempts += 1
        self.used_by_platform[platform_name] = (
            self.used_by_platform.get(platform_name, 0) + 1
        )
        operation_name = str(operation or "unknown")
        self.used_by_operation[operation_name] = (
            self.used_by_operation.get(operation_name, 0) + 1
        )
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "used_attempts": self.used_attempts,
            "remaining_attempts": max(0, self.max_attempts - self.used_attempts),
            "per_platform_limits": dict(self.per_platform_limits),
            "used_by_platform": dict(self.used_by_platform),
            "used_by_operation": dict(self.used_by_operation),
            "denied_attempts": self.denied_attempts,
            "exhausted": self.used_attempts >= self.max_attempts,
        }


PANEL_SOURCE_ANCHORS = {
    "automobiles": "car",
    "airlines": "airline",
    "hotels_travel": "hotel",
    "restaurants_qsr": "restaurant",
    "food_beverage": "grocery product",
    "beauty_skincare": "skincare product",
    "fashion_apparel": "clothing brand",
    "luxury": "luxury product",
    "retail": "retailer",
    "consumer_technology": "consumer gadget",
    "streaming": "streaming service",
    "telecom": "mobile plan",
    "fintech_payments": "payment app",
    "fitness_wearables": "fitness tracker",
    "pets": "pet product",
    "household_cleaning": "cleaning product",
}


def panel_platform_query(panel: Panel, platform: str) -> str:
    if platform == "reddit":
        return REDDIT_PANEL_QUERIES.get(panel.panel_id, panel.name)
    anchor = PANEL_SOURCE_ANCHORS.get(panel.panel_id, panel.name.casefold())
    if platform == "youtube":
        return f"why I switched {anchor}"
    if platform == "instagram":
        return f"{anchor} problem"
    if platform == "tiktok":
        return f"switching {anchor}"
    return panel.search_term


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_status(platform_result: Mapping[str, Any] | None, health: Sequence[Mapping[str, Any]]):
    result = platform_result or {}
    state = str(result.get("status") or "error")
    platform = result.get("platform")
    selected_connector = result.get("selected_connector")
    matching = [
        item for item in health
        if item.get("platform") == platform
        and (
            not selected_connector
            or item.get("connector") == selected_connector
        )
    ]
    if state == "ok":
        result_error = result.get("error")
        if result_error:
            return "partial", result_error
        if selected_connector and not matching:
            return "partial", "selected_connector_health_missing"
        selected_error = next(
            (
                item.get("error") or "selected_connector_unhealthy"
                for item in reversed(matching)
                if str(item.get("status") or "error") != "ok" or item.get("error")
            ),
            None,
        )
        if selected_error:
            return "partial", selected_error
        return "complete", None
    status = "partial" if state == "partial" else "failed"
    error = next(
        (item.get("error") for item in reversed(matching) if item.get("error")),
        result.get("error"),
    )
    return status, error


def _x_source_status(result) -> str:
    state = str(result.health.status or "error")
    if state == "ok" and result.health.error is None:
        return "complete"
    return "partial" if result.items else "failed"


def _metric(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _normalise_engagement(value: Mapping[str, Any] | None) -> dict[str, int | None]:
    source = value if isinstance(value, Mapping) else {}
    return {
        name: _metric(source.get(name))
        for name in (
            "views", "likes", "comments", "shares", "collects", "upvotes",
            "replies", "reposts", "bookmarks",
        )
    }


def _created_at_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_item(
    item: Mapping[str, Any], *, panel_id: str, window_key: str, query: str,
    query_lineage_id: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    require_timestamp: bool = False,
    max_age_days: int | None = DISCOVERY_LOOKBACK_DAYS,
) -> dict[str, Any] | None:
    value = dict(item)
    text = str(value.get("text") or value.get("title") or "").strip()
    url = str(value.get("url") or "").strip()
    if not text or not url.startswith(("http://", "https://")):
        return None
    created_at = value.get("created_at") or value.get("published_at")
    parsed_created_at = _created_at_utc(created_at)
    if require_timestamp and parsed_created_at is None:
        return None
    if (
        parsed_created_at is not None
        and (
            (window_start is not None and parsed_created_at < window_start)
            or (window_end is not None and parsed_created_at >= window_end)
        )
    ):
        return None
    age_days = None
    if parsed_created_at is not None:
        age_days = max(
            0,
            (datetime.now(timezone.utc) - parsed_created_at).days,
        )
    if (
        window_start is None
        and window_end is None
        and window_key == "current"
        and age_days is not None
        and max_age_days is not None
        and age_days > max(0, int(max_age_days))
    ):
        return None
    author = value.get("author") or {}
    if isinstance(author, Mapping):
        author_name = str(author.get("username") or author.get("display_name") or "").strip()
        creator_id = str(
            author.get("id") or author.get("external_id")
            or author.get("author_external_id") or ""
        ).strip() or None
    else:
        author_name = str(author or "").strip()
        creator_id = str(
            value.get("author_id") or value.get("author_external_id") or ""
        ).strip() or None
    external_id = str(value.get("post_id") or value.get("external_id") or "") or None
    community_id = str(
        value.get("community_id") or value.get("subreddit")
        or (value.get("provenance") or {}).get("subreddit")
        if isinstance(value.get("provenance"), Mapping)
        else value.get("community_id") or value.get("subreddit") or ""
    ).strip() or None
    raw_repost = value.get("is_repost")
    evidence = {
        "panel_id": panel_id,
        "platform": str(value.get("platform") or "unknown"),
        "external_id": external_id,
        "record_type": "root",
        "parent_external_id": None,
        "root_post_external_id": external_id,
        "thread_depth": 0,
        "query_lineage_id": query_lineage_id,
        "community_id": community_id,
        "creator_id": creator_id,
        "is_repost": raw_repost if isinstance(raw_repost, bool) else None,
        "copy_cluster_id": value.get("copy_cluster_id"),
        "truncated": False,
        "url": url,
        "author": author_name or None,
        "text": text[:6000],
        "engagement": _normalise_engagement(value.get("engagement")),
        "created_at": created_at,
        "age_days": age_days,
        "recency_bucket": (
            "timestamp_missing"
            if age_days is None else
            "last_30_days" if age_days <= 30 else
            "last_90_days" if age_days <= DISCOVERY_LOOKBACK_DAYS else
            "historical"
        ),
        "observed_at": _utc_iso(),
        "window_key": window_key,
        "query": query,
    }
    evidence["id"] = stable_evidence_id(evidence)
    evidence["root_post_external_id"] = external_id or evidence["id"]
    return evidence


def _thread_evidence(
    record, *, panel_id: str, query: str,
    query_lineage_id: str | None = None,
    thread_result=None,
) -> dict[str, Any] | None:
    raw = record.raw if isinstance(getattr(record, "raw", None), Mapping) else {}
    value = {
        "panel_id": panel_id,
        "platform": record.platform,
        "external_id": record.external_id,
        "record_type": record.record_type,
        "parent_external_id": record.parent_external_id,
        "root_post_external_id": record.root_post_external_id,
        "thread_depth": int(record.depth),
        "query_lineage_id": query_lineage_id,
        "community_id": raw.get("community_id") or raw.get("subreddit"),
        "creator_id": record.author_external_id,
        "is_repost": None,
        "copy_cluster_id": None,
        "truncated": bool(getattr(thread_result, "truncated", False)),
        "url": record.url,
        "author": record.author_username,
        "text": record.text,
        "engagement": _normalise_engagement({"likes": record.likes}),
        "created_at": record.published_at,
        "observed_at": _utc_iso(),
        "window_key": "current",
        "query": query,
    }
    if not value["text"] or not str(value["url"] or "").startswith(("http://", "https://")):
        return None
    value["id"] = stable_evidence_id(value)
    return value


class OwnedRadarCollector:
    def __init__(
        self, broker=None, x_connector=None, trajectory_check_fn=None,
        trend_discovery_fn=None,
        *,
        adaptive_max_attempts: int = 240,
        adaptive_per_platform_limits: Mapping[str, int] | None = None,
        trend_candidate_limit: int = 4,
    ):
        self.broker = broker or build_default_broker(route_timeout_seconds=90.0)
        self.x_connector = x_connector or XConnector()
        self.trajectory_check_fn = trajectory_check_fn or check_search_trajectory
        self.trend_discovery_fn = trend_discovery_fn or collect_worldwide_trend_candidates
        self.adaptive_max_attempts = max(0, int(adaptive_max_attempts))
        self.adaptive_per_platform_limits = dict(
            adaptive_per_platform_limits or DEFAULT_ADAPTIVE_PLATFORM_LIMITS
        )
        self.trend_candidate_limit = max(0, int(trend_candidate_limit))

    def new_adaptive_budget(self) -> AdaptiveCollectionBudget:
        return AdaptiveCollectionBudget(
            max_attempts=self.adaptive_max_attempts,
            per_platform_limits=self.adaptive_per_platform_limits,
        )

    async def preflight(self) -> dict[str, Any]:
        """Prove every required source before starting the expensive panel sweep."""
        panel = DEFAULT_PANELS[0]
        x_task = self.x_connector.search(
            panel.x_query_slices[0], count=3, time_filter="week", sort="latest"
        )
        social_platforms = tuple(NON_X_DISCOVERY_PLATFORMS)
        social_tasks = [
            self._broker_search(
                panel,
                platform,
                SOURCE_PREFLIGHT_QUERIES.get(
                    platform, panel_platform_query(panel, platform)
                ),
                count=3,
                time_filter=DISCOVERY_TIME_FILTER,
                sort="latest",
                hydrate=False,
                retry_empty=(platform == "tiktok"),
            )
            for platform in social_platforms
        ]
        trajectory_task = self.trajectory_check_fn("home gym")
        results = await asyncio.gather(
            x_task, trajectory_task, *social_tasks, return_exceptions=True
        )
        x_result, trajectory, *social_results = results

        if isinstance(x_result, BaseException):
            x_receipt = {
                "platform": "x", "stage": "preflight", "status": "failed",
                "count": 0, "error_category": type(x_result).__name__,
            }
        else:
            x_count = len(x_result.items)
            x_ok = _x_source_status(x_result) == "complete" and x_count > 0
            x_receipt = {
                "platform": "x", "stage": "preflight",
                "status": "complete" if x_ok else "failed",
                "count": x_count,
                "error_category": (
                    None if x_ok else x_result.health.error or "x_empty"
                ),
            }

        social_receipts = []
        for platform, result in zip(social_platforms, social_results):
            if isinstance(result, BaseException):
                social_receipts.append({
                    "platform": platform, "stage": "preflight", "status": "failed",
                    "count": 0, "error_category": type(result).__name__,
                    "coverage": {},
                })
                continue
            source = dict(result.get("source") or {})
            count = int(source.get("count") or 0)
            source_ok = source.get("status") == "complete" and count > 0
            social_receipts.append({
                "platform": platform,
                "stage": "preflight",
                "status": "complete" if source_ok else "failed",
                "count": count,
                "error_category": (
                    None if source_ok else source.get("error_category") or f"{platform}_empty"
                ),
                "coverage": source.get("coverage") or {},
            })

        if isinstance(trajectory, BaseException):
            trends_receipt = {
                "platform": "google_trends", "stage": "preflight",
                "status": "failed", "count": 0,
                "error_category": type(trajectory).__name__,
            }
        else:
            trends_ok = trajectory_is_usable(trajectory)
            trends_receipt = {
                "platform": "google_trends", "stage": "preflight",
                "status": "complete" if trends_ok else "failed",
                "count": len(trajectory.get("points") or []),
                "error_category": None if trends_ok else (
                    trajectory.get("error_category") or "trajectory_unavailable"
                ),
            }

        sources = [x_receipt, *social_receipts, trends_receipt]
        failed = [source["platform"] for source in sources if source["status"] != "complete"]
        return {
            "ok": not failed,
            "error_category": (
                None if not failed else f"preflight_{'_'.join(failed)}_unavailable"
            ),
            "sources": sources,
        }

    async def collect_trend_discovery(
        self,
        *,
        budget: AdaptiveCollectionBudget | None = None,
    ) -> dict[str, Any]:
        """Generate Google candidates first, then collect bounded social roots."""
        discovery = await asyncio.to_thread(self.trend_discovery_fn)
        candidates = [dict(value) for value in discovery.get("candidates") or []]
        investigated_candidates = candidates[: self.trend_candidate_limit]
        sources = [{
            "platform": "google_trends",
            "stage": "trend_discovery",
            "status": str(discovery.get("status") or "failed"),
            "count": len(candidates),
            "observed_at": discovery.get("observed_at"),
            "geographies": list(discovery.get("geographies") or []),
            "failures": list(discovery.get("failures") or []),
            "candidates": candidates,
            "coverage": {
                "social_investigation_limit": self.trend_candidate_limit,
                "social_investigated_candidates": len(investigated_candidates),
                "social_skipped_candidates": max(
                    0, len(candidates) - len(investigated_candidates)
                ),
            },
        }]
        evidence = []
        panels = {panel.panel_id: panel for panel in DEFAULT_PANELS}
        for candidate in investigated_candidates:
            panel = panels.get(str(candidate.get("panel_id") or ""))
            keyword = str(candidate.get("keyword") or "").strip()
            if panel is None or not keyword:
                continue
            x_query = f'"{keyword.replace(chr(34), "")}" -filter:retweets'
            if budget is not None and not budget.reserve(
                platform="x", operation="trend_candidate_search"
            ):
                sources.append({
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "stage": "trend_candidate",
                    "trend_keyword": keyword,
                    "status": "partial",
                    "count": 0,
                    "error_category": "adaptive_budget_exhausted",
                    "coverage": {"budget": budget.snapshot()},
                })
            else:
                try:
                    x_result = await self.x_connector.search(
                        x_query, count=10, time_filter=DISCOVERY_TIME_FILTER, sort="latest"
                    )
                except Exception as exc:
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": "x",
                        "stage": "trend_candidate",
                        "trend_keyword": keyword,
                        "status": "failed",
                        "count": 0,
                        "error_category": type(exc).__name__,
                        "coverage": (
                            {"budget": budget.snapshot()}
                            if budget is not None else {}
                        ),
                    })
                else:
                    for item in x_result.items:
                        normalized = _normalise_item(
                            item.to_dict(), panel_id=panel.panel_id,
                            window_key="current", query=keyword,
                        )
                        if normalized:
                            evidence.append(normalized)
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": "x",
                        "stage": "trend_candidate",
                        "trend_keyword": keyword,
                        "status": _x_source_status(x_result),
                        "count": len(x_result.items),
                        "error_category": x_result.health.error,
                        "coverage": {
                            **x_result.health.coverage,
                            **({"budget": budget.snapshot()} if budget is not None else {}),
                        },
                    })
            platform_results = await asyncio.gather(*(
                self._broker_search(
                    panel, platform, keyword,
                    count=5, time_filter=DISCOVERY_TIME_FILTER, sort="latest", hydrate=False,
                    budget=budget,
                    budget_operation="trend_candidate_search",
                )
                for platform in NON_X_DISCOVERY_PLATFORMS
            ))
            for result in platform_results:
                source = dict(result["source"])
                source.update(stage="trend_candidate", trend_keyword=keyword)
                evidence.extend(result["evidence"])
                sources.append(source)
        deduped = {item["id"]: item for item in evidence}
        return {
            "trend_candidates": candidates,
            "evidence": list(deduped.values()),
            "sources": sources,
        }

    async def _broker_search(
        self, panel: Panel, platform: str, query: str, *, count: int,
        time_filter: str = "week", sort: str = "latest", hydrate: bool = True,
        query_lineage_id: str | None = None,
        budget: AdaptiveCollectionBudget | None = None,
        budget_operation: str = "root_search",
        retry_empty: bool = False,
    ) -> dict[str, Any]:
        response = None
        platform_result = {}
        health = []
        status, error = "failed", None
        recovered_errors = []
        attempt_count = 0
        attempted_connectors: list[str] = []
        route_health: list[dict[str, Any]] = []
        for attempt in range(2):
            if budget is not None and not budget.reserve(
                platform=platform,
                operation=budget_operation if attempt == 0 else f"{budget_operation}_retry",
            ):
                return {
                    "evidence": [],
                    "roots": [],
                    "source": {
                        "panel_id": panel.panel_id,
                        "platform": platform,
                        "query": query,
                        "query_recipe_version": SOURCE_QUERY_RECIPE_VERSION,
                        "query_lineage_id": query_lineage_id,
                        "status": "partial",
                        "count": 0,
                        "error_category": "adaptive_budget_exhausted",
                        "coverage": {
                            "attempt_count": attempt_count,
                            "recovered_errors": recovered_errors,
                            "attempted_connectors": attempted_connectors,
                            "route_health": route_health,
                            "budget": budget.snapshot(),
                        },
                    },
                }
            attempt_count += 1
            try:
                response = await self.broker.search(
                    keyword=query,
                    platforms=[platform],
                    count=count,
                    time_filter=time_filter,
                    sort=sort,
                )
            except Exception as exc:
                if attempt == 0 and isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    recovered_errors.append(type(exc).__name__)
                    await asyncio.sleep(2.0)
                    continue
                return {
                    "evidence": [],
                    "source": {
                        "panel_id": panel.panel_id,
                        "platform": platform,
                        "query": query,
                        "query_recipe_version": SOURCE_QUERY_RECIPE_VERSION,
                        "status": "failed",
                        "count": 0,
                        "error_category": type(exc).__name__,
                        "coverage": {
                            "attempt_count": attempt_count,
                            "recovered_errors": recovered_errors,
                            "attempted_connectors": attempted_connectors,
                            "route_health": route_health,
                            **({"budget": budget.snapshot()} if budget is not None else {}),
                        },
                    },
                }
            platform_result = dict(
                (response.get("platform_results") or {}).get(platform) or {}
            )
            platform_result["platform"] = platform
            for connector_name in platform_result.get("attempted_connectors") or []:
                connector_name = str(connector_name or "").strip()
                if connector_name and connector_name not in attempted_connectors:
                    attempted_connectors.append(connector_name)
            health = list(response.get("source_health") or [])
            for health_item in health:
                if health_item.get("platform") != platform:
                    continue
                route_health.append({
                    "connector": health_item.get("connector"),
                    "status": health_item.get("status"),
                    "error": health_item.get("error"),
                    "items_returned": int(health_item.get("items_returned") or 0),
                    "coverage": dict(health_item.get("coverage") or {}),
                })
            status, error = _source_status(platform_result, health)
            if attempt == 0 and error == "connector_timeout":
                recovered_errors.append(error)
                await asyncio.sleep(2.0)
                continue
            if retry_empty:
                accepted_preview_count = sum(
                    _normalise_item(
                        item,
                        panel_id=panel.panel_id,
                        window_key="current",
                        query=query,
                        query_lineage_id=query_lineage_id,
                    ) is not None
                    for item in response.get("items") or []
                )
                transient_empty = (
                    accepted_preview_count == 0
                    and error in {
                        None,
                        "tiktok_empty_response",
                        "tiktok_query_empty",
                    }
                )
                if attempt == 0 and transient_empty:
                    recovered_errors.append(error or "tiktok_transient_empty")
                    await asyncio.sleep(2.0)
                    continue
                if transient_empty:
                    error = error or "tiktok_transient_empty"
            break
        if response is None:
            raise RuntimeError("broker search returned no response")
        evidence = []
        for item in response.get("items") or []:
            normalized = _normalise_item(
                item,
                panel_id=panel.panel_id,
                window_key="current",
                query=query,
                query_lineage_id=query_lineage_id,
            )
            if normalized:
                evidence.append(normalized)
        if hydrate and response.get("items"):
            ranked = sorted(
                response["items"],
                key=lambda item: (
                    (item.get("engagement") or {}).get("comments") or 0,
                    (item.get("engagement") or {}).get("likes") or 0,
                ),
                reverse=True,
            )[:1]
            for root in ranked:
                if budget is not None and not budget.reserve(
                    platform=platform, operation="hydrated_thread_read"
                ):
                    status = "partial"
                    error = "adaptive_budget_exhausted"
                    continue
                try:
                    thread = await self.broker.fetch_thread(root, max_comments=12, max_depth=2)
                except Exception:
                    continue
                for record in thread.records:
                    normalized = _thread_evidence(
                        record,
                        panel_id=panel.panel_id,
                        query=query,
                        query_lineage_id=query_lineage_id,
                        thread_result=thread,
                    )
                    if normalized:
                        evidence.append(normalized)
                if thread.status not in {"complete", "empty"}:
                    status = "partial" if evidence else "failed"
                    error = error or thread.error_category or "thread_partial"
        deduped = {item["id"]: item for item in evidence}
        return {
            "evidence": list(deduped.values()),
            "roots": [dict(item) for item in (response.get("items") or [])],
            "source": {
                "panel_id": panel.panel_id,
                "platform": platform,
                "query": query,
                "query_recipe_version": SOURCE_QUERY_RECIPE_VERSION,
                "query_lineage_id": query_lineage_id,
                "status": status,
                "count": len(deduped),
                "error_category": error,
                "coverage": {
                    **(platform_result.get("coverage") or {}),
                    "attempt_count": attempt_count,
                    "recovered_errors": recovered_errors,
                    "attempted_connectors": attempted_connectors,
                    "route_health": route_health,
                    **({"budget": budget.snapshot()} if budget is not None else {}),
                },
            },
        }

    async def collect_discovery(self, panel: Panel):
        evidence = []
        sources = []
        queries = panel.x_query_slices or (panel.x_query,)
        for query_index, query in enumerate(queries):
            try:
                x_result = await self.x_connector.search(
                    query, count=30, time_filter=DISCOVERY_TIME_FILTER, sort="latest"
                )
            except Exception as exc:
                sources.append({
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "stage": "discovery",
                    "query_index": query_index,
                    "query": query,
                    "status": "failed",
                    "count": 0,
                    "error_category": type(exc).__name__,
                    "coverage": {},
                })
                continue
            for item in x_result.items:
                normalized = _normalise_item(
                    item.to_dict(), panel_id=panel.panel_id,
                    window_key="current", query=query,
                )
                if normalized:
                    evidence.append(normalized)
            sources.append({
                "panel_id": panel.panel_id,
                "platform": "x",
                "stage": "discovery",
                "query_index": query_index,
                "query": query,
                "status": _x_source_status(x_result),
                "count": len(x_result.items),
                "error_category": x_result.health.error,
                "coverage": x_result.health.coverage,
            })
        platform_results = await asyncio.gather(*(
            self._broker_search(
                panel,
                platform,
                panel_platform_query(panel, platform),
                count=8,
                time_filter=DISCOVERY_TIME_FILTER,
                sort="latest",
                hydrate=False,
            )
            for platform in NON_X_DISCOVERY_PLATFORMS
        ))
        for result in platform_results:
            source = dict(result["source"])
            source["stage"] = "discovery"
            evidence.extend(result["evidence"])
            sources.append(source)
        deduped = {item["id"]: item for item in evidence}
        return {"evidence": list(deduped.values()), "sources": sources}

    async def collect_adaptive(
        self,
        panel: Panel,
        anchors: Sequence[Mapping[str, Any]],
        *,
        max_anchors: int = 4,
        max_roots_per_platform: int = 2,
        max_comments_per_root: int = 20,
        max_depth: int = 2,
        budget: AdaptiveCollectionBudget | None = None,
    ) -> dict[str, Any]:
        selected = [dict(value) for value in anchors[: max(0, int(max_anchors))]]
        evidence: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        depth_roots: dict[str, list[dict[str, Any]]] = {
            platform: [] for platform in NON_X_DISCOVERY_PLATFORMS
        }
        for anchor in selected:
            anchor_id = str(anchor.get("anchor_id") or "")
            query = str(anchor.get("normalized_anchor") or anchor.get("anchor_text") or "").strip()
            if not anchor_id or not query:
                continue
            x_query = f'"{query.replace(chr(34), "")}" -filter:retweets'
            x_lineage = make_query_lineage_id(
                panel_id=panel.panel_id,
                platform="x",
                seed_query=str(anchor.get("seed_query") or panel.search_term),
                anchor_id=anchor_id,
                query=x_query,
            )
            if budget is not None and not budget.reserve(
                platform="x", operation="adaptive_root_search"
            ):
                sources.append({
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "stage": "adaptive_root",
                    "anchor_id": anchor_id,
                    "query": x_query,
                    "query_lineage_id": x_lineage,
                    "status": "partial",
                    "count": 0,
                    "error_category": "adaptive_budget_exhausted",
                    "coverage": {"budget": budget.snapshot()},
                })
            else:
                try:
                    x_result = await self.x_connector.search(
                        x_query,
                        count=8,
                        time_filter=DISCOVERY_TIME_FILTER,
                        sort="latest",
                    )
                except Exception as exc:
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": "x",
                        "stage": "adaptive_root",
                        "anchor_id": anchor_id,
                        "query": x_query,
                        "query_lineage_id": x_lineage,
                        "status": "failed",
                        "count": 0,
                        "error_category": type(exc).__name__,
                        "coverage": (
                            {"budget": budget.snapshot()}
                            if budget is not None else {}
                        ),
                    })
                else:
                    for item in x_result.items:
                        normalized = _normalise_item(
                            item.to_dict(),
                            panel_id=panel.panel_id,
                            window_key="current",
                            query=x_query,
                            query_lineage_id=x_lineage,
                        )
                        if normalized:
                            evidence.append(normalized)
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": "x",
                        "stage": "adaptive_root",
                        "anchor_id": anchor_id,
                        "query": x_query,
                        "query_lineage_id": x_lineage,
                        "status": _x_source_status(x_result),
                        "count": len(x_result.items),
                        "error_category": x_result.health.error,
                        "coverage": {
                            **x_result.health.coverage,
                            **({"budget": budget.snapshot()} if budget is not None else {}),
                        },
                    })

            for platform in NON_X_DISCOVERY_PLATFORMS:
                lineage = make_query_lineage_id(
                    panel_id=panel.panel_id,
                    platform=platform,
                    seed_query=str(anchor.get("seed_query") or panel.search_term),
                    anchor_id=anchor_id,
                    query=query,
                )
                result = await self._broker_search(
                    panel,
                    platform,
                    query,
                    count=8,
                    time_filter=DISCOVERY_TIME_FILTER,
                    sort="latest",
                    hydrate=False,
                    query_lineage_id=lineage,
                    budget=budget,
                    budget_operation="adaptive_root_search",
                )
                source = dict(result.get("source") or {})
                source.update(
                    stage="adaptive_root",
                    anchor_id=anchor_id,
                    query_lineage_id=lineage,
                )
                sources.append(source)
                evidence.extend(result.get("evidence") or [])
                for root in result.get("roots") or []:
                    value = dict(root)
                    value["_adaptive_query"] = query
                    value["_adaptive_lineage"] = lineage
                    value["_adaptive_anchor_id"] = anchor_id
                    depth_roots.setdefault(platform, []).append(value)

        for platform, roots in depth_roots.items():
            ranked = sorted(
                roots,
                key=lambda item: (
                    -int((item.get("engagement") or {}).get("comments") or 0),
                    -int((item.get("engagement") or {}).get("likes") or 0),
                    str(item.get("post_id") or item.get("external_id") or item.get("url") or ""),
                ),
            )
            seen_roots = set()
            selected_roots = []
            for root in ranked:
                root_id = str(root.get("post_id") or root.get("external_id") or root.get("url") or "")
                if not root_id or root_id in seen_roots:
                    continue
                seen_roots.add(root_id)
                selected_roots.append(root)
                if len(selected_roots) >= max(0, int(max_roots_per_platform)):
                    break
            for root in selected_roots:
                root_id = str(root.get("post_id") or root.get("external_id") or root.get("url") or "")
                query = str(root.get("_adaptive_query") or "")
                lineage = str(root.get("_adaptive_lineage") or "")
                anchor_id = str(root.get("_adaptive_anchor_id") or "")
                requested_comments = max(0, int(max_comments_per_root))
                if platform == "reddit":
                    reported_comments = (root.get("engagement") or {}).get("comments")
                    if isinstance(reported_comments, int) and reported_comments > 0:
                        # One installed-client request can retrieve up to 100 comments.
                        # Match the source-reported thread size when practical rather
                        # than truncating every Reddit conversation at an arbitrary 20.
                        requested_comments = min(
                            100,
                            max(requested_comments, reported_comments),
                        )
                if budget is not None and not budget.reserve(
                    platform=platform, operation="adaptive_thread_read"
                ):
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": platform,
                        "stage": "adaptive_depth",
                        "anchor_id": anchor_id,
                        "root_external_id": root_id,
                        "query": query,
                        "query_lineage_id": lineage,
                        "status": "partial",
                        "returned_count": 0,
                        "truncated": True,
                        "error_category": "adaptive_budget_exhausted",
                        "limitations": ["Thread read skipped because the per-run adaptive budget was exhausted."],
                        "coverage": {"budget": budget.snapshot()},
                    })
                    continue
                try:
                    thread = await self.broker.fetch_thread(
                        root,
                        max_comments=requested_comments,
                        max_depth=max(0, int(max_depth)),
                    )
                except Exception as exc:
                    sources.append({
                        "panel_id": panel.panel_id,
                        "platform": platform,
                        "stage": "adaptive_depth",
                        "anchor_id": anchor_id,
                        "root_external_id": root_id,
                        "query": query,
                        "query_lineage_id": lineage,
                        "status": "failed",
                        "returned_count": 0,
                        "truncated": False,
                        "error_category": type(exc).__name__,
                        "coverage": (
                            {"budget": budget.snapshot()}
                            if budget is not None else {}
                        ),
                    })
                    continue
                for record in thread.records:
                    normalized = _thread_evidence(
                        record,
                        panel_id=panel.panel_id,
                        query=query,
                        query_lineage_id=lineage,
                        thread_result=thread,
                    )
                    if normalized:
                        evidence.append(normalized)
                sources.append({
                    "panel_id": panel.panel_id,
                    "platform": platform,
                    "stage": "adaptive_depth",
                    "anchor_id": anchor_id,
                    "root_external_id": root_id,
                    "query": query,
                    "query_lineage_id": lineage,
                    "status": thread.status,
                    "returned_count": len(thread.records),
                    "platform_reported_total": thread.platform_reported_total,
                    "truncated": bool(thread.truncated),
                    "max_comments": int(thread.max_comments),
                    "max_depth": int(thread.max_depth),
                    "attempted_route": thread.attempted_route,
                    "error_category": thread.error_category,
                    "limitations": list(thread.limitations),
                    "coverage": (
                        {"budget": budget.snapshot()}
                        if budget is not None else {}
                    ),
                })
        if budget is not None:
            budget_snapshot = budget.snapshot()
            sources.append({
                "panel_id": panel.panel_id,
                "platform": "adaptive_budget",
                "stage": "adaptive_budget",
                "status": "partial" if budget_snapshot["denied_attempts"] else "complete",
                "count": budget_snapshot["used_attempts"],
                "error_category": (
                    "adaptive_budget_exhausted"
                    if budget_snapshot["denied_attempts"] else None
                ),
                "coverage": budget_snapshot,
            })
        deduped = {item["id"]: item for item in evidence}
        return {
            "anchors": selected,
            "evidence": list(deduped.values()),
            "sources": sources,
        }

    @staticmethod
    def _anchor_query(anchor_terms: Sequence[str]) -> str:
        escaped = [str(term).replace('"', "").strip() for term in anchor_terms if str(term).strip()]
        return "(" + " OR ".join(f'"{term}"' for term in escaped[:5]) + ") -filter:retweets"

    async def collect_windows(
        self,
        panel: Panel,
        anchor_terms: Sequence[str],
        *,
        budget: AdaptiveCollectionBudget | None = None,
    ):
        now = datetime.now(timezone.utc)
        base_query = self._anchor_query(anchor_terms)
        windows = []
        evidence = []
        sources = []
        for index in range(4):
            # Use the latest fully completed seven-day window. Today is excluded
            # so every compared period has the same complete duration.
            end_date = (now - timedelta(days=7 * index)).date()
            start_date = end_date - timedelta(days=7)
            key = "current" if index == 0 else f"prior_{index}"
            query = f"{base_query} since:{start_date.isoformat()} until:{end_date.isoformat()}"
            if budget is not None and not budget.reserve(
                platform="x", operation="historical_window_search"
            ):
                budget_snapshot = budget.snapshot()
                windows.append({
                    "window_key": key,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "status": "partial",
                    "result_count": 0,
                    "unique_authors": 0,
                    "capped": False,
                    "query": query,
                    "anchor_query": base_query,
                    "error_category": "adaptive_budget_exhausted",
                    "missing_timestamp_count": 0,
                    "out_of_window_count": 0,
                })
                sources.append({
                    "panel_id": panel.panel_id,
                    "platform": "x",
                    "window_key": key,
                    "status": "partial",
                    "count": 0,
                    "capped": False,
                    "error_category": "adaptive_budget_exhausted",
                    "coverage": {"budget": budget_snapshot},
                })
                continue
            result = await self.x_connector.search(query, count=40, sort="latest")
            capped = bool(result.health.coverage.get("requested_limit_reached"))
            status = _x_source_status(result)
            window_start = datetime(
                start_date.year, start_date.month, start_date.day,
                tzinfo=timezone.utc,
            )
            window_end = datetime(
                end_date.year, end_date.month, end_date.day,
                tzinfo=timezone.utc,
            )
            authors = set()
            accepted_ids = set()
            missing_timestamp_count = 0
            out_of_window_count = 0
            for item in result.items:
                raw = item.to_dict()
                parsed_created_at = _created_at_utc(
                    raw.get("created_at") or raw.get("published_at")
                )
                if parsed_created_at is None:
                    missing_timestamp_count += 1
                    continue
                if not (window_start <= parsed_created_at < window_end):
                    out_of_window_count += 1
                    continue
                normalized = _normalise_item(
                    raw,
                    panel_id=panel.panel_id,
                    window_key=key,
                    query=query,
                    window_start=window_start,
                    window_end=window_end,
                    require_timestamp=True,
                )
                if not normalized or normalized["id"] in accepted_ids:
                    continue
                accepted_ids.add(normalized["id"])
                author_key = str(
                    normalized.get("creator_id") or normalized.get("author") or ""
                ).strip()
                if author_key:
                    authors.add(author_key)
                evidence.append(normalized)
            if status == "complete" and (
                missing_timestamp_count or out_of_window_count
            ):
                status = "partial"
            windows.append({
                "window_key": key,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "status": status,
                "result_count": len(accepted_ids),
                "unique_authors": len(authors),
                "capped": capped,
                "query": query,
                "anchor_query": base_query,
                "error_category": result.health.error,
                "missing_timestamp_count": missing_timestamp_count,
                "out_of_window_count": out_of_window_count,
            })
            sources.append({
                "panel_id": panel.panel_id,
                "platform": "x",
                "window_key": key,
                "status": status,
                "count": len(accepted_ids),
                "capped": capped,
                "error_category": result.health.error,
                "coverage": {
                    "missing_timestamp_count": missing_timestamp_count,
                    "out_of_window_count": out_of_window_count,
                    **({"budget": budget.snapshot()} if budget is not None else {}),
                },
            })
        return {"windows": windows, "evidence": evidence, "sources": sources}

    async def collect_corroboration(self, panel: Panel, anchor_terms: Sequence[str]):
        query = str(anchor_terms[0])
        evidence = []
        sources = []
        platform_results = await asyncio.gather(*(
            self._broker_search(
                panel,
                platform,
                query,
                count=5,
                time_filter=DISCOVERY_TIME_FILTER,
                sort="latest",
                hydrate=True,
            )
            for platform in NON_X_DISCOVERY_PLATFORMS
        ))
        for result in platform_results:
            source = dict(result["source"])
            source["stage"] = "corroboration"
            source["query"] = query
            evidence.extend(result["evidence"])
            sources.append(source)
        deduped = {item["id"]: item for item in evidence}
        return {"evidence": list(deduped.values()), "sources": sources}


async def check_search_trajectory(query: str) -> dict[str, Any]:
    return await asyncio.to_thread(collect_search_trajectory, query)


async def check_movement_bundles(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    # Worldwide history answers the initial durability question. Additional
    # countries are progressive enrichment, not a prerequisite for discovery.
    return await asyncio.to_thread(
        collect_movement_bundles,
        candidates,
        geographies=MOVEMENT_GEOGRAPHIES[:1],
    )


async def check_news_parity(label: str, anchors: Sequence[str]) -> dict[str, Any]:
    query = " OR ".join(f'"{str(anchor).replace(chr(34), "")}"' for anchor in anchors[:3]) or label
    url = GOOGLE_NEWS_RSS.format(query=quote(query))
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()
    except Exception:
        return {
            "level": "unknown", "status": "source_unavailable", "articles": [],
            "checked_source": "Google News RSS",
        }
    articles = parse_google_news_rss(response.text)[:20]
    public_articles = [{
        "title": article.title,
        "url": article.link,
        "source": article.source,
        "published": article.published,
    } for article in articles[:8]]
    financial = [
        article for article in articles
        if any(source in article.source.casefold() for source in FINANCIAL_SOURCES)
    ]
    if financial:
        level, status = "L3.5", "financial_coverage"
    elif articles:
        level, status = "L2", "consumer_or_specialist_coverage"
    else:
        level, status = "L0", "social_only_in_checked_sources"
    return {
        "level": level,
        "status": status,
        "articles": public_articles,
        "checked_source": "Google News RSS",
        "query": query,
    }


def build_private_scanner(store: PrivateRadarStore):
    return PrivateRadarScanner(
        store,
        OwnedRadarCollector(),
        panels=DEFAULT_PANELS,
        news_check_fn=check_news_parity,
        trajectory_check_fn=check_search_trajectory,
        movement_bundle_fn=check_movement_bundles,
    )
