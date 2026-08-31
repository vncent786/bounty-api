import asyncio

from social_scraper.base import BaseConnector, SocialItem
from social_scraper.broker import SourceBroker
from social_scraper.connectors.instagram_graphql import InstagramConnector
from social_scraper.connectors.tiktok_auth import TikTokAuthConnector
from social_scraper.conversations.thread_reader import ThreadFetchResult


def _post(platform, post_id="p1"):
    return SocialItem(
        platform=platform,
        post_id=post_id,
        url=f"https://example.test/{platform}/{post_id}",
        comments=10,
    )


def test_tiktok_owned_thread_reader_keeps_root_reply_relationships(monkeypatch):
    connector = TikTokAuthConnector()

    async def collect(_post, _max_comments, _max_depth):
        return {
            "root_payloads": [{
                "comments": [
                    {
                        "cid": "c1",
                        "text": "I bought it twice",
                        "create_time": 1700000000,
                        "digg_count": 7,
                        "reply_comment_total": 2,
                        "user": {"uid": "u1", "unique_id": "buyer"},
                    },
                    {
                        "cid": "c2",
                        "text": "Not for me",
                        "create_time": 1700000100,
                        "digg_count": 1,
                        "reply_comment_total": 0,
                        "user": {"uid": "u2", "unique_id": "skeptic"},
                    },
                ],
                "total": 10,
                "has_more": 1,
            }],
            "reply_payloads": [{
                "parent_comment_id": "c1",
                "payload": {
                    "comments": [{
                        "cid": "r1",
                        "text": "Same here",
                        "create_time": 1700000200,
                        "digg_count": 2,
                        "reply_id": "c1",
                        "user": {"uid": "u3", "unique_id": "replier"},
                    }],
                    "has_more": 0,
                },
            }],
            "limitations": [],
        }

    monkeypatch.setattr(connector, "_collect_thread_payloads", collect)
    result = asyncio.run(connector.fetch_thread(_post("tiktok"), 5, 2))

    assert result.status == "partial"
    assert result.truncated is True
    assert result.platform_reported_total == 10
    assert result.attempted_route == "tiktok_authenticated_browser_comments"
    assert [(record.external_id, record.parent_external_id, record.depth) for record in result.records] == [
        ("c1", "p1", 1),
        ("c2", "p1", 1),
        ("r1", "c1", 2),
    ]
    assert result.records[0].likes == 7
    assert result.records[0].published_at == "2023-11-14T22:13:20+00:00"


def test_tiktok_thread_reader_respects_depth_one(monkeypatch):
    connector = TikTokAuthConnector()

    async def collect(_post, _max_comments, _max_depth):
        return {
            "root_payloads": [{
                "comments": [{
                    "cid": "c1", "text": "root", "reply_comment_total": 1,
                    "user": {},
                }],
                "total": 1,
                "has_more": 0,
            }],
            "reply_payloads": [{
                "parent_comment_id": "c1",
                "payload": {"comments": [{"cid": "r1", "text": "reply", "user": {}}]},
            }],
            "limitations": [],
        }

    monkeypatch.setattr(connector, "_collect_thread_payloads", collect)
    result = asyncio.run(connector.fetch_thread(_post("tiktok"), 5, 1))
    assert [record.external_id for record in result.records] == ["c1"]


def test_instagram_owned_thread_reader_fetches_child_comments(monkeypatch):
    connector = InstagramConnector()

    async def roots(_media_id, _referer, _max_comments):
        return [{
            "comments": [{
                "pk": "c1",
                "text": "Switched brands",
                "created_at_utc": 1700000000,
                "comment_like_count": 5,
                "child_comment_count": 2,
                "user": {"pk": "u1", "username": "switcher"},
            }],
            "comment_count": 4,
            "has_more_comments": True,
            "next_min_id": "next",
        }]

    async def children(_media_id, parent_id, _referer, _limit):
        assert parent_id == "c1"
        return [{
            "child_comments": [{
                "pk": "r1",
                "text": "Why?",
                "created_at_utc": 1700000100,
                "comment_like_count": 1,
                "user": {"pk": "u2", "username": "asker"},
            }],
            "has_more_tail_child_comments": False,
        }]

    monkeypatch.setattr(connector, "_collect_media_comments", roots)
    monkeypatch.setattr(connector, "_collect_child_comments", children)
    monkeypatch.setattr(connector, "_ensure_authed", lambda: asyncio.sleep(0))
    result = asyncio.run(connector.fetch_thread(_post("instagram"), 5, 2))

    assert result.status == "partial"
    assert result.truncated is True
    assert result.platform_reported_total == 4
    assert result.attempted_route == "instagram_authenticated_web_comments"
    assert [(record.external_id, record.parent_external_id, record.depth) for record in result.records] == [
        ("c1", "p1", 1),
        ("r1", "c1", 2),
    ]
    assert result.records[0].likes == 5


