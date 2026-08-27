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
from social_scraper.investing.private_radar import (
    DEFAULT_PANELS,
    NON_X_DISCOVERY_PLATFORMS,
    Panel,
    PrivateRadarScanner,
    PrivateRadarStore,
    stable_evidence_id,
)


FINANCIAL_SOURCES = (
    "reuters", "bloomberg", "cnbc", "financial times", "wall street journal",
    "barron's", "marketwatch", "seeking alpha", "investor's business daily",
    "morningstar", "the motley fool", "yahoo finance", "benzinga",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_status(platform_result: Mapping[str, Any] | None, health: Sequence[Mapping[str, Any]]):
    state = str((platform_result or {}).get("status") or "error")
    if state == "ok":
        status = "complete"
    elif state == "partial":
        status = "partial"
    else:
        status = "failed"
    error = next(
        (item.get("error") for item in reversed(list(health)) if item.get("platform") == (platform_result or {}).get("platform") and item.get("error")),
        None,
    )
    return status, error


def _x_source_status(result) -> str:
    state = str(result.health.status or "error")
    if state == "ok" and result.health.error is None:
        return "complete"
    return "partial" if result.items else "failed"


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
    def __init__(self, broker=None, x_connector=None):
        self.broker = broker or build_default_broker(route_timeout_seconds=90.0)
        self.x_connector = x_connector or XConnector()

    async def _broker_search(
        self, panel: Panel, platform: str, query: str, *, count: int,
        time_filter: str = "week", sort: str = "latest", hydrate: bool = True,
    ) -> dict[str, Any]:
        response = await self.broker.search(
            keyword=query,
            platforms=[platform],
            count=count,
            time_filter=time_filter,
            sort=sort,
        )
        platform_result = dict((response.get("platform_results") or {}).get(platform) or {})
        platform_result["platform"] = platform
        health = list(response.get("source_health") or [])
        status, error = _source_status(platform_result, health)
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
                "status": status,
                "count": len(deduped),
                "error_category": error,
                "coverage": platform_result.get("coverage") or {},
            },
        }

    async def collect_discovery(self, panel: Panel):
        evidence = []
        sources = []
        queries = panel.x_query_slices or (panel.x_query,)
        for query_index, query in enumerate(queries):
            x_result = await self.x_connector.search(
                query, count=30, time_filter="week", sort="latest"
            )
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
                panel.search_term,
                count=8,
                time_filter="month" if platform == "youtube" else "week",
                sort="latest",
                hydrate=False,
            )
            for platform in NON_X_DISCOVERY_PLATFORMS
        ))
        for result in platform_results:
            source = dict(result["source"])
            source["stage"] = "discovery"
            source["query"] = panel.search_term
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
                time_filter="month" if platform == "youtube" else "week",
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
    )
