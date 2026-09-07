"""Shared, source-agnostic connector contract for Bounty evidence collection."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


_SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class ConnectorOperation(str, Enum):
    PREFLIGHT = "preflight"
    SEARCH = "search"
    COLLECT = "collect"
    NORMALIZE = "normalize"


class AuthMode(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    DEVICE_SESSION = "device_session"
    PERSISTENT_SESSION = "persistent_session"
    MIXED = "mixed"


class RawState(str, Enum):
    COMPLETE = "complete"
    EMPTY = "empty"
    PARTIAL = "partial"
    BOUNDED_PARTIAL = "bounded_partial"
    ERROR = "error"
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    CHALLENGED = "challenged"
    PARSER_ERROR = "parser_error"


class ExecutionState(str, Enum):
    COMPLETE = "complete"
    COMPLETE_EMPTY = "complete_empty"
    PARTIAL = "partial"
    BOUNDED_PARTIAL = "bounded_partial"
    CREDENTIAL_MISSING = "credential_missing"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True)
class TimeRangeCapability:
    """Declared source history. Unknown bounds stay None rather than being guessed."""

    max_lookback_days: int | None = None
    earliest_available: str | None = None
    latest_available: str = "current"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.max_lookback_days is not None and self.max_lookback_days < 0:
            raise ValueError("max_lookback_days cannot be negative")

    def supports(self, lookback_days: int | None) -> bool:
        if lookback_days is None or self.max_lookback_days is None:
            return True
        return lookback_days <= self.max_lookback_days


@dataclass(frozen=True)
class CostProfile:
    """Declared call cost. Unknown amounts remain None."""

    kind: str
    currency: str | None = None
    amount_per_request: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"free", "paid", "mixed", "unknown"}:
            raise ValueError("cost kind must be free, paid, mixed, or unknown")
        if self.kind == "free" and self.amount_per_request not in {None, "0"}:
            raise ValueError("free sources cannot declare a non-zero request cost")


@dataclass(frozen=True)
class RateLimitPolicy:
    """Published or connector-enforced request limits without invented values."""

    requests: int | None
    window_seconds: int | None
    concurrency: int | None = 1
    notes: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("requests", self.requests),
            ("window_seconds", self.window_seconds),
            ("concurrency", self.concurrency),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be positive when declared")


@dataclass(frozen=True)
class PreflightProbe:
    """Known-positive request shaped like production collection."""

    query: str
    geography: str = ""
    time_filter: str = ""
    limit: int = 1
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("preflight limit must be positive")


@dataclass(frozen=True)
class SourceCapability:
    source_id: str
    display_name: str
    data_kinds: frozenset[str]
    geographies: tuple[str, ...]
    time_range: TimeRangeCapability
    auth_mode: AuthMode
    credential_ref: str | None
    cost: CostProfile
    rate_limit: RateLimitPolicy
    limitations: tuple[str, ...]
    preflight: PreflightProbe
    operations: frozenset[ConnectorOperation] = field(
        default_factory=lambda: frozenset(ConnectorOperation)
    )
    priority: int = 100

    def __post_init__(self) -> None:
        if not _SOURCE_ID_RE.fullmatch(self.source_id):
            raise ValueError(f"invalid source_id: {self.source_id}")
        if not self.display_name.strip():
            raise ValueError("display_name is required")
        if not self.data_kinds:
            raise ValueError("at least one data kind is required")
        if not self.geographies:
            raise ValueError("at least one geography is required")
        if self.auth_mode is not AuthMode.NONE and not self.credential_ref:
            raise ValueError("authenticated sources require a named credential_ref")
        if self.auth_mode is AuthMode.NONE and self.credential_ref:
            raise ValueError("unauthenticated sources cannot declare credential_ref")
        required = frozenset(ConnectorOperation)
        if self.operations != required:
            missing = sorted(item.value for item in required - self.operations)
            raise ValueError(
                "source connectors must expose preflight/search/collect/normalize; "
                f"missing: {', '.join(missing)}"
            )

    def supports_geography(self, geography: str | None) -> bool:
        if not geography:
            return True
        supported = {item.upper() for item in self.geographies}
        return "*" in supported or geography.upper() in supported

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "data_kinds": sorted(self.data_kinds),
            "geographies": list(self.geographies),
            "time_range": asdict(self.time_range),
            "auth_mode": self.auth_mode.value,
            "credential_ref": self.credential_ref,
            "cost": asdict(self.cost),
            "rate_limit": asdict(self.rate_limit),
            "limitations": list(self.limitations),
            "preflight": {
                "query": self.preflight.query,
                "geography": self.preflight.geography,
                "time_filter": self.preflight.time_filter,
                "limit": self.preflight.limit,
                "options": dict(self.preflight.options),
            },
            "operations": sorted(item.value for item in self.operations),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class SourceExecutionRequest:
    query: str = ""
    geography: str = ""
    time_filter: str = ""
    sort: str = ""
    limit: int = 20
    cursor: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    resume_key: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be positive")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "geography": self.geography,
            "time_filter": self.time_filter,
            "sort": self.sort,
            "limit": self.limit,
            "cursor": self.cursor,
            "options": dict(self.options),
            "resume_key": self.resume_key,
        }


@dataclass(frozen=True)
class RawConnectorResult:
    records: tuple[Any, ...]
    state: RawState
    next_cursor: str | None = None
    error_category: str | None = None
    source_observed_at: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state is RawState.EMPTY and self.records:
            raise ValueError("empty result cannot contain records")
        if self.state is RawState.COMPLETE and not self.records:
            raise ValueError("complete result must contain at least one record")
        if self.state is RawState.COMPLETE and self.next_cursor is not None:
            raise ValueError("complete result cannot carry a resume cursor")
        if self.state is RawState.PARTIAL and (not self.records or not self.next_cursor):
            raise ValueError("partial result requires records and a resume cursor")
        if self.state is RawState.BOUNDED_PARTIAL and not self.records:
            raise ValueError("bounded partial result requires records")
        if self.state is RawState.BOUNDED_PARTIAL and self.next_cursor is not None:
            raise ValueError("bounded partial result cannot carry a resume cursor")
        if self.state in {
            RawState.ERROR,
            RawState.AUTH_ERROR,
            RawState.RATE_LIMITED,
            RawState.CHALLENGED,
            RawState.PARSER_ERROR,
        } and self.records:
            raise ValueError("failed result cannot contain records")


@dataclass(frozen=True)
class NormalizedEvidence:
    source_id: str
    external_id: str
    evidence_type: str
    observed_at: str
    url: str | None = None
    title: str | None = None
    text: str | None = None
    published_at: str | None = None
    geography: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not self.external_id or not self.evidence_type:
            raise ValueError("source_id, external_id, and evidence_type are required")
        if not self.observed_at:
            raise ValueError("observed_at is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "external_id": self.external_id,
            "evidence_type": self.evidence_type,
            "observed_at": self.observed_at,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "published_at": self.published_at,
            "geography": self.geography,
            "attributes": dict(self.attributes),
        }


@runtime_checkable
class SourceConnector(Protocol):
    """One reusable contract for every raw source implementation."""

    capability: SourceCapability

    async def preflight(self, credentials: Any) -> RawConnectorResult:
        ...

    async def search(
        self, request: SourceExecutionRequest, credentials: Any
    ) -> RawConnectorResult:
        ...

    async def collect(
        self, request: SourceExecutionRequest, credentials: Any
    ) -> RawConnectorResult:
        ...

    def normalize(
        self, record: Any, request: SourceExecutionRequest
    ) -> NormalizedEvidence:
        ...
