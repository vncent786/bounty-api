from social_scraper.base import SocialItem
from social_scraper.connectors.tiktok_auth import (
    _dom_social_items,
    _finalize_items,
)


def _item(post_id, created_at):
    return SocialItem(
        platform="tiktok",
        post_id=post_id,
        url=f"https://www.tiktok.com/@qa/video/{post_id}",
        created_at=created_at,
    )


def test_latest_sort_happens_before_requested_count_is_applied():
    values = [
        _item("old", "2024-01-01T00:00:00+00:00"),
        _item("newest", "2026-08-29T00:00:00+00:00"),
        _item("middle", "2026-08-01T00:00:00+00:00"),
    ]

    selected, parsed_count = _finalize_items(values, count=2, sort="latest")

    assert parsed_count == 3
    assert [item.post_id for item in selected] == ["newest", "middle"]


def test_finalize_deduplicates_post_ids_before_truncation():
    values = [
        _item("same", "2026-08-01T00:00:00+00:00"),
        _item("same", "2026-08-20T00:00:00+00:00"),
        _item("other", "2026-08-10T00:00:00+00:00"),
    ]

    selected, parsed_count = _finalize_items(values, count=5, sort="latest")

    assert parsed_count == 3
    assert [item.post_id for item in selected] == ["same", "other"]
    assert selected[0].created_at == "2026-08-20T00:00:00+00:00"


def test_dom_fallback_keeps_all_captured_items_until_latest_sort():
    dom_items = [{
        "id": "old",
        "url": "https://www.tiktok.com/@qa/video/old",
        "author": "qa",
        "caption": "old",
        "created_at": "2024-01-01T00:00:00+00:00",
    }, {
        "id": "new",
        "url": "https://www.tiktok.com/@qa/video/new",
        "author": "qa",
        "caption": "new",
        "created_at": "2026-08-29T00:00:00+00:00",
    }]

    parsed = _dom_social_items(dom_items)
    selected, parsed_count = _finalize_items(
        parsed, count=1, sort="latest"
    )

    assert parsed_count == 2
    assert [item.post_id for item in selected] == ["new"]
