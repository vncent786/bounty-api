import asyncio
import json
from dataclasses import dataclass, replace

import pytest

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.broker import SourceBroker
from social_scraper.source_connectors import (
    AuthMode,
    CapabilityCatalogue,
    CapabilityQuery,
    ConnectorOperation,
    CostProfile,
    CredentialAlternative,
    CredentialDefinition,
    CredentialResolver,
    ExecutionState,
    JsonReceiptStore,
    NormalizedEvidence,
    PreflightProbe,
    RateLimitPolicy,
    RawConnectorResult,
    RawState,
    SourceCapability,
    SourceExecutionRequest,
    SourceExecutor,
    TimeRangeCapability,
    register_google_trends,
    register_social_broker,
)


def capability(
    source_id="test.source",
    *,
    credential_ref=None,
    data_kinds=frozenset({"procurement_notice"}),
):
    return SourceCapability(
        source_id=source_id,
        display_name="Test source",
        data_kinds=data_kinds,
        geographies=("US", "SG"),
        time_range=TimeRangeCapability(max_lookback_days=365),
        auth_mode=AuthMode.API_KEY if credential_ref else AuthMode.NONE,
        credential_ref=credential_ref,
        cost=CostProfile(kind="free", currency="USD", amount_per_request="0"),
        rate_limit=RateLimitPolicy(
            requests=100,
            window_seconds=3600,
            concurrency=2,
            notes="Published test limit.",
        ),
        limitations=("Controlled test source.",),
        preflight=PreflightProbe(query="known-positive", geography="US", limit=1),
    )


class FakeConnector:
    def __init__(self, source_capability, *, preflight=None, search=None, collect=None):
        self.capability = source_capability
        self.preflight_results = list(preflight or [
            RawConnectorResult(records=({"id": "canary"},), state=RawState.COMPLETE)
        ])
        self.search_results = list(search or [
            RawConnectorResult(records=({"id": "result"},), state=RawState.COMPLETE)
        ])
        self.collect_results = list(collect or self.search_results)
        self.preflight_calls = 0
        self.search_calls = 0
        self.collect_calls = 0
        self.search_cursors = []
        self.collect_cursors = []

    @staticmethod
    def _result(results, call_count):
        return results[min(call_count - 1, len(results) - 1)]

    async def preflight(self, credentials):
        self.preflight_calls += 1
        return self._result(self.preflight_results, self.preflight_calls)

    async def search(self, request, credentials):
        self.search_calls += 1
        self.search_cursors.append(request.cursor)
        return self._result(self.search_results, self.search_calls)

    async def collect(self, request, credentials):
        self.collect_calls += 1
        self.collect_cursors.append(request.cursor)
        return self._result(self.collect_results, self.collect_calls)

    def normalize(self, record, request):
        return NormalizedEvidence(
            source_id=self.capability.source_id,
            external_id=str(record["id"]),
            evidence_type=next(iter(self.capability.data_kinds)),
            geography=request.geography or None,
            observed_at="2026-09-05T00:00:00+00:00",
            attributes=dict(record),
        )


def test_catalogue_exposes_complete_capabilities_and_selects_for_future_monitors():
    catalogue = CapabilityCatalogue()
    connector = FakeConnector(capability())
    catalogue.register(connector)

    descriptor = catalogue.describe()[0]
    assert descriptor["operations"] == ["collect", "normalize", "preflight", "search"]
    assert descriptor["geographies"] == ["US", "SG"]
    assert descriptor["time_range"]["max_lookback_days"] == 365
    assert descriptor["auth_mode"] == "none"
    assert descriptor["cost"]["amount_per_request"] == "0"
    assert descriptor["rate_limit"]["requests"] == 100
    assert descriptor["limitations"] == ["Controlled test source."]

    selected = catalogue.select(CapabilityQuery(
        operation=ConnectorOperation.COLLECT,
        data_kinds=frozenset({"procurement_notice"}),
        geography="SG",
        lookback_days=180,
        allow_paid=False,
    ))
    assert [item.capability.source_id for item in selected] == ["test.source"]
    assert catalogue.select(CapabilityQuery(
        operation=ConnectorOperation.COLLECT,
        data_kinds=frozenset({"procurement_notice"}),
        geography="GB",
    )) == ()


def test_disabling_paid_sources_excludes_mixed_and_paid_routes():
    mixed = FakeConnector(replace(
        capability(source_id="owned.mixed"),
        cost=CostProfile(kind="mixed", notes="Owned browser route"),
    ))
    paid = FakeConnector(replace(
        capability(source_id="provider.paid"),
        cost=CostProfile(kind="paid", currency="USD", amount_per_request="1"),
    ))
    catalogue = CapabilityCatalogue([mixed, paid])

    selected = catalogue.select(CapabilityQuery(
        operation=ConnectorOperation.SEARCH,
        data_kinds=frozenset({"procurement_notice"}),
        geography="US",
        allow_paid=False,
    ))

    assert selected == ()


