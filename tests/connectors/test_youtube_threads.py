import asyncio

from social_scraper.base import SocialItem
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
