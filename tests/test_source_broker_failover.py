import asyncio

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.broker import SourceBroker


class FakeConnector(BaseConnector):
    platform = "tiktok"

    def __init__(self, name, results):
        self.connector_name = name
        self.results = list(results)
        self.calls = 0

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        self.calls += 1
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    async def health_check(self):
        return SourceHealth(platform=self.platform, connector=self.connector_name, status="ok")


def connector_result(name, status, items=None, error=None, raw_records=None):
    items = items or []
    return ConnectorResult(
        items=items,
        health=SourceHealth(
            platform="tiktok",
            connector=name,
            status=status,
            items_returned=len(items),
            items_requested=10,
            error=error,
        ),
        raw_records=raw_records or [],
    )


def test_broker_falls_back_and_preserves_attempt_health_and_provenance():
    primary = FakeConnector("owned", [connector_result("owned", "error", error="blocked")])
    fallback_item = SocialItem(platform="tiktok", post_id="p1", url="https://example/p1")
    fallback = FakeConnector("provider", [connector_result("provider", "ok", [fallback_item])])
    broker = SourceBroker()
    broker.register(primary, priority=10)
    broker.register(fallback, priority=20)

    response = asyncio.run(broker.search("shoes", platforms=["tiktok"], count=10, region="US"))

    assert primary.calls == 1
    assert fallback.calls == 1
    assert [h["connector"] for h in response["source_health"]] == ["owned", "provider"]
    assert response["items"][0]["provenance"]["connector"] == "provider"
    assert response["items"][0]["provenance"]["fetched_at"]
    assert response["platform_results"]["tiktok"]["selected_connector"] == "provider"


def test_broker_treats_empty_partial_result_as_unavailable_and_uses_fallback():
    primary = FakeConnector("owned", [connector_result("owned", "partial")])
    fallback_item = SocialItem(platform="tiktok", post_id="p2", url="https://example/p2")
    fallback = FakeConnector("provider", [connector_result("provider", "ok", [fallback_item])])
    broker = SourceBroker()
    broker.register(primary, priority=10)
    broker.register(fallback, priority=20)

    response = asyncio.run(broker.search("bags", platforms=["tiktok"]))

    assert response["count"] == 1
    assert response["platform_results"]["tiktok"]["status"] == "ok"
    assert response["platform_results"]["tiktok"]["attempted_connectors"] == ["owned", "provider"]


def test_broker_keeps_backward_compatible_single_connector_registration():
    item = SocialItem(platform="tiktok", post_id="p3", url="https://example/p3")
    connector = FakeConnector("only", [connector_result("only", "ok", [item])])
    broker = SourceBroker()
    broker.register(connector)

    response = asyncio.run(broker.search("test", platforms=["tiktok"]))

    assert response["count"] == 1
    assert broker.list_platforms() == ["tiktok"]
    assert broker.list_routes()["tiktok"] == [{"connector": "only", "priority": 100}]


def test_broker_deduplicates_requested_platforms():
    item = SocialItem(platform="tiktok", post_id="p4", url="https://example/p4")
    connector = FakeConnector("only", [connector_result("only", "ok", [item])])
    broker = SourceBroker()
    broker.register(connector)

    response = asyncio.run(broker.search("test", platforms=["tiktok", "tiktok", "tiktok"]))

    assert connector.calls == 1
    assert response["platforms"] == ["tiktok"]


def test_broker_retains_nonempty_partial_result_if_fallback_fails():
    partial_item = SocialItem(platform="tiktok", post_id="partial", url="https://example/partial")
    primary = FakeConnector("owned", [connector_result("owned", "partial", [partial_item])])
    fallback = FakeConnector("provider", [connector_result("provider", "error", error="secret upstream URL")])
    broker = SourceBroker()
    broker.register(primary, priority=10)
    broker.register(fallback, priority=20)

    response = asyncio.run(broker.search("test", platforms=["tiktok"]))

    assert response["count"] == 1
    assert response["platform_results"]["tiktok"]["status"] == "partial"
    assert response["items"][0]["provenance"]["connector"] == "owned"
    assert response["source_health"][-1]["error"] == "connector_error"


class HangingConnector(FakeConnector):
    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        self.calls += 1
        await asyncio.sleep(1)
        return connector_result(self.connector_name, "partial")


def test_broker_times_out_hanging_route_and_falls_back():
    hanging = HangingConnector("hanging", [])
    item = SocialItem(platform="tiktok", post_id="fast", url="https://example/fast")
    fallback = FakeConnector("fast", [connector_result("fast", "ok", [item])])
    broker = SourceBroker(route_timeout_seconds=0.01)
    broker.register(hanging, priority=10)
    broker.register(fallback, priority=20)

    response = asyncio.run(broker.search("test", platforms=["tiktok"]))

    assert response["count"] == 1
    assert response["source_health"][0]["error"] == "connector_timeout"


def test_raw_records_from_unselected_attempt_are_preserved_for_collection():
    primary = FakeConnector("owned", [connector_result(
        "owned",
        "partial",
        raw_records=[{"source_id": "raw-1", "payload": {"id": "raw-1"}}],
    )])
    fallback_item = SocialItem(platform="tiktok", post_id="selected", url="https://example/selected")
    fallback = FakeConnector("fallback", [connector_result("fallback", "ok", [fallback_item])])
    broker = SourceBroker()
    broker.register(primary, priority=10)
    broker.register(fallback, priority=20)

    response = asyncio.run(broker.search(
        "test", platforms=["tiktok"], include_source_records=True,
    ))

    assert response["platform_results"]["tiktok"]["selected_connector"] == "fallback"
    assert response["_source_records"][0]["connector"] == "owned"
    assert response["_source_records"][0]["payload"] == {"id": "raw-1"}