def test_runtime_managed_credentials_are_explicit():
    with pytest.raises(ValueError, match="must declare keys, files, or runtime management"):
        CredentialAlternative(())
    runtime = CredentialResolver(environment={})
    runtime.register(CredentialDefinition(
        name="owned_browser",
        alternatives=(CredentialAlternative((), runtime_managed=True),),
    ))
    assert len(runtime.resolve("owned_browser")) == 0

    defaults = CredentialResolver.with_bounty_defaults(environment={})
    assert {
        "tiktok_owned_session",
        "reddit_mobile_device",
        "douyin_owned_session",
        "xiaohongshu_owned_session",
    } <= set(defaults.references())


def test_missing_credentials_fail_before_calling_source_and_receipt_is_safe(tmp_path):
    source_capability = capability(credential_ref="tender_api")
    connector = FakeConnector(source_capability)
    catalogue = CapabilityCatalogue([connector])
    credentials = CredentialResolver(environment={})
    credentials.register(CredentialDefinition(
        name="tender_api",
        alternatives=(CredentialAlternative(env_keys=("TENDER_API_TOKEN",)),),
    ))
    executor = SourceExecutor(catalogue, credentials, JsonReceiptStore(tmp_path))

    result = asyncio.run(executor.search(
        "test.source",
        SourceExecutionRequest(query="cooling award", geography="SG"),
    ))

    assert result.receipt.state is ExecutionState.CREDENTIAL_MISSING
    assert result.receipt.error_category == "credential_missing:tender_api"
    assert connector.preflight_calls == 0
    assert connector.search_calls == 0
    serialized = json.dumps(result.receipt.to_dict())
    assert "TENDER_API_TOKEN" not in serialized


def test_healthy_preflight_makes_explicit_empty_a_valid_no_result(tmp_path):
    connector = FakeConnector(
        capability(),
        search=[RawConnectorResult(records=(), state=RawState.EMPTY)],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
    )

    result = asyncio.run(executor.search(
        "test.source",
        SourceExecutionRequest(query="no matching award", geography="US"),
    ))

    assert result.receipt.state is ExecutionState.COMPLETE_EMPTY
    assert result.receipt.health_state == "healthy"
    assert result.receipt.evidence == ()
    assert connector.preflight_calls == 1
    assert connector.search_calls == 1


def test_failed_production_preflight_is_source_outage_not_empty(tmp_path):
    connector = FakeConnector(
        capability(),
        preflight=[RawConnectorResult(
            records=(), state=RawState.ERROR, error_category="upstream_503"
        )],
        search=[RawConnectorResult(records=(), state=RawState.EMPTY)],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )

    result = asyncio.run(executor.search(
        "test.source",
        SourceExecutionRequest(query="no matching award", geography="US"),
    ))

    assert result.receipt.state is ExecutionState.SOURCE_UNAVAILABLE
    assert result.receipt.health_state == "outage"
    assert result.receipt.error_category == "upstream_503"
    assert connector.search_calls == 0


def test_retries_are_shared_and_terminal_receipts_resume_with_verified_hash(tmp_path):
    connector = FakeConnector(
        capability(),
        search=[
            RawConnectorResult(records=(), state=RawState.ERROR, error_category="timeout"),
            RawConnectorResult(records=({"id": "award-1"},), state=RawState.COMPLETE),
        ],
    )
    store = JsonReceiptStore(tmp_path)
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        store,
        max_attempts=2,
        retry_delay_seconds=0,
    )
    request = SourceExecutionRequest(query="cooling award", geography="SG")

    first = asyncio.run(executor.search("test.source", request))
    second = asyncio.run(executor.search("test.source", request))

    assert first.receipt.state is ExecutionState.COMPLETE
    assert [attempt.raw_state for attempt in first.receipt.attempts] == ["error", "complete"]
    assert first.receipt.verify_hash()
    assert second.resumed is True
    assert second.receipt.receipt_sha256 == first.receipt.receipt_sha256
    assert connector.search_calls == 2
    assert store.load(first.receipt.operation_id).verify_hash()


