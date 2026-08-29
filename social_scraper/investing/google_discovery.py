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


def _article(value: Any) -> dict[str, Any] | None:
    title = str(getattr(value, "title", "") or "").strip()
    url = str(getattr(value, "url", "") or "").strip()
    if not title or not url.startswith(("http://", "https://")):
        return None
    published = _timestamp(getattr(value, "time", None))
    article = {
        "title": title,
        "url": url,
        "source": str(getattr(value, "source", "") or "").strip() or None,
        "published_at": (
            datetime.fromtimestamp(published, tz=timezone.utc).isoformat()
            if published is not None else None
        ),
        "snippet": str(getattr(value, "snippet", "") or "").strip() or None,
    }
    return article


def build_trend_context(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Create concise source-grounded context without spending model tokens."""
    articles = [
        dict(value) for value in candidate.get("context_articles") or []
        if isinstance(value, Mapping)
        and str(value.get("title") or "").strip()
        and str(value.get("url") or "").startswith(("http://", "https://"))
    ]
    categories = [str(value) for value in candidate.get("categories") or [] if str(value)]
    keyword = _norm(candidate.get("keyword"))
    related = [
        str(value).strip() for value in candidate.get("keyword_basket") or []
        if str(value).strip() and _norm(value) != keyword
    ][:3]
    if articles:
        category_text = ", ".join(categories[:2]) or "Search"
        related_text = f" Related searches: {', '.join(related)}." if related else ""
        what_it_is = (
            f"{category_text} topic. Recent coverage: {articles[0]['title']}."
            f"{related_text}"
        )
        sources = ", ".join(dict.fromkeys(
            str(article.get("source") or "").strip()
            for article in articles[:3]
            if str(article.get("source") or "").strip()
        ))
        why_rising = (
            f"Current headlines from {sources} are focused on this term."
            if sources else "Current headlines are focused on this term."
        )
    else:
        category_text = ", ".join(categories[:2]) or "Unclassified"
        related_text = ", ".join(related) or "no related terms were returned"
        what_it_is = f"{category_text} topic. Related searches: {related_text}."
        why_rising = (
            "Google flagged a recent search increase, but the exact catalyst was not "
            "established by the collected source context."
        )
    return {
        "what_it_is": what_it_is,
        "why_rising": why_rising,
        "investing_angle": (
            "Search attention alone does not establish a listed beneficiary. Check cited "
            "behavior and verified brand exposure before treating it as investable."
        ),
        "source_urls": [article["url"] for article in articles[:3]],
    }


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
                "_news_tokens": [],
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
            for token in getattr(row, "news_tokens", None) or []:
                normalized_token = list(token) if isinstance(token, (list, tuple)) else token
                if normalized_token not in candidate["_news_tokens"]:
                    candidate["_news_tokens"].append(normalized_token)
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
    for candidate in selected:
        tokens = candidate.pop("_news_tokens", [])
        articles = []
        context_error = None
        if tokens and hasattr(trends, "trending_now_news_by_ids"):
            try:
                raw_articles = trends.trending_now_news_by_ids(tokens, max_news=3)
                articles = [
                    parsed for value in (raw_articles or [])
                    if (parsed := _article(value)) is not None
                ]
            except Exception as exc:
                context_error = type(exc).__name__
        candidate["context_articles"] = articles
        candidate["context_error_category"] = context_error
        candidate["context"] = build_trend_context(candidate)
    return {
        "status": "complete" if not failures else "partial",
        "source": "Google Trends Trending Now",
        "observed_at": datetime.fromtimestamp(now_timestamp, tz=timezone.utc).isoformat(),
        "geographies": list(geographies),
        "candidate_count": len(selected),
        "candidates": selected,
        "failures": failures,
    }
