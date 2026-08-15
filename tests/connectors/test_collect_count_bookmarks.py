"""Connectors exposing verified collect payload fields populate canonical bookmarks.

Only connectors whose source payloads already provide a collect metric are
covered; nothing here invents upvotes/reposts/etc.
"""

from social_scraper.base import SocialItem
from social_scraper.connectors.douyin import DouyinConnector
from social_scraper.connectors.douyin_playwright import DouyinPlaywrightConnector
from social_scraper.connectors.tiktok import TikTokConnector
from social_scraper.connectors.tiktok_playwright import TikTokPlaywrightConnector
from social_scraper.connectors.xhs_playwright import XHSPlaywrightConnector
from social_scraper.conversations import normalize_broker_item

COLLECTED_AT = "2026-08-10T12:00:00+00:00"


def _collect_source(item: SocialItem):
    return item.raw.get("engagement_sources", {})


def test_douyin_connector_maps_collect_count_to_collects_and_bookmarks():
    item = DouyinConnector()._parse_item(
        {
            "aweme_id": "741",
            "desc": "switched to beta",
            "statistics": {"collect_count": 6, "digg_count": 5},
            "author": {"sec_uid": "s1", "nickname": "n", "unique_id": "u1"},
        }
    )
    assert item.collects == 6
    assert item.bookmarks == 6
    assert _collect_source(item) == {
        "collects": "collect_count",
        "bookmarks": "collect_count",
    }
    assert item.upvotes is None
    assert item.reposts is None
    assert item.replies is None


def test_douyin_playwright_connector_maps_collect_count_to_collects_and_bookmarks():
    item = DouyinPlaywrightConnector()._parse_item(
        {"aweme_id": "742", "desc": "d", "statistics": {"collect_count": 6}}
    )
    assert item.collects == 6
    assert item.bookmarks == 6
    assert _collect_source(item) == {
        "collects": "collect_count",
        "bookmarks": "collect_count",
    }


def test_tiktok_connector_maps_collect_count_to_collects_and_bookmarks():
    item = TikTokConnector()._parse_item(
        {"id": "743", "desc": "d", "stats": {"collectCount": 6}}
    )
    assert item.collects == 6
    assert item.bookmarks == 6
    assert _collect_source(item) == {
        "collects": "collectCount",
        "bookmarks": "collectCount",
    }


def test_tiktok_playwright_collect_count_flows_to_canonical_provenance():
    connector = TikTokPlaywrightConnector()
    item = connector._parse_item(
        {
            "id": "744",
            "desc": "d",
            "stats": {"collectCount": 6},
            "author": {"uniqueId": "creator", "followerCount": 500},
        }
    )
    assert item.collects == 6
    assert item.bookmarks == 6
    assert item.author_follower_count == 500
    assert _collect_source(item) == {
        "collects": "collectCount",
        "bookmarks": "collectCount",
    }

    serialized = item.to_dict()
    serialized["provenance"] = {
        "connector": connector.connector_name,
        "engagement_sources": _collect_source(item),
    }
    observation = normalize_broker_item(
        serialized, collected_at=COLLECTED_AT
    ).observation
    assert observation.engagement["bookmarks"] == 6
    assert observation.engagement["collects"] == 6
    assert observation.engagement["creator_followers"] == 500
    assert observation.engagement_sources["bookmarks"] == "collectCount"
    assert observation.engagement_sources["collects"] == "collectCount"
    assert (
        observation.engagement_sources["creator_followers"]
        == "author.follower_count"
    )


def test_xhs_connector_maps_collected_count_to_collects_and_bookmarks():
    item = XHSPlaywrightConnector()._parse_item(
        {
            "id": "note1",
            "note_card": {
                "desc": "d",
                "interact_info": {"collected_count": 6},
                "user": {"user_id": "u1", "nickname": "n"},
            },
        }
    )
    assert item.collects == 6
    assert item.bookmarks == 6
    assert _collect_source(item) == {
        "collects": "collected_count",
        "bookmarks": "collected_count",
    }


def test_missing_collect_count_stays_none_with_no_invention():
    item = TikTokPlaywrightConnector()._parse_item(
        {"id": "745", "desc": "d", "stats": {"diggCount": 4}}
    )
    assert item.collects is None
    assert item.bookmarks is None
    assert item.likes == 4
    assert item.upvotes is None
    assert item.reposts is None
    assert item.replies is None
    assert item.creator_followers is None