def test_partial_collection_resumes_from_persisted_cursor_and_keeps_evidence(tmp_path):
    connector = FakeConnector(
        capability(),
        collect=[
            RawConnectorResult(
                records=({"id": "page-1"},),
                state=RawState.PARTIAL,
                next_cursor="cursor-2",
            ),
            RawConnectorResult(
                records=({"id": "page-2"},),
                state=RawState.COMPLETE,
            ),
        ],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
    )
    request = SourceExecutionRequest(query="awards", geography="SG")

    first = asyncio.run(executor.collect("test.source", request))
    second = asyncio.run(executor.collect("test.source", request))
    third = asyncio.run(executor.collect("test.source", request))

    assert first.receipt.state is ExecutionState.PARTIAL
    assert first.receipt.next_cursor == "cursor-2"
    assert second.resumed is True
    assert second.receipt.state is ExecutionState.COMPLETE
    assert [item["external_id"] for item in second.receipt.evidence] == [
        "page-1", "page-2",
    ]
    assert second.receipt.next_cursor is None
    assert second.receipt.verify_hash()
    assert third.resumed is True
    assert connector.collect_cursors == [None, "cursor-2"]


def test_partial_search_resumes_from_persisted_cursor_and_keeps_evidence(tmp_path):
    connector = FakeConnector(
        capability(),
        search=[
            RawConnectorResult(
                records=({"id": "page-1"},),
                state=RawState.PARTIAL,
                next_cursor="cursor-2",
            ),
            RawConnectorResult(
                records=({"id": "page-2"},),
                state=RawState.COMPLETE,
            ),
        ],
    )
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        JsonReceiptStore(tmp_path),
    )
    request = SourceExecutionRequest(query="awards", geography="SG")

    first = asyncio.run(executor.search("test.source", request))
    second = asyncio.run(executor.search("test.source", request))
    third = asyncio.run(executor.search("test.source", request))

    assert first.receipt.state is ExecutionState.PARTIAL
    assert first.receipt.next_cursor == "cursor-2"
    assert second.resumed is True
    assert second.receipt.state is ExecutionState.COMPLETE
    assert [item["external_id"] for item in second.receipt.evidence] == ["page-1", "page-2"]
    assert third.resumed is True
    assert connector.search_cursors == [None, "cursor-2"]


def test_receipt_store_rejects_tampered_evidence(tmp_path):
    connector = FakeConnector(capability())
    store = JsonReceiptStore(tmp_path)
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        CredentialResolver(environment={}),
        store,
    )
    result = asyncio.run(executor.search(
        "test.source", SourceExecutionRequest(query="award", geography="US")
    ))
    path = tmp_path / f"{result.receipt.operation_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["evidence"][0]["external_id"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash does not verify"):
        store.load(result.receipt.operation_id)


def test_secret_values_and_secret_shaped_fields_never_enter_receipts(tmp_path):
    secret = "super-secret-123"
    source_capability = capability(credential_ref="private_api")
    connector = FakeConnector(
        source_capability,
        search=[RawConnectorResult(
            records=({
                "id": "safe-id",
                "note": f"response accidentally echoed {secret}",
                "authorization": f"Bearer {secret}",
                "nested": {"api_key": secret},
            },),
            state=RawState.COMPLETE,
        )],
    )
    credentials = CredentialResolver(environment={"PRIVATE_API_TOKEN": secret})
    credentials.register(CredentialDefinition(
        name="private_api",
        alternatives=(CredentialAlternative(env_keys=("PRIVATE_API_TOKEN",)),),
    ))
    executor = SourceExecutor(
        CapabilityCatalogue([connector]), credentials, JsonReceiptStore(tmp_path)
    )

    result = asyncio.run(executor.search(
        "test.source",
        SourceExecutionRequest(
            query="safe",
            geography="US",
            options={"opaque_provider_option": secret},
        ),
    ))
    serialized = json.dumps(result.receipt.to_dict(), sort_keys=True)
    persisted = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")

    assert secret not in serialized
    assert secret not in persisted
    assert "<redacted>" in serialized
    assert result.receipt.verify_hash()


def test_secret_echoed_in_error_category_is_redacted_from_receipt(tmp_path):
    secret = "Hunter2-Super-Secret-Token"
    source_capability = capability(credential_ref="private_api")
    connector = FakeConnector(
        source_capability,
        search=[RawConnectorResult(
            records=(),
            state=RawState.ERROR,
            error_category=f"invalid token {secret.casefold()}",
        )],
    )
    credentials = CredentialResolver(environment={"PRIVATE_API_TOKEN": secret})
    credentials.register(CredentialDefinition(
        name="private_api",
        alternatives=(CredentialAlternative(env_keys=("PRIVATE_API_TOKEN",)),),
    ))
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        credentials,
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )

    result = asyncio.run(executor.search(
        "test.source", SourceExecutionRequest(query="safe", geography="US")
    ))
    serialized = json.dumps(result.receipt.to_dict(), sort_keys=True)
    persisted = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")

    assert result.receipt.error_category == "connector_error"
    assert secret not in serialized
    assert secret.casefold() not in serialized.casefold()
    assert secret not in persisted
    assert secret.casefold() not in persisted.casefold()


