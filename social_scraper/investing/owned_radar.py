"""Owned-source collection adapter for the private investment Radar."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx

from apis.news_search import GOOGLE_NEWS_RSS, parse_google_news_rss
from apis.social_search_api import build_default_broker
from social_scraper.connectors.x_graphql import XConnector
from social_scraper.investing.google_discovery import collect_worldwide_trend_candidates
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


def panel_platform_query(panel: Panel, platform: str) -> str:
    if platform == "reddit":
        return REDDIT_PANEL_QUERIES.get(panel.panel_id, panel.name)
    return panel.search_term


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_status(platform_result: Mapping[str, Any] | None, health: Sequence[Mapping[str, Any]]):
    result = platform_result or {}
    state = str(result.get("status") or "error")
    if state == "ok":
        return "complete", None
    status = "partial" if state == "partial" else "failed"
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
    error = next(
        (item.get("error") for item in reversed(matching) if item.get("error")),
        None,
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


def _normalise_item(
    item: Mapping[str, Any], *, panel_id: str, window_key: str, query: str,
) -> dict[str, Any] | None:
    value = dict(item)
    text = str(value.get("text") or value.get("title") or "").strip()
    url = str(value.get("url") or "").strip()
    if not text or not url.startswith(("http://", "https://")):
        return None
    author = value.get("author") or {}
    if isinstance(author, Mapping):
        author_name = str(author.get("username") or author.get("display_name") or "").strip()
    else:
        author_name = str(author or "").strip()
    evidence = {
        "panel_id": panel_id,
        "platform": str(value.get("platform") or "unknown"),
        "external_id": str(value.get("post_id") or value.get("external_id") or "") or None,
        "url": url,
        "author": author_name or None,
        "text": text[:6000],
        "engagement": _normalise_engagement(value.get("engagement")),
        "created_at": value.get("created_at") or value.get("published_at"),
        "observed_at": _utc_iso(),
        "window_key": window_key,
        "query": query,
    }
    evidence["id"] = stable_evidence_id(evidence)
    return evidence


def _thread_evidence(record, *, panel_id: str, query: str) -> dict[str, Any] | None:
    value = {
        "panel_id": panel_id,
        "platform": record.platform,
        "external_id": record.external_id,
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
    ):
        self.broker = broker or build_default_broker(route_timeout_seconds=90.0)
        self.x_connector = x_connector or XConnector()
        self.trajectory_check_fn = trajectory_check_fn or check_search_trajectory
        self.trend_discovery_fn = trend_discovery_fn or collect_worldwide_trend_candidates

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
                panel_platform_query(panel, platform),
                count=3,
                time_filter="month",
                sort="latest",
                hydrate=False,
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

    async def collect_trend_discovery(self) -> dict[str, Any]:
        """Generate Google candidates first, then collect social roots for mapped panels."""
        discovery = await asyncio.to_thread(self.trend_discovery_fn)
        candidates = [dict(value) for value in discovery.get("candidates") or []]
        sources = [{
            "platform": "google_trends",
            "stage": "trend_discovery",
            "status": str(discovery.get("status") or "failed"),
            "count": len(candidates),
            "observed_at": discovery.get("observed_at"),
            "geographies": list(discovery.get("geographies") or []),
            "failures": list(discovery.get("failures") or []),
            "candidates": candidates,
        }]
        evidence = []
        panels = {panel.panel_id: panel for panel in DEFAULT_PANELS}
        for candidate in candidates:
            panel = panels.get(str(candidate.get("panel_id") or ""))
            keyword = str(candidate.get("keyword") or "").strip()
            if panel is None or not keyword:
                continue
            x_query = f'"{keyword.replace(chr(34), "")}" -filter:retweets'
            try:
                x_result = await self.x_connector.search(
                    x_query, count=10, time_filter="month", sort="latest"
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
                    "coverage": {},
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
                    "coverage": x_result.health.coverage,
                })
            platform_results = await asyncio.gather(*(
                self._broker_search(
                    panel, platform, keyword,
                    count=5, time_filter="month", sort="latest", hydrate=False,
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
    ) -> dict[str, Any]:
        response = None
        platform_result = {}
        health = []
        status, error = "failed", None
        recovered_errors = []
        attempt_count = 0
        for attempt in range(2):
            attempt_count = attempt + 1
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
                        "status": "failed",
                        "count": 0,
                        "error_category": type(exc).__name__,
                        "coverage": {},
                    },
                }
            platform_result = dict(
                (response.get("platform_results") or {}).get(platform) or {}
            )
            platform_result["platform"] = platform
            health = list(response.get("source_health") or [])
            status, error = _source_status(platform_result, health)
            if attempt == 0 and error == "connector_timeout":
                recovered_errors.append(error)
                await asyncio.sleep(2.0)
                continue
            break
        if response is None:
            raise RuntimeError("broker search returned no response")
        evidence = []
        for item in response.get("items") or []:
            normalized = _normalise_item(
                item, panel_id=panel.panel_id, window_key="current", query=query
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
                try:
                    thread = await self.broker.fetch_thread(root, max_comments=12, max_depth=2)
                except Exception:
                    continue
                for record in thread.records:
                    normalized = _thread_evidence(record, panel_id=panel.panel_id, query=query)
                    if normalized:
                        evidence.append(normalized)
                if thread.status not in {"complete", "empty"}:
                    status = "partial" if evidence else "failed"
                    error = error or thread.error_category or "thread_partial"
        deduped = {item["id"]: item for item in evidence}
        return {
            "evidence": list(deduped.values()),
            "source": {
                "panel_id": panel.panel_id,
                "platform": platform,
                "query": query,
                "status": status,
                "count": len(deduped),
                "error_category": error,
                "coverage": {
                    **(platform_result.get("coverage") or {}),
                    "attempt_count": attempt_count,
                    "recovered_errors": recovered_errors,
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
                    query, count=30, time_filter="month", sort="latest"
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
                time_filter="month",
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

    @staticmethod
    def _anchor_query(anchor_terms: Sequence[str]) -> str:
        escaped = [str(term).replace('"', "").strip() for term in anchor_terms if str(term).strip()]
        return "(" + " OR ".join(f'"{term}"' for term in escaped[:5]) + ") -filter:retweets"

    async def collect_windows(self, panel: Panel, anchor_terms: Sequence[str]):
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
            result = await self.x_connector.search(query, count=40, sort="latest")
            capped = bool(result.health.coverage.get("requested_limit_reached"))
            status = _x_source_status(result)
            authors = set()
            seen = set()
            for item in result.items:
                if item.post_id in seen:
                    continue
                seen.add(item.post_id)
                authors.add(item.author_username or item.post_id)
                normalized = _normalise_item(
                    item.to_dict(), panel_id=panel.panel_id,
                    window_key=key, query=query,
                )
                if normalized:
                    evidence.append(normalized)
            windows.append({
                "window_key": key,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "status": status,
                "result_count": len(seen),
                "unique_authors": len(authors),
                "capped": capped,
                "query": query,
                "anchor_query": base_query,
                "error_category": result.health.error,
            })
            sources.append({
                "panel_id": panel.panel_id,
                "platform": "x",
                "window_key": key,
                "status": status,
                "count": len(seen),
                "capped": capped,
                "error_category": result.health.error,
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
                time_filter="month",
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
    return await asyncio.to_thread(collect_movement_bundles, candidates)


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
