"""Contract tests for the GHOST three-platform authenticated deep-check."""

from pathlib import Path

from scripts.run_ghost_social_deepcheck import (
    classify_query_result,
    exact_object_match,
    load_contract,
    normalized_thread_state,
)
from social_scraper.base import ConnectorResult, SocialItem, SourceHealth
from social_scraper.conversations.thread_reader import ThreadFetchResult, ThreadRecord


def item(text: str, *, username: str = "user") -> SocialItem:
    return SocialItem(
        platform="x",
        post_id="1",
        url="https://x.com/user/status/1",
        author_username=username,
        text=text,
    )


def result(
    *,
    platform: str,
    status: str,
    items: list[SocialItem] | None = None,
    error: str | None = None,
    coverage: dict | None = None,
) -> ConnectorResult:
    return ConnectorResult(
        items=items or [],
        health=SourceHealth(
            platform=platform,
            connector="owned",
            status=status,
            error=error,
            coverage=coverage or {},
        ),
    )


def test_contract_freezes_multiple_identity_safe_queries_per_platform():
    root = Path(__file__).parents[2]
    contract = load_contract(
        root / "references" / "ghost-social-deepcheck-contract-v1.json"
    )

    assert set(contract["platforms"]) == {"x", "tiktok", "instagram"}
    assert all(
        len(platform["queries"]) >= 2
        for platform in contract["platforms"].values()
    )
    assert contract["window"] == {
        "time_filter": "halfyear",
        "sort": "latest",
        "root_cap_per_query": 60,
        "comment_reply_cap_per_root": 60,
        "max_reply_depth": 2,
    }


def test_exact_object_match_requires_ghost_plus_collaboration_identity():
    assert exact_object_match(item("GHOST Energy x A&W is finally here"))[0]
    assert exact_object_match(
        item("A&W Root Beer flavor review", username="ghostenergy")
    )[0]
    assert exact_object_match(item("GHOST Root Beer Energy Drink taste test"))[0]

    assert not exact_object_match(item("A&W Root Beer float recipe"))[0]
    assert not exact_object_match(item("GHOST movie review"))[0]
    assert not exact_object_match(item("generic root beer energy drink"))[0]
    assert not exact_object_match(item(
        "We visited A&W for root beer, then used ghost hunting equipment at the motel."
    ))[0]


def test_query_result_separates_relevant_no_match_empty_and_failure():
    irrelevant = item("unrelated post")
    assert classify_query_result(
        "x", "query", result(platform="x", status="ok", items=[irrelevant]), 0
    ) == ("complete_no_match", None)
    assert classify_query_result(
        "x", "query", result(platform="x", status="ok", items=[irrelevant]), 1
    ) == ("complete_relevant", None)
    assert classify_query_result(
        "x", "query", result(platform="x", status="ok"), 0
    ) == ("empty", None)

    tiktok_empty = result(
        platform="tiktok",
        status="partial",
        error="tiktok_query_empty",
        coverage={
            "page_state": "query_empty",
            "api_payloads_parsed": 1,
            "parsed_items_before_dedup": 0,
        },
    )
    assert classify_query_result(
        "tiktok", "query", tiktok_empty, 0
    ) == ("empty", None)

    instagram_empty = result(
        platform="instagram",
        status="partial",
        coverage={"route": "hashtag_web_info", "tag_media_count": 0},
    )
    assert classify_query_result(
        "instagram", "#ghostrootbeer", instagram_empty, 0
    ) == ("empty", None)
    assert classify_query_result(
        "instagram", "GHOST A&W", instagram_empty, 0
    ) == ("failed", "ambiguous_zero_no_explicit_source_state")


def test_bounded_partial_thread_requires_returned_records_without_error():
    record = ThreadRecord(
        platform="x",
        external_id="2",
        record_type="comment",
        parent_external_id="1",
        root_post_external_id="1",
        depth=1,
        text="comment",
    )
    bounded = ThreadFetchResult(
        platform="x",
        root_post_external_id="1",
        status="partial",
        records=(record,),
        truncated=True,
        max_comments=60,
        max_depth=2,
    )
    unresolved = ThreadFetchResult(
        platform="x",
        root_post_external_id="1",
        status="partial",
        truncated=True,
        max_comments=60,
        max_depth=2,
    )

    assert normalized_thread_state(bounded) == "bounded_partial"
    assert normalized_thread_state(unresolved) == "failed"
