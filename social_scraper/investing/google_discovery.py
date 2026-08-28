"""Worldwide-first Google Trends candidate discovery for the private Radar.

Trending Now is only a candidate generator. Source-native observations stay
separate by country; volumes are never summed or compared as absolute demand.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from social_scraper.monitoring.topdown import TOPIC_CATEGORIES


DEFAULT_DISCOVERY_GEOGRAPHIES = (
    "US", "GB", "SG", "DE", "FR", "JP", "IN", "CA", "AU", "BR",
)
MOVEMENT_GEOGRAPHIES = (
    {"code": "", "name": "Worldwide"},
    {"code": "US", "name": "United States"},
    {"code": "GB", "name": "United Kingdom"},
    {"code": "SG", "name": "Singapore"},
    {"code": "DE", "name": "Germany"},
    {"code": "FR", "name": "France"},
)
MOVEMENT_HORIZONS = (
    {"code": "3m", "name": "3 months", "timeframe": "today 3-m"},
    {"code": "1y", "name": "1 year", "timeframe": "today 12-m"},
    {"code": "5y", "name": "5 years", "timeframe": "today 5-y"},
)

_CATEGORY_TO_PANEL = {
    "Autos & Vehicles": "automobiles",
    "Beauty & Fashion": "fashion_apparel",
    "Business & Finance": "fintech_payments",
    "Entertainment": "streaming",
    "Food & Drink": "food_beverage",
    "Health": "fitness_wearables",
    "Pets & Animals": "pets",
    "Shopping": "retail",
    "Technology": "consumer_technology",
    "Travel & Transportation": "hotels_travel",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _categories(topic_ids: Sequence[int]) -> list[str]:
    values = []
    for topic_id in topic_ids:
        name = TOPIC_CATEGORIES.get(topic_id)
        if name and name not in values:
            values.append(name)
    return values or ["Other"]


def panel_for_trend_categories(categories: Sequence[str]) -> str | None:
    return next(
        (_CATEGORY_TO_PANEL[value] for value in categories if value in _CATEGORY_TO_PANEL),
        None,
    )


def _timestamp(value: Any) -> float | None:
    values = value if isinstance(value, list) else [value]
    parsed = []
    for item in values:
        try:
            parsed.append(float(item))
        except (TypeError, ValueError):
            continue
    return max(parsed) if parsed else None


def _candidate_rank(candidate: Mapping[str, Any]) -> tuple:
    observations = candidate.get("observations") or []
    growth = max(
        (float(item["growth_pct"]) for item in observations if item.get("growth_pct") is not None),
        default=float("-inf"),
    )
    newest_rank = min(
        (int(item.get("source_rank") or 10**9) for item in observations),
        default=10**9,
    )
    return (int(candidate.get("country_breadth") or 0), growth, -newest_rank)


def _balanced(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        buckets[(candidate.get("categories") or ["Other"])[0]].append(candidate)
    for values in buckets.values():
        values.sort(key=_candidate_rank, reverse=True)
    names = sorted(buckets, key=str.casefold)
    selected = []
    index = 0
    while names and len(selected) < limit:
        name = names[index]
        selected.append(buckets[name].pop(0))
        if not buckets[name]:
            names.pop(index)
            if not names:
                break
            index %= len(names)
        else:
            index = (index + 1) % len(names)
    return selected


def collect_worldwide_trend_candidates(
    *,
    trends=None,
    geographies: Sequence[str] = DEFAULT_DISCOVERY_GEOGRAPHIES,
    limit: int = 8,
    now_timestamp: float | None = None,
) -> dict[str, Any]:
    """Aggregate source-native Trending Now observations across countries."""
    if trends is None:
        from trendspy import Trends
        trends = Trends()
    now_timestamp = time.time() if now_timestamp is None else float(now_timestamp)
    merged: dict[str, dict[str, Any]] = {}
    failures = []
    for geo in geographies:
        try:
            rows = trends.trending_now(geo=geo)
        except Exception as exc:
            failures.append({"geo": geo, "error_category": type(exc).__name__})
            continue
        for rank, row in enumerate(rows, start=1):
            keyword = str(getattr(row, "keyword", "") or "").strip()
            key = _norm(keyword)
            if not key:
                continue
            topic_ids = [
                int(value) for value in (getattr(row, "topics", None) or [])
                if isinstance(value, int)
            ]
            candidate = merged.setdefault(key, {
                "keyword": keyword,
                "normalized_keyword": key,
                "categories": [],
                "countries": [],
                "country_breadth": 0,
                "observations": [],
                "keyword_basket": [keyword],
                "source": "Google Trends Trending Now",
            })
            for category in _categories(topic_ids):
                if category not in candidate["categories"]:
                    candidate["categories"].append(category)
            related = [
                str(value).strip() for value in (getattr(row, "trend_keywords", None) or [])
                if str(value).strip()
            ]
            for value in related:
                if _norm(value) not in {_norm(item) for item in candidate["keyword_basket"]}:
                    candidate["keyword_basket"].append(value)
            started = _timestamp(getattr(row, "started_timestamp", None))
            observation = {
                "geo": geo,
                "search_volume": getattr(row, "volume", None),
                "growth_pct": getattr(row, "volume_growth_pct", None),
                "source_rank": rank,
            }
            if started is not None:
                observation["source_started_at"] = datetime.fromtimestamp(
                    started, tz=timezone.utc
                ).isoformat()
                observation["started_hours_ago"] = max(
                    0.0, round((now_timestamp - started) / 3600, 1)
                )
            candidate["observations"].append(observation)
            if geo not in candidate["countries"]:
                candidate["countries"].append(geo)
    candidates = []
    for candidate in merged.values():
        candidate["countries"].sort()
        candidate["observations"].sort(key=lambda item: item["geo"])
        candidate["country_breadth"] = len(candidate["countries"])
        candidate["keyword_basket"] = candidate["keyword_basket"][:5]
        candidate["panel_id"] = panel_for_trend_categories(candidate["categories"])
        candidates.append(candidate)
    selected = _balanced(candidates, max(1, int(limit)))
    return {
        "status": "complete" if not failures else "partial",
        "source": "Google Trends Trending Now",
        "observed_at": datetime.fromtimestamp(now_timestamp, tz=timezone.utc).isoformat(),
        "geographies": list(geographies),
        "candidate_count": len(selected),
        "candidates": selected,
        "failures": failures,
    }
