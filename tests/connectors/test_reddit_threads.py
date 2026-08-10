import asyncio

from social_scraper.base import SocialItem
from social_scraper.connectors.reddit import RedditConnector, parse_reddit_json_thread


REDDIT_JSON = [
    {
        "data": {"children": [{"kind": "t3", "data": {"id": "p1", "num_comments": 5}}]}
    },
    {
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "id": "c1", "parent_id": "t3_p1", "body": "I switched last week",
                        "author": "alice", "created_utc": 1750000000, "score": 8,
                        "permalink": "/r/test/comments/p1/topic/c1/",
                        "replies": {
                            "data": {"children": [{
                                "kind": "t1", "data": {
                                    "id": "c2", "parent_id": "t1_c1", "body": "Why?",
                                    "author": "bob", "created_utc": 1750000100, "score": 2,
                                    "permalink": "/r/test/comments/p1/topic/c2/", "replies": "",
                                }
                            }]}
                        },
                    },
                },
                {"kind": "more", "data": {"count": 3, "children": ["c3", "c4", "c5"]}},
            ]
        }
    },
]


def test_reddit_json_parser_preserves_reply_tree_and_more_placeholder():
    result = parse_reddit_json_thread(
        post_id="p1", payload=REDDIT_JSON, max_comments=10, max_depth=3,
    )
    assert result.status == "partial"
    assert result.truncated is True
    assert result.platform_reported_total == 5
    assert [record.external_id for record in result.records] == ["c1", "c2"]
    assert result.records[0].parent_external_id == "p1"
    assert result.records[1].parent_external_id == "c1"
    assert result.records[1].depth == 2


def test_reddit_depth_bound_excludes_reply_without_reparenting():
    result = parse_reddit_json_thread(
        post_id="p1", payload=REDDIT_JSON, max_comments=10, max_depth=1,
    )
    assert [record.external_id for record in result.records] == ["c1"]
    assert result.truncated is True


def test_reddit_fetch_reports_unavailable_when_both_routes_fail():
    class FailedConnector(RedditConnector):
        def _fetch_json(self, req):
            raise OSError("blocked")

        async def _camoufox_fallback(self, post, max_comments, max_depth):
            raise RuntimeError("challenge")

    post = SocialItem(
        platform="reddit", post_id="p1",
        url="https://www.reddit.com/r/test/comments/p1/topic/",
    )
    result = asyncio.run(FailedConnector().fetch_thread(post, 20, 2))
    assert result.status == "unavailable"
    assert result.error_category == "reddit_thread_routes_failed"
