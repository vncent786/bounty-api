"""Contract tests for the GHOST three-platform authenticated deep-check."""

import asyncio
from pathlib import Path

import scripts.run_ghost_social_deepcheck as deepcheck
from scripts.run_ghost_social_deepcheck import (
    _preflight_platform,
    classify_query_result,
    exact_object_match,
    load_contract,
    normalized_thread_state,
    original_post_eligibility,
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


def test_v2_contract_adds_aw_aliases_and_auditable_root_retention():
    root = Path(__file__).parents[2]
    contract = load_contract(
        root / "references" / "ghost-social-deepcheck-contract-v2.json"
    )

    assert '"GHOST AW Root Beer" -filter:retweets' in contract["platforms"]["x"]["queries"]
    assert "GHOST AW Root Beer" in contract["platforms"]["tiktok"]["queries"]
    assert contract["retention_rules"]["original_post_only"]
    assert contract["retention_rules"]["provider_record_audit"]


def test_exact_object_match_requires_ghost_plus_collaboration_identity():
    assert exact_object_match(item("GHOST Energy x A&W is finally here"))[0]
    assert exact_object_match(
        item("A&W Root Beer flavor review", username="ghostenergy")
    )[0]
    assert exact_object_match(item("GHOST Root Beer Energy Drink taste test"))[0]
    assert exact_object_match(item("Ghost AW root beer is an 11/10"))[0]

    assert not exact_object_match(item("A&W Root Beer float recipe"))[0]
    assert not exact_object_match(item("GHOST movie review"))[0]
    assert not exact_object_match(item("generic root beer energy drink"))[0]
    assert not exact_object_match(item(
        "We visited A&W for root beer, then used ghost hunting equipment at the motel."
    ))[0]


def test_x_reply_is_not_eligible_as_an_original_post():
    original = item("Ghost AW root beer is an 11/10")
    original.raw = {"legacy": {"conversation_id_str": "1"}}
    assert original_post_eligibility(original) == (True, [])

    reply = item("Looking forward to the ghost AW root beer drop")
    reply.raw = {
        "legacy": {
            "conversation_id_str": "parent",
            "in_reply_to_status_id_str": "parent",
        }
    }
    eligible, reasons = original_post_eligibility(reply)
    assert eligible is False
    assert reasons == [
        "x_in_reply_to_status_present",
        "x_conversation_id_differs_from_post_id",
    ]


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


def test_tiktok_preflight_retries_transient_empty_response_before_blocking(monkeypatch):
    monkeypatch.setattr(deepcheck, "PREFLIGHT_RETRY_DELAYS", (0, 0, 0))

    class FakeConnector:
        connector_name = "authenticated"

        def __init__(self):
            self.calls = 0

        async def search(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return result(
                    platform="tiktok",
                    status="error",
                    error="tiktok_empty_response",
                )
            bad_root = SocialItem(
                platform="tiktok",
                post_id="root-bad",
                url="https://www.tiktok.com/@user/video/root-bad",
                author_username="user",
                text="Nike canary",
                comments=5,
            )
            good_root = SocialItem(
                platform="tiktok",
                post_id="root-good",
                url="https://www.tiktok.com/@user/video/root-good",
                author_username="user",
                text="Nike canary",
                comments=1,
            )
            return result(platform="tiktok", status="ok", items=[bad_root, good_root])

        async def fetch_thread(self, root, **_kwargs):
            if root.post_id == "root-bad":
                return ThreadFetchResult(
                    platform="tiktok",
                    root_post_external_id=root.post_id,
                    status="unavailable",
                    attempted_route="tiktok_authenticated_browser_comments",
                    error_category="tiktok_comments_unavailable",
                    max_comments=12,
                    max_depth=2,
                )
            return ThreadFetchResult(
                platform="tiktok",
                root_post_external_id=root.post_id,
                status="complete",
                records=(ThreadRecord(
                    platform="tiktok",
                    external_id="comment-1",
                    record_type="comment",
                    parent_external_id=root.post_id,
                    root_post_external_id=root.post_id,
                    depth=1,
                    text="comment",
                    url="https://www.tiktok.com/i18n/share/comment/comment-1",
                ),),
                attempted_route="tiktok_authenticated_browser_comments",
                max_comments=12,
                max_depth=2,
            )

    connector = FakeConnector()
    receipt = asyncio.run(_preflight_platform(
        connector,
        platform="tiktok",
        query="nike",
    ))

    assert receipt["status"] == "healthy"
    assert connector.calls == 2
    assert [row["returned_count"] for row in receipt["canary_attempts"]] == [0, 2]
    assert [row["root_post_external_id"] for row in receipt["depth_canary_attempts"]] == [
        "root-bad", "root-good",
    ]