def test_instagram_thread_reader_prioritizes_creator_reply_chains(monkeypatch):
    connector = InstagramConnector()
    child_calls = []

    async def roots(_media_id, _referer, _max_comments):
        return [{
            "comments": [
                {
                    "pk": "popular",
                    "text": "popular parent",
                    "child_comment_count": 100,
                    "user": {"username": "someone_else"},
                },
                {
                    "pk": "creator",
                    "text": "creator follow-up",
                    "child_comment_count": 1,
                    "user": {"username": "original_creator"},
                },
            ],
            "comment_count": 500,
            "has_more_comments": True,
        }]

    async def children(_media_id, parent_id, _referer, _limit):
        child_calls.append(parent_id)
        return [{
            "child_comments": [{
                "pk": f"reply-{parent_id}",
                "text": "reply",
                "user": {},
            }],
            "has_more_tail_child_comments": False,
        }]

    monkeypatch.setattr(connector, "_collect_media_comments", roots)
    monkeypatch.setattr(connector, "_collect_child_comments", children)
    monkeypatch.setattr(connector, "_ensure_authed", lambda: asyncio.sleep(0))
    result = asyncio.run(connector.fetch_thread(SocialItem(
        platform="instagram",
        post_id="p1",
        url="https://www.instagram.com/p/example/",
        author_username="original_creator",
        comments=500,
    ), 12, 2))

    assert child_calls[0] == "creator"
    assert {record.parent_external_id for record in result.records if record.depth == 2} == {
        "creator", "popular",
    }


def test_instagram_keyword_search_prefers_owned_browser_results(monkeypatch):
    connector = InstagramConnector()
    browser_media = [
        {
            "id": "m1",
            "code": "code1",
            "caption": {"text": "running shoe review"},
            "like_count": 10,
            "comment_count": 2,
            "taken_at": 1700000000,
            "user": {"username": "runner"},
        }
    ]

    async def browser_search(keyword, count):
        assert keyword == "running shoes"
        return browser_media, [{"source_id": "graphql-1", "payload": {"data": {}}}]

    async def fail_tag(*_args, **_kwargs):
        raise AssertionError("keyword browser success must not fall through to hashtag")

    monkeypatch.setattr(connector, "_browser_keyword_search", browser_search)
    monkeypatch.setattr(connector, "_fetch_tag_data", fail_tag)
    monkeypatch.setattr(connector, "_ensure_authed", lambda: asyncio.sleep(0))

    result = asyncio.run(connector.search("running shoes", count=5, sort="latest"))

    assert result.health.status == "ok"
    assert result.health.coverage["route"] == "keyword_browser_graphql"
    assert [item.post_id for item in result.items] == ["m1"]
    assert result.raw_records[0]["source_id"] == "graphql-1"


def test_tiktok_skips_reply_when_parent_root_was_not_retained(monkeypatch):
    connector = TikTokAuthConnector()

    async def collect(*_args):
        return {
            "root_payloads": [{
                "comments": [
                    {"cid": "kept", "text": "kept", "user": {}},
                    {"cid": "dropped", "text": "dropped", "user": {}},
                ],
                "total": 2,
                "has_more": 0,
            }],
            "reply_payloads": [{
                "parent_comment_id": "dropped",
                "payload": {"comments": [{"cid": "orphan", "text": "orphan", "user": {}}]},
            }],
            "limitations": [],
        }

    monkeypatch.setattr(connector, "_collect_thread_payloads", collect)
    result = asyncio.run(connector.fetch_thread(_post("tiktok"), 2, 2))
    assert [record.external_id for record in result.records] == ["kept"]
    assert result.status == "partial"


def test_instagram_skips_reply_when_parent_root_was_not_retained(monkeypatch):
    connector = InstagramConnector()

    async def roots(*_args):
        return [{
            "comments": [
                {"pk": "kept", "text": "kept", "user": {}},
                {"pk": "dropped", "text": "dropped", "child_comment_count": 1, "user": {}},
            ],
            "comment_count": 2,
            "has_more_comments": False,
        }]

    async def children(_media_id, parent_id, _referer, _limit):
        return [{"child_comments": [{"pk": "orphan", "text": "orphan", "user": {}}]}]

    monkeypatch.setattr(connector, "_collect_media_comments", roots)
    monkeypatch.setattr(connector, "_collect_child_comments", children)
    monkeypatch.setattr(connector, "_ensure_authed", lambda: asyncio.sleep(0))
    result = asyncio.run(connector.fetch_thread(_post("instagram"), 2, 2))
    assert [record.external_id for record in result.records] == ["kept"]
    assert result.status == "partial"


