"""Reusable Bounty source connector catalogue, credentials, and receipts."""

from .adapters import (
    GoogleTrendsConnectorAdapter,
    LegacySocialConnectorAdapter,
    build_bounty_source_catalogue,
    register_google_trends,
    register_social_broker,
)
from .airline import (
    OfficialAirlineConnector,
    register_airline_official_sources,
)
from .public_parity import PublicParityConnector
from .google_trends_interest import (
    canonical_trendspy_gprop,
    collect_interest_plan,
    fetch_interest_over_time,
)
from .catalogue import CapabilityCatalogue, CapabilityQuery
from .credentials import (
    CredentialAlternative,
    CredentialBundle,
    CredentialDefinition,
    CredentialResolver,
    MissingCredentialError,
)
from .execution import (
    AttemptReceipt,
    JsonReceiptStore,
    SourceExecutionResult,
    SourceExecutor,
    SourceReceipt,
    canonical_json,
    payload_sha256,
    redact_secrets,
)
from .models import (
    AuthMode,
    ConnectorOperation,
    CostProfile,
    ExecutionState,
    NormalizedEvidence,
    PreflightProbe,
    RateLimitPolicy,
    RawConnectorResult,
    RawState,
    SourceCapability,
    SourceConnector,
    SourceExecutionRequest,
    TimeRangeCapability,
)

__all__ = [
    "AttemptReceipt",
    "AuthMode",
    "CapabilityCatalogue",
    "CapabilityQuery",
    "ConnectorOperation",
    "CostProfile",
    "CredentialAlternative",
    "CredentialBundle",
    "CredentialDefinition",
    "CredentialResolver",
    "ExecutionState",
    "GoogleTrendsConnectorAdapter",
    "JsonReceiptStore",
    "LegacySocialConnectorAdapter",
    "OfficialAirlineConnector",
    "MissingCredentialError",
    "NormalizedEvidence",
    "PreflightProbe",
    "PublicParityConnector",
    "RateLimitPolicy",
    "RawConnectorResult",
    "RawState",
    "SourceCapability",
    "SourceConnector",
    "SourceExecutionRequest",
    "SourceExecutionResult",
    "SourceExecutor",
    "SourceReceipt",
    "TimeRangeCapability",
    "build_bounty_source_catalogue",
    "canonical_json",
    "canonical_trendspy_gprop",
    "collect_interest_plan",
    "fetch_interest_over_time",
    "payload_sha256",
    "redact_secrets",
    "register_airline_official_sources",
    "register_google_trends",
    "register_social_broker",
]
