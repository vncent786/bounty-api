"""Behavior tests for collection, clustering, snapshots, and partial failures."""

from __future__ import annotations

import pytest

from social_scraper.monitoring.monitor import TrendMonitor


class PartialBroker:
    """One keyword succeeds while another route fails."""

    def __init__(self):
        self.calls = []

    async def search(self, keyword, platforms, count):
        self.calls.append((keyword, tuple(platforms), count))
        if keyword == "cancelled alpha":
            raise RuntimeError("reddit route unavailable")
        return {
            "items": [
                {
                    "platform": "youtube",
                    "title": f"Why I {keyword}",
                    "text": "I changed products after repeated quality problems",
                    "author_username": "source-user",
                    "url": f"https://example.test/{len(self.calls)}",
                    "engagement": {"likes": 12, "views": 240},
                }
            ]
        }


@pytest.mark.anyio
async def test_run_zone_keeps_successes_when_one_keyword_fails(registry, sample_zone):
    zone_id = registry.create(sample_zone)
    broker = PartialBroker()
    monitor = TrendMonitor(registry, broker)

    report = await monitor.run_zone(sample_zone.name)

    assert len(broker.calls) == 3
    assert all(call[2] == 10 for call in broker.calls)
    assert report.zone_name == sample_zone.name
    assert report.total_items == 2
    assert report.platform_summary == {
        "youtube": {"items": 2, "likes": 24, "views": 480}
    }
    assert report.cluster_count >= 1
    assert all(alert.alert_type == "new" for alert in report.alerts)
    assert all(alert.zone_name == sample_zone.name for alert in report.alerts)
    assert any(
        health["keyword"] == "cancelled alpha" and health["status"] == "error"
        for health in report.source_health
    )

    snapshots = registry.get_snapshots(zone_id)
    assert len(snapshots) == 1
    assert snapshots[0]["item_count"] == 2
    assert snapshots[0]["source_health"] == report.source_health
    assert registry.get(zone_id).last_collected_at


@pytest.mark.anyio
async def test_second_run_compares_against_previous_snapshot(registry, sample_zone):
    registry.create(sample_zone)
    monitor = TrendMonitor(registry, PartialBroker())

    first = await monitor.run_zone(sample_zone.name)
    second = await monitor.run_zone(sample_zone.name)

    assert first.alerts
    assert second.total_items == first.total_items
    assert second.cluster_count == first.cluster_count
    assert second.alerts == []
    assert len(registry.get_snapshots(sample_zone.id or 1)) == 2


def test_cluster_posts_preserves_source_urls_and_platforms(registry):
    monitor = TrendMonitor(registry, broker=None)
    clusters = monitor.cluster_posts(
        [
            {
                "platform": "reddit",
                "title": "Customers switching after quality decline",
                "text": "I stopped buying Alpha and moved to Beta",
                "author_username": "user-a",
                "url": "https://example.test/reddit-a",
                "engagement": {"likes": 8, "views": None},
            },
            {
                "platform": "youtube",
                "title": "Why buyers are moving away from Alpha",
                "text": "Quality decline made me switch to Beta",
                "author_username": "user-b",
                "url": "https://example.test/youtube-b",
                "engagement": {"likes": 10, "views": 100},
            },
        ]
    )

    assert len(clusters) == 1
    cluster = clusters[0]
    assert set(cluster.platforms) == {"reddit", "youtube"}
    assert {p["url"] for p in cluster.sample_posts} == {
        "https://example.test/reddit-a",
        "https://example.test/youtube-b",
    }