def test_owned_thread_empty_without_reported_total_is_not_complete(monkeypatch):
    tiktok = TikTokAuthConnector()
    instagram = InstagramConnector()

    async def tiktok_empty(*_args):
        return {"root_payloads": [{"comments": [], "has_more": 0}], "reply_payloads": []}

    async def instagram_empty(*_args):
        return [{"comments": [], "has_more_comments": False}]

    monkeypatch.setattr(tiktok, "_collect_thread_payloads", tiktok_empty)
    monkeypatch.setattr(instagram, "_collect_media_comments", instagram_empty)
    monkeypatch.setattr(instagram, "_ensure_authed", lambda: asyncio.sleep(0))

    tiktok_result = asyncio.run(tiktok.fetch_thread(
        SocialItem(platform="tiktok", post_id="p1", url="https://example.test/tiktok/p1"),
        5,
        2,
    ))
    instagram_result = asyncio.run(instagram.fetch_thread(
        SocialItem(platform="instagram", post_id="p1", url="https://example.test/instagram/p1"),
        5,
        2,
    ))
    assert tiktok_result.status == "partial"
    assert instagram_result.status == "partial"


def test_owned_thread_nonempty_without_reported_total_stays_partial(monkeypatch):
    tiktok = TikTokAuthConnector()
    instagram = InstagramConnector()

    async def tiktok_one(*_args):
        return {
            "root_payloads": [{"comments": [{"cid": "c1", "text": "one", "user": {}}]}],
            "reply_payloads": [],
        }

    async def instagram_one(*_args):
        return [{"comments": [{"pk": "c1", "text": "one", "user": {}}]}]

    monkeypatch.setattr(tiktok, "_collect_thread_payloads", tiktok_one)
    monkeypatch.setattr(instagram, "_collect_media_comments", instagram_one)
    monkeypatch.setattr(instagram, "_ensure_authed", lambda: asyncio.sleep(0))

    tiktok_result = asyncio.run(tiktok.fetch_thread(
        SocialItem(platform="tiktok", post_id="p1", url="https://example.test/tiktok/p1"),
        5,
        1,
    ))
    instagram_result = asyncio.run(instagram.fetch_thread(
        SocialItem(platform="instagram", post_id="p1", url="https://example.test/instagram/p1"),
        5,
        1,
    ))
    assert tiktok_result.status == "partial"
    assert instagram_result.status == "partial"


def test_instagram_media_parser_tolerates_missing_user_object():
    item = InstagramConnector._media_to_item({
        "id": "m1",
        "code": "code1",
        "caption": {"text": "caption"},
        "user": None,
        "image_versions2": None,
    })
    assert item.post_id == "m1"
    assert item.author_username == ""
    assert item.thumbnail_url is None


def test_broker_does_not_mask_selected_tiktok_depth_failure_with_unsupported_fallback():
    class AuthenticatedTikTok(BaseConnector):
        platform = "tiktok"
        connector_name = "authenticated"

        async def search(self, *_args, **_kwargs):
            raise AssertionError("not used")

        async def health_check(self):
            raise AssertionError("not used")

        async def fetch_thread(self, post, max_comments, max_depth):
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=post.post_id,
                status="unavailable",
                attempted_route="tiktok_authenticated_browser_comments",
                error_category="tiktok_comments_unavailable",
                max_comments=max_comments,
                max_depth=max_depth,
            )

    class SearchOnlyFallback(BaseConnector):
        platform = "tiktok"
        connector_name = "playwright"

        async def search(self, *_args, **_kwargs):
            raise AssertionError("not used")

        async def health_check(self):
            raise AssertionError("not used")

    broker = SourceBroker()
    broker.register(AuthenticatedTikTok(), priority=1)
    broker.register(SearchOnlyFallback(), priority=2)

    result = asyncio.run(broker.fetch_thread({
        "platform": "tiktok",
        "post_id": "video-1",
        "url": "https://www.tiktok.com/@user/video/video-1",
        "provenance": {"connector": "authenticated"},
    }, max_comments=40, max_depth=2))

    assert result.status == "unavailable"
    assert result.error_category == "tiktok_comments_unavailable"
    assert result.attempted_route == "tiktok_authenticated_browser_comments"


def test_broker_records_selected_tiktok_depth_timeout_instead_of_unsupported():
    class TimedOutTikTok(BaseConnector):
        platform = "tiktok"
        connector_name = "authenticated"

        async def search(self, *_args, **_kwargs):
            raise AssertionError("not used")

        async def health_check(self):
            raise AssertionError("not used")

        async def fetch_thread(self, *_args, **_kwargs):
            raise asyncio.TimeoutError

    class SearchOnlyFallback(BaseConnector):
        platform = "tiktok"
        connector_name = "playwright"

        async def search(self, *_args, **_kwargs):
            raise AssertionError("not used")

        async def health_check(self):
            raise AssertionError("not used")

    broker = SourceBroker()
    broker.register(TimedOutTikTok(), priority=1)
    broker.register(SearchOnlyFallback(), priority=2)

    result = asyncio.run(broker.fetch_thread({
        "platform": "tiktok",
        "post_id": "video-1",
        "url": "https://www.tiktok.com/@user/video/video-1",
        "provenance": {"connector": "authenticated"},
    }, max_comments=40, max_depth=2))

    assert result.status == "unavailable"
    assert result.error_category == "tiktok_thread_timeout"
    assert result.attempted_route == "authenticated"
