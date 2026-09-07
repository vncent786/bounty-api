"""Adapters that register current Google and social collectors without rewriting them."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from social_scraper.base import ConnectorResult, SocialItem

from .airline import register_airline_official_sources
from .catalogue import CapabilityCatalogue
from .credentials import CredentialBundle
from .models import (
    AuthMode,
    CostProfile,
    NormalizedEvidence,
    PreflightProbe,
    RateLimitPolicy,
    RawConnectorResult,
    RawState,
    SourceCapability,
    SourceExecutionRequest,
    TimeRangeCapability,
)


_EXPLICIT_EMPTY_ERRORS = {
    "ig_empty_tag",
    "tiktok_query_empty",
}
_CANARY_QUERIES = {
    "instagram": "#nike",
    "reddit": "running shoes",
    "tiktok": "nike",
    "x": "nike",
    "youtube": "iphone",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_raw_result(result: ConnectorResult, *, preflight: bool) -> RawConnectorResult:
    items = tuple(result.items or ())
    health = result.health
    status = str(health.status or "").casefold()
    error = str(health.error or "").strip().casefold() or None
    if status == "ok":
        state = RawState.COMPLETE if items else RawState.EMPTY
    elif status == "partial" and items:
        state = RawState.BOUNDED_PARTIAL
    elif status == "partial" and error in _EXPLICIT_EMPTY_ERRORS:
        state = RawState.EMPTY
    elif status == "partial":
        state = RawState.PARSER_ERROR
        error = error or "ambiguous_empty_response"
    elif "auth" in str(error) or "credential" in str(error) or "session_expired" in str(error):
        state = RawState.AUTH_ERROR
    elif "rate" in str(error) or "budget_exhausted" in str(error):
        state = RawState.RATE_LIMITED
    elif "challenge" in str(error) or "blocked" in str(error):
        state = RawState.CHALLENGED
    elif "parser" in str(error):
        state = RawState.PARSER_ERROR
    else:
        state = RawState.ERROR
    if preflight and state is RawState.EMPTY:
        state = RawState.ERROR
        error = "preflight_known_positive_empty"
    return RawConnectorResult(
        records=items,
        state=state,
        error_category=error,
        source_observed_at=health.fetched_at,
        metadata={
            "connector": health.connector,
            "coverage": dict(health.coverage or {}),
            "latency_ms": health.latency_ms,
        },
    )


def _route_auth(platform: str, connector_name: str) -> tuple[AuthMode, str | None]:
    name = connector_name.casefold()
    if platform == "tiktok" and name != "api_direct":
        return AuthMode.PERSISTENT_SESSION, "tiktok_owned_session"
    if platform == "douyin":
        return AuthMode.PERSISTENT_SESSION, "douyin_owned_session"
    if platform == "xiaohongshu":
        return AuthMode.PERSISTENT_SESSION, "xiaohongshu_owned_session"
    if name == "ig_auth_web" or name.startswith("instagram_"):
        return AuthMode.PERSISTENT_SESSION, "instagram_owned_session"
    if name in {"x_owned_graphql", "x_graphql", "x_scweet", "scweet_owned"}:
        return AuthMode.PERSISTENT_SESSION, "x_owned_session"
    if name.startswith("x_official"):
        return AuthMode.BEARER_TOKEN, "x_official_api"
    if name == "reddit_mobile_owned":
        return AuthMode.DEVICE_SESSION, "reddit_mobile_device"
    if "brave" in name:
        return AuthMode.API_KEY, "brave_search_api"
    return AuthMode.NONE, None


def _route_limitations(platform: str, connector_name: str) -> tuple[str, ...]:
    base = [
        "Raw collector status is preserved; ambiguous empty responses fail closed.",
        "Upstream coverage and rate limits may change without notice.",
    ]
    name = connector_name.casefold()
    if platform == "youtube":
        base.append("Video metadata and bounded comments are available; spoken transcripts are not collected.")
    elif platform == "reddit" and name == "reddit_mobile_owned":
        base.append("Search requires subreddit scope discovered before collection.")
    elif platform == "tiktok":
        base.append("Owned persistent browser use is serialized on the residential worker.")
    elif platform == "instagram":
        base.append("Owned session cookies must run from their session-origin network.")
    elif platform == "x":
        base.append("Search is ranked and bounded rather than a guaranteed complete firehose.")
    return tuple(base)


class LegacySocialConnectorAdapter:
    """Map an existing BaseConnector into the shared source contract."""

    def __init__(self, connector, *, priority: int = 100):
        self.connector = connector
        platform = str(connector.platform).casefold()
        connector_name = str(connector.connector_name).casefold()
        auth_mode, credential_ref = _route_auth(platform, connector_name)
        if auth_mode is AuthMode.PERSISTENT_SESSION:
            cost_kind = "mixed"
            cost_notes = "Collector code is free; owned browser routes may use separately paid network access."
        elif auth_mode in {AuthMode.API_KEY, AuthMode.BEARER_TOKEN}:
            cost_kind = "paid"
            cost_notes = "Credentialed provider route; the account-specific price is not declared here."
        else:
            cost_kind = "free"
            cost_notes = "No per-request provider fee is declared."
        self.capability = SourceCapability(
            source_id=f"social.{platform}.{connector_name}",
            display_name=f"{platform.title()} via {connector_name}",
            data_kinds=frozenset({"social_conversation"}),
            geographies=("*",),
            time_range=TimeRangeCapability(
                max_lookback_days=180,
                notes="The shared investing production window is at most six months.",
            ),
            auth_mode=auth_mode,
            credential_ref=credential_ref,
            cost=CostProfile(
                kind=cost_kind,
                currency="USD" if cost_kind == "free" else None,
                amount_per_request="0" if cost_kind == "free" else None,
                notes=cost_notes,
            ),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1 if auth_mode is not AuthMode.NONE else None,
                notes="No universal numeric limit is declared; connector and upstream budgets apply.",
            ),
            limitations=_route_limitations(platform, connector_name),
            preflight=PreflightProbe(
                query=_CANARY_QUERIES.get(platform, "test"),
                geography="US",
                time_filter="halfyear",
                limit=1,
            ),
            priority=priority,
        )

    async def _search(self, request: SourceExecutionRequest) -> RawConnectorResult:
        if request.options and hasattr(self.connector, "search_with_options"):
            operation = self.connector.search_with_options(
                request.query,
                request.limit,
                request.time_filter,
                request.sort,
                request.geography,
                dict(request.options),
            )
        else:
            operation = self.connector.search(
                request.query,
                request.limit,
                request.time_filter,
                request.sort,
                request.geography,
            )
        result = await operation
        return _legacy_raw_result(result, preflight=False)

    async def preflight(self, credentials: CredentialBundle) -> RawConnectorResult:
        probe = self.capability.preflight
        request = SourceExecutionRequest(
            query=probe.query,
            geography=probe.geography,
            time_filter=probe.time_filter,
            sort="latest",
            limit=probe.limit,
            options=probe.options,
        )
        result = await self._search(request)
        if result.state is RawState.EMPTY:
            return RawConnectorResult(
                records=(),
                state=RawState.ERROR,
                error_category="preflight_known_positive_empty",
                source_observed_at=result.source_observed_at,
                metadata=result.metadata,
            )
        return result

    async def search(
        self, request: SourceExecutionRequest, credentials: CredentialBundle
    ) -> RawConnectorResult:
        return await self._search(request)

    async def collect(
        self, request: SourceExecutionRequest, credentials: CredentialBundle
    ) -> RawConnectorResult:
        return await self._search(request)

    def normalize(
        self, record: SocialItem, request: SourceExecutionRequest
    ) -> NormalizedEvidence:
        if not isinstance(record, SocialItem):
            raise TypeError("legacy social adapter requires SocialItem records")
        serialized = record.to_dict()
        attributes = {
            key: value
            for key, value in serialized.items()
            if key not in {"platform", "post_id", "url", "text", "created_at", "region"}
        }
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=str(record.post_id),
            evidence_type="social_conversation",
            observed_at=_now(),
            url=record.url or None,
            text=record.text or None,
            published_at=record.created_at,
            geography=record.region or request.geography or None,
            attributes=attributes,
        )


class GoogleTrendsConnectorAdapter:
    """Map TopDownDiscovery.fetch_candidates into the shared source contract."""

    def __init__(self, discovery, geographies: tuple[str, ...]):
        self.discovery = discovery
        self.capability = SourceCapability(
            source_id="search.google_trends.trending_now",
            display_name="Google Trends Trending Now via trendspy",
            data_kinds=frozenset({"search_attention"}),
            geographies=geographies,
            time_range=TimeRangeCapability(
                max_lookback_days=1,
                notes="Trending Now is a current discovery feed, not a historical series.",
            ),
            auth_mode=AuthMode.NONE,
            credential_ref=None,
            cost=CostProfile(
                kind="free",
                currency="USD",
                amount_per_request="0",
                notes="No API credential or per-request provider fee.",
            ),
            rate_limit=RateLimitPolicy(
                requests=None,
                window_seconds=None,
                concurrency=1,
                notes="No numeric provider limit is declared; serialize trendspy calls.",
            ),
            limitations=(
                "Trending Now is attention evidence, not purchase evidence.",
                "An empty feed is ambiguous and fails closed rather than becoming a no-result.",
                "Interest-over-time is a separate rate-limited route.",
            ),
            preflight=PreflightProbe(
                query="trending_now",
                geography="US",
                limit=1,
            ),
            priority=10,
        )

    async def _fetch(self, geography: str, limit: int) -> RawConnectorResult:
        records = tuple(await self.discovery.fetch_candidates(geo=geography))
        if not records:
            return RawConnectorResult(
                records=(),
                state=RawState.ERROR,
                error_category="google_trends_empty_or_unavailable",
            )
        return RawConnectorResult(records=records[:limit], state=RawState.COMPLETE)

    async def preflight(self, credentials: CredentialBundle) -> RawConnectorResult:
        probe = self.capability.preflight
        return await self._fetch(probe.geography, probe.limit)

    async def search(
        self, request: SourceExecutionRequest, credentials: CredentialBundle
    ) -> RawConnectorResult:
        result = await self._fetch(request.geography or "US", max(request.limit, 50))
        if result.state is not RawState.COMPLETE:
            return result
        query = " ".join(request.query.casefold().split())
        matches = tuple(
            item
            for item in result.records
            if query in " ".join(str(getattr(item, "keyword", "")).casefold().split())
        )
        if not matches:
            return RawConnectorResult(records=(), state=RawState.EMPTY)
        return RawConnectorResult(records=matches[: request.limit], state=RawState.COMPLETE)

    async def collect(
        self, request: SourceExecutionRequest, credentials: CredentialBundle
    ) -> RawConnectorResult:
        return await self._fetch(request.geography or "US", request.limit)

    def normalize(self, record: Any, request: SourceExecutionRequest) -> NormalizedEvidence:
        if is_dataclass(record):
            raw = asdict(record)
        elif hasattr(record, "__dict__"):
            raw = dict(vars(record))
        elif isinstance(record, dict):
            raw = dict(record)
        else:
            raise TypeError("unsupported Google Trends candidate type")
        keyword = str(raw.get("keyword") or "").strip()
        if not keyword:
            raise ValueError("Google Trends candidate has no keyword")
        attributes = {
            "source": raw.get("source"),
            "search_volume": raw.get("search_volume"),
            "growth_pct": raw.get("growth_pct"),
            "related_terms": raw.get("related_terms") or [],
        }
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=keyword.casefold(),
            evidence_type="search_attention",
            observed_at=_now(),
            text=keyword,
            published_at=raw.get("source_started_at"),
            geography=request.geography or None,
            attributes=attributes,
        )


def register_social_broker(
    catalogue: CapabilityCatalogue, broker
) -> tuple[str, ...]:
    """Register each existing broker route through a non-mutating adapter."""

    source_ids = []
    for route in broker.iter_routes():
        adapter = LegacySocialConnectorAdapter(
            route.connector, priority=route.priority
        )
        catalogue.register(adapter)
        source_ids.append(adapter.capability.source_id)
    return tuple(source_ids)


def register_google_trends(
    catalogue: CapabilityCatalogue, discovery
) -> str:
    """Register the existing TopDownDiscovery collector without changing it."""

    try:
        from social_scraper.monitoring.topdown import TRENDING_NOW_COUNTRY_CODES

        geographies = tuple(sorted(TRENDING_NOW_COUNTRY_CODES))
    except ImportError:
        geographies = ("US",)
    adapter = GoogleTrendsConnectorAdapter(discovery, geographies)
    catalogue.register(adapter)
    return adapter.capability.source_id


def build_bounty_source_catalogue(
    *, broker=None, google_discovery=None, include_airline_sources: bool = True
) -> CapabilityCatalogue:
    """Build the reusable catalogue from whichever existing routes are active."""

    catalogue = CapabilityCatalogue()
    if broker is not None:
        register_social_broker(catalogue, broker)
    if google_discovery is not None:
        register_google_trends(catalogue, google_discovery)
    if include_airline_sources:
        register_airline_official_sources(catalogue)
    return catalogue
