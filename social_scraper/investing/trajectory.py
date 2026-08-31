"""Search-interest movement for Radar subjects.

Google Trends is a movement sensor, not behavioral evidence. Values are normalized
0-100 inside one comparable request and never used as social proof.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Mapping, Sequence

from social_scraper.investing.google_discovery import (
    MOVEMENT_GEOGRAPHIES,
    MOVEMENT_HORIZONS,
)


_QUERY_STOPWORDS = {
    "a", "an", "and", "after", "as", "at", "because", "before", "bought",
    "buy", "cancelling", "cancellation", "cancel", "from", "in", "into", "my",
    "of", "on", "pain", "post", "pushing", "replacing", "reported", "services",
    "streaming", "the", "their", "this", "to", "true", "using", "with",
    "adoption", "recall", "trying", "membership",
}


def _google_request_delay() -> float:
    try:
        return max(
            1.0,
            float(os.getenv("BOUNTY_GOOGLE_TRENDS_REQUEST_DELAY", "16")),
        )
    except ValueError:
        return 16.0


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def derive_trajectory_query(candidate: Mapping[str, Any]) -> str:
    """Choose a concise repeated phrase without hardcoding a product or subject."""
    phrases = [
        str(candidate.get("label") or ""),
        *[str(value) for value in candidate.get("anchor_terms") or []],
    ]
    normalized = [_norm(value) for value in phrases if _norm(value)]
    token_counts = Counter(
        token for phrase in normalized for token in phrase.split()
        if token not in _QUERY_STOPWORDS and len(token) > 1
    )
    candidates: list[tuple[float, int, int, str]] = []
    seen = set()
    for phrase_index, phrase in enumerate(normalized):
        tokens = phrase.split()
        for size in (2, 3):
            for start in range(len(tokens) - size + 1):
                chunk = tokens[start:start + size]
                if any(token in _QUERY_STOPWORDS or len(token) <= 1 for token in chunk):
                    continue
                value = " ".join(chunk)
                if value in seen:
                    continue
                seen.add(value)
                average_frequency = sum(token_counts[token] for token in chunk) / size
                # Prefer repeated, concise phrases. Later phrases win exact ties so an
                # explicit anchor can beat broad label wording.
                candidates.append((average_frequency, -size, phrase_index, value))
    if candidates:
        base = max(candidates)[-1]
        base_tokens = base.split()
        single_entities = [
            _norm(value)
            for value in candidate.get("anchor_terms") or []
            if len(_norm(value).split()) == 1
            and str(value).strip()[:1].isupper()
            and _norm(value) not in _QUERY_STOPWORDS
            and _norm(value) not in base_tokens
        ]
        if single_entities and len(base_tokens) < 4:
            entity = single_entities[0]
            label_tokens = _norm(candidate.get("label")).split()
            if entity in label_tokens and base_tokens[0] in label_tokens:
                if label_tokens.index(entity) < label_tokens.index(base_tokens[0]):
                    base_tokens.insert(0, entity)
                else:
                    base_tokens.append(entity)
            else:
                base_tokens.append(entity)
        return " ".join(base_tokens[:4])
    fallback = [
        token for token, _count in token_counts.most_common(3)
        if token not in _QUERY_STOPWORDS
    ]
    return " ".join(fallback[:3]) or _norm(candidate.get("label"))[:80]


def build_trajectory_query_basket(
    candidate: Mapping[str, Any], *, max_queries: int = 2,
) -> list[dict[str, str]]:
    """Build a short, transparent set of public Google Trends queries."""
    limit = max(1, int(max_queries))
    options: list[dict[str, str]] = []
    seen = set()

    def add(value: Any, *, source: str, reason: str) -> None:
        query = " ".join(str(value or "").strip().strip('"').split())
        if not query or len(query) > 80 or len(query.split()) > 6:
            return
        key = query.casefold()
        if key in seen or len(options) >= limit:
            return
        seen.add(key)
        options.append({"query": query, "source": source, "reason": reason})

    movement_bundle = (
        candidate.get("movement_bundle")
        if isinstance(candidate.get("movement_bundle"), Mapping)
        else {}
    )
    primary = movement_bundle.get("query") or candidate.get("trajectory_query")
    if primary:
        add(
            primary,
            source="selected_query",
            reason="Primary query selected for this subject.",
        )

    keyword_basket = candidate.get("keyword_basket") or []
    for value in keyword_basket:
        add(
            value,
            source="google_related_term",
            reason="Related term returned with the Google Trends candidate.",
        )

    if not keyword_basket and candidate.get("keyword"):
        add(
            candidate.get("keyword"),
            source="google_related_term",
            reason="Canonical term returned by Google Trends.",
        )

    for value in candidate.get("anchor_terms") or []:
        add(
            value,
            source="cited_social_anchor",
            reason="Exact phrase found in the cited social evidence.",
        )

    if len(options) < limit:
        add(
            derive_trajectory_query(candidate),
            source="derived_subject_phrase",
            reason="Concise phrase derived from the subject and its evidence anchors.",
        )
    return options


def collect_search_trajectory(
    query: str,
    *,
    trends=None,
    timeframe: str = "today 3-m",
    geo: str = "",
) -> dict[str, Any]:
    """Fetch one comparable normalized Google Trends series."""
    query = str(query or "").strip()
    base = {
        "query": query,
        "source": "Google Trends",
        "timeframe": timeframe,
        "geo": geo,
        "normalized": True,
        "points": [],
    }
    if not query:
        return {**base, "status": "failed", "error_category": "empty_query"}
    try:
        if trends is None:
            from trendspy import Trends
            trends = Trends(request_delay=_google_request_delay())
        frame = trends.interest_over_time(
            [query],
            timeframe=timeframe,
            geo=geo,
            headers={"referer": "https://trends.google.com/"},
        )
        if frame is None or len(frame) == 0 or query not in frame:
            return {**base, "status": "insufficient_search_volume", "error_category": None}
        values = frame[query].tolist()
        dates = frame.index.tolist()
        points = [
            {"date": str(day)[:10], "value": int(value)}
            for day, value in zip(dates, values)
            if value is not None
        ]
    except Exception as exc:
        return {
            **base,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
    nonzero = sum(int(point["value"]) > 0 for point in points)
    status = "complete" if len(points) >= 30 and nonzero >= 8 else "insufficient_search_volume"
    return {
        **base,
        "status": status,
        "points": points,
        "error_category": None,
        "nonzero_points": nonzero,
    }


def _frame_series(frame, query: str, *, geo: str, horizon: str, timeframe: str) -> dict[str, Any]:
    base = {
        "query": query,
        "source": "Google Trends",
        "geo": geo,
        "horizon": horizon,
        "timeframe": timeframe,
        "normalized": True,
        "points": [],
    }
    if frame is None or len(frame) == 0 or query not in frame:
        return {**base, "status": "insufficient_search_volume", "error_category": None}
    values = frame[query].tolist()
    dates = frame.index.tolist()
    points = [
        {"date": str(day)[:10], "value": int(value)}
        for day, value in zip(dates, values)
        if value is not None
    ]
    nonzero = sum(int(point["value"]) > 0 for point in points)
    status = "complete" if len(points) >= 30 and nonzero >= 8 else "insufficient_search_volume"
    return {
        **base,
        "status": status,
        "points": points,
        "error_category": None,
        "nonzero_points": nonzero,
    }


def select_default_movement_query(bundle: dict[str, Any]) -> dict[str, Any]:
    """Select the query with the most usable, complete history without hiding options."""
    options = bundle.get("query_options") or []
    if not options:
        return bundle

    def option_score(index_option):
        index, option = index_option
        series = option.get("series") or {}
        complete_series = sum(
            value.get("status") == "complete"
            for horizons_by_geo in series.values()
            for value in horizons_by_geo.values()
            if isinstance(value, Mapping)
        )
        default_series = (
            (series.get(bundle.get("default_geo") or "WORLDWIDE") or {}).get(
                bundle.get("default_horizon") or "3m"
            ) or {}
        )
        points = default_series.get("points") or []
        nonzero = sum(int(point.get("value") or 0) > 0 for point in points)
        usable = (
            default_series.get("status") == "complete"
            and len(points) >= 30
            and nonzero >= 8
        )
        return (int(usable), complete_series, nonzero, -index)

    _selected_index, primary = max(enumerate(options), key=option_score)
    bundle["query"] = primary["query"]
    bundle["default_query"] = primary["query"]
    bundle["series"] = primary["series"]
    bundle["classification"] = primary.get("classification")
    return bundle


def collect_movement_bundles(
    candidates: Sequence[Mapping[str, Any]],
    *,
    trends=None,
    geographies: Sequence[Mapping[str, str]] = MOVEMENT_GEOGRAPHIES,
    horizons: Sequence[Mapping[str, str]] = MOVEMENT_HORIZONS,
    batch_size: int = 5,
) -> list[dict[str, Any]]:
    """Collect multiple transparent queries across selectable geographies/horizons."""
    if trends is None:
        from trendspy import Trends
        trends = Trends(request_delay=_google_request_delay())

    baskets = [build_trajectory_query_basket(candidate) for candidate in candidates]
    bundles: list[dict[str, Any]] = []
    query_targets: dict[str, list[tuple[int, int]]] = {}
    for bundle_index, basket in enumerate(baskets):
        options = [
            {**dict(option), "series": {}}
            for option in basket
        ]
        primary_query = options[0]["query"] if options else ""
        bundle = {
            "query": primary_query,
            "source": "Google Trends",
            "default_query": primary_query,
            "default_geo": "WORLDWIDE",
            "default_horizon": "3m",
            "geographies": [
                {
                    "code": str(value.get("code") or "WORLDWIDE"),
                    "name": str(value.get("name") or ""),
                }
                for value in geographies
            ],
            "horizons": [
                {
                    "code": str(value.get("code") or ""),
                    "name": str(value.get("name") or ""),
                }
                for value in horizons
            ],
            "query_options": options,
            "series": {},
        }
        bundles.append(bundle)
        for option_index, option in enumerate(options):
            query_targets.setdefault(option["query"], []).append(
                (bundle_index, option_index)
            )

    unique_queries = list(query_targets)
    for geography in geographies:
        geo = str(geography.get("code") or "")
        geo_key = geo or "WORLDWIDE"
        for bundle in bundles:
            for option in bundle["query_options"]:
                option["series"][geo_key] = {}
        for horizon in horizons:
            horizon_code = str(horizon.get("code") or "")
            timeframe = str(horizon.get("timeframe") or "")
            for start in range(0, len(unique_queries), max(1, int(batch_size))):
                chunk = unique_queries[start:start + max(1, int(batch_size))]
                if not chunk:
                    continue
                try:
                    frame = trends.interest_over_time(
                        chunk,
                        timeframe=timeframe,
                        geo=geo,
                        headers={"referer": "https://trends.google.com/"},
                    )
                except Exception as exc:
                    for query in chunk:
                        series = {
                            "query": query,
                            "source": "Google Trends",
                            "geo": geo,
                            "horizon": horizon_code,
                            "timeframe": timeframe,
                            "normalized": True,
                            "status": "failed",
                            "points": [],
                            "error_category": type(exc).__name__,
                        }
                        for bundle_index, option_index in query_targets[query]:
                            bundles[bundle_index]["query_options"][option_index][
                                "series"
                            ][geo_key][horizon_code] = dict(series)
                    continue
                for query in chunk:
                    try:
                        series = _frame_series(
                            frame, query, geo=geo, horizon=horizon_code,
                            timeframe=timeframe,
                        )
                    except Exception as exc:
                        series = {
                            "query": query,
                            "source": "Google Trends",
                            "geo": geo,
                            "horizon": horizon_code,
                            "timeframe": timeframe,
                            "normalized": True,
                            "status": "failed",
                            "points": [],
                            "error_category": type(exc).__name__,
                        }
                    for bundle_index, option_index in query_targets[query]:
                        bundles[bundle_index]["query_options"][option_index][
                            "series"
                        ][geo_key][horizon_code] = dict(series)

    for bundle in bundles:
        for option in bundle["query_options"]:
            option["classification"] = classify_movement_bundle({
                "default_geo": bundle["default_geo"],
                "series": option["series"],
            })
        if bundle["query_options"]:
            select_default_movement_query(bundle)
        else:
            bundle["classification"] = classify_movement_bundle(None)
    return bundles


def _values(value: Mapping[str, Any] | None) -> list[float]:
    if not isinstance(value, Mapping) or value.get("status") != "complete":
        return []
    parsed = []
    for point in value.get("points") or []:
        try:
            parsed.append(float(point.get("value")))
        except (TypeError, ValueError, AttributeError):
            continue
    return parsed


def classify_movement_bundle(bundle: Mapping[str, Any] | None) -> dict[str, Any]:
    """Separate isolated spikes from sustained or strengthening search movement."""
    if not isinstance(bundle, Mapping):
        return {
            "movement_type": "unavailable", "trend_eligible": False,
            "reason": "No comparable search movement bundle was collected.",
            "metrics": {},
        }
    geo = str(bundle.get("default_geo") or "WORLDWIDE")
    series = (bundle.get("series") or {}).get(geo) or {}
    short_values = _values(series.get("3m"))
    annual_values = _values(series.get("1y"))
    five_year_values = _values(series.get("5y"))
    if not short_values:
        return {
            "movement_type": "unavailable", "trend_eligible": False,
            "reason": "No usable three-month search series was collected.",
            "metrics": {},
        }
    peak = max(short_values)
    total = sum(short_values)
    top3_share = (
        sum(sorted(short_values, reverse=True)[:3]) / total if total > 0 else 0.0
    )
    latest = short_values[-1]
    chunk_size = len(five_year_values) // 5 if len(five_year_values) >= 5 else 0
    five_year_peaks = []
    if chunk_size:
        for index in range(5):
            start = index * chunk_size
            end = len(five_year_values) if index == 4 else (index + 1) * chunk_size
            values = five_year_values[start:end]
            five_year_peaks.append(round(max(values), 2) if values else 0.0)
    rising_transitions = sum(
        right > left for left, right in zip(five_year_peaks, five_year_peaks[1:])
    )
    quarter = max(1, len(annual_values) // 4) if annual_values else 0
    prior_mean = (
        sum(annual_values[-2 * quarter:-quarter]) / quarter
        if quarter and len(annual_values) >= 2 * quarter else None
    )
    recent_mean = (
        sum(annual_values[-quarter:]) / quarter if quarter else None
    )
    metrics = {
        "three_month_peak": round(peak, 2),
        "three_month_latest": round(latest, 2),
        "top_three_points_share": round(top3_share, 4),
        "five_year_peaks": five_year_peaks,
        "rising_peak_transitions": rising_transitions,
        "prior_quarter_mean": round(prior_mean, 2) if prior_mean is not None else None,
        "recent_quarter_mean": round(recent_mean, 2) if recent_mean is not None else None,
    }
    if peak > 0 and top3_share >= 0.45 and latest <= peak * 0.35:
        movement_type = "event_spike"
        reason = "Search attention is concentrated in a few points and has already fallen sharply."
        eligible = False
    elif len(five_year_peaks) == 5 and rising_transitions >= 3 and five_year_peaks[-1] > five_year_peaks[0]:
        movement_type = "rising_peaks"
        reason = "Five-year search peaks are repeatedly moving higher."
        eligible = True
    elif (
        recent_mean is not None and prior_mean is not None and prior_mean > 0
        and recent_mean >= prior_mean * 1.15
    ):
        movement_type = "accelerating"
        reason = "The latest quarter is materially above the preceding quarter."
        eligible = True
    elif (
        recent_mean is not None and prior_mean is not None and prior_mean > 0
        and recent_mean <= prior_mean * 0.85
    ):
        movement_type = "declining"
        reason = "The latest quarter is materially below the preceding quarter."
        eligible = False
    else:
        movement_type = "stable_or_unclear"
        reason = "Search movement is visible but does not show a durable rising pattern."
        eligible = False
    return {
        "movement_type": movement_type,
        "trend_eligible": eligible,
        "reason": reason,
        "metrics": metrics,
    }


def trajectory_is_usable(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping) or value.get("status") != "complete":
        return False
    points: Sequence[Mapping[str, Any]] = value.get("points") or []
    return len(points) >= 30 and sum(int(point.get("value") or 0) > 0 for point in points) >= 8
