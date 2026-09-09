import asyncio
import json

from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.connectors.youtube import YouTubeConnector, parse_youtube_thread


RAW_COMMENTS = [
    {
        "id": "c1", "parent": "root", "text": "This fixed my problem",
        "author": "Alice", "author_id": "UC1", "like_count": 4,
        "timestamp": 1750000000,
    },
    {
        "id": "c2", "parent": "c1", "text": "It did not work for me",
        "author": "Bob", "author_id": "UC2", "like_count": 2,
        "timestamp": 1750000100,
    },
    {
        "id": "c3", "parent": "root", "text": "What about Android?",
        "author": "Cara", "author_id": "UC3", "like_count": 1,
        "timestamp": 1750000200,
    },
]


def test_youtube_parser_keeps_comments_replies_and_bounds():
    result = parse_youtube_thread(
        video_id="vid1", comments=RAW_COMMENTS,
        max_comments=2, max_depth=2, platform_reported_total=5,
    )
    assert result.status == "partial"
    assert result.truncated is True
    assert [record.external_id for record in result.records] == ["c1", "c2"]
    assert result.records[0].parent_external_id == "vid1"
    assert result.records[0].depth == 1
    assert result.records[1].parent_external_id == "c1"
    assert result.records[1].depth == 2


def test_youtube_depth_one_excludes_replies_without_reparenting():
    result = parse_youtube_thread(
        video_id="vid1", comments=RAW_COMMENTS,
        max_comments=10, max_depth=1, platform_reported_total=3,
    )
    assert [record.external_id for record in result.records] == ["c1", "c3"]
    assert result.truncated is True


def test_youtube_fetch_reports_disabled_comments_explicitly():
    class DisabledConnector(YouTubeConnector):
        def _run_ytdlp_result(self, cmd, timeout=30):
            return 1, "", "ERROR: Comments are turned off"

    post = SocialItem(
        platform="youtube", post_id="vid1",
        url="https://www.youtube.com/watch?v=vid1",
    )
    result = asyncio.run(DisabledConnector().fetch_thread(post, 20, 2))
    assert result.status == "disabled"
    assert result.error_category == "comments_disabled"
    assert result.records == ()


def test_youtube_search_preserves_timeout_and_process_failure_categories():
    class TimeoutConnector(YouTubeConnector):
        def _run_ytdlp_result(self, cmd, timeout=30):
            return 124, "", "yt-dlp retrieval timed out"

    class FailedConnector(YouTubeConnector):
        def _run_ytdlp_result(self, cmd, timeout=30):
            return 1, "", "provider process failed"

    timeout = asyncio.run(TimeoutConnector().search("iphone", count=1))
    failed = asyncio.run(FailedConnector().search("iphone", count=1))

    assert timeout.items == []
    assert timeout.health.status == "error"
    assert timeout.health.error == "youtube_timeout"
    assert failed.items == []
    assert failed.health.status == "error"
    assert failed.health.error == "youtube_process_error"


def test_youtube_search_uses_bounded_overfetch_and_strict_network_retries():
    captured = {}

    class CapturingConnector(YouTubeConnector):
        def _run_ytdlp_result(self, cmd, timeout=30):
            captured["cmd"] = list(cmd)
            captured["timeout"] = timeout
            return 0, json.dumps({
                "id": "video-1",
                "title": "iPhone review",
                "webpage_url": "https://www.youtube.com/watch?v=video-1",
                "upload_date": "20260909",
                "view_count": 100,
            }), ""

    result = asyncio.run(CapturingConnector().search(
        "iphone", count=10, time_filter="halfyear", sort="latest"
    ))

    assert result.health.status == "ok"
    assert "ytsearch15:iphone" in captured["cmd"]
    assert "--ignore-errors" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--socket-timeout") + 1] == "10"
    assert captured["cmd"][captured["cmd"].index("--retries") + 1] == "1"
    assert captured["cmd"][captured["cmd"].index("--extractor-retries") + 1] == "1"
    assert captured["timeout"] == 105


def test_youtube_health_uses_known_positive_canary_query():
    class CanaryConnector(YouTubeConnector):
        async def search(self, keyword, **_kwargs):
            assert keyword == "iphone"
            item = SocialItem(
                platform="youtube",
                post_id="known-positive",
                url="https://www.youtube.com/watch?v=known-positive",
            )
            return ConnectorResult(
                items=[item],
                health=SourceHealth(
                    platform="youtube",
                    connector="ytdlp_free",
                    status="ok",
                    items_returned=1,
                ),
            )

    health = asyncio.run(CanaryConnector().health_check())
    assert health.status == "ok"
    assert health.items_returned == 1
