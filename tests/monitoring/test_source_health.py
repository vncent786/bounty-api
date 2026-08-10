"""Source-health propagation and compatibility tests for zone monitoring."""

from __future__ import annotations

import pytest

from social_scraper.base import BaseConnector, ConnectorResult, SocialItem, SourceHealth
from social_scraper.broker import SourceBroker
from social_scraper.monitoring.monitor import TrendMonitor


class FixtureConnector(BaseConnector):
    def __init__(self, platform, connector_name, status):
        self.platform = platform
        self.connector_name = connector_name
        self.status = status

    async def search(self, keyword, count=20, time_filter="", sort="", region=""):
        if self.status == "ok":
            items = [
                SocialItem(
                    platform=self.platform,
                    post_id=f"{self.platform}-{keyword}",
                    url=f"https://example.test/{self.platform}/{keyword}",
                    text="I switched products after the quality changed",
                    likes=3,
                    views=30,
                )
            ]
            error = None
        else:
            items = []
            error = "camoufox_timeout"
        return ConnectorResult(
            items=items,
            health=SourceHealth(
                platform=self.platform,
                connector=self.connector_name,
                status=self.status,
                items_requested=count,
                items_returned=len(items),
                error=error,
            ),
        )

    async def health_check(self):
        return SourceHealth(
            platform=self.platform,
            connector=self.connector_name,
            status=self.status,
        )


def health_visible_broker():
    broker = SourceBroker()
    broker.register(FixtureConnector("youtube", "yt-dlp", "ok"))
    broker.register(FixtureConnector("reddit", "camoufox", "error"))
    return broker


@pytest.mark.anyio
async def test_real_broker_health_is_propagated_and_persisted(registry, sample_zone):
    zone_id = registry.create(sample_zone)
    report = await TrendMonitor(registry, health_visible_broker()).run_zone(sample_zone.name)

    assert len(report.source_health) == len(sample_zone.keywords) * 2
    assert {
        (entry["keyword"], entry["platform"], entry["status"])
        for entry in report.source_health
    } >= {
        ("switched from alpha", "youtube", "ok"),
        ("switched from alpha", "reddit", "error"),
    }
    assert all("keyword" in entry for entry in report.source_health)
    assert registry.get_snapshots(zone_id)[0]["source_health"] == report.source_health


@pytest.mark.anyio
async def test_collect_zone_keeps_two_value_public_contract(registry, sample_zone):
    registry.create(sample_zone)
    monitor = TrendMonitor(registry, health_visible_broker())

    result = await monitor.collect_zone(sample_zone)

    assert isinstance(result, tuple)
    assert len(result) == 2
    items, platform_summary = result
    assert items
    assert platform_summary["youtube"]["items"] == len(sample_zone.keywords)
