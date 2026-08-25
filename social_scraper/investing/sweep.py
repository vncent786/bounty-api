"""Global, failure-isolated collection for the persisted investing Radar."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from social_scraper.monitoring.topdown import (
    TRENDING_NOW_COUNTRIES,
    EmergingKeyword,
    _topic_ids_to_categories,
)

from .storage import InvestingRadarStore, RadarConflictError


AsyncFetcher = Callable[[str], Awaitable[Sequence[Any]]]
_PROVIDER_CONNECT_TIMEOUT_SECONDS = 5
_PROVIDER_READ_TIMEOUT_SECONDS = 12


def _fetch_topdown_candidates_sync(geo: str) -> list[EmergingKeyword]:
    """Run the synchronous trendspy client with bounded network requests."""
    from trendspy import Trends

    trends_client = Trends(request_delay=0, max_retries=1)
    original_request = trends_client.session.request

    def bounded_request(*args, **kwargs):
        kwargs.setdefault(
            "timeout",
            (_PROVIDER_CONNECT_TIMEOUT_SECONDS, _PROVIDER_READ_TIMEOUT_SECONDS),
        )
        return original_request(*args, **kwargs)

    trends_client.session.request = bounded_request
    raw_trends = trends_client.trending_now(geo=geo)
    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    candidates: list[EmergingKeyword] = []
    for trend in raw_trends:
        volume = getattr(trend, "volume", None)
        growth = getattr(trend, "volume_growth_pct", None)
        started = getattr(trend, "started_timestamp", None)
        source_started_at = None
        hours_ago = None
        if started:
            timestamp = max(started) if isinstance(started, list) else started
            if timestamp:
                hours_ago = max(0.0, (now_ts - float(timestamp)) / 3600)
                source_started_at = datetime.fromtimestamp(
                    float(timestamp), timezone.utc
                ).isoformat()
        related = getattr(trend, "trend_keywords", []) or []
        topic_ids = getattr(trend, "topics", []) or []
        candidates.append(EmergingKeyword(
            keyword=trend.keyword,
            source="google_trends",
            engagement_signal=growth or 0,
            related_terms=related[:5],
            discovered_at=now.isoformat(),
            sample_context=(
                f"Google Trends {geo}: "
                f"vol={volume if volume is not None else 'unknown'}, "
                f"growth={growth if growth is not None else 'unknown'}"
            ),
            search_volume=volume,
            growth_pct=growth,
            started_hours_ago=round(hours_ago, 1) if hours_ago is not None else None,
            source_started_at=source_started_at,
            topic_ids=topic_ids,
            categories=_topic_ids_to_categories(topic_ids),
            source_observations=[{
                "keyword": trend.keyword,
                "search_volume": volume,
                "growth_pct": growth,
                "source_started_at": source_started_at,
                "related_terms": related,
                "topic_ids": topic_ids,
            }],
        ))
    return candidates


async def fetch_topdown_candidates(geo: str) -> Sequence[Any]:
    """Production adapter that never blocks the web event loop."""
    candidates = await asyncio.to_thread(_fetch_topdown_candidates_sync, geo)
    # All 125 allowlisted markets returned data during the source audit. An
    # empty default result is therefore a source gap, not a successful market.
    if not candidates:
        error = RuntimeError("Trending Now returned no candidates")
        error.error_category = "source_empty_or_unavailable"
        raise error
    return candidates


def _markets(values: Iterable[tuple[str, str] | str]) -> list[tuple[str, str]]:
    names = dict(TRENDING_NOW_COUNTRIES)
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            code = value.strip().upper()
            name = names.get(code, code)
        else:
            code = str(value[0]).strip().upper()
            name = str(value[1]).strip()
        if not code:
            raise ValueError("market country code is required")
        if code in seen:
            raise ValueError(f"duplicate market country code {code!r}")
        seen.add(code)
        result.append((code, name))
    return result


def _error_category(exc: Exception) -> str:
    explicit = str(getattr(exc, "error_category", "") or "").strip()
    return explicit or type(exc).__name__


class GlobalRadarSweep:
    """Persist one outcome for every market; a failed market never stops peers."""

    def __init__(
        self,
        store: InvestingRadarStore,
        fetcher: AsyncFetcher | None = None,
    ) -> None:
        self.store = store
        self.fetcher = fetcher or fetch_topdown_candidates
        if not callable(self.fetcher):
            raise TypeError("fetcher must be an async callable accepting geo")

    async def run(
        self,
        countries: Iterable[tuple[str, str] | str] | None = None,
        *,
        sweep_id: str | None = None,
    ) -> dict[str, Any]:
        markets = _markets(TRENDING_NOW_COUNTRIES if countries is None else countries)
        if sweep_id is None:
            sweep_id = self.store.create_sweep(total_markets=len(markets))
        else:
            existing = self.store.get_sweep(sweep_id)
            if existing is None:
                raise ValueError(f"sweep {sweep_id!r} was not found")
            if existing["status"] != "running":
                raise RadarConflictError(f"sweep {sweep_id!r} is already finalized")
            if existing["total_markets"] != len(markets):
                raise ValueError("sweep market count does not match the requested markets")
        try:
            for country, country_name in markets:
                try:
                    candidates = await self.fetcher(country)
                    if candidates is None or isinstance(candidates, (str, bytes, dict)):
                        raise TypeError("fetcher must return a candidate sequence")
                    self.store.record_market_success(
                        sweep_id,
                        country,
                        list(candidates),
                        country_name=country_name,
                    )
                except Exception as exc:
                    self.store.record_market_failure(
                        sweep_id,
                        country,
                        _error_category(exc),
                        country_name=country_name,
                    )
        finally:
            sweep = self.store.finalize_sweep(sweep_id)
        return sweep

    async def sweep(
        self,
        countries: Iterable[tuple[str, str] | str] | None = None,
        *,
        sweep_id: str | None = None,
    ) -> dict[str, Any]:
        """Readable alias for ``run``."""
        return await self.run(countries=countries, sweep_id=sweep_id)