def test_secret_echoed_as_resume_cursor_is_not_persisted(tmp_path):
    secret = "cursor-secret-token"
    source_capability = capability(credential_ref="private_api")
    connector = FakeConnector(
        source_capability,
        collect=[RawConnectorResult(
            records=({"id": "page-1"},),
            state=RawState.PARTIAL,
            next_cursor=secret,
        )],
    )
    credentials = CredentialResolver(environment={"PRIVATE_API_TOKEN": secret})
    credentials.register(CredentialDefinition(
        name="private_api",
        alternatives=(CredentialAlternative(env_keys=("PRIVATE_API_TOKEN",)),),
    ))
    executor = SourceExecutor(
        CapabilityCatalogue([connector]),
        credentials,
        JsonReceiptStore(tmp_path),
        max_attempts=1,
    )

    result = asyncio.run(executor.collect(
        "test.source", SourceExecutionRequest(query="safe", geography="US")
    ))
    serialized = json.dumps(result.receipt.to_dict(), sort_keys=True)
    persisted = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")

    assert result.receipt.state is ExecutionState.BOUNDED_PARTIAL
    assert result.receipt.error_category == "unsafe_resume_cursor"
    assert result.receipt.next_cursor is None
    assert secret not in serialized
    assert secret not in persisted


def test_partial_raw_result_requires_resume_cursor():
    with pytest.raises(ValueError, match="partial result requires records and a resume cursor"):
        RawConnectorResult(
            records=({"id": "page-1"},),
            state=RawState.PARTIAL,
        )


class LegacySocialConnector(BaseConnector):
    platform = "youtube"
    connector_name = "legacy_test"

    def __init__(self):
        self.calls = []

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        self.calls.append((keyword, count, time_filter, sort, region))
        return ConnectorResult(
            items=[SocialItem(
                platform="youtube",
                post_id="video-1",
                url="https://www.youtube.com/watch?v=video-1",
                text="Cooling order",
            )],
            health=SourceHealth(
                platform="youtube",
                connector=self.connector_name,
                status="ok",
                items_returned=1,
                items_requested=count,
            ),
        )

    async def health_check(self):
        return SourceHealth(
            platform="youtube", connector=self.connector_name, status="ok"
        )


def test_existing_social_collectors_register_through_adapter_without_changes(tmp_path):
    raw_connector = LegacySocialConnector()
    broker = SourceBroker()
    broker.register(raw_connector, priority=7)
    catalogue = CapabilityCatalogue()

    source_ids = register_social_broker(catalogue, broker)
    source_id = "social.youtube.legacy_test"
    executor = SourceExecutor(
        catalogue,
        CredentialResolver.with_bounty_defaults(environment={}),
        JsonReceiptStore(tmp_path),
    )
    result = asyncio.run(executor.search(
        source_id,
        SourceExecutionRequest(
            query="liquid cooling",
            geography="US",
            time_filter="month",
            limit=4,
        ),
    ))

    assert source_ids == (source_id,)
    assert result.receipt.state is ExecutionState.COMPLETE
    assert result.receipt.evidence[0]["external_id"] == "video-1"
    assert raw_connector.calls[0] == ("iphone", 1, "halfyear", "latest", "US")
    assert raw_connector.calls[1] == ("liquid cooling", 4, "month", "", "US")


@dataclass
class TrendCandidate:
    keyword: str
    source: str = "google_trends"
    search_volume: int = 100
    growth_pct: int = 250
    source_started_at: str = "2026-09-05T00:00:00+00:00"
    related_terms: tuple = ("cooling",)


class LegacyGoogleDiscovery:
    def __init__(self):
        self.geographies = []

    async def fetch_candidates(self, geo="US"):
        self.geographies.append(geo)
        return [TrendCandidate(keyword="liquid cooling")]


def test_existing_google_route_registers_and_normalizes_without_collector_changes(tmp_path):
    discovery = LegacyGoogleDiscovery()
    catalogue = CapabilityCatalogue()
    source_id = register_google_trends(catalogue, discovery)
    executor = SourceExecutor(
        catalogue,
        CredentialResolver.with_bounty_defaults(environment={}),
        JsonReceiptStore(tmp_path),
    )

    result = asyncio.run(executor.collect(
        source_id,
        SourceExecutionRequest(geography="SG", limit=50),
    ))

    assert source_id == "search.google_trends.trending_now"
    assert result.receipt.state is ExecutionState.COMPLETE
    assert result.receipt.evidence[0]["external_id"] == "liquid cooling"
    assert result.receipt.evidence[0]["attributes"]["growth_pct"] == 250
    assert discovery.geographies == ["US", "SG"]
