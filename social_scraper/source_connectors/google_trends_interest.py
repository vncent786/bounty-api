"""Fail-closed Google Trends interest-over-time collection.

Trendspy's default property is Google web search. Passing the literal string
``gprop="web"`` produces an invalid multiline request in current Trendspy/Google
behavior, so web requests are deliberately sent without a gprop parameter.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence


WEB_PROPERTY_ALIASES = {"", "web", "web_default"}


def _request_delay() -> float:
    try:
        return max(
            1.0,
            float(os.getenv("BOUNTY_GOOGLE_TRENDS_REQUEST_DELAY", "16")),
        )
    except ValueError:
        return 16.0


def canonical_trendspy_gprop(value: Any) -> tuple[str | None, str]:
    """Return the provider argument and auditable effective-property label."""
    requested = str(value or "").strip().casefold()
    if requested in WEB_PROPERTY_ALIASES:
        return None, "web_default"
    return requested, requested


def _query_basket(values: Sequence[Any]) -> list[str]:
    queries = [" ".join(str(value or "").strip().split()) for value in values]
    if not 1 <= len(queries) <= 5:
        raise ValueError("Google Trends query basket must contain between 1 and 5 queries")
    if any(not query for query in queries):
        raise ValueError("Google Trends queries must be non-empty")
    if len({query.casefold() for query in queries}) != len(queries):
        raise ValueError("Google Trends query basket must be unique after normalization")
    return queries


def _error_category(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return f"HTTP_{status_code}"
    return type(exc).__name__


def fetch_interest_over_time(
    queries: Sequence[Any],
    *,
    trends=None,
    timeframe: str = "today 3-m",
    geo: str = "",
    gprop: str = "web",
) -> dict[str, Any]:
    """Fetch one comparable basket without misencoding Google web search."""
    basket = _query_basket(queries)
    requested_gprop = str(gprop or "web").strip().casefold() or "web"
    provider_gprop, effective_gprop = canonical_trendspy_gprop(requested_gprop)
    geo_value = str(geo or "").strip().upper()
    base = {
        "source": "Google Trends",
        "route": "trendspy_interest_over_time",
        "query_basket": basket,
        "timeframe": str(timeframe),
        "geo": geo_value,
        "requested_gprop": requested_gprop,
        "effective_gprop": effective_gprop,
        "normalized": True,
        "returned_values": None,
        "isPartial_flags": None,
        "rows_returned": 0,
        "error_category": None,
    }
    try:
        if trends is None:
            from trendspy import Trends

            trends = Trends(request_delay=_request_delay())
        kwargs = {
            "timeframe": str(timeframe),
            "geo": geo_value,
            "headers": {"referer": "https://trends.google.com/"},
        }
        if provider_gprop is not None:
            kwargs["gprop"] = provider_gprop
        frame = trends.interest_over_time(basket, **kwargs)
    except Exception as exc:
        return {
            **base,
            "status": "SOURCE_FAILURE",
            "error_category": _error_category(exc),
        }

    if frame is None or len(frame) == 0:
        return {
            **base,
            "status": "SOURCE_FAILURE",
            "error_category": "EMPTY_RESPONSE",
        }
    missing = [query for query in basket if query not in frame.columns]
    if missing:
        return {
            **base,
            "status": "SOURCE_FAILURE",
            "error_category": "MISSING_QUERY_COLUMN",
        }

    dates = [str(value)[:10] for value in frame.index.tolist()]
    returned_values = {
        "dates": dates,
        **{
            query: [None if value is None else int(value) for value in frame[query].tolist()]
            for query in basket
        },
    }
    partial_flags = (
        [bool(value) for value in frame["isPartial"].tolist()]
        if "isPartial" in frame.columns
        else None
    )
    if any(len(values) != len(dates) for key, values in returned_values.items() if key != "dates"):
        return {
            **base,
            "status": "SOURCE_FAILURE",
            "error_category": "ROW_COUNT_MISMATCH",
        }
    if partial_flags is not None and len(partial_flags) != len(dates):
        return {
            **base,
            "status": "SOURCE_FAILURE",
            "error_category": "PARTIAL_FLAG_COUNT_MISMATCH",
        }
    return {
        **base,
        "status": "complete",
        "returned_values": returned_values,
        "isPartial_flags": partial_flags,
        "rows_returned": len(dates),
    }


def collect_interest_plan(
    plan: Sequence[Mapping[str, Any]],
    *,
    trends=None,
    max_requests: int = 12,
) -> dict[str, dict[str, Any]]:
    """Execute a bounded request plan serially through one Trendspy client."""
    rows = [dict(row) for row in plan]
    if max_requests < 1:
        raise ValueError("max_requests must be positive")
    if len(rows) > max_requests:
        raise ValueError(
            f"Google Trends request plan exceeds bounded maximum of {max_requests}"
        )
    keys = [str(row.get("request_key") or "").strip() for row in rows]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("Google Trends request plan requires unique non-empty request keys")
    if trends is None:
        from trendspy import Trends

        trends = Trends(request_delay=_request_delay())

    results: dict[str, dict[str, Any]] = {}
    for key, row in zip(keys, rows):
        results[key] = fetch_interest_over_time(
            row.get("query_basket") or (),
            trends=trends,
            timeframe=str(row.get("timeframe") or "today 3-m"),
            geo=str(row.get("geo") or ""),
            gprop=str(row.get("gprop") or "web"),
        )
    return results
