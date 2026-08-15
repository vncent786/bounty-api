"""Contract tests for SocialItem engagement fields (legacy + canonical)."""

from social_scraper.base import SocialItem


def test_social_item_defaults_keep_legacy_contract():
    item = SocialItem(platform="tiktok", post_id="1", url="https://example/1")
    data = item.to_dict()
    assert data["author"]["follower_count"] is None
    assert data["engagement"] == {
        "views": None,
        "likes": None,
        "comments": None,
        "shares": None,
        "collects": None,
        "upvotes": None,
        "replies": None,
        "reposts": None,
        "bookmarks": None,
        "creator_followers": None,
    }


def test_social_item_emits_canonical_engagement_fields():
    item = SocialItem(
        platform="reddit",
        post_id="2",
        url="https://example/2",
        likes=10,
        comments=4,
        views=100,
        shares=6,
        collects=3,
        upvotes=5,
        replies=1,
        reposts=2,
        bookmarks=3,
        creator_followers=9,
    )
    assert item.to_dict()["engagement"] == {
        "views": 100,
        "likes": 10,
        "comments": 4,
        "shares": 6,
        "collects": 3,
        "upvotes": 5,
        "replies": 1,
        "reposts": 2,
        "bookmarks": 3,
        "creator_followers": 9,
    }


def test_social_item_keeps_author_follower_count_and_defaults_new_fields():
    item = SocialItem(
        platform="x",
        post_id="3",
        url="https://example/3",
        author_follower_count=77,
    )
    data = item.to_dict()
    assert data["author"]["follower_count"] == 77
    for metric in ("upvotes", "replies", "reposts", "bookmarks", "creator_followers"):
        assert data["engagement"][metric] is None


def test_social_item_explicit_zero_is_preserved():
    item = SocialItem(
        platform="x",
        post_id="4",
        url="https://example/4",
        bookmarks=0,
        creator_followers=0,
        upvotes=0,
    )
    engagement = item.to_dict()["engagement"]
    assert engagement["bookmarks"] == 0
    assert engagement["creator_followers"] == 0
    assert engagement["upvotes"] == 0
